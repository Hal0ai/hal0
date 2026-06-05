"""Config + URL discovery endpoints (mounted under /api/config).

The dashboard reads ``/api/config/urls`` on mount to discover the live
hostnames it should point its "Chat" button at.  The hal0 API itself
binds 0.0.0.0:8080 (PLAN §2 "public" tier) and OpenWebUI binds
0.0.0.0:3001, so the same hostname the dashboard is loaded from is the
right answer for both.

``openwebui_enabled`` reflects the unit's runtime state via ``systemctl
is-active hal0-openwebui`` (cheap — single subprocess, no parsing).
False on a host without systemd (CI / dev laptop), so the UI hides the
Chat link instead of leading users to a 404.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from hal0.api.middleware.error_codes import Hal0Error
from hal0.config.loader import load_hal0_config, save_hal0_config
from hal0.config.schema import ModelsConfig
from hal0.registry.discover import scan_and_register

router = APIRouter()


class ConfigInvalidError(Hal0Error):
    """Schema validation failure for the [models] section."""

    code = "config.invalid"
    status = 400


def _validation_error_details(exc: ValidationError) -> dict[str, str]:
    """Render a pydantic ValidationError into ``{field_path: message}``."""
    out: dict[str, str] = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out[loc or "<root>"] = err.get("msg", "invalid")
    return out


# Default ports — these match what `hal0-api.service` and
# `hal0-openwebui.service` bind to.  The API port can be overridden via
# the HAL0_PORT env var that ``hal0-api.service`` sources from
# /etc/hal0/api.env; OpenWebUI's port is fixed at 3001 in the unit.
_DEFAULT_API_PORT = 8080
_OPENWEBUI_PORT = 3001

_OPENWEBUI_UNIT = "hal0-openwebui.service"


def _resolve_host(request: Request) -> str:
    """Pick the right hostname for the URLs we return.

    The user reaches the API at whatever hostname/IP they typed into
    their browser — exactly the hostname FastAPI sees in the request
    URL.  Mirroring that means the Chat link works whether the user
    typed ``http://hal0.local:8080``, ``http://10.0.1.230:8080``, or
    ``http://127.0.0.1:8080``.

    Falls back to ``127.0.0.1`` if the request has no hostname (rare —
    e.g. raw ASGI calls in tests).
    """
    hostname = request.url.hostname
    if not hostname:
        return "127.0.0.1"
    return hostname


def _api_port() -> int:
    """Return the API port the unit is bound to.

    ``HAL0_PORT`` is the same env var ``hal0-api.service`` consumes via
    ``EnvironmentFile=/etc/hal0/api.env``, so reading it here keeps the
    dashboard and the unit in lockstep without an extra config read.
    """
    raw = os.environ.get("HAL0_PORT", "").strip()
    if not raw:
        return _DEFAULT_API_PORT
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_API_PORT


async def _openwebui_is_active() -> bool:
    """Return True if ``hal0-openwebui.service`` is active under systemd.

    Uses ``systemctl is-active --quiet`` which exits 0 when the unit is
    active and non-zero otherwise.  Any failure (missing systemctl, the
    unit doesn't exist, permission denied) is treated as inactive so the
    dashboard hides the Chat link rather than dangling it at a 404.

    Async + short timeout so a wedged systemd never stalls the route.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "is-active",
            "--quiet",
            _OPENWEBUI_UNIT,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, PermissionError, OSError):
        return False
    try:
        rc = await asyncio.wait_for(proc.wait(), timeout=2.0)
    except TimeoutError:
        proc.kill()
        return False
    return rc == 0


def _behind_proxy(request: Request) -> bool:
    """True when the request arrived through a reverse proxy.

    Detected via the proxy-set ``X-Forwarded-*`` headers (Caddy, Traefik,
    nginx all set at least one of these). A direct hit on hal0-api's
    bound port has none of them — that path keeps the legacy host:port
    URL shape so ``http://hal0.local:8080`` still produces a usable
    Chat link.
    """
    fwd = request.headers
    return any(fwd.get(h) for h in ("x-forwarded-host", "x-forwarded-proto", "x-forwarded-for"))


@router.get("/urls")
async def get_urls(request: Request) -> dict[str, object]:
    """Return the canonical URLs the dashboard should advertise.

    Response shape (stable contract — the dashboard depends on every key
    being present)::

        {
          "api":               "http://<host>:8080" | "https://<host>",
          "openwebui":         "http://<host>:3001" | "https://chat.<host>",
          "openwebui_enabled": true | false,
          "hermes":            "" | "https://hermes.<host>",
          "hermes_enabled":    true | false,
        }

    ``HAL0_OPENWEBUI_PUBLIC_URL`` (set in /etc/hal0/api.env) wins
    whenever it's defined — it's how a reverse-proxy deploy declares the
    public hostname for OpenWebUI (e.g. ``https://hal0-chat.example.com``).
    Open WebUI emits root-absolute asset URLs and cannot be served under
    a path prefix, so the proxy must give it its own (sub)domain; there
    is no safe way to synthesise that from the request alone.

    Without the env var, LAN-direct deploys fall back to
    ``http://<host>:3001`` (matching the systemd unit's bound port).
    Reverse-proxy deploys without the env var get ``openwebui_enabled =
    false`` so the dashboard hides the Chat link instead of dangling it
    at a broken URL.

    Hermes is different: its dashboard binds ``127.0.0.1:9119`` (loopback)
    so — unlike OpenWebUI — there is NO browser-reachable host:port
    fallback. It is advertised ONLY when the operator declares a public
    URL via ``HAL0_HERMES_PUBLIC_URL`` (its own (sub)domain behind the
    proxy, e.g. ``https://hermes.example.com``). On a stock install the
    key is ``""`` / ``hermes_enabled = false`` so the dashboard renders the
    hermes health row without a dead-end link.
    """
    public = os.environ.get("HAL0_OPENWEBUI_PUBLIC_URL", "").strip().rstrip("/")
    hermes = os.environ.get("HAL0_HERMES_PUBLIC_URL", "").strip().rstrip("/")
    host = _resolve_host(request)
    enabled = await _openwebui_is_active()

    if public:
        if _behind_proxy(request):
            scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
            forwarded_host = request.headers.get("x-forwarded-host") or host
            api_url = f"{scheme}://{forwarded_host}"
        else:
            api_url = f"http://{host}:{_api_port()}"
        return {
            "api": api_url,
            "openwebui": public,
            "openwebui_enabled": enabled,
            "hermes": hermes,
            "hermes_enabled": bool(hermes),
        }

    if _behind_proxy(request):
        scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
        forwarded_host = request.headers.get("x-forwarded-host") or host
        return {
            "api": f"{scheme}://{forwarded_host}",
            "openwebui": "",
            "openwebui_enabled": False,
            "hermes": hermes,
            "hermes_enabled": bool(hermes),
        }
    return {
        "api": f"http://{host}:{_api_port()}",
        "openwebui": f"http://{host}:{_OPENWEBUI_PORT}",
        "openwebui_enabled": enabled,
        "hermes": hermes,
        "hermes_enabled": bool(hermes),
    }


# ── [models] config ───────────────────────────────────────────────────────


@router.get("/models")
async def get_models_config() -> dict[str, Any]:
    """Return the current [models] section (roots / auto-scan / extensions)."""
    cfg = load_hal0_config()
    return cfg.models.model_dump(mode="json")


@router.put("/models")
async def update_models_config(request: Request) -> dict[str, Any]:
    """Replace the [models] section, persist hal0.toml, then re-scan.

    Body shape: any subset of ``ModelsConfig`` fields. Validation goes
    through the pydantic schema so an invalid relative-path root surfaces
    as ``config.invalid``. After a successful save the discovery scan
    runs immediately so newly-added roots show up in the registry without
    a manual extra POST.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error("request body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise Hal0Error("request body must be a JSON object")

    cfg = load_hal0_config()
    merged_raw = {**cfg.models.model_dump(mode="python"), **body}
    try:
        new_models = ModelsConfig.model_validate(merged_raw)
    except ValidationError as exc:
        raise ConfigInvalidError(
            "models config failed schema validation",
            details=_validation_error_details(exc),
        ) from exc

    # pull_root is always implicitly scanned — otherwise a user who only
    # ever pulls (never points roots at an external dir) gets an empty
    # registry after a save.
    if new_models.pull_root not in new_models.roots:
        new_models = new_models.model_copy(
            update={"roots": [*new_models.roots, new_models.pull_root]},
        )

    cfg.models = new_models
    try:
        save_hal0_config(cfg)
    except OSError as exc:
        raise Hal0Error(
            f"could not persist hal0 config: {exc}",
            details={"error": str(exc), "errno": getattr(exc, "errno", None)},
        ) from exc

    # Run a discovery scan so newly-added roots produce results immediately.
    # The scan walks the filesystem synchronously (potentially seconds on
    # large/NFS roots), so we offload to a thread — leaving it on the
    # event loop blocked /api/status and /api/stats/hardware polls and
    # the browser would surface those as "Fetch failed loading".
    scan_result: dict[str, Any] = {"added": [], "skipped": [], "scanned_roots": []}
    try:
        registry = request.app.state.model_registry
        scan_result = await asyncio.to_thread(scan_and_register, registry, new_models)
    except Exception as exc:  # pragma: no cover — defensive
        scan_result = {"added": [], "skipped": [], "scanned_roots": [], "error": str(exc)}

    out = new_models.model_dump(mode="json")
    out["scan"] = scan_result
    return out
