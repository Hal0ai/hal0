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

and the operator watches and stops the runs clients have delegated to Hermes::

    GET    /api/bridge/runs                    — runs, newest first (?client= ?status= ?limit=)
    GET    /api/bridge/runs/{run_id}           — one run
    GET    /api/bridge/runs/{run_id}/events    — SSE: ledger replay, then live, until the run ends
    POST   /api/bridge/runs/{run_id}/stop      — ask Hermes to stop a live run

Auth
----

Enforcement is central (:mod:`hal0.security.exposure`): ``/api/bridge/me`` is
BRIDGE class, admitting a bridge key or the operator's admin credential, and
every other route here is ADMIN class, so a bridge key is refused with 403
before a handler runs. Nothing here declares an auth dependency of its own.

State and errors
----------------

The :class:`~hal0.bridge.store.BridgeStore` lives on ``app.state.bridge_store``,
opened by the ``bridge_store`` boot phase (``None`` when ``bridge.db`` could not
be opened, or when the bridge is disabled and no file exists). It is the very
instance the auth middleware verifies keys against, so a row written here is
honoured on the next request. Store calls are synchronous SQLite and run in a
worker thread. The store's ``bridge.*`` codes keep their string and map onto
the shared error envelope (:mod:`hal0.errors`).

Disabled
--------

``[bridge].enabled`` is the value hal0-api booted with
(:func:`hal0.api.auth.bridge_enabled`). While it is false, adding, rotating
and re-scoping a client and ``GET /me`` answer 404 ``bridge.disabled``, from a
router-level dependency that runs before the body is validated. Listing and
revoking keep working, so an operator can clean up clients while the bridge is
off: no bridge key verifies then anyway (ADR-0043, Decision 6). The run
routes are gated like add and rotate: ADMIN class, and 404 ``bridge.disabled``
while the bridge is off.

The run routes are thin projections of the :class:`~hal0.bridge.service.BridgeService`
on ``app.state.bridge_service`` (the ``bridge`` boot phase; ``None`` when it could
not be built, which answers 503 ``bridge.unavailable``). They act as the admin
caller, so they see every client's runs. A :class:`~hal0.bridge.errors.BridgeError`
from the service keeps its code and status in the envelope.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, NoReturn

import structlog
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from hal0.api import live_sockets
from hal0.api.auth import bridge_enabled, judged_principal
from hal0.api.routes.config import api_base_url
from hal0.bridge.errors import BridgeError
from hal0.bridge.keys import SCOPE_SERVERS
from hal0.bridge.service import BridgeService, Caller
from hal0.bridge.store import TERMINAL_STATUSES, BridgeStore, BridgeStoreError, ClientRow
from hal0.config import paths
from hal0.config.loader import load_hal0_config
from hal0.errors import Conflict, Hal0Error, NotFound, Unauthorized, UnprocessableEntity

log = structlog.get_logger(__name__)

#: The identity and reach of the operator's own credential on ``GET /me``.
_ADMIN_NAME = "admin"
_ADMIN_SCOPES = list(SCOPE_SERVERS)

_DISABLED_MESSAGE = "The agent bridge is disabled in [bridge] config."

#: ``created_by`` for a client added over REST. The routes are ADMIN class, so
#: the caller is always the operator's credential.
_CREATED_BY = "api:admin"

#: Seconds of silence after which a run's event stream sends a ``: keepalive`` comment.
STREAM_KEEPALIVE_S = 10.0

#: Events read from the ledger per query while a run's stream replays.
_EVENT_PAGE = 200


class BridgeStoreUnavailable(Hal0Error):
    """``bridge.db`` could not be opened at boot, so no client can be read or written."""

    code = "bridge.store_unavailable"
    status = 503


class BridgeFailure(Hal0Error):
    """A :class:`~hal0.bridge.errors.BridgeError` in the shared envelope: same code, message, status."""

    def __init__(self, error: BridgeError) -> None:
        super().__init__(error.message, code=error.code)
        self.status = error.status


class CreateClientBody(BaseModel):
    """``POST /api/bridge/clients``. ``scopes`` omitted means the store's default set."""

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


def _require_enabled() -> None:
    """Refuse with 404 ``bridge.disabled`` when ``[bridge].enabled`` was false at boot.

    The dependency of :data:`_gated`, so it runs before FastAPI validates the
    body: a malformed body on a disabled bridge is 404, not 422.
    """
    if not bridge_enabled():
        raise NotFound(_DISABLED_MESSAGE, code="bridge.disabled")


#: The public router (mounted at ``/api/bridge``). It carries list and revoke,
#: which work while the bridge is disabled, and includes :data:`_gated`.
router = APIRouter()

#: Add, rotate, re-scope and ``GET /me``: 404 ``bridge.disabled`` while off.
_gated = APIRouter(dependencies=[Depends(_require_enabled)])


def _store(request: Request) -> BridgeStore:
    """Return the process's bridge store, or refuse with 503 when it could not be opened."""
    store: BridgeStore | None = getattr(request.app.state, "bridge_store", None)
    if store is None:
        raise BridgeStoreUnavailable(
            "The bridge client store (bridge.db) could not be opened; see the hal0-api log."
        )
    return store


def _store_while_off(request: Request) -> BridgeStore | None:
    """The store for list and revoke, which also run while the bridge is off.

    ``None`` when the bridge is disabled and no ``bridge.db`` exists: there are
    no clients to list or revoke, and a disabled bridge never creates the file.
    Otherwise :func:`_store`, with its 503 when the store could not be opened.
    """
    store: BridgeStore | None = getattr(request.app.state, "bridge_store", None)
    if store is None and not bridge_enabled() and not paths.bridge_db().exists():
        return None
    return _store(request)


def _raise_for(exc: BridgeStoreError | BridgeError) -> NoReturn:
    """Map a store or service refusal onto the shared error envelope, keeping its ``bridge.*`` code.

    A :class:`BridgeError` carries its own HTTP status; a store refusal is mapped by code.
    """
    if isinstance(exc, BridgeError):
        raise BridgeFailure(exc) from exc
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


async def _serve[T](work: Awaitable[T]) -> T:
    """Await a :class:`BridgeService` call, mapping its refusals."""
    try:
        return await work
    except BridgeError as exc:
        _raise_for(exc)


def _service(request: Request) -> BridgeService:
    """Return the run service, or refuse with 503 when it is not running.

    The bridge being off is refused before this runs, by :data:`_gated`.
    """
    service: BridgeService | None = getattr(request.app.state, "bridge_service", None)
    if service is None:
        _raise_for(
            BridgeError(
                "bridge.unavailable",
                "The agent bridge's run service is not running; see the hal0-api log.",
            )
        )
    return service


def _caller(request: Request) -> Caller:
    """Who the run routes act as: the admin, whose view is every client's runs.

    These routes are ADMIN class, so only the operator's credential reaches them
    (or anyone, on a box with auth off). A bridge principal that got here anyway
    is held to its own runs.
    """
    principal = judged_principal(request)
    if principal.tier == "bridge":
        return Caller.from_principal(principal)
    return Caller("admin", "admin")


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

    The lines are pasted on another machine, so the URL base is
    ``[network].public_url`` when set; else the address this request reached
    the API at, except that a loopback host (``hal0 bridge add`` calls
    ``http://127.0.0.1``) becomes the box's first LAN address, on the API port
    (:func:`hal0.api.routes.config.api_base_url` with ``prefer_lan``).
    """
    fallback = getattr(request.app.state, "hal0_config", None)
    base = await asyncio.to_thread(_configured_public_url, fallback)
    if not base:
        base = await asyncio.to_thread(api_base_url, request, prefer_lan=True)
    lines = []
    for scope, (server, mount) in SCOPE_SERVERS.items():
        if scope not in scopes:
            continue
        mcp_url = f"{base}{mount}/mcp"
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
    return [server for scope, (server, _mount) in SCOPE_SERVERS.items() if scope in scopes]


async def _minted(request: Request, row: ClientRow, key: str) -> dict[str, Any]:
    """The create / rotate response: the client, its key (shown once) and the onboarding lines."""
    return {
        "client": row.as_dict(),
        "key": key,
        "onboarding": await _onboarding(request, row.scopes, key),
    }


# ---------------------------------------------------------------------------
# Routes


@_gated.post("/clients", status_code=201)
async def create_client(body: CreateClientBody, request: Request) -> dict[str, Any]:
    """Add a client. The plaintext key is in this response and nowhere else, ever.

    ``scopes`` left out gets the store's default set
    (:data:`hal0.bridge.keys.DEFAULT_SCOPES`).
    """
    store = _store(request)
    if body.scopes is None:
        row, key = await _run(store.add_client, body.name, _CREATED_BY, body.note)
    else:
        row, key = await _run(store.add_client, body.name, _CREATED_BY, body.note, body.scopes)
    return await _minted(request, row, key)


@router.get("/clients")
async def list_clients(request: Request) -> dict[str, Any]:
    """Every client, revoked ones included (``revoked_at`` set), ordered by name.

    Works while the bridge is disabled, so the operator can see what to revoke.
    """
    store = _store_while_off(request)
    if store is None:
        return {"items": []}
    rows = await _run(store.list_clients)
    return {"items": [row.as_dict() for row in rows]}


@_gated.patch("/clients/{name}")
async def set_client_scopes(name: str, body: ScopesBody, request: Request) -> dict[str, Any]:
    """Replace the client's scopes (``agents`` is always kept).

    Open streams are re-checked, so a stream that only a dropped scope admitted
    ends now instead of at the next periodic re-check.
    """
    store = _store(request)
    row = await _run(store.set_scopes, name, body.scopes)
    live_sockets.revalidate()
    return {"client": row.as_dict()}


@_gated.post("/clients/{name}/rotate")
async def rotate_client(name: str, request: Request) -> dict[str, Any]:
    """Replace the client's key. The old key stops verifying and its open streams end."""
    store = _store(request)
    row, key = await _run(store.rotate_client, name)
    live_sockets.revalidate()
    return await _minted(request, row, key)


@router.delete("/clients/{name}")
async def revoke_client(name: str, request: Request) -> dict[str, Any]:
    """Revoke the client's key. The name stays taken so past runs stay attributable.

    Works while the bridge is disabled, so the operator can end a key for good
    before turning the bridge back on.
    """
    store = _store_while_off(request)
    if store is None:
        raise NotFound(f"no bridge client named {name!r}", code="bridge.client_not_found")
    row = await _run(store.revoke_client, name)
    live_sockets.revalidate()
    return {"client": row.as_dict()}


@_gated.get("/runs")
async def list_runs(
    request: Request,
    client: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """Runs, newest first; ``client`` and ``status`` narrow the list."""
    service = _service(request)
    items = await _serve(
        service.list_runs(_caller(request), status=status, limit=limit, client=client)
    )
    return {"items": items}


@_gated.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    """One run's RunView; 404 ``bridge.run_not_found`` for an id the ledger does not hold."""
    service = _service(request)
    return await _serve(service.get_run(_caller(request), run_id))


@_gated.post("/runs/{run_id}/stop")
async def stop_run(run_id: str, request: Request) -> dict[str, Any]:
    """Ask Hermes to stop a live run. The view shows ``stopping`` until the run ends."""
    service = _service(request)
    return await _serve(service.stop_run(_caller(request), run_id))


def _sse_name(name: str) -> str:
    """An SSE field value on one line (an event name from Hermes must not inject fields)."""
    return " ".join(name.split())


def _event_frame(event: dict[str, Any]) -> str:
    """One ledger event as an SSE frame: ``id`` is its sequence, ``data`` the whole event."""
    return f"id: {event['seq']}\nevent: {_sse_name(str(event['event']))}\ndata: {json.dumps(event)}\n\n"


def _closed_frame(view: dict[str, Any]) -> str:
    """The last frame of a stream: the run ended and everything it recorded has been sent."""
    body = json.dumps({"run_id": view["run_id"], "status": view["status"]})
    return f"event: run.closed\ndata: {body}\n\n"


async def run_event_frames(
    request: Request, service: BridgeService, caller: Caller, run_id: str, *, since_seq: int
) -> AsyncIterator[str]:
    """SSE frames for one run: the ledger from ``since_seq``, then live until the run ends.

    Each loop takes the run's change signal before reading, so a change that
    lands during the read still wakes the wait. The status is read before the
    events: the watcher writes a run's last event and its terminal status in one
    transaction, so a terminal status means the events read next are complete.
    A ``: keepalive`` comment goes out after :data:`STREAM_KEEPALIVE_S` of
    silence. The stream also ends when the client disconnects or the run is
    pruned mid-stream.
    """
    cursor = since_seq
    while True:
        if await request.is_disconnected():
            return
        signal = service.change_signal(run_id)
        try:
            view = await service.get_run(caller, run_id)
            events = await service.run_events(caller, run_id, since_seq=cursor, limit=_EVENT_PAGE)
        except BridgeError:
            return
        for event in events:
            cursor = event["seq"]
            yield _event_frame(event)
        if len(events) == _EVENT_PAGE:
            continue  # a full page: more may be waiting
        if view["status"] in TERMINAL_STATUSES:
            yield _closed_frame(view)
            return
        try:
            await asyncio.wait_for(signal.wait(), STREAM_KEEPALIVE_S)
        except TimeoutError:
            yield ": keepalive\n\n"


def _resume_point(last_event_id: str | None) -> int:
    """The sequence an ``EventSource`` reconnect resumes after (``Last-Event-ID``), else 0."""
    if last_event_id is not None and last_event_id.isdecimal():
        return int(last_event_id)
    return 0


@_gated.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    since_seq: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    """SSE: the run's recorded events after ``since_seq``, then live ones until it ends.

    Frames are ``id: <seq>``, ``event: <name>``, ``data: <event as JSON>``. After
    the run's last event comes ``event: run.closed`` and the stream ends.
    ``since_seq`` wins over a ``Last-Event-ID`` header; with neither, the stream
    starts at the run's first event. An unknown run is a 404 before any stream.
    """
    service = _service(request)
    caller = _caller(request)
    await _serve(service.get_run(caller, run_id))
    start = since_seq if since_seq is not None else _resume_point(last_event_id)
    return StreamingResponse(
        run_event_frames(request, service, caller, run_id, since_seq=start),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@_gated.get("/me")
async def get_me(request: Request) -> dict[str, Any]:
    """Who the caller is and which MCP servers it may reach.

    A bridge client sees its own record. The operator's admin credential sees
    ``name: "admin"`` and every server. ``tools`` is empty until the
    ``hal0-agents`` server (a later task) lists its tool names here. The caller
    is the principal the auth middleware judged
    (:func:`hal0.api.auth.judged_principal`); its key is not verified again.
    """
    principal = judged_principal(request)
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


router.include_router(_gated)
