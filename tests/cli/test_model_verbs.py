"""Tests for the R3/R4 model verbs the CLI was missing (§5.2 R5 sync
assessment): ``model default``, ``model update [--check]``, and
``model pull --cancel``.
"""

from __future__ import annotations

from pathlib import Path
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

    def fake_put(path: str, *, json: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        captured["method"] = "PUT"
        captured["path"] = path
        captured["body"] = json or {}
        return {
            "id": path.rsplit("/", 1)[-1],
            "draft": (json or {}).get("draft"),
            "draft_kind": "dflash",
        }

    monkeypatch.setattr(model_commands, "api_post", fake_post)
    # `model add` imports api_post from the shared module at call time.
    monkeypatch.setattr("hal0.cli._shared.api_post", fake_post)
    monkeypatch.setattr(model_commands, "api_put", fake_put)
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


def test_model_pull_draft_sends_draft_coordinates(captured: dict[str, Any]) -> None:
    result = runner.invoke(
        model_commands.app,
        ["pull", "qwen3-4b", "--draft", "d-Q8_0.gguf", "--draft-repo", "org/drafters"],
    )
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/models/qwen3-4b/pull"
    assert captured["body"] == {"draft_filename": "d-Q8_0.gguf", "draft_repo": "org/drafters"}


def test_model_pull_draft_repo_needs_draft(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["pull", "qwen3-4b", "--draft-repo", "org/x"])
    assert result.exit_code != 0
    assert "path" not in captured


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


# ── model rm → DELETE /api/models/<id>?delete_files=… (ADR-0026) ──────────


@pytest.fixture
def deletes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_delete(path: str, **kw: Any) -> dict[str, Any]:
        calls.append((path, kw))
        keep = kw.get("params", {}).get("delete_files") == "false"
        return {
            "id": "qwen3-4b",
            "deleted": True,
            "affected_slots": [],
            "files_removed": [] if keep else [{"path": "/m/q.gguf", "size_bytes": 2048}],
            "files_kept": [{"path": "/m/q.gguf", "reason": "kept_by_request"}] if keep else [],
            "bytes_freed": 0 if keep else 2048,
            "tombstoned": ["/m/q.gguf"] if keep else [],
            "warnings": [],
        }

    monkeypatch.setattr(model_commands, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(model_commands, "api_delete", fake_delete)
    return calls


def test_model_rm_deletes_files_by_default(deletes: list[tuple[str, dict[str, Any]]]) -> None:
    result = runner.invoke(model_commands.app, ["rm", "qwen3-4b", "--force"])

    assert result.exit_code == 0, result.output
    assert deletes == [
        ("/api/models/qwen3-4b", {"params": {"delete_files": "true"}, "timeout": 60.0})
    ]
    assert "freed 2.0KB" in result.output


def test_model_rm_keep_files_sends_opt_out(deletes: list[tuple[str, dict[str, Any]]]) -> None:
    result = runner.invoke(model_commands.app, ["rm", "qwen3-4b", "--force", "--keep-files"])

    assert result.exit_code == 0, result.output
    assert deletes == [
        ("/api/models/qwen3-4b", {"params": {"delete_files": "false"}, "timeout": 60.0})
    ]
    assert "kept_by_request" in result.output
    assert "hidden from future scans" in result.output


def test_model_rm_keep_files_keeps_add_hint_for_addable_extensions(
    deletes: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A kept file whose extension add accepts keeps the `model add` hint."""
    from hal0.config import paths as cfg_paths

    monkeypatch.setattr(cfg_paths, "hal0_toml", lambda: tmp_path / "hal0.toml")
    result = runner.invoke(model_commands.app, ["rm", "qwen3-4b", "--force", "--keep-files"])

    assert result.exit_code == 0, result.output
    # rich wraps at the console width; collapse whitespace before matching.
    assert "`hal0 model add <path>` brings one back" in " ".join(result.output.split())


def test_model_rm_keep_files_hints_pull_when_add_would_refuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """#2748: a kept file outside [models].file_extensions cannot come back
    through `model add`, so the hint must name `model pull` instead."""
    from hal0.config import paths as cfg_paths

    monkeypatch.setattr(cfg_paths, "hal0_toml", lambda: tmp_path / "hal0.toml")
    monkeypatch.setattr(model_commands, "_api_unreachable", lambda _url: False)
    kept = "/srv/hal0/models/tts/kokoro-v1/kokoro-v1.0.onnx"
    monkeypatch.setattr(
        model_commands,
        "api_delete",
        lambda path, **kw: {
            "id": "kokoro-v1",
            "deleted": True,
            "files_removed": [],
            "files_kept": [{"path": kept, "reason": "kept_by_request"}],
            "bytes_freed": 0,
            "tombstoned": [kept],
            "warnings": [],
        },
    )
    result = runner.invoke(model_commands.app, ["rm", "kokoro-v1", "--force", "--keep-files"])

    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "`hal0 model pull kokoro-v1` is the way back" in out
    assert "brings one back" not in out


def test_model_rm_keep_files_hints_no_pull_for_uncurated_models(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """rm dropped the registry row that carried a non-curated model's HF
    source, so a bare `model pull <id>` cannot resolve it (#2748 review).
    The hint must not name a command that would raise."""
    from hal0.config import paths as cfg_paths

    monkeypatch.setattr(cfg_paths, "hal0_toml", lambda: tmp_path / "hal0.toml")
    monkeypatch.setattr(model_commands, "_api_unreachable", lambda _url: False)
    kept = "/srv/hal0/models/local-custom-7b/weights.onnx"
    monkeypatch.setattr(
        model_commands,
        "api_delete",
        lambda path, **kw: {
            "id": "local-custom-7b",
            "deleted": True,
            "files_removed": [],
            "files_kept": [{"path": kept, "reason": "kept_by_request"}],
            "bytes_freed": 0,
            "tombstoned": [kept],
            "warnings": [],
        },
    )
    result = runner.invoke(model_commands.app, ["rm", "local-custom-7b", "--force", "--keep-files"])

    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "`hal0 model add` refuses their extension(s)" in out
    assert "model pull" not in out


def test_add_would_accept_falls_back_to_schema_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable config must not reinstate the false add hint (#2748
    review): compare suffixes against the schema's default extensions."""
    from hal0.config import loader

    def unreadable() -> None:
        raise RuntimeError("no config")

    monkeypatch.setattr(loader, "load_hal0_config", unreadable)

    assert model_commands._add_would_accept(["/models/weights.gguf"]) is True
    assert model_commands._add_would_accept(["/models/voices.onnx"]) is False


def test_is_curated_returns_false_when_the_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed catalogue lookup must not promise a pull it cannot resolve."""
    from hal0.registry import curated

    def unreadable(_model_id: str) -> None:
        raise RuntimeError("catalogue unreadable")

    monkeypatch.setattr(curated, "get_curated", unreadable)

    assert model_commands._is_curated("orphan-7b") is False


def test_model_rm_prompt_names_the_files(deletes: list[tuple[str, dict[str, Any]]]) -> None:
    result = runner.invoke(model_commands.app, ["rm", "qwen3-4b"], input="n\n")

    assert result.exit_code != 0
    assert "and its files on disk" in result.output
    assert deletes == []


def test_model_rm_timeout_outlasts_the_server_pull_cancel_wait() -> None:
    from hal0.services.models_service import PULL_CANCEL_WAIT_S

    assert model_commands._RM_TIMEOUT_S > PULL_CANCEL_WAIT_S


# ── model add --draft / model set --draft ───────────────────────────────────


def test_model_add_draft_flag(captured: dict[str, Any]) -> None:
    result = runner.invoke(
        model_commands.app, ["add", "/m/tgt.gguf", "--draft", "/m/tgt-dflash.gguf"]
    )
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/models/add-from-path"
    assert captured["body"]["draft"] == "/m/tgt-dflash.gguf"


def test_model_add_without_draft_sends_none(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["add", "/m/tgt.gguf"])
    assert result.exit_code == 0, result.output
    assert "draft" not in captured["body"]


def test_model_set_draft_pairs(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["set", "tgt", "--draft", "/m/tgt-dflash.gguf"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "PUT"
    assert captured["path"] == "/api/models/tgt"
    assert captured["body"] == {"draft": "/m/tgt-dflash.gguf"}


def test_model_set_draft_none_unpairs(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["set", "tgt", "--draft", "none"])
    assert result.exit_code == 0, result.output
    assert captured["body"] == {"draft": None}


def test_model_set_needs_a_field(captured: dict[str, Any]) -> None:
    result = runner.invoke(model_commands.app, ["set", "tgt"])
    assert result.exit_code != 0
    assert "path" not in captured
