"""BridgeError and its status table (ADR-0043).

Every bridge failure carries a stable ``bridge.<reason>`` code. The HTTP
status for each code lives in one table so the REST routes, the MCP tools and
the CLI all report the same status for the same failure.
"""

from __future__ import annotations

import pytest

from hal0.bridge.errors import BRIDGE_ERROR_STATUS, BridgeError

EXPECTED = {
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
    "bridge.skill_invalid": 422,
    "bridge.skill_not_found": 404,
    "bridge.skill_readonly": 403,
    "bridge.store_unavailable": 503,
    "bridge.unavailable": 503,
}


def test_status_table_is_exact() -> None:
    assert BRIDGE_ERROR_STATUS == EXPECTED


@pytest.mark.parametrize(("code", "status"), sorted(EXPECTED.items()))
def test_status_defaults_from_table(code: str, status: int) -> None:
    exc = BridgeError(code, "something went wrong")
    assert exc.code == code
    assert exc.message == "something went wrong"
    assert exc.status == status


def test_explicit_status_overrides_table() -> None:
    exc = BridgeError("bridge.run_not_found", "gone", status=410)
    assert exc.status == 410


def test_is_an_exception_with_readable_str() -> None:
    exc = BridgeError("bridge.disabled", "the bridge is off")
    assert isinstance(exc, Exception)
    assert "bridge.disabled" in str(exc)
    assert "the bridge is off" in str(exc)


def test_unknown_code_falls_back_to_500() -> None:
    assert BridgeError("bridge.not_a_real_code", "x").status == 500
