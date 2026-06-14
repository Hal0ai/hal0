"""GET /api/services/health — dashboard services health aggregator.

Returns a stable list of four well-known services (comfyui, hermes,
openwebui, n8n) with honest up/down state.  Every source degrades
gracefully — a probe failure yields up=false, never a 500.

Real probes: comfyui (in-process /system_stats+/queue), hermes (systemd
unit state), openwebui (loopback GET /health — SpikeB §5.4).  n8n has no
reachable probe from the API process (not deployed on this host) and
reports up=false, detail="unmonitored".

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
from fastapi import APIRouter

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


def _n8n_url() -> str | None:
    """Configured public URL for n8n, or None when absent."""
    public = os.environ.get("HAL0_N8N_PUBLIC_URL", "").strip().rstrip("/")
    return public or None


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


async def _probe_n8n() -> tuple[bool, str]:
    # TODO(spike §5.4): real reachability probe — n8n exposes /healthz.
    # Wire here once the spike lands.  Until then: honest unmonitored placeholder.
    return False, "unmonitored"


# ── route ─────────────────────────────────────────────────────────────────────


@router.get("/health")
async def services_health() -> dict[str, Any]:
    """Aggregate health of the four known hal0 companion services.

    Response shape::

        {
          "services": [
            {
              "id":     "comfyui"|"hermes"|"openwebui"|"n8n",
              "name":   str,
              "up":     bool,
              "detail": str,
              "url":    str | null,
              "stat":   {"label": str, "value": str} | null
            },
            ...
          ]
        }

    Never returns 500 — every probe failure degrades to up=false.
    """
    services: list[dict[str, Any]] = []

    # ── comfyui ──────────────────────────────────────────────────────────────
    try:
        cu_up, cu_detail, cu_stat, cu_url = await _probe_comfyui()
    except Exception as exc:
        log.warning("services_health.comfyui_probe_error", exc=repr(exc))
        cu_up, cu_detail, cu_stat, cu_url = False, type(exc).__name__, None, None

    services.append(
        {
            "id": "comfyui",
            "name": "ComfyUI",
            "up": cu_up,
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

    services.append(
        {
            "id": "hermes",
            "name": "Hermes",
            "up": h_up,
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

    services.append(
        {
            "id": "openwebui",
            "name": "OpenWebUI",
            "up": ow_up,
            "detail": ow_detail,
            "url": _openwebui_url(),
            "stat": None,
        }
    )

    # ── n8n ──────────────────────────────────────────────────────────────────
    try:
        n8n_up, n8n_detail = await _probe_n8n()
    except Exception as exc:
        log.warning("services_health.n8n_probe_error", exc=repr(exc))
        n8n_up, n8n_detail = False, type(exc).__name__

    services.append(
        {
            "id": "n8n",
            "name": "n8n",
            "up": n8n_up,
            "detail": n8n_detail,
            "url": _n8n_url(),
            "stat": None,
        }
    )

    return {"services": services}


__all__ = ["router"]
