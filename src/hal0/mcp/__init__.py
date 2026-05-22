"""hal0 MCP server surface (Phase 8 — Agents v0.2).

This package exposes two Streamable-HTTP MCP servers that bundled and
external agents use to drive hal0 without re-implementing the REST API:

  - :mod:`hal0.mcp.admin`  — slot / model / capability / config /
    provider / version / hardware / logs tools (ADR-0004 §4 catalog).
  - :mod:`hal0.mcp.memory` — Cognee-backed long-term memory tools
    (ADR-0005 §2 schema).

Both servers mount as sub-ASGI apps under the main FastAPI app at
``/mcp/admin`` and ``/mcp/memory`` respectively. The wiring lives in
:mod:`hal0.api.__init__` (other team owns that file).

Gated tools (destructive — pull/delete/restart, capability assignments,
config + credential writes, bulk memory delete) do NOT execute directly:
they enqueue an :class:`hal0.mcp.approval_queue.ApprovalEntry` and
return ``{"status": "pending_approval", "approval_id": "..."}`` so the
owner can review the call in the dashboard's Approvals inbox before
anything moves.

Audit
-----

Every tool invocation — autonomous or gated — emits a structured log
event on the ``hal0.mcp.audit`` logger with fields::

    {client_id, tool, args, gated, timestamp}

The main API already configures structlog to route through journald
(see ``hal0.api.__init__``), so no extra wiring is required here — a
single ``log.info("mcp.tool.invoked", ...)`` is enough to land the
audit row in the systemd journal where the rest of hal0's logs live.
"""

from __future__ import annotations

from hal0.mcp.approval_queue import ApprovalEntry, ApprovalQueue

__all__ = [
    "ApprovalEntry",
    "ApprovalQueue",
]
