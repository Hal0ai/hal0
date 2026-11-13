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
        return {"journal": None, "gate": None, "summary": "no fold-in is in progress"}


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


def test_a_refused_dry_run_exits_1() -> None:
    FakeFoldIn.plan = {"banks": [], "refusals": [{"code": "x", "message": "no"}], "warnings": []}
    out = invoke()
    assert out.exit_code == 1
    assert "refused: no" in out.output


@pytest.mark.parametrize("args", [["--apply", "--resume"], ["--status", "--resume"]])
def test_modes_are_one_at_a_time(args: list[str]) -> None:
    assert invoke(*args).exit_code == 2


@pytest.mark.parametrize("flag", ["--owner", "--map"])
def test_a_pair_without_an_equals_sign_is_a_usage_error(flag: str) -> None:
    assert invoke(flag, "claude-code").exit_code == 2


def test_status_prints_without_an_engine() -> None:
    out = invoke("--status")
    assert out.exit_code == 0
    assert "no fold-in is in progress" in out.output


def test_apply_reports_the_folded_and_left_banks() -> None:
    FakeFoldIn.result = {
        "folded": ["shared"],
        "left": ["pi-coder"],
        "banks": [{"bank": "pi-coder", "step": "left", "note": "ids taken"}],
        "targets": {"shared": "hal0:shared", "private": "hal0:private"},
        "archive_dir": "/x",
    }
    out = invoke("--apply")
    assert out.exit_code == 0, out.output
    assert "1 bank(s) folded" in out.output
    assert "--bank pi-coder" in out.output


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
