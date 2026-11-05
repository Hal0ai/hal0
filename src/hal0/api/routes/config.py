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
import ipaddress
import os
from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from hal0.api._redact import redact_config
from hal0.api.middleware.error_codes import BadRequest, Hal0Error
from hal0.comfyui.reach import editor_on_lan
from hal0.config import network
from hal0.config.loader import hal0_config_txn, load_hal0_config
from hal0.config.network import api_port
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


# Default ports — these match what `hal0-openwebui.service` binds to.  The API
# port is :func:`hal0.config.network.api_port` (the HAL0_PORT env var that
# ``hal0-api.service`` sources from /etc/hal0/api.env); OpenWebUI's port is fixed
# at 3001 in the unit.
_OPENWEBUI_PORT = 3001
# ComfyUI's own web UI is :8188 on the runtime host. The img slot's container
# shares the host network; it binds the LAN under [network].comfyui_listen =
# "lan" (the default, ADR-0041) and 127.0.0.1 under "loopback" (the fence in
# providers/container.py). The ``http://<host>:8188`` fallback below is the
# request host plus that port: it answers from other machines in the first case
# and on the box itself alone in the second (``comfyui_lan`` tells the dashboard
# which); HAL0_COMFYUI_PUBLIC_URL declares a URL that answers regardless.
_COMFYUI_PORT = 8188


_OPENWEBUI_UNIT = "hal0-openwebui.service"


def _resolve_host(request: Request) -> str:
    """Pick the right hostname for the URLs we return.

    The user reaches the API at whatever hostname/IP they typed into
    their browser — exactly the hostname FastAPI sees in the request
    URL.  Mirroring that means the Chat link works whether the user
    typed ``http://hal0.local:8080``, ``http://192.0.2.10:8080``, or
    ``http://127.0.0.1:8080``.

    Falls back to ``127.0.0.1`` if the request has no hostname (rare —
    e.g. raw ASGI calls in tests).
    """
    hostname = request.url.hostname
    if not hostname:
        return "127.0.0.1"
    return hostname


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


def _is_loopback_host(host: str) -> bool:
    """True iff ``host`` (a request's host part, no port) names the box's loopback."""
    if network.is_loopback_bind(host.lower()):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def api_base_url(request: Request, *, prefer_lan: bool = False) -> str:
    """Return the URL this request reached the hal0 API at, with no trailing slash.

    Behind a reverse proxy that is the proxy's ``scheme://host`` (from
    ``X-Forwarded-Proto`` / ``X-Forwarded-Host``); on a direct hit it is
    ``http://<request host>:<API port>``, an IPv6 host in brackets.
    :func:`get_urls` advertises it as ``api``.

    ``prefer_lan`` is for a URL another machine will use: the bridge
    onboarding lines (ADR-0043), which ``hal0 bridge add`` asks for over
    ``http://127.0.0.1``. A direct hit on a loopback host then names the box's
    main LAN address (:func:`hal0.config.network.primary_lan_ip`: the
    default-route interface's) instead, when it has one; the port stays the
    API port.
    """
    host = _resolve_host(request)
    if _behind_proxy(request):
        scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
        forwarded_host = request.headers.get("x-forwarded-host") or host
        return f"{scheme}://{forwarded_host}"
    if prefer_lan and _is_loopback_host(host):
        lan = network.primary_lan_ip()
        if lan is not None:
            host = lan
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{api_port()}"


def _host_without_port(host: str) -> str:
    """Return a hostname suitable for adding the OpenWebUI fixed port."""
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end >= 0 else host
    return host.rsplit(":", 1)[0] if ":" in host else host


def _declared_url(env_var: str) -> str:
    """The URL the operator declared in ``env_var``, trailing slash trimmed, or ``""``."""
    return os.environ.get(env_var, "").strip().rstrip("/")


def _comfyui_public_url() -> str:
    """The URL the operator declared for ComfyUI (``HAL0_COMFYUI_PUBLIC_URL``), or ``""``."""
    return _declared_url("HAL0_COMFYUI_PUBLIC_URL")


def openwebui_public_url() -> str:
    """The URL the operator declared for Open WebUI (``HAL0_OPENWEBUI_PUBLIC_URL``), or ``""``."""
    return _declared_url("HAL0_OPENWEBUI_PUBLIC_URL")


def hermes_public_url() -> str:
    """The URL the operator declared for the Hermes dashboard (``HAL0_HERMES_PUBLIC_URL``), or ``""``.

    The one place that reads it: ``/api/config/urls`` and the Hermes agent overview's
    ``links.desktop`` both call this, so the two cannot disagree.
    """
    return _declared_url("HAL0_HERMES_PUBLIC_URL")


# ``<app>_reason`` in ``GET /api/config/urls`` when the page was reached through a reverse proxy and the
# operator declared no ``HAL0_<APP>_PUBLIC_URL``: the proxy's ``host:<app port>`` is not the app, so
# hal0 returns no link instead of a dead one.
PROXY_NEEDS_PUBLIC_URL = "proxy_needs_public_url"


def _comfyui_link(request: Request) -> str:
    """Resolve the link the dashboard should use to open ComfyUI, or ``""`` for none.

    ``HAL0_COMFYUI_PUBLIC_URL`` (set in /etc/hal0/api.env) wins when
    defined — it's how a reverse-proxy deploy declares a clean HTTPS
    hostname (e.g. ``https://comfyui.example.com``). That matters because
    an HTTPS dashboard opening the bare ``http://<host>:8188`` fallback is
    blocked by the browser as mixed content, so the workflow links appear
    dead. Without the env var we fall back to ``http://<host>:8188`` on the
    request host. That link answers from other machines while ComfyUI binds
    the LAN (``[network].comfyui_listen = "lan"``, the default;
    ``comfyui_lan`` in the response). Under ``"loopback"`` it works only
    where ComfyUI's loopback is: a browser on the box itself, or one that
    tunnels port 8188 to it; the dashboard then opens only a declared URL
    (``comfyui_public``).

    Behind a reverse proxy with no declared URL there is no fallback: the
    forwarded host is the proxy, and port 8188 on the proxy is not ComfyUI.
    The link is ``""`` and ``comfyui_reason`` says why.
    """

    public = _comfyui_public_url()
    if public:
        return public
    if _behind_proxy(request):
        return ""
    return f"http://{_host_without_port(_resolve_host(request))}:{_COMFYUI_PORT}"


def _lan_ip_link(request: Request, port: int, primary: str) -> str:
    """``http://<LAN IP>:<port>``: the link that needs no name resolution, or ``""`` for none.

    ``.local`` names do not resolve on every client (some Android and Windows setups, another
    VLAN or subnet, a VPN), so the dashboard offers this next to the name-based link. The
    address is the one :func:`api_base_url` uses for ``prefer_lan``
    (:func:`hal0.config.network.primary_lan_ip`). ``""`` behind a reverse proxy (the proxy case
    is ``*_reason``), when the box has no LAN address, and when it would repeat ``primary``
    (the page was already reached by that address).
    """
    if _behind_proxy(request):
        return ""
    lan = network.primary_lan_ip()
    if lan is None:
        return ""
    link = f"http://{lan}:{port}"
    return "" if link == primary else link


@router.get("/urls")
async def get_urls(request: Request) -> dict[str, object]:
    """Return the canonical URLs the dashboard should advertise.

    Response shape (stable contract — the dashboard depends on every key
    being present)::

        {
          "api":               "http://<host>:8080" | "https://<host>",
          "openwebui":         "http://<host>:3001" | "https://chat.<host>",
          "openwebui_enabled": true | false,
          "openwebui_reason":  "" | "proxy_needs_public_url",
          "openwebui_ip":      "" | "http://<LAN IP>:3001",
          "hermes":            "" | "https://hermes.<host>",
          "hermes_enabled":    true | false,
          "comfyui":           "http://<host>:8188" | "https://comfyui.<host>",
          "comfyui_public":    true | false,
          "comfyui_lan":       true | false,
          "comfyui_reason":    "" | "proxy_needs_public_url",
          "comfyui_ip":        "" | "http://<LAN IP>:8188",
        }

    ``openwebui_ip`` / ``comfyui_ip`` are the fallback for clients that cannot resolve a
    ``.local`` name: the same app on the box's main LAN address
    (:func:`hal0.config.network.primary_lan_ip`, the address ``prefer_lan`` uses), on the
    same port as the name-based link. They are ``""`` behind a reverse proxy, when the
    box has no LAN address, and when the request already came in on that address (it
    would repeat ``openwebui`` / ``comfyui``). ``comfyui_ip`` is also ``""`` while
    ComfyUI binds loopback (``comfyui_lan`` false). They are independent of
    ``HAL0_<APP>_PUBLIC_URL``: a declared URL stays the primary link, and the IP link
    is the direct-LAN route to the same app. Hermes has none: its dashboard binds
    loopback only, so no address reaches it.

    ``openwebui`` / ``comfyui`` are ``""`` and the matching ``*_reason`` is
    ``"proxy_needs_public_url"`` when the request came through a reverse proxy
    (``X-Forwarded-*``) and the operator declared no ``HAL0_OPENWEBUI_PUBLIC_URL`` /
    ``HAL0_COMFYUI_PUBLIC_URL``. The proxy's ``host:3001`` / ``host:8188`` is not the
    app, so there is no link to give; the dashboard tells the operator which variable
    to set in ``/etc/hal0/api.env``. A ``*_reason`` is ``""`` whenever the link is set.

    ``comfyui_public`` is true only when ``HAL0_COMFYUI_PUBLIC_URL`` is set, that
    is, when ``comfyui`` is a URL the operator declared. ``comfyui_lan`` is true
    while ComfyUI's web UI binds the LAN (``[network].comfyui_listen = "lan"``,
    the default), so the ``http://<host>:8188`` fallback answers from other
    machines. Under ``"loopback"`` that fallback is only the request host plus
    ComfyUI's port and answers on the box itself alone; the dashboard uses the
    two flags to decide whether to open the link (ADR-0041).


    ``HAL0_OPENWEBUI_PUBLIC_URL`` (set in /etc/hal0/api.env) wins
    whenever it's defined — it's how a reverse-proxy deploy declares the
    public hostname for OpenWebUI (e.g. ``https://hal0-chat.example.com``).
    Open WebUI emits root-absolute asset URLs and cannot be served under
    a path prefix, so a polished proxy deployment should give it its own
    (sub)domain.

    Without the env var, LAN-direct deploys fall back to
    ``http://<host>:3001`` (matching the systemd unit's bound port).
    Reverse-proxy deploys without the env var get no link (see above):
    the forwarded host is the proxy, not OpenWebUI.

    Hermes is different: its dashboard binds ``127.0.0.1:9119`` (loopback)
    so — unlike OpenWebUI — there is NO browser-reachable host:port
    fallback. It is advertised ONLY when the operator declares a public
    URL via ``HAL0_HERMES_PUBLIC_URL`` (its own (sub)domain behind the
    proxy, e.g. ``https://hermes.example.com``). On a stock install the
    key is ``""`` / ``hermes_enabled = false`` so the dashboard renders the
    hermes health row without a dead-end link.
    """
    public = openwebui_public_url()
    hermes = hermes_public_url()
    behind_proxy = _behind_proxy(request)
    comfyui = _comfyui_link(request)
    if public:
        openwebui = public
    elif behind_proxy:
        openwebui = ""
    else:
        openwebui = f"http://{_resolve_host(request)}:{_OPENWEBUI_PORT}"

    return {
        "api": api_base_url(request),
        "openwebui": openwebui,
        "openwebui_enabled": await _openwebui_is_active(),
        "openwebui_reason": "" if openwebui else PROXY_NEEDS_PUBLIC_URL,
        "hermes": hermes,
        "hermes_enabled": bool(hermes),
        "comfyui": comfyui,
        "comfyui_public": bool(_comfyui_public_url()),
        "comfyui_lan": editor_on_lan(),
        "comfyui_reason": "" if comfyui else PROXY_NEEDS_PUBLIC_URL,
        "openwebui_ip": _lan_ip_link(request, _OPENWEBUI_PORT, openwebui),
        "comfyui_ip": (_lan_ip_link(request, _COMFYUI_PORT, comfyui) if editor_on_lan() else ""),
    }


# ── [models] config ───────────────────────────────────────────────────────


@router.get("/models")
async def get_models_config() -> dict[str, Any]:
    """Return the current [models] section (roots / auto-scan / extensions).

    Routed through :func:`redact_config` so any sensitive-keyed value the
    operator has tucked into the ``extra``-allow escape hatch
    (``api_key`` / ``token`` / …) is masked before it leaves the API
    (#553). The stock schema carries only paths and booleans, so the
    walk is a no-op on a clean install — but a stray ``api_key = "sk-x"``
    in hal0.toml's ``[models]`` table will not be echoed in plaintext.
    """
    cfg = load_hal0_config()
    return redact_config(cfg.models.model_dump(mode="json"))


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
        raise BadRequest(
            "request body must be valid JSON",
            code="request.invalid_json",
            details={"error": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.invalid_json")

    # Serialized read-modify-write (#1721): this route used to load, merge and
    # save hal0.toml with no lock at all, so it could persist its own
    # pre-read snapshot over a concurrent settings / memory-graph / auth /
    # channel write and erase that section.
    async with hal0_config_txn(request) as txn:
        cfg = txn.config
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
            txn.save(cfg)
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
    return redact_config(out)
