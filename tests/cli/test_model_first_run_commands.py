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


def _assert_clears_load_budget(kwargs: dict[str, Any]) -> None:
    """A blocking slot-load POST must out-wait the server's own worst case.

    Floor is computed from the server modules (the health poll plus the two
    sequential pre-load evictions ``preload_evict.admit`` may run inside the
    load path), never from the client constant under test.
    """
    from hal0.providers.container import _HEALTH_TIMEOUT_S
    from hal0.slots.manager import SlotManager

    floor = float(_HEALTH_TIMEOUT_S) + 2 * float(SlotManager._terminate_timeout_s)
    assert "timeout" in kwargs, "blocking load POST passed no explicit timeout kwarg"
    assert kwargs["timeout"] >= floor, (
        f"timeout={kwargs['timeout']} is under the server's {floor}s load worst case"
    )


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


def test_model_add_warns_on_failed_gguf_header_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1838 part B: a .gguf file with no valid GGUF magic still registers
    (by design), but the CLI must print the warning instead of the same
    confident success line a real header-derived registration gets."""

    def fake_post(path: str, *, json: Any = None, **_kw: Any) -> dict[str, Any]:
        return {
            "id": "zzskeprand",
            "path": "/mnt/ai-models/zzskeprand.gguf",
            "capabilities": ["chat"],
            "metadata": {
                "detection_confidence": "low",
                "detection_warning": (
                    "no valid GGUF header found (bad or missing magic bytes); "
                    "capabilities/backends are a filename guess"
                ),
            },
        }

    monkeypatch.setattr(shared, "api_post", fake_post)
    result = runner.invoke(
        model_commands.app,
        ["add", "/mnt/ai-models/zzskeprand.gguf"],
    )
    assert result.exit_code == 0, result.output
    assert "warning" in result.output.lower()
    assert "no valid GGUF header" in result.output


def test_model_add_shows_medium_confidence_for_filename_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1838: a filename-derived capability guess must be visibly flagged,
    not printed identically to a header-derived 'high' confidence hit."""

    def fake_post(path: str, *, json: Any = None, **_kw: Any) -> dict[str, Any]:
        return {
            "id": "jina-reranker-v2-base",
            "path": "/mnt/ai-models/jina-reranker-v2-base.gguf",
            "capabilities": ["rerank"],
            "metadata": {"detection_confidence": "medium"},
        }

    monkeypatch.setattr(shared, "api_post", fake_post)
    result = runner.invoke(
        model_commands.app,
        ["add", "/mnt/ai-models/jina-reranker-v2-base.gguf"],
    )
    assert result.exit_code == 0, result.output
    assert "medium" in result.output.lower()


def test_model_add_medium_confidence_message_not_filename_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1838 (codex review): a medium result can come from a header signal
    (attention.causal=False contradicting the chat default) rather than a
    filename guess — the printed explanation must not claim it's always
    the filename."""

    def fake_post(path: str, *, json: Any = None, **_kw: Any) -> dict[str, Any]:
        return {
            "id": "zzscratchrr",
            "path": "/mnt/ai-models/zzscratchrr.gguf",
            "capabilities": ["chat"],
            "metadata": {"detection_confidence": "medium"},
        }

    monkeypatch.setattr(shared, "api_post", fake_post)
    result = runner.invoke(
        model_commands.app,
        ["add", "/mnt/ai-models/zzscratchrr.gguf"],
    )
    assert result.exit_code == 0, result.output
    assert "medium" in result.output.lower()
    assert "filename" not in result.output.lower()


def test_model_add_with_license_follows_up_with_put(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI consolidation: `add` folded in `register`'s explicit-metadata
    flags — `--license` isn't accepted by add-from-path, so it's applied
    via a follow-up PUT /api/models/{id}."""
    put_calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(
        shared,
        "api_post",
        lambda path, **_kw: {"id": "demo-model", "path": "/mnt/ai-models/demo.gguf"},
    )
    monkeypatch.setattr(
        model_commands,
        "api_put",
        lambda path, *, json=None, **_kw: (
            put_calls.append((path, json))
            or {"id": "demo-model", "path": "/mnt/ai-models/demo.gguf", "license": json["license"]}
        ),
    )
    result = runner.invoke(
        model_commands.app,
        ["add", "/mnt/ai-models/demo.gguf", "--license", "Apache-2.0"],
    )
    assert result.exit_code == 0, result.output
    assert put_calls == [("/api/models/demo-model", {"license": "Apache-2.0"})]


def test_model_add_without_license_skips_put(monkeypatch: pytest.MonkeyPatch) -> None:
    put_calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        shared,
        "api_post",
        lambda path, **_kw: {"id": "demo-model", "path": "/mnt/ai-models/demo.gguf"},
    )
    monkeypatch.setattr(
        model_commands,
        "api_put",
        lambda path, *, json=None, **_kw: put_calls.append((path, json)),
    )
    result = runner.invoke(model_commands.app, ["add", "/mnt/ai-models/demo.gguf"])
    assert result.exit_code == 0, result.output
    assert put_calls == []


def _visible_command_names(typer_app) -> set[str]:
    """Return the subcommand names Click would actually list in --help
    (i.e. excluding ``hidden=True`` commands) — substring checks against
    rendered --help text are unreliable since command names can appear
    inside *other* commands' help strings (e.g. "add" mentions "Register")."""
    import click
    import typer

    click_cmd = typer.main.get_command(typer_app)
    ctx = click.Context(click_cmd)
    return {
        name for name in click_cmd.list_commands(ctx) if not click_cmd.get_command(ctx, name).hidden
    }


def test_model_register_is_hidden_deprecated_alias_for_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`model register` still works (no breaking removal), delegates to
    `model add`, and is no longer advertised in `--help`."""
    assert "register" not in _visible_command_names(model_commands.app)

    captured: dict[str, Any] = {}

    def fake_post(path: str, *, json: Any = None, **_kw: Any) -> dict[str, Any]:
        captured["post_path"] = path
        captured["post_json"] = json
        return {"id": json["id"], "path": json["path"]}

    put_calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(shared, "api_post", fake_post)
    monkeypatch.setattr(
        model_commands,
        "api_put",
        lambda path, *, json=None, **_kw: (
            put_calls.append((path, json))
            or {"id": "demo-model", "path": "/mnt/ai-models/demo.gguf", "license": json["license"]}
        ),
    )
    result = runner.invoke(
        model_commands.app,
        ["register", "demo-model", "--path", "/mnt/ai-models/demo.gguf"],
    )
    assert result.exit_code == 0, result.output
    assert "deprecat" in result.stderr.lower()
    assert "model add" in result.stderr.lower()
    # Delegated straight through to add-from-path with the explicit id.
    assert captured["post_path"] == "/api/models/add-from-path"
    assert captured["post_json"]["id"] == "demo-model"
    # register's default license ("unknown") is applied via the follow-up PUT.
    assert put_calls == [("/api/models/demo-model", {"license": "unknown"})]


def test_model_assign_is_hidden_deprecated_alias_for_slot_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`model assign` still works, delegates to the same PUT
    /api/slots/{slot}/config endpoint `slot edit --model` uses, and is no
    longer advertised in `--help`."""
    from hal0.cli import slot_commands

    assert "assign" not in _visible_command_names(model_commands.app)

    put_calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(slot_commands, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(
        slot_commands,
        "api_get",
        lambda path, **_kw: {"model": {"default": "old-model", "context_size": 4096}},
    )
    monkeypatch.setattr(
        slot_commands,
        "api_put",
        lambda path, *, json=None, **_kw: put_calls.append((path, json)) or {"state": "idle"},
    )
    result = runner.invoke(
        model_commands.app,
        ["assign", "demo-model", "--slot", "primary"],
    )
    assert result.exit_code == 0, result.output
    assert "deprecat" in result.stderr.lower()
    assert "slot edit" in result.stderr.lower()
    assert put_calls == [
        (
            "/api/slots/primary/config",
            {"model": {"default": "demo-model", "context_size": 4096}},
        )
    ]


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

    posted: dict[str, Any] = {}

    def fake_post(path: str, *, json: Any = None, **kw: Any) -> Any:
        calls.append(f"POST {path}")
        assert path == "/api/slots/chat/load"
        assert json == {"model_id": "qwen3-4b"}
        posted["kwargs"] = kw
        return {"state": "loading"}

    monkeypatch.setattr(model_commands, "api_get", fake_get)
    monkeypatch.setattr(shared, "api_post", fake_post)
    result = runner.invoke(model_commands.app, ["run", "qwen3-4b"])
    assert result.exit_code == 0, result.output
    assert "POST /api/slots/chat/load" in calls
    # /api/slots/{name}/load blocks until the slot converges — the POST must
    # carry the lifecycle budget, not api_post's generic 10s default (#1832).
    # Without it the command reports failure on a load that succeeded, and
    # never reaches the readiness poll its own --timeout advertises.
    _assert_clears_load_budget(posted["kwargs"])
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
