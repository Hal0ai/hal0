"""Glue layer that mounts the hal0 admin + memory MCP servers on the
FastAPI app.

Lives here (the orchestrator's package) because every piece it touches —
the FastAPI ``app``, the ``AuthIdentity`` middleware, the lifespan-scoped
:class:`hal0.mcp.approval_queue.ApprovalQueue` — is orchestrator-owned.

The trick the mount needs to solve: FastMCP runs as a sub-ASGI app
underneath ``/mcp/admin`` and ``/mcp/memory``, but the MCP tool handlers
need to know *who* is calling so the audit log + the ``--private``
namespace promotion can stamp the right ``client_id``. Solution = a
contextvar populated by a thin Starlette middleware on each mounted
sub-app, paired with resolver callbacks that the FastMCP ``build_server``
functions accept.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

log = structlog.get_logger("hal0.api.mcp_mount")

# Localhost values FastMCP itself uses when it auto-enables DNS-rebinding
# protection for a 127.0.0.1 server. We keep them as the secure floor so
# the default posture is unchanged even when an operator widens the set.
_LOCALHOST_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
_LOCALHOST_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")


def _resolve_bearer(request: Request) -> str | None:
    """Extract a bearer token from the Authorization header, if present."""
    raw = request.headers.get("authorization") or request.headers.get("Authorization")
    if not raw:
        return None
    parts = raw.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _mcp_transport_security():
    """Build the MCP transport's DNS-rebinding allowlist from the env.

    FastMCP auto-enables a *localhost-only* ``TransportSecuritySettings``
    whenever a server is built with the default ``127.0.0.1`` host (see
    ``mcp.server.fastmcp.FastMCP.__init__``). That lockdown is invisible
    until a non-localhost client hits the mount and gets a bare
    ``421 Invalid Host header`` — exactly what happens the moment another
    homelab node, or the Traefik vhost, tries to reach ``/mcp/*``.

    hal0 removed network auth entirely (ADR-0012) and binds ``0.0.0.0`` on
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


@dataclass
class _MCPCallerCtx:
    """Per-request caller info populated by :class:`MCPAuthMiddleware`."""

    bearer: str | None
    client_id: str
    private: bool


_caller: ContextVar[_MCPCallerCtx | None] = ContextVar("hal0_mcp_caller", default=None)


def bearer_resolver() -> tuple[str | None, str]:
    """Return ``(raw_bearer, client_id)`` for the current MCP request.

    Wired into :func:`hal0.mcp.admin.build_server`. Falls back to
    ``(None, "anonymous")`` outside of an MCP request — useful for
    direct dispatcher calls in tests.
    """
    ctx = _caller.get()
    if ctx is None:
        return None, "anonymous"
    return ctx.bearer, ctx.client_id


def client_id_resolver() -> str:
    """Return ``client_id`` for the current MCP request. See
    :func:`bearer_resolver`."""
    ctx = _caller.get()
    return ctx.client_id if ctx is not None else "anonymous"


def private_resolver() -> bool:
    """Return whether the calling client toggled ``--private`` mode.

    Read from a ``X-hal0-Private: 1`` request header by the middleware
    — namespace promotion per ADR-0005 §3 is opt-in per client.
    """
    ctx = _caller.get()
    return bool(ctx and ctx.private)


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """Stash MCP caller ctx in a contextvar (auth removed in v0.3).

    The middleware no longer enforces bearer auth — that surface was
    removed alongside the FastAPI auth modules. It still parses any
    Authorization: Bearer token off the request (so upstream tools that
    do pass one can be identified for logging / scoping) but does not
    require one.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        bearer = _resolve_bearer(request)
        client_id = bearer or "anonymous"
        private = request.headers.get("x-hal0-private", "").lower() in {"1", "true"}

        token = _caller.set(_MCPCallerCtx(bearer=bearer, client_id=client_id, private=private))
        try:
            return await call_next(request)
        finally:
            _caller.reset(token)


def mount_mcp_servers(
    app,
    *,
    approval_queue,
    memory_wrapper=None,
    memory_dispatcher=None,
    base_url: str = "http://127.0.0.1:8080",
) -> None:
    """Build + mount the admin and (optionally) memory MCP sub-apps.

    Called once from :func:`create_app`. ``memory_wrapper`` may be None
    when Cognee isn't initialized (e.g. tests that don't exercise
    /mcp/memory); the mount silently skips the memory server in that
    case.

    The admin server takes an in-process ``memory_dispatcher`` callable
    so its ``memory_*`` tools route through the memory MCP server's
    dispatcher without an HTTP round-trip — same in-process call path
    the memory MCP itself uses.
    """
    from hal0.mcp.admin import build_server as build_admin_server

    # DNS-rebinding allowlist for every mounted FastMCP sub-app. Set on
    # the server's settings *before* ``streamable_http_app()`` builds the
    # session manager — that's when ``self.settings.transport_security``
    # is read. Without this each server inherits FastMCP's localhost-only
    # default and rejects LAN / Traefik clients with a bare 421.
    transport_security = _mcp_transport_security()

    admin_server = build_admin_server(
        approval_queue=approval_queue,
        base_url=base_url,
        memory_dispatcher=memory_dispatcher,
        bearer_resolver=bearer_resolver,
    )
    admin_server.settings.transport_security = transport_security
    # ``streamable_http_app()`` must be called BEFORE
    # ``session_manager`` is accessible — FastMCP creates the manager
    # lazily on the first app build. The lifespan reads
    # ``app.state.mcp_session_managers`` to enter each manager's
    # ``run()`` ctxmgr (which actually starts the anyio task group);
    # without that, mounted requests crash with
    # ``Task group is not initialized``.
    admin_app: ASGIApp = admin_server.streamable_http_app()
    admin_app.add_middleware(MCPAuthMiddleware)
    app.mount("/mcp/admin", admin_app, name="mcp-admin")

    session_managers = [admin_server.session_manager]
    # Issue #206 — stash the live FastMCP instances on app.state so the
    # /api/mcp/* introspection routes can read tool / resource / prompt
    # counts via ``await server.list_tools()`` without re-importing the
    # builders. Keyed by mount id (matches ``connect_url`` last segment).
    mcp_servers: dict[str, object] = {"hal0-admin": admin_server}

    if memory_wrapper is not None:
        from hal0.mcp.memory import build_server as build_memory_server

        memory_server = build_memory_server(
            wrapper=memory_wrapper,
            client_id_resolver=client_id_resolver,
            private_resolver=private_resolver,
        )
        memory_server.settings.transport_security = transport_security
        memory_app: ASGIApp = memory_server.streamable_http_app()
        memory_app.add_middleware(MCPAuthMiddleware)
        app.mount("/mcp/memory", memory_app, name="mcp-memory")
        session_managers.append(memory_server.session_manager)
        mcp_servers["hal0-memory"] = memory_server

    app.state.mcp_session_managers = session_managers
    app.state.mcp_servers = mcp_servers

    log.info(
        "hal0.mcp.mounted",
        admin=True,
        memory=memory_wrapper is not None,
        dns_rebinding_protection=transport_security.enable_dns_rebinding_protection,
        allowed_hosts=len(transport_security.allowed_hosts),
    )


__all__ = [
    "bearer_resolver",
    "client_id_resolver",
    "mount_mcp_servers",
    "private_resolver",
]
