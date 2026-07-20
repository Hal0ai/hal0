"""Tests for the R3/R4 model verbs the CLI was missing (§5.2 R5 sync
assessment): ``model default``, ``model update [--check]``, and
``model pull --cancel``.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import model_commands

runner = CliRunner()


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_unreachable(_url: str) -> bool:
        return False

    def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        captured.setdefault("gets", []).append((path, kw))
        if path.endswith("/pull/status"):
            return {
                "state": "completed",
                "bytes_downloaded": 100,
                "bytes_total": 100,
                "sha256": "deadbeef" * 8,
                "path": "/var/lib/hal0/models/x.gguf",
            }
        if path == "/api/models/updates/check":
            return {
                "checked_at": 0,
                "checked": 1,
                "updates_available": 1,
                "models": {"qwen3-4b": {"update_available": True, "reason": "newer_sha256"}},
            }
        return {}

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        captured["method"] = "POST"
        captured["path"] = path
        captured["body"] = json or {}
        captured["kw"] = kw
        if path.endswith("/pull/cancel"):
            return {"state": "cancelled", "id": "job-1"}
        if path.endswith("/default"):
            return {
                "model_id": path.split("/")[-2],
                "changed": True,
                "demoted": ["old-default"],
            }
        if path.endswith("/update"):
            return {
                "id": "job-2",
                "model_id": "qwen3-4b",
                "hf_repo": "org/repo",
                "hf_file": "f.gguf",
            }
        return {}

    monkeypatch.setattr(model_commands, "_api_unreachable", fake_unreachable)
    monkeypatch.setattr(model_commands, "api_get", fake_get)
    monkeypatch.setattr(model_commands, "api_post", fake_post)
    return captured


# ── model default ───────────────────────────────────────────────────────────


def test_model_default_promotes(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["default", "qwen3-4b"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/models/qwen3-4b/default"
    assert captured["body"] == {"default": True}
    assert "default" in result.output.lower()
    assert "old-default" in result.output


def test_model_default_clear(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["default", "qwen3-4b", "--clear"])
    assert result.exit_code == 0, result.output
    assert captured["body"] == {"default": False}
    assert "cleared" in result.output.lower()


# ── model pull --cancel ─────────────────────────────────────────────────────


def test_model_pull_cancel_hits_cancel_route(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["pull", "qwen3-4b", "--cancel"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/models/qwen3-4b/pull/cancel"
    assert "cancelled" in result.output.lower() or "cancel" in result.output.lower()


def test_model_pull_starts_and_polls(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["pull", "qwen3-4b"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/models/qwen3-4b/pull"
    assert any(p.endswith("/pull/status") for p, _ in captured["gets"])


# ── model update [--check] ──────────────────────────────────────────────────


def test_model_update_check_hits_updates_check(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["update", "--check"])
    assert result.exit_code == 0, result.output
    path, kw = captured["gets"][0]
    assert path == "/api/models/updates/check"
    assert kw.get("params") is None
    assert "qwen3-4b" in result.output


def test_model_update_check_refresh_passes_param(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["update", "--check", "--refresh"])
    assert result.exit_code == 0, result.output
    path, kw = captured["gets"][0]
    assert path == "/api/models/updates/check"
    assert kw.get("params") == {"refresh": "1"}


def test_model_update_ref_hits_update_route_and_polls(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["update", "qwen3-4b"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/models/qwen3-4b/update"
    assert any(p.endswith("/pull/status") for p, _ in captured["gets"])
    assert "Updated" in result.output


def test_model_update_requires_ref_or_check(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["update"])
    assert result.exit_code != 0
    assert "--check" in result.output
