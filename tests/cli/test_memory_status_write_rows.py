"""#1420 — ``hal0 memory status`` must be able to tell a box whose retains are
all failing from a healthy one.

Before this it printed exactly two rows — ``State ON`` / ``Provider durable``
— on both, because both are derived from daemon reachability alone. The retain
rows added here carry the verdict plus the engine's own failed/pending/
processing operation counts.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from hal0.cli import memory_commands as mc

runner = CliRunner()

_test_app = typer.Typer()
_test_app.command("status")(mc.status_cmd)


def _stub(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    monkeypatch.setattr(mc, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(mc, "api_get", lambda _path: payload)


_FAILING = {
    "memory_enabled": True,
    "memory_degraded": False,
    "memory_write_degraded": True,
    "memory_write_health": {
        "degraded": True,
        "reason": "retain_operations_failing",
        "last_error": "3 retain operation(s) failed on bank shared since the last check",
        "operations": {"failed": 173, "pending": 5, "processing": 0},
        "bank": "shared",
    },
}

_HEALTHY = {
    "memory_enabled": True,
    "memory_degraded": False,
    "memory_write_degraded": False,
    "memory_write_health": {
        "degraded": False,
        "reason": "ok",
        "last_error": None,
        "operations": {"failed": 0, "pending": 0, "processing": 0},
        "bank": "shared",
    },
}


def test_failing_retains_are_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, _FAILING)
    result = runner.invoke(_test_app, [])
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "FAILING" in out
    assert "retain_operations_failing" in out
    assert "failed=173" in out
    assert "pending=5" in out


def test_healthy_box_reads_differently(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, _HEALTHY)
    result = runner.invoke(_test_app, [])
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "FAILING" not in out
    assert "landing" in out
    assert "failed=0" in out


def test_provider_without_a_retain_pipeline_prints_no_write_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(
        monkeypatch,
        {
            "memory_enabled": True,
            "memory_degraded": True,
            "memory_write_degraded": None,
            "memory_write_health": None,
        },
    )
    result = runner.invoke(_test_app, [])
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "Writes" not in out
    assert "Operations" not in out


def test_json_output_carries_the_write_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, _FAILING)
    result = runner.invoke(_test_app, ["--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["memory_write_degraded"] is True
    assert body["memory_write_health"]["operations"]["failed"] == 173
    # #1301's field is untouched.
    assert body["memory_degraded"] is False
