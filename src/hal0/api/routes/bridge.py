"""Agent bridge REST routes (mounted under ``/api/bridge``; ADR-0043).

The REST surface behind ``hal0 bridge``. The operator mints, lists, revokes,
rotates and re-scopes bridge clients (coding agents on other machines), and a
client asks who it is::

    POST   /api/bridge/clients                 — add a client; the key is shown once
    GET    /api/bridge/clients                 — every client, revoked ones flagged
    PATCH  /api/bridge/clients/{name}          — replace the client's scopes
    POST   /api/bridge/clients/{name}/rotate   — replace the key; the old one dies at once
    DELETE /api/bridge/clients/{name}          — revoke the key (the name stays taken)
    GET    /api/bridge/me                      — the caller's own record

Auth
----

Enforcement is central (:mod:`hal0.security.exposure`): ``/api/bridge/me`` is
BRIDGE class, admitting a bridge key or the operator's admin credential, and
every other route here is ADMIN class, so a bridge key is refused with 403
before a handler runs. Nothing here declares a dependency of its own.

State and errors
----------------

The :class:`~hal0.bridge.store.BridgeStore` lives on ``app.state.bridge_store``,
opened by the ``bridge_store`` boot phase (``None`` when the bridge is disabled
or ``bridge.db`` could not be opened). It is the very instance the auth
middleware verifies keys against, so a row written here is honoured on the
next request. Store calls are synchronous SQLite and run in a worker thread.
The store's ``bridge.*`` codes keep their string and map onto the shared error
envelope (:mod:`hal0.errors`); a disabled bridge answers 404
``bridge.disabled`` on every route.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, NoReturn

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from hal0.api import live_sockets
from hal0.api.auth import resolve_principal_from_scope
from hal0.api.routes.config import api_base_url
from hal0.bridge.store import BridgeStore, BridgeStoreError, ClientRow
from hal0.config.loader import load_hal0_config
from hal0.errors import Conflict, Hal0Error, NotFound, Unauthorized, UnprocessableEntity

log = structlog.get_logger(__name__)

router = APIRouter()

#: Scope -> MCP server id, in the order onboarding lines are printed. A scope's
#: mount is ``/mcp/<scope>/mcp``; ``agents`` is held by every client.
_SERVERS: tuple[tuple[str, str], ...] = (
    ("agents", "hal0-agents"),
    ("memory", "hal0-memory"),
    ("admin", "hal0-admin"),
)

#: The identity and reach of the operator's own credential on ``GET /me``.
_ADMIN_NAME = "admin"
_ADMIN_SCOPES = [scope for scope, _server in _SERVERS]

_DISABLED_MESSAGE = "The agent bridge is disabled in [bridge] config."


class BridgeStoreUnavailable(Hal0Error):
    """``bridge.db`` could not be opened at boot, so no client can be read or written."""

    code = "bridge.store_unavailable"
    status = 503


class CreateClientBody(BaseModel):
    """``POST /api/bridge/clients``. ``scopes`` omitted means agents and memory."""

    model_config = ConfigDict(extra="forbid")

    name: str
    note: str = ""
    scopes: list[str] | None = None


class ScopesBody(BaseModel):
    """``PATCH /api/bridge/clients/{name}``: the full replacement scope list."""

    model_config = ConfigDict(extra="forbid")

    scopes: list[str]


# ---------------------------------------------------------------------------
# Helpers


def _enabled(request: Request) -> bool:
    """True unless ``[bridge].enabled`` is false (applies at the next restart, like the mount)."""
    config = getattr(request.app.state, "hal0_config", None)
    if config is None:
        config = load_hal0_config()
    return bool(config.bridge.enabled)


def _require_enabled(request: Request) -> None:
    """Refuse with 404 ``bridge.disabled`` when ``[bridge].enabled`` is false."""
    if not _enabled(request):
        raise NotFound(_DISABLED_MESSAGE, code="bridge.disabled")


def _store(request: Request) -> BridgeStore:
    """Return the process's bridge store, or refuse when the bridge is off or the store is down."""
    _require_enabled(request)
    store: BridgeStore | None = getattr(request.app.state, "bridge_store", None)
    if store is None:
        raise BridgeStoreUnavailable(
            "The bridge client store (bridge.db) could not be opened; see the hal0-api log."
        )
    return store


def _raise_for(exc: BridgeStoreError) -> NoReturn:
    """Map a store refusal onto the shared error envelope, keeping its ``bridge.*`` code."""
    error_class: dict[str, type[Hal0Error]] = {
        "bridge.client_exists": Conflict,
        "bridge.client_not_found": NotFound,
        "bridge.client_revoked": Conflict,
        "bridge.invalid_name": UnprocessableEntity,
        "bridge.invalid_scope": UnprocessableEntity,
    }
    cls = error_class.get(exc.code)
    if cls is None:
        raise exc
    raise cls(exc.message, code=exc.code) from exc


async def _run[**P, T](fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Run a synchronous store call in a worker thread, mapping its refusals."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except BridgeStoreError as exc:
        _raise_for(exc)


def _created_by(request: Request) -> str:
    """Who is recorded as having added the client: the bridge caller's name, else the operator."""
    principal = resolve_principal_from_scope(request)
    if principal.tier == "bridge" and principal.name:
        return f"api:bridge:{principal.name}"
    return "api:admin"


def _configured_public_url(fallback: Any) -> str | None:
    """Return ``[network].public_url`` read fresh (it applies without a restart), or None.

    ``fallback`` is the boot-time config, used when ``hal0.toml`` cannot be read
    right now: a broken file must not stop an operator printing onboarding lines.
    """
    try:
        config = load_hal0_config()
    except Exception as exc:
        log.warning("hal0.bridge.public_url_unreadable", error=f"{type(exc).__name__}: {exc}")
        config = fallback
    url = None if config is None else config.network.public_url
    return url.rstrip("/") if url else None


async def _onboarding(request: Request, scopes: frozenset[str], key: str) -> dict[str, Any]:
    """One ``claude mcp add`` line per MCP server the scopes reach, with the key inlined.

    The URL base is ``[network].public_url`` when set, else the address this
    request reached the API at (the one ``GET /api/config/urls`` advertises).
    """
    fallback = getattr(request.app.state, "hal0_config", None)
    base = await asyncio.to_thread(_configured_public_url, fallback) or api_base_url(request)
    lines = []
    for scope, server in _SERVERS:
        if scope not in scopes:
            continue
        mcp_url = f"{base}/mcp/{scope}/mcp"
        lines.append(
            {
                "server": server,
                "scope": scope,
                "mcp_url": mcp_url,
                "claude_mcp_add": (
                    f"claude mcp add --transport http {server} {mcp_url} "
                    f'--header "Authorization: Bearer {key}"'
                ),
            }
        )
    return {"lines": lines}


def _servers_for(scopes: frozenset[str]) -> list[str]:
    return [server for scope, server in _SERVERS if scope in scopes]


async def _minted(request: Request, row: ClientRow, key: str) -> dict[str, Any]:
    """The create / rotate response: the client, its key (shown once) and the onboarding lines."""
    return {
        "client": row.as_dict(),
        "key": key,
        "onboarding": await _onboarding(request, row.scopes, key),
    }


# ---------------------------------------------------------------------------
# Routes


@router.post("/clients", status_code=201)
async def create_client(body: CreateClientBody, request: Request) -> dict[str, Any]:
    """Add a client. The plaintext key is in this response and nowhere else, ever."""
    store = _store(request)
    created_by = _created_by(request)
    if body.scopes is None:
        row, key = await _run(store.add_client, body.name, created_by, body.note)
    else:
        row, key = await _run(store.add_client, body.name, created_by, body.note, body.scopes)
    return await _minted(request, row, key)


@router.get("/clients")
async def list_clients(request: Request) -> dict[str, Any]:
    """Every client, revoked ones included (``revoked_at`` set), ordered by name."""
    store = _store(request)
    rows = await _run(store.list_clients)
    return {"items": [row.as_dict() for row in rows]}


@router.patch("/clients/{name}")
async def set_client_scopes(name: str, body: ScopesBody, request: Request) -> dict[str, Any]:
    """Replace the client's scopes (``agents`` is always kept).

    Open streams are re-checked, so a stream that only a dropped scope admitted
    ends now instead of at the next periodic re-check.
    """
    store = _store(request)
    row = await _run(store.set_scopes, name, body.scopes)
    live_sockets.revalidate()
    return {"client": row.as_dict()}


@router.post("/clients/{name}/rotate")
async def rotate_client(name: str, request: Request) -> dict[str, Any]:
    """Replace the client's key. The old key stops verifying and its open streams end."""
    store = _store(request)
    row, key = await _run(store.rotate_client, name)
    live_sockets.revalidate()
    return await _minted(request, row, key)


@router.delete("/clients/{name}")
async def revoke_client(name: str, request: Request) -> dict[str, Any]:
    """Revoke the client's key. The name stays taken so past runs stay attributable."""
    store = _store(request)
    row = await _run(store.revoke_client, name)
    live_sockets.revalidate()
    return {"client": row.as_dict()}


@router.get("/me")
async def get_me(request: Request) -> dict[str, Any]:
    """Who the caller is and which MCP servers it may reach.

    A bridge client sees its own record. The operator's admin credential sees
    ``name: "admin"`` and every server. ``tools`` is empty until the
    ``hal0-agents`` server (a later task) lists its tool names here.
    """
    _require_enabled(request)
    principal = resolve_principal_from_scope(request)
    if principal.tier == "admin":
        return {
            "name": _ADMIN_NAME,
            "tier": "admin",
            "scopes": list(_ADMIN_SCOPES),
            "created_at": None,
            "last_seen_at": None,
            "servers": _servers_for(frozenset(_ADMIN_SCOPES)),
            "tools": [],  # filled from the hal0-agents server's tool list once it exists
        }
    if principal.tier != "bridge" or principal.name is None:
        raise Unauthorized("A bridge client key or the admin key is required.")
    row = await _run(_store(request).get_client, principal.name)
    if row is None or row.revoked_at is not None:
        raise Unauthorized("This bridge key is no longer valid.")
    return {
        "name": row.name,
        "tier": "bridge",
        "scopes": sorted(row.scopes),
        "created_at": row.created_at,
        "last_seen_at": row.last_seen_at,
        "servers": _servers_for(row.scopes),
        "tools": [],  # as above
    }
