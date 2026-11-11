"""Tests for hal0 memory kb/directive/models/agents CLI commands.

Uses typer's CliRunner against ``hal0.cli.memory_commands.app`` with
``hal0.cli._shared.api_get``/``api_post``/``api_delete`` monkeypatched —
no live hal0 API or Hindsight needed.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from hal0.cli import memory_commands

runner = CliRunner()


@pytest.fixture(autouse=True)
def _api_reachable(monkeypatch):
    # Every module imports `_api_unreachable` directly, so patch each copy.
    for mod in (
        "hal0.cli.memory_kb_commands",
        "hal0.cli.memory_directive_commands",
        "hal0.cli.memory_models_agents_commands",
    ):
        monkeypatch.setattr(f"{mod}._api_unreachable", lambda url: False)


def test_kb_tree(monkeypatch):
    monkeypatch.setattr(
        "hal0.cli.memory_kb_commands.api_get",
        lambda path, **kw: {
            "nodes": [{"id": "f1", "name": "Showcase", "type": "folder", "children": []}]
        },
    )
    r = runner.invoke(memory_commands.app, ["kb", "tree", "shared", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert payload["nodes"][0]["name"] == "Showcase"


def test_kb_show(monkeypatch):
    monkeypatch.setattr(
        "hal0.cli.memory_kb_commands.api_get",
        lambda path, **kw: {"id": "p1", "name": "Overview", "content": "hello"},
    )
    r = runner.invoke(memory_commands.app, ["kb", "show", "shared", "p1"])
    assert r.exit_code == 0
    assert "hello" in r.output


def test_kb_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hal0.cli.memory_kb_commands.api_post",
        lambda path, **kw: calls.append(path) or {"queued": True},
    )
    r = runner.invoke(memory_commands.app, ["kb", "refresh", "shared", "p1", "--json"])
    assert r.exit_code == 0
    assert calls == ["/api/memory/banks/shared/knowledge-base/pages/p1/refresh"]
    assert json.loads(r.output) == {"queued": True}


def test_directive_list(monkeypatch):
    monkeypatch.setattr(
        "hal0.cli.memory_directive_commands.api_get",
        lambda path, **kw: {"items": [{"id": "d1", "text": "always cite sources", "active": True}]},
    )
    r = runner.invoke(memory_commands.app, ["directive", "list", "shared"])
    assert r.exit_code == 0
    assert "always cite sources" in r.output


def test_directive_add(monkeypatch):
    captured = {}

    def fake_post(path, **kw):
        captured["path"] = path
        captured["json"] = kw.get("json")
        return {"id": "d2"}

    monkeypatch.setattr("hal0.cli.memory_directive_commands.api_post", fake_post)
    r = runner.invoke(memory_commands.app, ["directive", "add", "shared", "be concise", "--json"])
    assert r.exit_code == 0
    assert captured["path"] == "/api/memory/banks/shared/directives"
    assert captured["json"] == {"text": "be concise"}


def test_directive_rm(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "hal0.cli.memory_directive_commands.api_delete",
        lambda path, **kw: captured.setdefault("path", path) or {},
    )
    r = runner.invoke(memory_commands.app, ["directive", "rm", "shared", "d1"])
    assert r.exit_code == 0
    assert captured["path"] == "/api/memory/banks/shared/directives/d1"


def test_models_bare_invocation(monkeypatch):
    monkeypatch.setattr(
        "hal0.cli.memory_models_agents_commands.api_get",
        lambda path, **kw: {
            "embeddings": {"provider": "openai", "model": "text-embedding-3-small"},
            "reranker": {"provider": "cohere", "model": "rerank-v3"},
            "decision": {
                "enabled": True,
                "provider": "local",
                "model": "hal0-hs-decide",
                "reachable": True,
            },
        },
    )
    r = runner.invoke(memory_commands.app, ["models", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert payload["embeddings"]["provider"] == "openai"


def test_models_table_shows_the_recorded_embedder_and_flags_a_difference(monkeypatch):
    """#3518: the engine runs the recorded model; the configured one is named as not applied."""
    monkeypatch.setattr("hal0.cli.memory_models_agents_commands._require_api", lambda: "http://x")
    monkeypatch.setattr(
        "hal0.cli.memory_models_agents_commands.api_get",
        lambda path, **kw: {
            "embeddings": {
                "provider": "local",
                "model": "hindsight-ai/hs-embed-1",
                "recorded": "BAAI/bge-small-en-v1.5",
                "configured": "hindsight-ai/hs-embed-1",
                "mixed": False,
                "needs_switch": True,
            },
            "reranker": {"provider": "local", "model": "hindsight-ai/hs-rerank-1"},
            "decision": {"enabled": False, "provider": "off", "model": "x", "reachable": False},
        },
    )
    # A wide console: the default one wraps the status cell at the runner's
    # width. Height is pinned too, or rich falls back to 80x25 on a dumb terminal.
    from rich.console import Console

    monkeypatch.setattr(
        "hal0.cli.memory_models_agents_commands.console",
        Console(width=200, height=50, color_system=None),
    )
    r = runner.invoke(memory_commands.app, ["models"])
    assert r.exit_code == 0, r.output
    assert "BAAI/bge-small-en-v1.5" in r.output
    assert "configured: hindsight-ai/hs-embed-1 (not applied;" in r.output
    # #3518: the row names the command that moves the store.
    assert "`hal0 memory embedder switch` moves the store" in r.output


def test_agents_install_dry_run(monkeypatch):
    monkeypatch.setattr(
        "hal0.cli.memory_models_agents_commands.api_get",
        lambda path, **kw: {
            "install": {
                "harnesses": ["claude-code"],
                "command": "npx @vectorize-io/hindsight-coding-agents@0.8.5 install <harness> --server self-hosted --api-url http://x:9177",
            }
        },
    )
    r = runner.invoke(
        memory_commands.app, ["agents", "install", "claude-code", "--dry-run", "--json"]
    )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert "install claude-code" in payload["command"]
    assert payload["dry_run"] is True


def test_agents_install_runs_and_merges_config(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hal0.cli.memory_models_agents_commands.api_get",
        lambda path, **kw: {
            "install": {
                "harnesses": ["claude-code"],
                "command": "echo installed-ok",
            }
        },
    )
    config_path = tmp_path / "coding-agent.json"
    monkeypatch.setattr(
        "hal0.cli.memory_models_agents_commands._hindsight_config_path", lambda: config_path
    )

    r = runner.invoke(memory_commands.app, ["agents", "install", "claude-code", "--json"])
    assert r.exit_code == 0
    assert config_path.exists()
    cfg = json.loads(config_path.read_text())
    assert cfg["pageTriggerType"] == "after-consolidation"
    assert cfg["observationScopes"] == "shared"
    assert cfg["retainTags"] == ["agent:claude-code"]


def test_agents_install_merges_existing_retain_tags(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hal0.cli.memory_models_agents_commands.api_get",
        lambda path, **kw: {"install": {"command": "echo ok"}},
    )
    config_path = tmp_path / "coding-agent.json"
    config_path.write_text(json.dumps({"retainTags": ["agent:cursor"], "other": "kept"}))
    monkeypatch.setattr(
        "hal0.cli.memory_models_agents_commands._hindsight_config_path", lambda: config_path
    )

    r = runner.invoke(memory_commands.app, ["agents", "install", "codex", "--json"])
    assert r.exit_code == 0
    cfg = json.loads(config_path.read_text())
    assert sorted(cfg["retainTags"]) == ["agent:codex", "agent:cursor"]
    assert cfg["other"] == "kept"
