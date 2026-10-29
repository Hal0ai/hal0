"""Glue layer that mounts the hal0 admin + memory MCP servers on the
FastAPI app.

Lives here (the orchestrator's package) because every piece it touches —
the FastAPI ``app``, the lifespan-scoped
:class:`hal0.mcp.approval_queue.ApprovalQueue` — is orchestrator-owned.

The trick the mount needs to solve: FastMCP runs as a sub-ASGI app
underneath ``/mcp/admin`` and ``/mcp/memory``, but the MCP tool handlers
need to know *who* is calling so the audit log + the ``--private``
namespace promotion can stamp the right ``client_id``. Solution =
resolver callbacks (wired into each ``build_server``) that read the
caller headers straight off the MCP SDK's per-handler request context.

Why not a Starlette-middleware contextvar (issue #413)? FastMCP runs
tool handlers inside a *lifespan-scoped* anyio task group spun up by
``StreamableHTTPSessionManager.run()`` — not in the per-request task a
Starlette ``BaseHTTPMiddleware`` writes into. A contextvar ``set()`` in
request-dispatch therefore never propagates to the handler: it always
reads the default (``private=False``, ``client_id="anonymous"``), so
every write silently collapsed to the ``shared`` namespace regardless of
``X-hal0-Private: 1``. The MCP SDK *does* set its own ``request_ctx``
contextvar inside the handler's own task (``mcp.server.lowlevel.server``)
and stashes the originating Starlette ``Request`` on it; we read the
headers off that request at call time, mirroring the REST surface in
:mod:`hal0.api.routes.memory` which reads them straight off ``request``.
"""

from __future__ import annotations

import hashlib
import os
import re

import structlog
from starlette.requests import Request
from starlette.types import ASGIApp

from hal0.api.auth import bearer_token, bears_bridge_key, judged_principal
from hal0.service_identity import service_key

log = structlog.get_logger("hal0.api.mcp_mount")

# Header carrying the caller's agent identity (Bearer auth was removed;
# identity flows on this header, same as the REST memory
# surface in :mod:`hal0.api.routes.memory`).
_AGENT_HEADER = "x-hal0-agent"

# Identity grammar — alnum + ``-`` + ``_``, ≤64 chars. The
# value feeds the ``private:<agent>`` dataset name + the audit source, so
# it must be path-traversal-free and bounded. Mirrors
# ``hal0.api.routes.memory._AGENT_ID_PATTERN`` so MCP + REST resolve the
# same identity to the same namespace (issue #317).
_AGENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

# Localhost values FastMCP itself uses when it auto-enables DNS-rebinding
# protection for a 127.0.0.1 server. We keep them as the secure floor so
# the default posture is unchanged even when an operator widens the set.
_LOCALHOST_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
_LOCALHOST_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")


def _resolve_bearer(request: Request) -> str | None:
    """Extract a bearer token from the Authorization header, if present.

    Parsed by :func:`hal0.api.auth.bearer_token`, the auth gate's own parser,
    so this mount never reads a bearer the gate did not see.
    """
    return bearer_token(request.headers) or None


def _mcp_transport_security():
    """Build the MCP transport's DNS-rebinding allowlist from the env.

    FastMCP auto-enables a *localhost-only* ``TransportSecuritySettings``
    whenever a server is built with the default ``127.0.0.1`` host (see
    ``mcp.server.fastmcp.FastMCP.__init__``). That lockdown is invisible
    until a non-localhost client hits the mount and gets a bare
    ``421 Invalid Host header`` — exactly what happens the moment another
    homelab node, or the Traefik vhost, tries to reach ``/mcp/*``.

    hal0 removed network auth entirely and binds ``0.0.0.0`` on
    the trusted LAN, so the mount has to be reachable by its real host
    name. We keep the secure localhost default but let operators widen it,
    mirroring the ``HAL0_ALLOWED_ORIGINS`` knob in ``api/agents/_auth``:

    * ``HAL0_MCP_ALLOWED_HOSTS`` — comma-separated ``host`` / ``host:port``
      / ``host:*`` values added to the localhost allowlist. The single
      value ``*`` disables DNS-rebinding protection altogether (the
      fully-open posture some LAN-only deployments want).
    * ``HAL0_MCP_ALLOWED_ORIGINS`` — comma-separated browser origins.
      When unset, ``http``+``https`` origins are derived from each added
      host so the dashboard and Traefik vhost work without a second knob.

    Returns a ``TransportSecuritySettings``. Imported lazily to keep the
    ``mcp`` dependency out of this module's import path until a mount
    actually happens.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    raw_hosts = os.environ.get("HAL0_MCP_ALLOWED_HOSTS", "").strip()
    if raw_hosts == "*":
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    extra_hosts = [h.strip() for h in raw_hosts.split(",") if h.strip()]
    hosts = [*_LOCALHOST_HOSTS, *extra_hosts]

    raw_origins = os.environ.get("HAL0_MCP_ALLOWED_ORIGINS", "").strip()
    extra_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]
    if not extra_origins:
        # Derive http+https origins from each operator host so browser
        # clients (dashboard, Traefik vhost) work without a second env var.
        for host in extra_hosts:
            base = host[:-2] if host.endswith(":*") else host
            extra_origins.extend((f"http://{base}", f"https://{base}"))

    # dict.fromkeys de-dupes while preserving order.
    origins = list(dict.fromkeys((*_LOCALHOST_ORIGINS, *extra_origins)))

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def _current_mcp_request() -> Request | None:
    """Return the Starlette ``Request`` for the in-flight MCP tool call.

    The MCP SDK sets its ``request_ctx`` contextvar *inside* the handler's
    own anyio task (``mcp.server.lowlevel.server``) and stashes the
    originating Starlette ``Request`` on ``RequestContext.request`` — that
    request carries the caller's HTTP headers. Returns ``None`` outside an
    MCP request (e.g. direct dispatcher calls in unit tests), so resolvers
    fall back to their anonymous defaults.
    """
    try:
        from mcp.server.lowlevel.server import request_ctx
    except ImportError:  # pragma: no cover — mcp SDK absent (stubbed tests)
        return None
    try:
        ctx = request_ctx.get()
    except LookupError:
        return None
    request = getattr(ctx, "request", None)
    return request if isinstance(request, Request) else None


def _bearer_label(bearer: str) -> str:
    """Derive a non-secret ``client_id`` label from a raw bearer token.

    The label is a SHA-256 prefix, never the bearer itself — the returned
    value is stamped into the audit trail (:func:`hal0.mcp.admin._audit`)
    which feeds journald, and journald entries are readable via
    ``/api/logs`` and support bundles. A raw admin/client key surviving in
    ``client_id`` there would be a live, replayable credential (and one
    the redactor regexes — keyed on header/config *names*, not on
    arbitrary ``client_id=...`` log fields — can't catch). The hash is
    stable per-key (same key -> same label across calls) without being
    reversible.
    """
    return hashlib.sha256(bearer.encode()).hexdigest()[:12]


def bearer_resolver() -> tuple[str | None, str]:
    """Return ``(raw_bearer, client_id)`` for the current MCP request.

    Wired into :func:`hal0.mcp.admin.build_server`. Reads the
    ``Authorization`` header off the live MCP request context; falls back
    to ``(None, "anonymous")`` outside of an MCP request — useful for
    direct dispatcher calls in tests.

    ``client_id`` (element 1) is derived from the bearer via
    :func:`_bearer_label` — a hashed label, NOT the raw token. Callers
    that need the actual bearer for auth read element 0; nothing should
    ever treat element 1 as a credential. See :func:`_bearer_label` for
    why (issue: raw bearer stamped into journald as client_id).

    A bridge client holding the ``admin`` scope (ADR-0043) gets the box's
    own admin key as element 0, because its bridge key opens no ``/api/*``
    route the admin tools call, and ``bridge:<name>`` as element 1, so the
    audit trail names the client rather than the service key. The bridge
    principal is the one the auth middleware judged and stored on the request
    (:func:`hal0.api.auth.judged_principal`); the key is never verified again
    here. A bridge-shaped bearer without one gets ``(None, "anonymous")``:
    never the service key, and never the bridge key forwarded as a bearer.
    """
    request = _current_mcp_request()
    if request is None:
        return None, "anonymous"
    principal = judged_principal(request)
    if principal.tier == "bridge" and "admin" in principal.scopes:
        return service_key(prefer="admin"), f"bridge:{principal.name}"
    if principal.tier != "bridge" and bears_bridge_key(request):
        return None, "anonymous"
    bearer = _resolve_bearer(request)
    return bearer, _bearer_label(bearer) if bearer else "anonymous"


def client_id_resolver() -> str:
    """Return ``client_id`` for the current MCP request (issue #317).

    Identity comes from the ``X-hal0-Agent`` header — the same header the
    REST memory surface reads (:func:`hal0.api.routes.memory._agent_id`).
    Before this fix the resolver returned the Bearer token, so MCP callers
    sending ``X-hal0-Agent`` + ``X-hal0-Private`` collapsed to the
    ``anonymous`` client and their private writes silently landed in the
    ``shared`` namespace.

    Validation mirrors the REST side: the value must match
    ``^[a-zA-Z0-9_\\-]{1,64}$`` and must not carry a ``private:`` prefix
    (the namespace is reached via :func:`private_resolver`, not by
    smuggling the prefix through identity). An absent / malformed header
    falls back to ``"anonymous"`` — the downstream namespace resolver then
    rejects a ``private`` write with no client_id rather than mis-scoping
    it.

    A request carrying a bridge client key (ADR-0043) is the exception: its
    identity is the client's name, bound to the key, and the header is
    ignored, so a bridge client cannot write as another agent. The name comes
    from the principal the auth middleware judged and stored on the request
    (:func:`hal0.api.auth.judged_principal`), not from a second verify. A
    bridge-shaped bearer without one (a revoke race, a store error, a disabled
    bridge on a dev-open box) is ``"anonymous"``, never the header's claim.
    """
    request = _current_mcp_request()
    if request is None:
        return "anonymous"
    principal = judged_principal(request)
    if principal.tier == "bridge" and principal.name:
        return principal.name
    if bears_bridge_key(request):
        return "anonymous"
    raw = request.headers.get(_AGENT_HEADER)
    if raw is None:
        return "anonymous"
    candidate = raw.strip()
    if not candidate or candidate.startswith("private:"):
        return "anonymous"
    if not _AGENT_ID_PATTERN.match(candidate):
        return "anonymous"
    return candidate


def private_resolver() -> bool:
    """Return whether the calling client toggled ``--private`` mode.

    Read from the ``X-hal0-Private: 1`` request header off the live MCP
    request context — namespace promotion is opt-in per
    client.
    """
    request = _current_mcp_request()
    if request is None:
        return False
    return request.headers.get("x-hal0-private", "").strip().lower() in {"1", "true"}


def mount_mcp_servers(
    app,
    *,
    approval_queue,
    memory_provider=None,
    memory_dispatcher=None,
    base_url: str = "http://127.0.0.1:8080",
) -> None:
    """Build + mount the admin and (optionally) memory MCP sub-apps.

    Called once from :func:`create_app`. ``memory_provider`` may be None
    when the memory engine isn't initialized (e.g. tests that don't exercise
    /mcp/memory); the mount silently skips the memory server in that
    case.

    The admin server takes an in-process ``memory_dispatcher`` callable
    so its ``memory_*`` tools route through the memory MCP server's
    dispatcher without an HTTP round-trip — same in-process call path
    the memory MCP itself uses.

    Cheap per app (#2396): the tool definitions are built once per process
    and shared; each mounted server carries ``app.state``, and its tools read
    the approval queue, memory provider, memory dispatcher and base URL off
    it at call time. The arguments are therefore recorded on ``app.state``
    here (``create_app`` already set the same values for all but
    ``mcp_base_url``), never captured in a tool closure.
    """
    from hal0.mcp.admin import build_server as build_admin_server
    from hal0.mcp.admin import install_admin_route_map

    # The per-app state every mounted tool resolves at call time.
    app.state.approval_queue = approval_queue
    app.state.memory_provider = memory_provider
    app.state.memory_dispatcher = memory_dispatcher
    app.state.mcp_base_url = base_url

    # Autogenerate the admin route map from the LIVE route table before the
    # server is built. ``build_admin_server`` reads ``_PATH_ARGS`` to advertise
    # each tool's schema in tools/list, so the map MUST be installed first. By
    # this point every ``/api/*`` router is included (only the SPA catch-all +
    # the /mcp mounts land afterwards, and both are skipped by the walker), so
    # every classified route resolves (spec §4.4, route-id keyed, deny-by-default).
    install_admin_route_map(app)

    # DNS-rebinding allowlist for every mounted FastMCP sub-app. Set on
    # the server's settings *before* ``streamable_http_app()`` builds the
    # session manager — that's when ``self.settings.transport_security``
    # is read. Without this each server inherits FastMCP's localhost-only
    # default and rejects LAN / Traefik clients with a bare 421.
    transport_security = _mcp_transport_security()

    admin_server = build_admin_server(state=app.state, bearer_resolver=bearer_resolver)
    admin_server.settings.transport_security = transport_security
    # ``streamable_http_app()`` must be called BEFORE
    # ``session_manager`` is accessible — FastMCP creates the manager
    # lazily on the first app build. The lifespan reads
    # ``app.state.mcp_session_managers`` to enter each manager's
    # ``run()`` ctxmgr (which actually starts the anyio task group);
    # without that, mounted requests crash with
    # ``Task group is not initialized``.
    admin_app: ASGIApp = admin_server.streamable_http_app()
    app.mount("/mcp/admin", admin_app, name="mcp-admin")

    session_managers = [admin_server.session_manager]
    # Issue #206 — stash the live FastMCP instances on app.state so the
    # /api/mcp/* introspection routes can read tool / resource / prompt
    # counts via ``await server.list_tools()`` without re-importing the
    # builders. Keyed by mount id (matches ``connect_url`` last segment).
    mcp_servers: dict[str, object] = {"hal0-admin": admin_server}

    if memory_provider is not None:
        from hal0.api.routes.memory import make_add_preflight
        from hal0.mcp.memory import build_server as build_memory_server

        # The memory server is an outermost mount — no admin dispatcher
        # sits in front of it to classify calls — so it carries the
        # approval queue (``app.state.approval_queue``) itself and gates bulk
        # deletes exactly like ``/mcp/admin`` does (#1302). The
        # ``memory_dispatcher`` the admin server uses deliberately does NOT
        # carry the queue: admin gates first, then runs the approved call
        # through it.
        #
        # #1930: the same extraction-window preflight /api/memory/add runs
        # (#1903) — this is the path agents actually retain through.
        app.state.mcp_memory_add_preflight = make_add_preflight(app, memory_provider)
        memory_server = build_memory_server(
            state=app.state,
            client_id_resolver=client_id_resolver,
            private_resolver=private_resolver,
        )
        memory_server.settings.transport_security = transport_security
        memory_app: ASGIApp = memory_server.streamable_http_app()
        app.mount("/mcp/memory", memory_app, name="mcp-memory")
        session_managers.append(memory_server.session_manager)
        mcp_servers["hal0-memory"] = memory_server

    app.state.mcp_session_managers = session_managers
    app.state.mcp_servers = mcp_servers

    log.info(
        "hal0.mcp.mounted",
        admin=True,
        memory=memory_provider is not None,
        dns_rebinding_protection=transport_security.enable_dns_rebinding_protection,
        allowed_hosts=len(transport_security.allowed_hosts),
    )


__all__ = [
    "bearer_resolver",
    "client_id_resolver",
    "mount_mcp_servers",
    "private_resolver",
]
