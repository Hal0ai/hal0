"""GET /api/services/health — dashboard services health aggregator.

Returns a stable list of three well-known services (comfyui, hermes,
openwebui) with honest up/down state.  Every source degrades
gracefully — a probe failure yields up=false, never a 500.

Real probes: comfyui (in-process /system_stats+/queue), hermes
(systemd unit state), openwebui (loopback GET /health — SpikeB §5.4).

HARD RULE: up=true requires a real signal.  Services with no wired probe
report up=false, detail="unmonitored" — never a fabricated "up".

Mount: lead wires ``router`` under prefix="/api/services" in
src/hal0/api/__init__.py — do NOT edit that file here.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Request

# In-process callables reused from existing routes — no HTTP self-calls.
from hal0.api.routes.comfyui import (
    _HERMES_UNIT,
    _comfyui_base_url,
    _fetch_json,
    _queue_counts,
    _systemd_active,
)

log = structlog.get_logger(__name__)

router = APIRouter()

# OpenWebUI binds 0.0.0.0:3001 in hal0-openwebui.service (the port is fixed
# in the unit — see config.py _OPENWEBUI_PORT). We probe it over loopback,
# independent of the browser-facing public URL (_openwebui_url). The probe
# host:port is overridable via env for tests / non-default deployments.
# SpikeB §5.4 confirmed GET http://127.0.0.1:3001/health → 200 when up.
_OPENWEBUI_PROBE_URL = (
    os.environ.get("HAL0_OPENWEBUI_PROBE_URL", "").strip().rstrip("/")
    or "http://127.0.0.1:3001/health"
)
# Tight timeout: this probe runs on the dashboard's /api/services/health
# poll path and must never stall it. A down/refusing service returns fast.
_PROBE_TIMEOUT = httpx.Timeout(connect=1.0, read=2.0, write=1.0, pool=1.0)

# ── helpers ───────────────────────────────────────────────────────────────────


def _openwebui_url() -> str | None:
    """Configured public URL for OpenWebUI, or None when absent."""
    public = os.environ.get("HAL0_OPENWEBUI_PUBLIC_URL", "").strip().rstrip("/")
    return public or None


async def _unit_active_state(unit: str) -> str:
    """Raw ``systemctl is-active`` output for *unit* (``active`` / ``inactive``
    / ``failed`` / ``unknown`` on any probe error).

    Down is not one state: ``failed`` means the unit crashed and needs
    attention; ``inactive`` means it was stopped on purpose (or never
    started) and will come back on demand. The dashboard renders those
    differently — red vs grey — so the distinction must survive the probe.
    """
    import asyncio
    import shutil

    systemctl = shutil.which("systemctl")
    if not systemctl:
        return "unknown"
    try:
        proc = await asyncio.create_subprocess_exec(
            systemctl,
            "is-active",
            unit,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
    except (TimeoutError, OSError):
        return "unknown"
    return out.decode("utf-8", "replace").strip() or "unknown"


def _down_state(unit_state: str, fallback_detail: str) -> tuple[str, str]:
    """Map a not-up unit's is-active output to (service state, detail).

    ``inactive`` → ("stopped", on-demand wording) — deliberate, grey.
    ``failed``   → ("down", crash wording) — needs attention, red.
    Anything else (``unknown``, ``activating``, or an ``active`` unit whose
    HTTP probe still failed) → ("down", *fallback_detail*): we can't prove
    the stop was deliberate, so keep the honest probe detail and stay red.
    """
    if unit_state == "inactive":
        return "stopped", "stopped — starts on demand"
    if unit_state == "failed":
        return "down", "crashed — systemd unit failed"
    return "down", fallback_detail


_IMG_SLOT_UNIT = "hal0-slot@img.service"
_OPENWEBUI_UNIT = "hal0-openwebui.service"


async def _img_unit_state(request: Request) -> str:
    """is-active state of the img slot's unit, resolved through the naming
    seam (#1417: on an id-keyed box the unit is ``hal0-slot@<id>``, not
    ``hal0-slot@img``). Falls back to the name-keyed unit when the slot
    manager or config is unavailable.
    """
    unit = _IMG_SLOT_UNIT
    manager = getattr(request.app.state, "slot_manager", None)
    if manager is not None and hasattr(manager, "get_config"):
        try:
            cfg = await manager.get_config("img")
            from hal0.slots.naming import slot_instance_token, slot_unit_name

            unit = slot_unit_name(slot_instance_token(cfg))
        except Exception:
            pass
    return await _unit_active_state(unit)


# ── per-service probes ────────────────────────────────────────────────────────


async def _probe_comfyui() -> tuple[bool, str, dict[str, str] | None, str | None]:
    """Probe ComfyUI via its /system_stats + /queue endpoints (in-process).

    Returns (up, detail, stat, url).
    Reuses _fetch_json / _queue_counts from hal0.api.routes.comfyui —
    same logic the /api/comfyui/status route uses, no HTTP self-call.
    """
    import asyncio

    stats, queue_data = await asyncio.gather(
        _fetch_json("/system_stats"),
        _fetch_json("/queue"),
    )
    reachable = stats is not None
    counts = _queue_counts(queue_data)
    running = counts["running"]
    pending = counts["pending"]

    if reachable:
        detail = f"running — {running} job(s) active"
        stat: dict[str, str] | None = {
            "label": "jobs",
            "value": f"{running} running / {pending} queued",
        }
    else:
        detail = "unreachable"
        stat = None

    url: str | None = _comfyui_base_url() if reachable else None
    return reachable, detail, stat, url


async def _probe_hermes() -> tuple[bool, str]:
    """Probe Hermes via systemd unit state (in-process, same as comfyui/status).

    Real signal: _systemd_active("hal0-agent@hermes.service") — the same
    call /api/comfyui/status makes.  Returns (up, detail).
    """
    active = await _systemd_active(_HERMES_UNIT)
    if active:
        return True, "systemd unit active"
    return False, "systemd unit inactive or absent"


async def _probe_openwebui() -> tuple[bool, str]:
    """Real reachability probe — GET <loopback>/health on OpenWebUI.

    SpikeB §5.4 confirmed the running unit answers GET /health with 200.
    up=True only on a 2xx response; any connect/timeout/non-2xx degrades
    to up=False with an honest detail (never a fabricated "up").
    """
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            resp = await client.get(_OPENWEBUI_PROBE_URL)
    except httpx.HTTPError as exc:
        return False, f"unreachable ({type(exc).__name__})"
    if 200 <= resp.status_code < 300:
        return True, "reachable — /health ok"
    return False, f"unhealthy (HTTP {resp.status_code})"


# ── route ─────────────────────────────────────────────────────────────────────


@router.get("/health")
async def services_health(request: Request) -> dict[str, Any]:
    """Aggregate health of the known hal0 companion services.

    Response shape::

        {
          "services": [
            {
              "id":     "comfyui"|"hermes"|"openwebui",
              "name":   str,
              "up":     bool,
              "state":  "up"|"stopped"|"down",
              "detail": str,
              "url":    str | null,
              "stat":   {"label": str, "value": str} | null
            },
            ...
          ]
        }

    ``state`` splits not-up into ``stopped`` (deliberate — unit inactive,
    comes back on demand, renders grey) and ``down`` (unit failed /
    unknown — renders red). ``up`` stays for older consumers.

    Never returns 500 — every probe failure degrades to up=false.
    """
    services: list[dict[str, Any]] = []

    # ── comfyui ──────────────────────────────────────────────────────────────
    try:
        cu_up, cu_detail, cu_stat, cu_url = await _probe_comfyui()
    except Exception as exc:
        log.warning("services_health.comfyui_probe_error", exc=repr(exc))
        cu_up, cu_detail, cu_stat, cu_url = False, type(exc).__name__, None, None

    cu_state = "up"
    if not cu_up:
        # ComfyUI runs as the img slot's container — a cleanly-stopped slot
        # (idle-evicted / never started) is not an error condition. Only
        # consult the unit when the probe saw NOTHING listening; an HTTP-level
        # failure means something is running and stays down/red as-is.
        if cu_detail == "unreachable":
            cu_state, cu_detail = _down_state(await _img_unit_state(request), cu_detail)
        else:
            cu_state = "down"

    services.append(
        {
            "id": "comfyui",
            "name": "ComfyUI",
            "up": cu_up,
            "state": cu_state,
            "detail": cu_detail,
            "url": cu_url,
            "stat": cu_stat,
        }
    )

    # ── hermes ───────────────────────────────────────────────────────────────
    try:
        h_up, h_detail = await _probe_hermes()
    except Exception as exc:
        log.warning("services_health.hermes_probe_error", exc=repr(exc))
        h_up, h_detail = False, type(exc).__name__

    h_state = "up"
    if not h_up:
        h_state, h_detail = _down_state(await _unit_active_state(_HERMES_UNIT), h_detail)

    services.append(
        {
            "id": "hermes",
            "name": "Hermes",
            "up": h_up,
            "state": h_state,
            "detail": h_detail,
            "url": None,  # loopback-only, no browser-reachable URL
            "stat": None,
        }
    )

    # ── openwebui ─────────────────────────────────────────────────────────────
    try:
        ow_up, ow_detail = await _probe_openwebui()
    except Exception as exc:
        log.warning("services_health.openwebui_probe_error", exc=repr(exc))
        ow_up, ow_detail = False, type(exc).__name__

    ow_state = "up"
    if not ow_up:
        # Same rule as comfyui: only reinterpret pure unreachability. A non-2xx
        # /health answer means the service is up-but-unhealthy — stays red.
        if ow_detail.startswith("unreachable"):
            ow_state, ow_detail = _down_state(await _unit_active_state(_OPENWEBUI_UNIT), ow_detail)
        else:
            ow_state = "down"

    services.append(
        {
            "id": "openwebui",
            "name": "OpenWebUI",
            "up": ow_up,
            "state": ow_state,
            "detail": ow_detail,
            "url": _openwebui_url(),
            "stat": None,
        }
    )

    return {"services": services}


__all__ = ["router"]
