"""``hal0 memory embedder switch`` — flags, output and exit codes (#3518).

The work itself is covered in ``tests/memory/test_embedder_switch.py``; here
the switch is a stub and only the command line around it is exercised.
"""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from hal0.cli import memory_embedder_commands as mec
from hal0.cli.memory_commands import app as memory_app
from hal0.memory import embedder_switch as es
from hal0.memory.embedder_record import Embedder

runner = CliRunner()
HS = "hindsight-ai/hs-embed-1"

PLAN: dict[str, Any] = {
    "target": {"provider": "local", "model": HS},
    "recorded": {
        "provider": "local",
        "model": "BAAI/bge-small-en-v1.5",
        "source": "engine-default",
    },
    "mixed": False,
    "engine": {"reachable": True, "version": "1.2.0"},
    "banks": [
        {
            "bank": "hal0:shared",
            "counts": {
                "facts": 1200,
                "observations": 300,
                "mental_models": 4,
                "directives": 3,
                "documents": 80,
            },
            "links": 9000,
            "pending": 0,
            "processing": 0,
        }
    ],
    "totals": {"facts": 1200, "observations": 300},
    "disk": {
        "archive_root": "/var/lib/hal0/backups",
        "store_bytes": 2**30,
        "needed_bytes": 3 * 2**29,
        "free_bytes": 2**34,
    },
    "estimate_s": 25,
    "allow_pending": False,
    "refusals": [],
    "warnings": [],
}


class StubSwitch:
    """Stands in for ``EmbedderSwitch``; records how the command built and drove it."""

    def __init__(self, calls: list[Any], outcome: Any) -> None:
        self.calls = calls
        self.outcome = outcome

    async def _answer(self, name: str) -> Any:
        self.calls.append(name)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def preflight(self) -> Any:
        return await self._answer("preflight")

    async def run(self) -> Any:
        return await self._answer("run")

    async def resume(self) -> Any:
        return await self._answer("resume")

    async def status(self) -> Any:
        return await self._answer("status")

    def clear_gate(self) -> Any:
        self.calls.append("clear_gate")
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _Client:
    async def aclose(self) -> None:
        return None


@pytest.fixture
def stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    state: dict[str, Any] = {"calls": [], "kwargs": None, "outcome": dict(PLAN)}

    def make(_client: Any, **kwargs: Any) -> StubSwitch:
        state["kwargs"] = kwargs
        return StubSwitch(state["calls"], state["outcome"])

    # Wide enough that rich never folds a bank id or a command across lines.
    monkeypatch.setattr(mec, "console", Console(width=200))
    monkeypatch.setattr(mec, "_client", lambda: _Client())
    monkeypatch.setattr(mec, "_make_switch", make)
    return state


def test_the_command_is_mounted_under_hal0_memory() -> None:
    """Read from the command tree, not from rendered help: rich colours the
    help on some terminals and splits an option name across escape codes."""
    import typer

    memory = typer.main.get_command(memory_app)
    switch = memory.commands["embedder"].commands["switch"]  # type: ignore[attr-defined]
    options = {opt for param in switch.params for opt in (*param.opts, *param.secondary_opts)}
    assert {
        "--apply",
        "--dry-run",
        "--allow-pending",
        "--resume",
        "--provider",
        "--json",
    } <= options
    assert [p.name for p in switch.params if p.param_type_name == "argument"] == ["model"]


def test_it_is_a_dry_run_by_default(stub) -> None:
    result = runner.invoke(mec.app, ["switch", HS])

    assert result.exit_code == 0, result.output
    assert stub["calls"] == ["preflight"], "no --apply, nothing but the preflight"
    assert stub["kwargs"]["target"] == Embedder("local", HS)
    assert "dry run" in result.output and "hal0:shared" in result.output
    assert "1200" in result.output and "about 25 s" in result.output
    assert "503 memory.writes_paused" in result.output
    assert "Re-run with --apply" in result.output


def test_the_dry_run_json_is_the_plan(stub) -> None:
    result = runner.invoke(mec.app, ["switch", HS, "--provider", "local", "--json"])

    payload = json.loads(result.output)
    assert payload["applied"] is False
    assert payload["banks"][0]["counts"]["facts"] == 1200
    assert payload["estimate_s"] == 25


def test_a_refused_dry_run_exits_non_zero_and_says_why(stub) -> None:
    stub["outcome"] = {
        **PLAN,
        "refusals": [{"code": "pending_operations", "message": "hal0:shared (7 pending)"}],
    }

    result = runner.invoke(mec.app, ["switch", HS])

    assert result.exit_code == 1
    assert "refused" in result.output and "hal0:shared (7 pending)" in result.output
    assert "Re-run with --apply" not in result.output


def test_apply_runs_the_switch_and_says_where_the_archives_are(stub) -> None:
    stub["outcome"] = {
        "status": "switched",
        "run_id": "3f9a1c2e",
        "to": {"provider": "local", "model": HS},
        "banks": [{"bank": "hal0:shared"}],
        "warnings": [],
        "archive_dir": "/var/lib/hal0/backups/memory-embedder-switch-20261111T020000Z-3f9a1c2e",
        "delete_archives": "rm -r /var/lib/hal0/backups/memory-embedder-switch-20261111T020000Z-3f9a1c2e",
    }

    result = runner.invoke(mec.app, ["switch", HS, "--apply", "--allow-pending"])

    assert result.exit_code == 0, result.output
    assert stub["calls"] == ["run"]
    assert stub["kwargs"]["allow_pending"] is True
    flat = " ".join(result.output.split())
    assert f"The memory store is on {HS}" in flat
    assert "20261111T020000Z-3f9a1c2e" in flat and "rm -r" in flat


def test_a_failed_run_that_was_undone_exits_non_zero_naming_the_bank(stub) -> None:
    stub["outcome"] = es.SwitchError(
        "bank hal0:shared did not come back whole: facts 1199 (expected 1200)",
        bank="hal0:shared",
        details={"differs": {"facts": {"expected": 1200, "got": 1199}}},
    )

    result = runner.invoke(mec.app, ["switch", HS, "--apply", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "failed" and payload["bank"] == "hal0:shared"
    assert payload["details"]["differs"]["facts"] == {"expected": 1200, "got": 1199}
    assert payload["resume_needed"] is False


def test_a_run_that_stopped_in_the_swap_points_at_resume(stub) -> None:
    es.journal_path().parent.mkdir(parents=True)
    es.journal_path().write_text(
        json.dumps(
            {
                "version": 1,
                "run_id": "3f9a1c2e",
                "started_at": "t",
                "pid": 1,
                "phase": "swapping",
                "target": {"provider": "local", "model": HS},
                "previous": {"provider": "local", "model": "BAAI/bge-small-en-v1.5"},
                "archive_dir": "/x",
            }
        )
    )
    stub["outcome"] = es.SwitchError("hindsight-admin rename-bank exited 1", resumable=True)

    result = runner.invoke(mec.app, ["switch", HS, "--apply"])

    assert result.exit_code == 1
    flat = " ".join(result.output.split())
    assert "Memory writes stay paused" in flat
    assert "hal0 memory embedder switch --resume" in flat
    # Where to look, and where the archives are.
    assert "hal0 memory embedder switch --status" in flat and "Archives: /x" in flat


def test_a_refused_apply_prints_the_plan_it_refused(stub) -> None:
    plan = {**PLAN, "refusals": [{"code": "not_privileged", "message": "run this as root"}]}
    stub["outcome"] = es.SwitchRefused("run this as root", plan)

    result = runner.invoke(mec.app, ["switch", HS, "--apply"])

    assert result.exit_code == 1
    assert "refused" in result.output and "run this as root" in result.output


def test_resume_needs_no_model(stub) -> None:
    stub["outcome"] = {
        "status": "rolled_back",
        "run_id": "3f9a1c2e",
        "archive_dir": "/a",
        "to": {"provider": "local", "model": "BAAI/bge-small-en-v1.5"},
    }

    result = runner.invoke(mec.app, ["switch", "--resume"])

    assert result.exit_code == 0, result.output
    assert stub["calls"] == ["resume"] and stub["kwargs"]["target"] is None
    assert "was undone" in " ".join(result.output.split())


STATUS: dict[str, Any] = {
    "journal": {
        "run_id": "3f9a1c2e",
        "phase": "swapping",
        "target": {"provider": "local", "model": HS},
        "started_at": "2026-11-11T02:00:00+00:00",
        "archive_dir": "/var/lib/hal0/backups/memory-embedder-switch-x-3f9a1c2e",
        "error": "hindsight-admin rename-bank exited 1",
    },
    "journal_source": "/var/lib/hal0/state/memory-embedder-switch.json",
    "journal_error": None,
    "gate": {"run_id": "3f9a1c2e", "since": "2026-11-11T02:00:01+00:00", "archive_dir": "/a"},
    "lock_held": False,
    "engine": {"reachable": True, "live": {"provider": "local", "model": HS}},
    "banks": [
        {"bank": "shared", "role": "original or rebuilt", "counts": {"facts": 12}},
        {
            "bank": "agents.switch-3f9a1c2e",
            "role": "temporary (rebuilt copy of agents)",
            "counts": {"facts": 3},
        },
    ],
    "resume": [
        {"bank": "shared", "step": "renamed", "action": "check the bank under its final id"},
        {"bank": "agents", "step": "deleted", "action": "rename the rebuilt bank into place"},
    ],
    "summary": "the run stopped inside the swap: --resume finishes it bank by bank as listed",
}


def test_status_prints_the_journal_the_gate_the_banks_and_what_resume_would_do(stub) -> None:
    stub["outcome"] = STATUS

    result = runner.invoke(mec.app, ["switch", "--status"])

    assert result.exit_code == 0, result.output
    assert stub["calls"] == ["status"], "status only reads"
    flat = " ".join(result.output.split())
    assert "run 3f9a1c2e, phase swapping" in flat
    assert "memory writes are paused" in flat and "no switch process is running" in flat
    assert "agents.switch-3f9a1c2e" in flat and "temporary (rebuilt copy of agents)" in flat
    assert "agents (step deleted): rename the rebuilt bank into place" in flat
    assert "--resume finishes it" in flat
    assert json.loads(runner.invoke(mec.app, ["switch", "--status", "--json"]).output) == STATUS


def test_clear_gate_is_loud_about_what_it_did_not_check(stub) -> None:
    stub["outcome"] = {
        "status": "cleared",
        "gate": {"run_id": "3f9a1c2e", "since": "t", "archive_dir": "/a"},
    }

    result = runner.invoke(mec.app, ["switch", "--clear-gate"])

    assert result.exit_code == 0 and stub["calls"] == ["clear_gate"]
    flat = " ".join(result.output.split())
    assert "lifted by hand" in flat and "Nothing was checked" in flat
    assert "hal0 memory embedder switch --status" in flat


def test_a_refused_clear_gate_exits_non_zero(stub) -> None:
    stub["outcome"] = es.SwitchRefused("an embedder switch (run x, phase swapping) is unfinished")
    result = runner.invoke(mec.app, ["switch", "--clear-gate"])
    assert result.exit_code == 1 and "is unfinished" in " ".join(result.output.split())


def test_the_modes_do_not_combine(stub) -> None:
    for argv in (
        ["switch", HS, "--apply", "--resume"],
        ["switch", "--status", "--clear-gate"],
        ["switch", "--resume", "--status"],
    ):
        assert runner.invoke(mec.app, argv).exit_code == 2
    assert stub["calls"] == []


def test_the_kill_point_is_only_armed_from_the_environment(monkeypatch) -> None:
    """The live-verification aid: off unless the variable names a hook point."""
    monkeypatch.delenv(mec.KILL_AT_ENV, raising=False)
    assert mec._kill_hook() is None

    monkeypatch.setenv(mec.KILL_AT_ENV, "deleted:shared")
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(mec.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    hook = mec._kill_hook()
    assert hook is not None
    hook("before_delete:shared")
    assert killed == []
    hook("deleted:shared")
    assert killed == [(os.getpid(), signal.SIGKILL)]


def test_a_model_is_required_without_resume(stub) -> None:
    result = runner.invoke(mec.app, ["switch"])
    assert result.exit_code == 2
    assert stub["calls"] == []


def test_an_unforeseen_error_is_reported_without_a_traceback(stub) -> None:
    stub["outcome"] = OSError(28, "No space left on device")

    result = runner.invoke(mec.app, ["switch", HS, "--apply", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "No space left on device" in payload["error"] and payload["resume_needed"] is False


def test_an_interrupt_is_reported_like_any_other_stop(stub) -> None:
    stub["outcome"] = KeyboardInterrupt()

    result = runner.invoke(mec.app, ["switch", HS, "--apply", "--json"])

    assert result.exit_code == 130
    assert json.loads(result.output)["error"] == "interrupted"
