"""Unit tests for the logs_tail Bearer redactor in :mod:`hal0.mcp.admin`.

Security review MED-1 (Phase 8 closeout): logs_tail forwards journald
output verbatim to the agent. Bearer tokens, HAL0_BEARER_TOKEN env
prints, and bare ``Bearer <token>`` debug lines that land in journald
would otherwise leak the operator's credentials to whatever agent has
``logs_tail`` approval.

The redactor lives in :mod:`hal0.mcp.admin` as ``_redact_log_line`` +
``_redact_logs_payload``. These tests exercise them directly
(unit-level) and assert via the dispatch path that the redaction
actually runs when ``_execute_tool`` returns the payload.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue

# ── Direct line-level coverage ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "GET /v1/models Authorization: Bearer sk-or-supersecret-xyz",
            "GET /v1/models Authorization: Bearer ***REDACTED***",
        ),
        (
            "  authorization: Bearer hal0_tok_abc123  (case-insensitive)",
            "  authorization: Bearer ***REDACTED***  (case-insensitive)",
        ),
        (
            "env loaded: HAL0_BEARER_TOKEN=hal0_tok_xyz",
            "env loaded: HAL0_BEARER_TOKEN=***REDACTED***",
        ),
        (
            "raw fallback: Bearer abcDEF123_-.tok still gets masked",
            "raw fallback: Bearer ***REDACTED*** still gets masked",
        ),
    ],
)
def test_redact_log_line_masks_known_secret_shapes(raw: str, expected: str) -> None:
    assert admin._redact_log_line(raw) == expected


def test_redact_log_line_passes_through_safe_content() -> None:
    """No false positives on lines that don't carry secrets."""
    line = "[12:00:00] hal0.api.startup version=0.2.0a2"
    assert admin._redact_log_line(line) == line


def test_redact_logs_payload_walks_lines_array() -> None:
    payload = {
        "unit": "hal0-api",
        "count": 3,
        "lines": [
            "[00:00] starting up",
            "[00:01] Authorization: Bearer sk-or-leak",
            "[00:02] no secret here",
        ],
    }
    redacted = admin._redact_logs_payload(payload)
    assert "sk-or-leak" not in str(redacted)
    assert "Bearer ***REDACTED***" in redacted["lines"][1]
    # Non-secret lines unchanged.
    assert redacted["lines"][0] == "[00:00] starting up"
    assert redacted["lines"][2] == "[00:02] no secret here"


def test_redact_logs_payload_tolerates_missing_or_malformed_shape() -> None:
    """If the upstream gives us an unexpected shape we return it
    unchanged — never swallow content, only mask known patterns."""
    # No ``lines`` key.
    assert admin._redact_logs_payload({"unit": "x"}) == {"unit": "x"}
    # ``lines`` is not a list.
    assert admin._redact_logs_payload({"lines": "oops"}) == {"lines": "oops"}
    # Non-dict envelope.
    assert admin._redact_logs_payload("just a string") == "just a string"


# ── Dispatch-path coverage ───────────────────────────────────────────────────


@pytest.fixture
def queue() -> ApprovalQueue:
    return ApprovalQueue()


@pytest.fixture
def _logs_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch httpx.AsyncClient so GET /api/logs returns a leak-bearing
    payload — the redactor should strip the secret before the dispatch
    result returns."""

    class _MockResponse:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {
                "unit": "hal0-api",
                "count": 2,
                "lines": [
                    "[00:00] hal0.api.startup",
                    "[00:01] outbound Authorization: Bearer sk-or-LEAK-1 to provider",
                ],
            }

    class _MockClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            self.base_url = base_url

        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, url: str, params: Any = None, headers: Any = None) -> _MockResponse:
            return _MockResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)


@pytest.mark.asyncio
async def test_logs_tail_dispatch_redacts_before_returning(
    queue: ApprovalQueue,
    _logs_transport: None,
) -> None:
    """End-to-end via :func:`admin.dispatch` — the approval-gated
    ``logs_tail`` tool's executor runs the redactor before the JSON
    envelope ships to the agent.

    ``logs_tail`` is in :data:`admin.GATED_TOOLS`, so dispatch returns
    a ``pending_approval`` envelope and the actual REST call happens
    via the approval queue's executor. We call the executor directly
    here so the test exercises the redaction path without standing up
    an approval-resolution UI.
    """
    # Pre-approve handler — grab the executor closure dispatch builds.
    bound_executor = {}

    real_enqueue = queue.enqueue

    async def _capture_executor(*, tool, args, client_id, executor):
        bound_executor["fn"] = executor
        return await real_enqueue(tool=tool, args=args, client_id=client_id, executor=executor)

    queue.enqueue = _capture_executor  # type: ignore[assignment]

    envelope = await admin.dispatch(
        tool="logs_tail",
        args={"unit": "hal0-api"},
        client_id="test-agent",
        bearer="hal0_tok_test",
        base_url="http://127.0.0.1:8080",
        approval_queue=queue,
    )
    assert envelope["status"] == "pending_approval"
    assert "fn" in bound_executor

    result = await bound_executor["fn"]({"unit": "hal0-api"})

    # Pivot — the leak is gone, the prefix preserved.
    text = str(result)
    assert "sk-or-LEAK-1" not in text
    assert "Bearer ***REDACTED***" in text
    # Other content survived.
    assert any("hal0.api.startup" in line for line in result["lines"])
