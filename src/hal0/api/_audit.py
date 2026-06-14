"""Request-scoped helper for recording user actions to the audit store.

Thin wrapper over :func:`hal0.activity.audit_action` that pulls the actor from
the request and no-ops gracefully when the activity store is disabled. Use it
in any mutation handler::

    @router.delete("/{name}")
    async def delete_slot(name: str, request: Request):
        async with record_action(request, category="slot",
                                 action="slot.delete", target=name):
            manager.delete(name)        # raises → recorded as outcome=error
        return {"ok": True}             # returns → recorded as outcome=ok

For edits, pass ``before=`` and set ``rec.after`` inside the block to capture
the config diff that proves the change landed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Request

from hal0.activity import ActionRecorder, audit_action

# Header the MCP admin surface sets to identify the calling agent
# (see hal0_memory_mcp_connected memory). Absent → a dashboard/local caller.
_AGENT_HEADER = "X-hal0-Agent"


def actor_of(request: Request) -> str:
    """Derive the audit actor string from the request.

    ``mcp:<agent>`` when an agent header is present, else ``dashboard`` (the
    web UI and local curl callers). The CLI records its own actor directly
    when it writes config out-of-band.
    """
    agent = request.headers.get(_AGENT_HEADER)
    if agent:
        return f"mcp:{agent}"
    return "dashboard"


@asynccontextmanager
async def record_action(
    request: Request,
    *,
    category: str,
    action: str,
    target: str | None,
    before: dict[str, Any] | None = None,
    message: str | None = None,
) -> AsyncIterator[ActionRecorder]:
    """Record a mutation with a truthful outcome, or no-op if audit is off."""
    store = getattr(request.app.state, "audit", None)
    if store is None:
        # Activity disabled (or odd entrypoint) — hand back a throwaway
        # recorder so handler code is identical either way.
        yield ActionRecorder(target=target)
        return
    request_id = request.headers.get("X-Request-ID")
    async with audit_action(
        store,
        category=category,
        action=action,
        target=target,
        actor=actor_of(request),
        before=before,
        message=message,
        request_id=request_id,
    ) as rec:
        yield rec
