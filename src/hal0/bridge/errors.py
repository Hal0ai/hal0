"""Bridge errors and their HTTP statuses (ADR-0043).

Every failure the agent bridge reports carries a stable ``bridge.<reason>``
code, a human-readable message, and an HTTP status. The status for each code
lives in :data:`BRIDGE_ERROR_STATUS` so the REST routes, the MCP tools and the
CLI all report the same status for the same failure.

This module imports nothing from hal0 so any bridge module can raise
:class:`BridgeError` without an import cycle.
"""

from __future__ import annotations

__all__ = ["BRIDGE_ERROR_STATUS", "BridgeError"]

#: HTTP status for each bridge error code.
BRIDGE_ERROR_STATUS: dict[str, int] = {
    "bridge.disabled": 404,
    "bridge.unknown_agent": 404,
    "bridge.agent_unkeyed": 503,
    "bridge.agent_unavailable": 502,
    "bridge.agent_refused": 502,
    "bridge.run_not_found": 404,
    "bridge.too_many_runs": 429,
    "bridge.idempotency_conflict": 409,
    "bridge.run_not_running": 409,
    "bridge.no_pending_request": 409,
    "bridge.invalid_session_key": 422,
    "bridge.invalid_idempotency_key": 422,
    "bridge.invalid_offset": 422,
    "bridge.skill_invalid": 422,
    "bridge.skill_not_found": 404,
    "bridge.skill_readonly": 403,
    # Teach tools: memory the box does not run (503), a memory write or search the
    # memory layer refused as malformed (422) or failed (502).
    "bridge.memory_disabled": 503,
    "bridge.memory_invalid": 422,
    "bridge.memory_failed": 502,
    "bridge.store_unavailable": 503,
    "bridge.unavailable": 503,
    # An unexpected failure behind a tool call: the detail is logged, never sent (ADR-0043).
    "bridge.internal": 500,
}


class BridgeError(Exception):
    """A bridge failure with a stable ``code``, a ``message`` and an HTTP ``status``.

    ``status`` defaults to the :data:`BRIDGE_ERROR_STATUS` entry for ``code``,
    or 500 for a code the table does not know.
    """

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status if status is not None else BRIDGE_ERROR_STATUS.get(code, 500)
