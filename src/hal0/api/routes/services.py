"""Companion-service management routes (mounted under /api/services).

The richer sibling of ``services_health.py`` (which keeps its stable
read-only ``GET /api/services/health`` contract for the Overview card).
This router powers the dedicated dashboard Services page:

    GET  /api/services                → full per-service detail: probe state,
                                        systemd unit state (+uptime), browser
                                        URL, mDNS URL, permitted actions.
    POST /api/services/{id}/action    → run one allow-listed lifecycle verb
                                        (start/stop/restart/enable/disable),
                                        audit-logged.
    GET  /api/services/mdns           → avahi/mDNS discovery status.
    POST /api/services/mdns           → advertise/withdraw addon services as
                                        <name>.local avahi announcements.

Per-service logs reuse the existing generic journald surface
(``GET /api/logs?unit=<unit>``) — each list entry carries its ``unit`` so
the UI can wire the logs drawer without a new endpoint.

Probes are shared with services_health (same honest up/down rules: a
probe failure degrades to up=false, never a 500 and never a fabricated
"up"). Lifecycle verbs run through ``hal0.services.systemd`` which
re-enforces the verb allow-list at the execution boundary; the per-service
verb subset lives in ``hal0.services.registry`` (e.g. ComfyUI only exposes
``restart`` because the GpuArbiter owns its start/stop path).
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, BackgroundTasks, Request

from hal0.api._audit import record_action
from hal0.api.routes.config import _behind_proxy, _host_without_port, _resolve_host
from hal0.api.routes.services_health import (
    _PROBE_TIMEOUT,
    _probe_comfyui,
    _probe_hermes,
    _probe_openwebui,
)
from hal0.errors import BadRequest, NotFound
from hal0.services import mdns
from hal0.services import systemd as svc_systemd
from hal0.services.registry import SERVICES, ServiceDef, service_by_id

log = structlog.get_logger(__name__)

router = APIRouter()


# ── URL derivation ────────────────────────────────────────────────────────────


def _public_url(sdef: ServiceDef) -> str | None:
    """Operator-declared public URL (reverse-proxy deploys), or None."""
    if not sdef.public_url_env:
        return None
    return os.environ.get(sdef.public_url_env, "").strip().rstrip("/") or None


def _browser_url(request: Request, sdef: ServiceDef) -> str | None:
    """Browser-reachable URL for a service, mirroring config.py semantics.

    Public-URL env wins; otherwise LAN-published services fall back to
    ``http://<request-host>:<port>``. Loopback-only services (hermes,
    hindsight) have no fallback — link only when the env is set.
    """
    public = _public_url(sdef)
    if public:
        return public
    if sdef.port is None:
        return None
    if _behind_proxy(request):
        host = request.headers.get("x-forwarded-host") or _resolve_host(request)
    else:
        host = _resolve_host(request)
    return f"http://{_host_without_port(host)}:{sdef.port}"


def _mdns_url(sdef: ServiceDef, hostname: str, advertised: list[str]) -> str | None:
    """``http://<host>.local:<port>`` when this service is being advertised."""
    if sdef.port is None or sdef.id not in advertised:
        return None
    return f"http://{hostname}:{sdef.port}"


# ── probes ────────────────────────────────────────────────────────────────────


async def _probe_http_env(sdef: ServiceDef) -> tuple[bool, str]:
    """Generic loopback HTTP probe with env override; honest when unwired."""
    url = ""
    if sdef.probe_url_env:
        url = os.environ.get(sdef.probe_url_env, "").strip().rstrip("/")
    url = url or (sdef.probe_url or "")
    if not url:
        return False, "unmonitored"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        return False, f"unreachable ({type(exc).__name__})"
    if 200 <= resp.status_code < 300:
        return True, "reachable — probe ok"
    return False, f"unhealthy (HTTP {resp.status_code})"


async def _probe(
    sdef: ServiceDef, unit_state: dict[str, Any] | None
) -> tuple[bool, str, dict[str, str] | None]:
    """Dispatch to the service's probe strategy → (up, detail, stat)."""
    if sdef.probe == "comfyui":
        up, detail, stat, _url = await _probe_comfyui()
        return up, detail, stat
    if sdef.probe == "http":
        if sdef.id == "openwebui":
            up, detail = await _probe_openwebui()
        else:
            up, detail = await _probe_http_env(sdef)
        return up, detail, None
    if sdef.probe == "systemd":
        if sdef.id == "hermes":
            up, detail = await _probe_hermes()
            return up, detail, None
        active = bool(unit_state) and unit_state.get("active_state") == "active"
        detail = "systemd unit active" if active else "systemd unit inactive or absent"
        return active, detail, None
    return False, "unmonitored", None


# ── routes ────────────────────────────────────────────────────────────────────


@router.get("")
async def list_services(request: Request) -> dict[str, Any]:
    """Full management view of every registered companion service.

    Never 500s — every probe/systemd failure degrades to honest unknowns.
    """
    disco = await mdns.status()
    hostname = str(disco["hostname"])
    advertised = list(disco["advertised"])  # type: ignore[arg-type]

    services: list[dict[str, Any]] = []
    for sdef in SERVICES:
        unit_state: dict[str, Any] | None = None
        if sdef.unit:
            try:
                unit_state = await svc_systemd.unit_state(sdef.unit)
            except Exception as exc:  # pragma: no cover — defensive
                log.warning("services.unit_state_error", unit=sdef.unit, exc=repr(exc))

        try:
            up, detail, stat = await _probe(sdef, unit_state)
        except Exception as exc:
            log.warning("services.probe_error", service=sdef.id, exc=repr(exc))
            up, detail, stat = False, type(exc).__name__, None

        services.append(
            {
                "id": sdef.id,
                "name": sdef.name,
                "description": sdef.description,
                "managed": sdef.unit is not None,
                "unit": sdef.unit,
                "unit_state": unit_state,
                "up": up,
                "detail": detail,
                "stat": stat,
                "url": _browser_url(request, sdef),
                "mdns_url": _mdns_url(sdef, hostname, advertised),
                "loopback_port": sdef.loopback_port,
                "actions": list(sdef.actions),
                "mdns_capable": sdef.mdns,
                "hints": list(sdef.hints),
            }
        )
    return {"services": services, "mdns": disco}


async def _comfyui_start(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """ComfyUI "start" = the GPU-arbiter switchover to generation mode.

    A raw ``systemctl start hal0-slot@img`` would boot ComfyUI *under* the
    resident LLM stack — the arbiter exists precisely to drain and hand the
    iGPU over first. Delegates to the comfyui router's own switchover handler
    so the guards (switch-in-flight 409, already-there noop, arbiter-missing
    503) stay in one place.
    """
    from hal0.api.routes.comfyui import SwitchoverBody, comfyui_switchover

    resp = await comfyui_switchover(request, background_tasks, SwitchoverBody(mode="generation"))
    try:
        payload = json.loads(bytes(resp.body))
    except Exception:  # pragma: no cover — defensive
        payload = {}
    if resp.status_code == 202:
        return {"ok": True, "message": "GPU switchover to generation started — track via status"}
    if resp.status_code == 200 and payload.get("status") == "noop":
        return {"ok": True, "message": "already in generation mode"}
    detail = (payload.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
    return {"ok": False, "message": f"start comfyui failed: {detail}"}


@router.post("/{service_id}/action")
async def service_action(
    service_id: str, request: Request, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Run one lifecycle verb against a registered service's unit.

    Body: ``{"action": "start"|"stop"|"restart"|"enable"|"disable"}``.
    The verb must be in the service's registry allow-list. ComfyUI's
    ``start`` is special-cased into the GPU-arbiter switchover (#1590);
    its ``restart`` stays a plain unit bounce. Audit-logged with a
    truthful outcome.
    """
    sdef = service_by_id(service_id)
    if sdef is None:
        raise NotFound(
            f"unknown service {service_id!r}",
            details={"service": service_id, "known": [s.id for s in SERVICES]},
            code="services.unknown",
        )

    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("request body must be valid JSON", code="request.invalid_json") from exc
    action = body.get("action") if isinstance(body, dict) else None
    if not isinstance(action, str) or not action:
        raise BadRequest("'action' is required", code="services.action_required")
    if action not in sdef.actions or sdef.unit is None:
        raise BadRequest(
            f"action {action!r} is not allowed for service {service_id!r}",
            details={"service": service_id, "allowed": list(sdef.actions)},
            code="services.action_not_allowed",
        )

    async with record_action(
        request,
        category="services",
        action=f"services.{action}",
        target=sdef.unit,
    ) as rec:
        if sdef.id == "comfyui" and action == "start":
            result = await _comfyui_start(request, background_tasks)
        else:
            result = await svc_systemd.unit_action(sdef.unit, action)
        active = await svc_systemd.unit_is_active(sdef.unit)
        rec.after = {"ok": result["ok"], "active": active}
        rec.message = str(result["message"])

    return {
        "id": sdef.id,
        "unit": sdef.unit,
        "action": action,
        "ok": result["ok"],
        "active": active,
        "message": result["message"],
    }


@router.get("/mdns")
async def mdns_status() -> dict[str, Any]:
    """avahi/mDNS discovery status + which addon services we advertise."""
    disco = await mdns.status()
    disco["advertisable"] = [
        {"id": s.id, "name": s.name, "port": s.port} for s in SERVICES if s.mdns
    ]
    return disco


@router.post("/mdns")
async def mdns_apply(request: Request) -> dict[str, Any]:
    """Advertise (or withdraw) the addon services over mDNS.

    Body: ``{"advertise": true|false}``. Advertise writes one avahi
    service-group file per mdns-capable service (avahi picks the files up
    via inotify — no reload); withdraw removes every hal0-addon file.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("request body must be valid JSON", code="request.invalid_json") from exc
    advertise = body.get("advertise") if isinstance(body, dict) else None
    if not isinstance(advertise, bool):
        raise BadRequest("'advertise' (boolean) is required", code="services.advertise_required")

    async with record_action(
        request,
        category="services",
        action="services.mdns_advertise" if advertise else "services.mdns_withdraw",
        target="avahi",
    ) as rec:
        if advertise:
            entries = [(s.id, s.name, s.port) for s in SERVICES if s.mdns and s.port]
            result = mdns.advertise(entries)
        else:
            result = mdns.withdraw()
        rec.after = {"ok": result["ok"], "advertised": result["advertised"]}
        if result["message"]:
            rec.message = str(result["message"])

    disco = await mdns.status()
    disco["ok"] = result["ok"]
    disco["message"] = result["message"]
    return disco


__all__ = ["router"]
