"""Tests for ``hal0 agent status --json``.

Regression coverage for m4 (live install-validation, 2026-07-19): the
``--json`` flag on ``hal0 agent status`` didn't exist at all, so
``hal0 agent status hermes --json`` raised a Typer/Click UsageError
("No such option: --json") to stderr and left stdout completely empty —
CI/pipe consumers got nothing to parse. The fix adds a real ``--json``
option that mirrors the Rich-table output as a parseable JSON object.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from hal0.cli import agent_commands

runner = CliRunner()


@pytest.fixture
def provision_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the provision-state-file seam at a tmp_path fixture with a
    populated provision.json, so the CLI runs without touching the real
    (root-owned) /var/lib/hal0 tree.
    """
    state_dir = tmp_path / "hermes"
    state_dir.mkdir(parents=True)
    state_file = state_dir / "provision.json"
    state_file.write_text(
        json.dumps(
            {
                "hal0_version": "1.2.3",
                "hermes_version": "0.9.0",
                "completed_at": "2026-07-19T00:00:00Z",
                "phases": {
                    "toolchain": {"status": "ok", "at": "2026-07-19T00:00:00Z"},
                    "provision": {"status": "ok", "at": "2026-07-19T00:00:01Z"},
                },
            }
        )
    )
    monkeypatch.setattr(
        agent_commands, "_agent_provision_state_file", lambda name: state_dir / "provision.json"
    )
    return state_file


def test_status_json_produces_parseable_nonempty_json(provision_state: Path) -> None:
    """``--json`` on a known agent emits real JSON on stdout, exit 0.

    This is the direct regression test for m4: previously ``--json`` was
    an unrecognized option and stdout came back empty.
    """
    result = runner.invoke(agent_commands.app, ["status", "hermes", "--json"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() != ""
    payload = json.loads(result.output)
    assert payload["name"] == "hermes"
    assert payload["provisioned"] is True
    assert payload["hal0_version"] == "1.2.3"
    assert payload["hermes_version"] == "0.9.0"
    assert "toolchain" in payload["phases"]


def test_status_json_before_bootstrap_is_still_valid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No provision.json yet → --json still emits a parseable object, not
    the empty-stdout / UsageError regression."""
    missing = tmp_path / "hermes" / "provision.json"
    monkeypatch.setattr(agent_commands, "_agent_provision_state_file", lambda name: missing)

    result = runner.invoke(agent_commands.app, ["status", "hermes", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {"name": "hermes", "provisioned": False, "phases": {}}


def test_status_non_json_still_renders_table(provision_state: Path) -> None:
    """Sanity: the pre-existing non-json path is untouched by the fix."""
    result = runner.invoke(agent_commands.app, ["status", "hermes"])
    assert result.exit_code == 0, result.output
    assert "bootstrap status" in result.output
    assert "toolchain" in result.output


@pytest.fixture
def warn_provision_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A provision.json with a smoke_tests phase carrying real failures
    (#1793) — the exact shape that used to render as a silent ``ok``."""
    state_dir = tmp_path / "hermes"
    state_dir.mkdir(parents=True)
    state_file = state_dir / "provision.json"
    state_file.write_text(
        json.dumps(
            {
                "hal0_version": "1.2.3",
                "hermes_version": "0.9.0",
                "completed_at": "2026-08-09T00:00:00Z",
                "phases": {
                    "toolchain": {"status": "ok", "at": "2026-08-09T00:00:00Z"},
                    "smoke_tests": {
                        "status": "warn",
                        "at": "2026-08-09T00:00:01Z",
                        "failure_count": 1,
                        "skipped_count": 2,
                        "details": {
                            "failures": ["wrapper_ready: wrapper missing"],
                            "skipped": [
                                "chat_completions: skipped: no chat model loaded",
                                "memory_roundtrip: skipped: no chat model loaded",
                            ],
                        },
                    },
                },
            }
        )
    )
    monkeypatch.setattr(
        agent_commands, "_agent_provision_state_file", lambda name: state_dir / "provision.json"
    )
    return state_file


def test_status_json_surfaces_warn_status_and_counts(warn_provision_state: Path) -> None:
    """#1793: a phase with recorded failures must report itself ``warn`` (not
    ``ok``) and the failure/skip counts must be present for CI/pipe
    consumers, not buried only in the (possibly truncated) Detail text."""
    result = runner.invoke(agent_commands.app, ["status", "hermes", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    smoke = payload["phases"]["smoke_tests"]
    assert smoke["status"] == "warn"
    assert smoke["failure_count"] == 1
    assert smoke["skipped_count"] == 2


def test_status_table_surfaces_warn_phase_and_failure_count(
    warn_provision_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same fixture through the Rich-table path: the status column must not
    read ``ok``, and the dedicated Failures column must show the count
    instead of leaving it only in the (possibly truncated) Detail JSON.

    Force a wide console so Rich doesn't wrap the "1 failed 2 skipped" cell
    across lines and break the substring checks below. Pinned on the module
    console itself: Rich fixes a Console's width from COLUMNS at
    construction (module import), so setting COLUMNS here does not reach
    it, and an xdist worker imports with COLUMNS=80 (#2341).
    """
    monkeypatch.setattr(agent_commands, "console", Console(width=200))
    result = runner.invoke(agent_commands.app, ["status", "hermes"])
    assert result.exit_code == 0, result.output
    assert "warn" in result.output
    assert "1 failed" in result.output
    assert "2 skipped" in result.output


def test_status_table_shows_why_a_skip_only_phase_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A smoke phase whose gated probes only skipped (the agent's model slot
    is cold, so the probes were held back rather than cold-load it) reads
    ``ok`` with the skip reasons in Detail — not a truncated JSON blob, and
    never as a pass or a failure."""
    state_dir = tmp_path / "hermes"
    state_dir.mkdir(parents=True)
    chat_skip = (
        "chat_completions: skipped: chat model not loaded (slot agent is offline; "
        "skipped so the smoke test does not cold-load it)"
    )
    (state_dir / "provision.json").write_text(
        json.dumps(
            {
                "phases": {
                    "smoke_tests": {
                        "status": "ok",
                        "at": "2026-10-23T00:00:00Z",
                        "skipped_count": 1,
                        "details": {"results": {}, "failures": [], "skipped": [chat_skip]},
                    }
                }
            }
        )
    )
    monkeypatch.setattr(
        agent_commands, "_agent_provision_state_file", lambda name: state_dir / "provision.json"
    )
    monkeypatch.setattr(agent_commands, "console", Console(width=400))
    result = runner.invoke(agent_commands.app, ["status", "hermes"])
    assert result.exit_code == 0, result.output
    assert "1 skipped" in result.output
    assert "failed" not in result.output
    assert chat_skip in result.output
    assert "recorded failures" not in result.output


@pytest.mark.parametrize(
    "entry",
    [
        {"status": "ok", "skipped_count": 1, "details": {"failures": [], "skipped": ["{line}"]}},
        {"status": "warn", "failure_count": 1, "details": {"failures": ["{line}"]}},
        {"status": "fail", "reason": "{line}"},
    ],
    ids=["skip", "failure", "reason"],
)
def test_status_table_prints_square_brackets_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: dict[str, object]
) -> None:
    """Detail text is the provisioner's plain text, not Rich markup: the
    memory gate's "could not read [memory] from hal0.toml" must not lose
    ``[memory]`` to Rich's tag parser."""
    line = (
        "memory_roundtrip: skipped: memory engine's model not loaded (could not read "
        "[memory] from hal0.toml (permission denied); skipped so the smoke test does "
        "not cold-load a slot)"
    )
    phase = json.loads(json.dumps(entry).replace("{line}", line))
    state_dir = tmp_path / "hermes"
    state_dir.mkdir(parents=True)
    (state_dir / "provision.json").write_text(json.dumps({"phases": {"smoke_tests": phase}}))
    monkeypatch.setattr(
        agent_commands, "_agent_provision_state_file", lambda name: state_dir / "provision.json"
    )
    monkeypatch.setattr(agent_commands, "console", Console(width=400))
    result = runner.invoke(agent_commands.app, ["status", "hermes"])
    assert result.exit_code == 0, result.output
    assert "could not read [memory] from hal0.toml" in result.output
