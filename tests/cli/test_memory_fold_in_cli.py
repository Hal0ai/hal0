"""``hal0 memory migrate fold-in``: flags, exit codes and output (the work is tested in tests/memory)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from hal0.cli import memory_fold_in_commands as cmd
from hal0.cli.main import app
from hal0.memory import fold_in as fi

runner = CliRunner()


class FakeFoldIn:
    """Records how it was built and answers each entry point with a canned result."""

    built: ClassVar[list[dict[str, Any]]] = []
    plan: ClassVar[dict[str, Any]] = {}
    result: ClassVar[dict[str, Any]] = {}
    error: Exception | None = None

    def __init__(self, _client: Any, **kwargs: Any) -> None:
        FakeFoldIn.built.append(kwargs)

    async def preflight(self) -> dict[str, Any]:
        return FakeFoldIn.plan

    async def run(self) -> dict[str, Any]:
        if FakeFoldIn.error is not None:
            raise FakeFoldIn.error
        return FakeFoldIn.result

    async def resume(self) -> dict[str, Any]:
        return FakeFoldIn.result

    def status(self) -> dict[str, Any]:
        return {
            "journal": None,
            "gate": None,
            "summary": "no fold-in is in progress",
            "archives_needed": ["/var/lib/hal0/backups/memory-fold-in-x-1"],
        }

    def clear_gate(self) -> dict[str, Any]:
        return {"status": "gate_cleared", "run_id": "r1", "phase": "incomplete", "cut_over": False}


class _Client:
    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    monkeypatch.setattr(cmd, "FoldIn", FakeFoldIn)
    monkeypatch.setattr(cmd, "_client", _Client)
    FakeFoldIn.built = []
    FakeFoldIn.plan = {"banks": [], "refusals": [], "warnings": []}
    FakeFoldIn.result = {"folded": ["shared"], "left": [], "banks": [], "targets": {}}
    FakeFoldIn.error = None


def invoke(*args: str):
    return runner.invoke(app, ["memory", "migrate", "fold-in", *args])


def test_a_dry_run_is_the_default_and_passes_the_options_through() -> None:
    out = invoke(
        "--owner", "Claude-Code=laptop", "--map", "hal0-mono=claude-code", "--bank", "x", "--json"
    )
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["applied"] is False
    assert FakeFoldIn.built[0]["owners"] == {"claude-code": "laptop"}
    assert FakeFoldIn.built[0]["maps"] == {"hal0-mono": "claude-code"}
    assert FakeFoldIn.built[0]["only"] == ["x"]
    assert FakeFoldIn.built[0]["allow_exposed_engine"] is False
    assert invoke("--allow-exposed-engine", "--json").exit_code == 0
    assert FakeFoldIn.built[1]["allow_exposed_engine"] is True


def test_skip_and_clear_gate_reach_the_fold_in() -> None:
    out = invoke("--skip", "hal0-mono", "--json")
    assert out.exit_code == 0, out.output
    assert FakeFoldIn.built[0]["skips"] == ["hal0-mono"]
    out = invoke("--clear-gate")
    assert out.exit_code == 0, out.output
    assert "Memory writes are open again" in out.output
    assert "old layout" in out.output


def test_status_names_the_archives_to_keep() -> None:
    out = invoke("--status")
    assert out.exit_code == 0, out.output
    assert "keep until the cutover" in out.output
    assert "memory-fold-in-x-1" in out.output


def test_a_refused_dry_run_exits_1() -> None:
    FakeFoldIn.plan = {"banks": [], "refusals": [{"code": "x", "message": "no"}], "warnings": []}
    out = invoke()
    assert out.exit_code == 1
    assert "refused: no" in out.output


@pytest.mark.parametrize(
    "args", [["--apply", "--resume"], ["--status", "--resume"], ["--clear-gate", "--apply"]]
)
def test_modes_are_one_at_a_time(args: list[str]) -> None:
    assert invoke(*args).exit_code == 2


@pytest.mark.parametrize("flag", ["--owner", "--map"])
def test_a_pair_without_an_equals_sign_is_a_usage_error(flag: str) -> None:
    assert invoke(flag, "claude-code").exit_code == 2


def test_status_prints_without_an_engine() -> None:
    out = invoke("--status")
    assert out.exit_code == 0
    assert "no fold-in is in progress" in out.output


def test_apply_reports_the_folded_and_left_banks_and_exits_3_without_a_cutover() -> None:
    FakeFoldIn.result = {
        "status": "incomplete",
        "folded": ["shared"],
        "left": ["pi-coder"],
        "pending": ["private__brain"],
        "retry": "sudo hal0 memory migrate fold-in --apply --bank pi-coder --bank private__brain",
        "banks": [{"bank": "pi-coder", "step": "left", "note": "ids taken"}],
        "targets": {"shared": "hal0:shared", "private": "hal0:private"},
        "archive_dir": "/x",
    }
    out = invoke("--apply")
    assert out.exit_code == cmd.INCOMPLETE_EXIT, out.output
    assert "1 bank(s) folded" in out.output
    assert "No cutover" in out.output
    assert "--bank pi-coder --bank private__brain" in out.output
    assert "not in this run: private__brain" in out.output
    assert invoke("--apply", "--json").exit_code == cmd.INCOMPLETE_EXIT


def test_apply_that_cuts_over_exits_0() -> None:
    FakeFoldIn.result = {
        "status": "folded",
        "folded": ["shared"],
        "left": [],
        "banks": [],
        "targets": {"shared": "hal0:shared", "private": "hal0:private"},
        "archive_dir": "/x",
    }
    out = invoke("--apply")
    assert out.exit_code == 0, out.output
    assert "The store is on the two banks" in out.output


def test_a_cutover_lists_the_skipped_banks_and_says_nothing_reads_them() -> None:
    FakeFoldIn.result = {
        "status": "folded",
        "cut_over": True,
        "folded": ["shared"],
        "left": [],
        "banks": [],
        "targets": {"shared": "hal0:shared", "private": "hal0:private"},
        "archive_dir": "/x",
        "skipped": [{"bank": "hal0-mono", "class": "unclassified", "facts": 7, "documents": 2}],
        "skipped_note": fi.SKIPPED_NOTE,
    }
    out = invoke("--apply")
    assert out.exit_code == 0, out.output
    text = " ".join(out.output.replace("│", " ").split())
    assert "hal0-mono: 7 fact(s), 2 document(s)" in text
    assert "nothing reads them after the cutover" in text
    payload = json.loads(invoke("--apply", "--json").output)
    assert payload["skipped"][0] == {
        "bank": "hal0-mono",
        "class": "unclassified",
        "facts": 7,
        "documents": 2,
    }
    assert "nothing reads them" in payload["skipped_note"]


def test_the_dry_run_says_what_it_will_not_carry_and_whether_it_cuts_over() -> None:
    FakeFoldIn.plan = {
        "banks": [
            {
                "bank": "private__hermes",
                "class": "private",
                "agent": "hermes",
                "action": "fold",
                "fold": {
                    "mental_models": {"private": 2, "dropped_private": 1},
                    "unreadable_shared_facts": 0,
                },
            },
            {
                "bank": "Hal0::Agents",
                "class": "coding",
                "action": "fold",
                "fold": {
                    "unreadable_shared_facts": 4,
                    "mixed_owner_documents": [{"document": "d", "owners": ["a", "b"]}],
                },
            },
        ],
        "pending": ["pi-coder"],
        "refusals": [],
        "warnings": [],
    }
    out = invoke()
    assert out.exit_code == 0, out.output
    text = " ".join(out.output.split())
    assert "2 mental model(s) go to the private bank" in text
    assert "1 mental model(s) are not carried" in text
    assert "4 shared fact(s) will be readable by no agent" in text
    assert "the bank will be left" in text
    assert "cutover: not in this run" in text


def test_a_failed_run_says_writes_stay_paused_when_a_journal_is_left(monkeypatch) -> None:
    FakeFoldIn.error = fi.FoldInError("engine gone", resumable=True)
    monkeypatch.setattr(cmd, "interrupted_fold_in", lambda: {"archive_dir": "/x"})
    out = invoke("--apply", "--json")
    assert out.exit_code == 1
    payload = json.loads(out.output)
    assert payload["resume_needed"] is True and payload["error"] == "engine gone"


def test_the_kill_hook_needs_both_variables(monkeypatch) -> None:
    monkeypatch.setenv(cmd.KILL_AT_ENV, "paused")
    assert cmd._kill_hook() is None
    monkeypatch.setenv(cmd.TEST_HOOKS_ENV, "1")
    assert cmd._kill_hook() is not None
