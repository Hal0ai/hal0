"""Tests for ``_uninstall_hermes_memory`` outcome reporting (#350).

Pre-fix the helper swallowed every failure mode behind a bare ``return``;
the CLI exited 0 with no signal even when the Cognee ``agents`` dataset
still held identity-card rows after the delete returned OK (observed
2026-05-26 on LXC 105: 9 hermes-agent rows survived).

These tests pin the structured-outcome contract:

* ``deleted`` — pre-search hit, delete OK, post-verify empty → silent.
* ``not_found`` — pre-search empty → silent (true no-op, no delete issued).
* ``unreachable`` — search OR delete raised a URLError → yellow stderr
  warning naming the URL, exit 0.
* ``leftover`` — delete OK but post-verify still has rows → yellow
  stderr warning naming the dataset + count, exit 0.

The HTTP layer is stubbed by monkey-patching ``urllib.request.urlopen``
inside the module — same pattern used elsewhere in the agent_commands
test suite (test_agent_approvals_list.py stubs ``api_get``).
"""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Callable, Iterator
from io import BytesIO
from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import agent_commands

runner = CliRunner()


# ── HTTP stub plumbing ───────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal stand-in for the object urlopen's context manager yields."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._buf = BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


@pytest.fixture
def fake_urlopen(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Stub ``urllib.request.urlopen`` inside ``_uninstall_hermes_memory``.

    The helper does ``import urllib.request`` inline (kept that way so
    CLI startup stays snappy), so we patch the module-level
    ``urllib.request.urlopen`` and the inline import resolves to the
    same module object. Returns a setter the test calls to install a
    sequence of responses, one per request the function issues.
    """
    import urllib.request as _urllib

    def _install(responses: list[Any]) -> None:
        """Each entry is either a dict (JSON body) or an Exception to raise."""
        iter_responses: Iterator[Any] = iter(responses)

        def _fake_urlopen(req: Any, timeout: float = 5.0) -> _FakeResponse:
            try:
                next_value = next(iter_responses)
            except StopIteration as exc:  # pragma: no cover — test bug
                raise AssertionError("urlopen called more times than stubbed") from exc
            if isinstance(next_value, Exception):
                raise next_value
            return _FakeResponse(next_value)

        monkeypatch.setattr(_urllib, "urlopen", _fake_urlopen)

    return _install


@pytest.fixture
def stub_api_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the API base so url assertions are deterministic."""
    monkeypatch.setenv("HAL0_API_URL", "http://127.0.0.1:8080")


# ── Outcome dataclass: each branch of the state machine ──────────────────────


def test_outcome_not_found_when_search_returns_zero_rows(
    fake_urlopen: Callable[..., None], stub_api_base: None
) -> None:
    """Search returns 0 → no delete issued → outcome=not_found, silent."""
    fake_urlopen([{"items": []}])

    outcome = agent_commands._uninstall_hermes_memory()

    assert outcome.outcome == "not_found"
    assert outcome.deleted_count == 0
    assert outcome.leftover_count == 0
    assert outcome.url == "http://127.0.0.1:8080"


def test_outcome_deleted_when_delete_succeeds_and_verify_empty(
    fake_urlopen: Callable[..., None], stub_api_base: None
) -> None:
    """Pre-search has rows → delete OK → verify-search empty → outcome=deleted."""
    fake_urlopen(
        [
            # Pre-delete search — three matching rows.
            {
                "items": [
                    {"id": "id-1", "metadata": {"agent_id": "hermes-agent"}},
                    {"id": "id-2", "metadata": {"agent_id": "hermes-agent"}},
                    {"id": "id-3", "metadata": {"agent_id": "hermes-agent"}},
                ]
            },
            # Delete response — body is ignored by the helper.
            {"deleted": 3},
            # Post-delete verify — empty, dataset is clean.
            {"items": []},
        ]
    )

    outcome = agent_commands._uninstall_hermes_memory()

    assert outcome.outcome == "deleted"
    assert outcome.deleted_count == 3
    assert outcome.leftover_count == 0


def test_outcome_leftover_when_verify_still_finds_rows(
    fake_urlopen: Callable[..., None], stub_api_base: None
) -> None:
    """The 2026-05-26 incident: delete returns OK but rows survive."""
    fake_urlopen(
        [
            # Pre-delete search — two matching rows.
            {
                "items": [
                    {"id": "id-a", "metadata": {"agent_id": "hermes-agent"}},
                    {"id": "id-b", "metadata": {"agent_id": "hermes-agent"}},
                ]
            },
            # Delete reports success.
            {"deleted": 2},
            # But verify still sees rows.
            {
                "items": [
                    {"id": "id-a", "metadata": {"agent_id": "hermes-agent"}},
                    {"id": "id-b", "metadata": {"agent_id": "hermes-agent"}},
                    {"id": "id-c", "metadata": {"agent_id": "hermes-agent"}},
                ]
            },
        ]
    )

    outcome = agent_commands._uninstall_hermes_memory()

    assert outcome.outcome == "leftover"
    assert outcome.deleted_count == 2
    assert outcome.leftover_count == 3


def test_outcome_unreachable_when_search_raises(
    fake_urlopen: Callable[..., None], stub_api_base: None
) -> None:
    """Search raises URLError → outcome=unreachable, leftover_count=None."""
    fake_urlopen([urllib.error.URLError("connection refused")])

    outcome = agent_commands._uninstall_hermes_memory()

    assert outcome.outcome == "unreachable"
    assert outcome.deleted_count == 0
    assert outcome.leftover_count is None


def test_outcome_unreachable_when_delete_raises(
    fake_urlopen: Callable[..., None], stub_api_base: None
) -> None:
    """Search OK but delete raises → outcome=unreachable, deleted_count records intent."""
    fake_urlopen(
        [
            {"items": [{"id": "id-x", "metadata": {"agent_id": "hermes-agent"}}]},
            urllib.error.URLError("daemon died mid-call"),
        ]
    )

    outcome = agent_commands._uninstall_hermes_memory()

    assert outcome.outcome == "unreachable"
    assert outcome.deleted_count == 1
    assert outcome.leftover_count is None


def test_outcome_deleted_when_verify_call_itself_unreachable(
    fake_urlopen: Callable[..., None], stub_api_base: None
) -> None:
    """Delete OK but verify can't run → best-effort: report deleted, leftover=None.

    Per #350 acceptance criteria: don't escalate to ``leftover`` when we
    have no evidence rows survived. Operators get a silent pass here —
    the unreachable warning surface is reserved for cases where the
    teardown attempt itself failed.
    """
    fake_urlopen(
        [
            {"items": [{"id": "id-y", "metadata": {"agent_id": "hermes-agent"}}]},
            {"deleted": 1},
            urllib.error.URLError("verify call dropped"),
        ]
    )

    outcome = agent_commands._uninstall_hermes_memory()

    assert outcome.outcome == "deleted"
    assert outcome.deleted_count == 1
    assert outcome.leftover_count is None


# ── CLI integration: exit code stays 0; stderr text matches outcome ──────────


@pytest.fixture
def stub_uninstall_api(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Stub the lifecycle API layer so the CLI runs offline."""

    def _install(*, status: str = "uninstalled") -> None:
        monkeypatch.setattr(agent_commands, "_api_unreachable", lambda _u: False)

        def fake_delete(path: str, **_kw: Any) -> dict[str, Any]:
            assert path == "/api/agents/hermes"
            return {"status": status}

        monkeypatch.setattr(agent_commands, "api_delete", fake_delete)

    return _install


def _stub_memory_outcome(
    monkeypatch: pytest.MonkeyPatch, outcome: agent_commands.MemoryUninstallOutcome
) -> None:
    monkeypatch.setattr(agent_commands, "_uninstall_hermes_memory", lambda: outcome)


def test_cli_silent_on_deleted_outcome(
    stub_uninstall_api: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_uninstall_api()
    _stub_memory_outcome(
        monkeypatch,
        agent_commands.MemoryUninstallOutcome(
            outcome="deleted",
            deleted_count=3,
            leftover_count=0,
            url="http://127.0.0.1:8080",
        ),
    )
    result = runner.invoke(agent_commands.app, ["uninstall", "hermes"])
    assert result.exit_code == 0, result.output
    # No warning markup on stderr.
    assert "warning" not in (result.stderr or "")
    # Happy-path success line on stdout.
    assert "Uninstalled" in result.output


def test_cli_silent_on_not_found_outcome(
    stub_uninstall_api: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_uninstall_api(status="not_installed")
    _stub_memory_outcome(
        monkeypatch,
        agent_commands.MemoryUninstallOutcome(
            outcome="not_found",
            deleted_count=0,
            leftover_count=0,
            url="http://127.0.0.1:8080",
        ),
    )
    result = runner.invoke(agent_commands.app, ["uninstall", "hermes"])
    assert result.exit_code == 0, result.output
    assert "warning" not in (result.stderr or "")


def test_cli_warns_on_unreachable_outcome(
    stub_uninstall_api: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_uninstall_api()
    _stub_memory_outcome(
        monkeypatch,
        agent_commands.MemoryUninstallOutcome(
            outcome="unreachable",
            deleted_count=0,
            leftover_count=None,
            url="http://127.0.0.1:8080",
        ),
    )
    # Pin a wide console so Rich doesn't truncate the URL we assert on.
    monkeypatch.setenv("COLUMNS", "200")

    result = runner.invoke(agent_commands.app, ["uninstall", "hermes"])

    assert result.exit_code == 0, result.output
    assert "warning" in result.stderr
    assert "unreachable" in result.stderr
    assert "127.0.0.1:8080" in result.stderr


def test_cli_warns_on_leftover_outcome(
    stub_uninstall_api: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_uninstall_api()
    _stub_memory_outcome(
        monkeypatch,
        agent_commands.MemoryUninstallOutcome(
            outcome="leftover",
            deleted_count=2,
            leftover_count=9,
            url="http://127.0.0.1:8080",
        ),
    )
    monkeypatch.setenv("COLUMNS", "200")

    result = runner.invoke(agent_commands.app, ["uninstall", "hermes"])

    assert result.exit_code == 0, result.output
    assert "warning" in result.stderr
    assert "incomplete" in result.stderr
    # The leftover count surfaces so operators know the scale of the gap.
    assert "9" in result.stderr
    # Dataset name is part of the message so they know where to look.
    assert "agents" in result.stderr


def test_cli_keep_memory_skips_outcome_path(
    stub_uninstall_api: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """--keep-memory must not invoke ``_uninstall_hermes_memory`` at all."""
    stub_uninstall_api()
    called: dict[str, int] = {"n": 0}

    def _should_not_run() -> agent_commands.MemoryUninstallOutcome:
        called["n"] += 1
        raise AssertionError("memory teardown ran with --keep-memory")

    monkeypatch.setattr(agent_commands, "_uninstall_hermes_memory", _should_not_run)

    result = runner.invoke(agent_commands.app, ["uninstall", "hermes", "--keep-memory"])
    assert result.exit_code == 0, result.output
    assert called["n"] == 0
    # Stdout still surfaces the preservation hint.
    assert "memory preserved" in result.output
