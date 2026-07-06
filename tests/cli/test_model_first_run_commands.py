"""Tests for the first-run model commands: scan, add, store, run.

These are the CLI face of the storage-mismatch fixes: users who hand-place
weights (or relocate the store) must be able to register + serve a model
without touching the API or restarting hal0-api.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

import hal0.cli._shared as shared
from hal0.cli import model_commands

runner = CliRunner()


@pytest.fixture(autouse=True)
def _api_reachable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(model_commands, "_api_unreachable", lambda _url: False)


def test_model_scan_lists_added_models(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(path: str, **_kw: Any) -> dict[str, Any]:
        assert path == "/api/models/scan"
        return {"added": ["qwen3-4b-q4_k_m"], "skipped": 2, "scanned_roots": ["/mnt/ai-models"]}

    monkeypatch.setattr(shared, "api_post", fake_post)
    result = runner.invoke(model_commands.app, ["scan"])
    assert result.exit_code == 0, result.output
    assert "qwen3-4b-q4_k_m" in result.output
    assert "/mnt/ai-models" in result.output


def test_model_scan_zero_added_hints_at_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        shared,
        "api_post",
        lambda path, **_kw: {"added": [], "skipped": 0, "scanned_roots": ["/var/lib/hal0/models"]},
    )
    result = runner.invoke(model_commands.app, ["scan"])
    assert result.exit_code == 0, result.output
    assert "hal0 model store" in result.output


def test_model_add_posts_add_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(path: str, *, json: Any = None, **_kw: Any) -> dict[str, Any]:
        captured["path"] = path
        captured["json"] = json
        return {
            "id": "chadrock-35b-ace-saber",
            "path": "/mnt/ai-models/chadrock-35b-ace-saber/model.gguf",
            "capabilities": ["chat"],
        }

    monkeypatch.setattr(shared, "api_post", fake_post)
    result = runner.invoke(
        model_commands.app,
        ["add", "/mnt/ai-models/chadrock-35b-ace-saber/model.gguf"],
    )
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/models/add-from-path"
    assert captured["json"]["path"] == "/mnt/ai-models/chadrock-35b-ace-saber/model.gguf"
    assert "chadrock-35b-ace-saber" in result.output
    # Points the user at the next step.
    assert "hal0 model run" in result.output


def test_model_store_show_reports_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        model_commands,
        "api_get",
        lambda path, **_kw: {
            "store": None,
            "effective": "/var/lib/hal0/models",
            "fallback_active": True,
            "suggestions": [{"path": "/mnt/ai-models", "note": "existing files"}],
        },
    )
    result = runner.invoke(model_commands.app, ["store"])
    assert result.exit_code == 0, result.output
    assert "unset" in result.output
    assert "/var/lib/hal0/models" in result.output
    assert "/mnt/ai-models" in result.output


def test_model_store_set_surfaces_scan_results(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(path: str, *, json: Any = None, **_kw: Any) -> dict[str, Any]:
        assert path == "/api/settings/models/store"
        assert json == {"path": "/mnt/ai-models", "migrate": False}
        return {
            "status": "ok",
            "migration": None,
            "scan": {"added": ["found-on-disk-model"], "skipped": 0},
        }

    monkeypatch.setattr(shared, "api_post", fake_post)
    result = runner.invoke(model_commands.app, ["store", "/mnt/ai-models"])
    assert result.exit_code == 0, result.output
    assert "found-on-disk-model" in result.output


def test_model_run_loads_and_waits_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(path: str, **_kw: Any) -> Any:
        calls.append(f"GET {path}")
        if path == "/api/models/qwen3-4b":
            return {"id": "qwen3-4b"}
        if path == "/api/slots":
            return {"slots": [{"name": "chat"}, {"name": "npu"}]}
        if path == "/api/slots/chat":
            return {"name": "chat", "status": "ready", "port": 8081}
        raise AssertionError(path)

    def fake_post(path: str, *, json: Any = None, **_kw: Any) -> Any:
        calls.append(f"POST {path}")
        assert path == "/api/slots/chat/load"
        assert json == {"model_id": "qwen3-4b"}
        return {"state": "loading"}

    monkeypatch.setattr(model_commands, "api_get", fake_get)
    monkeypatch.setattr(shared, "api_post", fake_post)
    result = runner.invoke(model_commands.app, ["run", "qwen3-4b"])
    assert result.exit_code == 0, result.output
    assert "POST /api/slots/chat/load" in calls
    assert "Ready" in result.output
    assert "curl" in result.output  # prints a copy-paste smoke test


def test_model_run_unregistered_model_names_the_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(path: str, **_kw: Any) -> Any:
        raise shared.CliApiError("404 model not found")

    monkeypatch.setattr(model_commands, "api_get", fake_get)
    result = runner.invoke(model_commands.app, ["run", "missing-model"])
    assert result.exit_code == 1
    out = result.output
    assert "hal0 model pull" in out
    assert "hal0 model add" in out
    assert "hal0 model scan" in out
