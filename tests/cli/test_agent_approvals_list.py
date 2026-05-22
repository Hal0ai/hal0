"""Tests for ``hal0 agent approvals list`` table projection.

Regression coverage for the field-name drift between
``ApprovalEntry.as_dict()`` (``enqueued_at`` / ``client_id`` / ``args``)
and the original CLI renderer (which read ``requested_at`` / ``agent`` /
``summary`` — none of those exist). Every operator-visible column on the
``hal0 agent approvals list`` table used to render as "—".

The fix mirrors ``ui/src/components/agent/AgentApprovalRow.vue``:

* ``enqueued_at`` → "Requested at" as a short ISO timestamp.
* ``client_id`` → "Agent" with "—" fallback.
* "Summary" built from ``tool`` + primary target arg from
  :data:`hal0.mcp.approval_queue._PRIMARY_TARGET_ARG`.

ADR-0004 §5 — the inbox row contract.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import agent_commands

# Rich auto-shrinks columns to terminal width; CliRunner defaults to
# 80 cols, which truncates long tool names + arg values with "…"
# ellipsis chars. We assert against the projection helpers directly
# (sharp + width-independent) AND through the Typer CLI (catches the
# wiring + ensures the column order didn't regress). The Typer leg
# uses a wide console so the strings we assert on don't get clipped.
runner = CliRunner()


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch):
    """Stub the API layer so the CLI runs offline and we drive its input."""

    def fake_unreachable(_url: str) -> bool:
        return False

    monkeypatch.setattr(agent_commands, "_api_unreachable", fake_unreachable)

    def _install(payload: dict[str, Any]) -> None:
        def fake_get(path: str, **_kw: Any) -> dict[str, Any]:
            assert path == "/api/agent/approvals"
            return payload

        monkeypatch.setattr(agent_commands, "api_get", fake_get)

    return _install


# ── Helper unit tests (width-independent) ────────────────────────────────────


def test_summary_uses_primary_target_arg_for_registered_tool() -> None:
    """``model_delete`` → ``"model_delete <model_id>"`` via _PRIMARY_TARGET_ARG."""
    entry = {
        "tool": "model_delete",
        "args": {"model_id": "qwen3:0.6b"},
    }
    assert agent_commands._approval_summary(entry) == "model_delete qwen3:0.6b"


def test_summary_falls_back_to_first_scalar_for_unregistered_tool() -> None:
    """Unknown tools get a best-effort scalar so operators see context."""
    entry = {
        "tool": "some_unregistered_tool",
        "args": {"thing": "value-x"},
    }
    summary = agent_commands._approval_summary(entry)
    assert "some_unregistered_tool" in summary
    assert "value-x" in summary


def test_summary_renders_tool_alone_when_args_empty() -> None:
    """Empty args → bare tool name (no trailing space, no crash)."""
    entry = {"tool": "model_pull", "args": {}}
    assert agent_commands._approval_summary(entry) == "model_pull"


def test_summary_truncates_to_60_chars() -> None:
    """A pathological model id can't blow out the column width."""
    long_id = "a" * 200
    entry = {
        "tool": "model_pull",
        "args": {"model_id": long_id},
    }
    summary = agent_commands._approval_summary(entry)
    assert len(summary) == 60
    assert summary.startswith("model_pull a")


def test_summary_joins_list_primary_target() -> None:
    """``memory_delete`` has a list-valued primary arg (ids=[…])."""
    entry = {
        "tool": "memory_delete",
        "args": {"ids": ["m1", "m2", "m3"]},
    }
    summary = agent_commands._approval_summary(entry)
    assert "memory_delete" in summary
    # All ids appear in the joined form.
    assert "m1" in summary and "m2" in summary and "m3" in summary


def test_fmt_enqueued_at_renders_iso_from_epoch() -> None:
    """Epoch float → short ISO with ``Z`` suffix (UTC), no microseconds."""
    out = agent_commands._fmt_enqueued_at(1716400000.0)
    # 1716400000.0 → 2024-05-22T17:46:40Z
    assert out == "2024-05-22T17:46:40Z"


def test_fmt_enqueued_at_dash_for_missing() -> None:
    assert agent_commands._fmt_enqueued_at(None) == "—"
    assert agent_commands._fmt_enqueued_at("") == "—"


def test_fmt_enqueued_at_passes_through_non_float_strings() -> None:
    """If the API ever migrates to a pre-formatted ISO string, don't mangle it."""
    assert agent_commands._fmt_enqueued_at("2024-05-22T17:46:40Z") == "2024-05-22T17:46:40Z"


# ── CLI integration tests (wide console so Rich doesn't truncate) ────────────


def test_approvals_list_projects_tool_and_target_into_summary(
    stub_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending ``model_delete`` row renders the model id in Summary,
    the agent's ``client_id`` in Agent, and a non-"—" Requested-at.
    """
    # Rich reads COLUMNS off the env to size its console; pin a wide
    # value so the strings we assert on don't get clipped by ellipsis.
    monkeypatch.setenv("COLUMNS", "200")

    stub_api(
        {
            "approvals": [
                {
                    "id": "abc123",
                    "tool": "model_delete",
                    # Per ApprovalEntry.as_dict() — NOT a flat "summary"
                    # field. The bug pre-fix was reading `summary`,
                    # `agent`, and `requested_at` (none of which exist).
                    "args": {"model_id": "qwen3:0.6b"},
                    "client_id": "pi-coder",
                    "enqueued_at": 1716400000.0,
                    "state": "pending",
                    "hit_count": 1,
                }
            ]
        }
    )

    result = runner.invoke(agent_commands.approvals_app, ["list"])
    assert result.exit_code == 0, result.output

    out = result.output
    # Summary column: tool + primary target arg, mirroring the Vue row.
    assert "model_delete" in out
    assert "qwen3:0.6b" in out
    # Agent column: client_id, not the literal "—" the bug emitted.
    assert "pi-coder" in out
    # Requested at: a real ISO timestamp from epoch 1716400000.0 →
    # 2024-05-22T17:46:40Z. The presence of "2024-" is enough to prove
    # we're not falling back to the "—" sentinel.
    assert "2024-" in out
    # Em-dash sentinels for Agent / Requested-at MUST NOT appear in
    # this row (one per missing field — the bug showed three).
    # The table title + headers don't contain em-dashes, so a strict
    # count works here.
    assert out.count("—") == 0


def test_approvals_list_falls_back_to_dash_when_client_id_missing(
    stub_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agent column degrades to "—" when ``client_id`` is absent / empty."""
    monkeypatch.setenv("COLUMNS", "200")
    stub_api(
        {
            "approvals": [
                {
                    "id": "def456",
                    "tool": "slot_delete",
                    "args": {"name": "scratch"},
                    "client_id": "",  # empty string — fall back to —
                    "enqueued_at": 1716400000.0,
                    "state": "pending",
                }
            ]
        }
    )

    result = runner.invoke(agent_commands.approvals_app, ["list"])
    assert result.exit_code == 0, result.output
    # Summary still works (tool + primary target).
    assert "slot_delete" in result.output
    assert "scratch" in result.output
    # Empty client_id falls back to the em-dash sentinel.
    assert "—" in result.output


def test_approvals_list_empty_short_circuits(stub_api) -> None:
    """Empty pending set renders the dim "No pending approvals." line."""
    stub_api({"approvals": []})
    result = runner.invoke(agent_commands.approvals_app, ["list"])
    assert result.exit_code == 0, result.output
    assert "No pending approvals" in result.output
