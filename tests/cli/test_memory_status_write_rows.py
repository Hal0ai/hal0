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

_WAITING = {
    "memory_enabled": True,
    "memory_degraded": False,
    "memory_write_degraded": False,
    "memory_write_health": {
        "degraded": False,
        "reason": "no_chat_model",
        "last_error": None,
        "operations": {"failed": 2, "pending": 0, "processing": 0},
        "bank": "shared",
        "waiting_on": "chat_model",
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


def test_no_chat_model_window_reads_as_waiting_not_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1792: a fresh install before any chat slot serves a model must not
    read as "memory is broken" — it self-heals once a model loads."""
    _stub(monkeypatch, _WAITING)
    result = runner.invoke(_test_app, [])
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "FAILING" not in out
    assert "waiting" in out
    assert "no chat model loaded" in out


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


def test_no_write_ever_landed_names_the_counts_and_the_remedy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#2404: a static failed backlog with zero facts is not "landing"."""
    payload = {
        "memory_enabled": True,
        "memory_degraded": False,
        "memory_write_degraded": True,
        "memory_write_health": {
            "degraded": True,
            "reason": "no_write_ever_landed",
            "last_error": "No write has landed: 7 failed, 0 facts stored on bank shared",
            "operations": {"failed": 7, "pending": 0, "processing": 0},
            "bank": "shared",
            "fact_count": 0,
        },
    }
    _stub(monkeypatch, payload)
    result = runner.invoke(_test_app, [])
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "No write has landed: 7 failed, 0 facts stored." in out
    assert "hal0 memory ops retry --all-failed" in out
    assert "landing" not in out.replace("No write has landed", "")


# ── #2353: the cached extraction throughput estimate, never a probe ──────────


def test_status_shows_the_cached_extraction_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.memory.extraction_env import ExtractionThroughput

    est = ExtractionThroughput(
        slot="utility", timeout_s=300, max_tokens=4096, prompt_tps=12.0, decode_tps=4.0
    ).as_dict()
    paths: list[str] = []

    def fake_get(path: str) -> dict[str, Any]:
        paths.append(path)
        if path == "/api/memory/graph/throughput":
            return {"throughput": est}
        return _HEALTHY

    def _no_probe(*_a: Any, **_k: Any) -> None:  # pragma: no cover — a call here is the bug
        raise AssertionError("hal0 memory status must not probe the slot")

    monkeypatch.setattr(mc, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(mc, "api_get", fake_get)
    monkeypatch.setattr(mc, "api_post", _no_probe)

    result = runner.invoke(_test_app, [], terminal_width=400)
    assert result.exit_code == 0, result.output
    assert "Extraction speed" in result.output
    assert "12.0 prompt tok/s" in result.output
    assert paths == ["/api/status", "/api/memory/graph/throughput"]

    as_json = runner.invoke(_test_app, ["--json"])
    assert json.loads(as_json.output)["extraction_throughput"]["verdict"] == "too_slow"
