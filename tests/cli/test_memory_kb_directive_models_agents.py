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


_TREE = {
    "roots": [
        {
            "id": "kf-1",
            "kind": "folder",
            "name": "Showcase",
            "mental_model_id": None,
            "children": [
                {
                    "id": "kp-1",
                    "kind": "page",
                    "name": "Overview",
                    "mental_model_id": "mm-1",
                    "children": [],
                }
            ],
        }
    ]
}


def test_kb_refresh_goes_through_the_pages_mental_model(monkeypatch):
    """#3564: the engine refreshes a page through its mental model, a different id."""
    gets, posts = [], []
    monkeypatch.setattr(
        "hal0.cli.memory_kb_commands.api_get",
        lambda path, **kw: gets.append(path) or _TREE,
    )
    monkeypatch.setattr(
        "hal0.cli.memory_kb_commands.api_post",
        lambda path, **kw: posts.append(path) or {"operation_id": "op-1"},
    )
    r = runner.invoke(memory_commands.app, ["kb", "refresh", "shared", "kp-1", "--json"])
    assert r.exit_code == 0, r.output
    assert gets == ["/api/memory/banks/shared/knowledge-base/tree"]
    assert posts == ["/api/memory/banks/shared/mental-models/mm-1/refresh"]
    assert json.loads(r.output) == {"operation_id": "op-1"}


@pytest.mark.parametrize(
    ("node_id", "said"),
    [("kf-1", "is a folder, not a page"), ("kp-missing", "is not in the knowledge base")],
)
def test_kb_refresh_refuses_what_is_not_a_page(monkeypatch, node_id, said):
    posts = []
    monkeypatch.setattr("hal0.cli.memory_kb_commands.api_get", lambda path, **kw: _TREE)
    monkeypatch.setattr(
        "hal0.cli.memory_kb_commands.api_post", lambda path, **kw: posts.append(path)
    )
    r = runner.invoke(memory_commands.app, ["kb", "refresh", "shared", node_id])
    assert r.exit_code == 1
    assert said in " ".join(r.output.split())
    assert posts == []


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


_M = "hal0.cli.memory_models_agents_commands"
_COMPAT = "http://127.0.0.1:8080/api/memory/compat"


@pytest.fixture
def agents_box(monkeypatch, tmp_path):
    """``agents install`` against a temp config, an ``echo`` installer and a
    stubbed endpoint check. Returns ``(config_path, accepts)``: set
    ``accepts["value"]`` to what the endpoint says of a key."""
    monkeypatch.setattr(f"{_M}._HINDSIGHT_COMMAND", "echo")
    monkeypatch.delenv("HAL0_API_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".hindsight" / "coding-agent.json"
    config_path.parent.mkdir()
    monkeypatch.setattr(f"{_M}._hindsight_config_path", lambda: config_path)
    accepts: dict[str, object] = {"value": True, "seen": []}

    def check(url, token, **kw):
        accepts["seen"].append((url, token))
        return accepts["value"]

    monkeypatch.setattr("hal0.memory.compat_client.compat_accepts", check)
    return config_path, accepts


def test_agents_install_writes_the_harness_section_with_the_key(agents_box):
    config_path, _ = agents_box
    r = runner.invoke(
        memory_commands.app,
        ["agents", "install", "claude-code", "--bridge-key", "hal0bk_claude-code.k", "--json"],
    )
    assert r.exit_code == 0, r.output
    cfg = json.loads(config_path.read_text())
    section = cfg["harnesses"]["claude-code"]
    assert section["serverMode"] == "self-hosted"
    assert section["apiUrl"] == _COMPAT
    assert section["apiToken"] == "hal0bk_claude-code.k"
    assert section["pageTriggerType"] == "after-consolidation"
    assert section["observationScopes"] == "shared"
    assert section["retainTags"] == ["agent:claude-code"]
    assert oct(config_path.stat().st_mode & 0o777) == oct(0o600)
    # The key is never echoed back.
    assert "hal0bk_claude-code.k" not in r.output


def test_agents_install_leaves_the_flat_keys_and_other_harnesses_alone(agents_box):
    config_path, _ = agents_box
    original = {
        "apiUrl": "http://127.0.0.1:9177",
        "apiToken": "flat-token",
        "retainTags": ["agent:cursor"],
        "other": "kept",
        "harnesses": {"pi": {"apiUrl": "http://x/api/memory/compat", "apiToken": "pi-key"}},
    }
    config_path.write_text(json.dumps(original))
    r = runner.invoke(
        memory_commands.app, ["agents", "install", "codex", "--bridge-key", "hal0bk_codex.k"]
    )
    assert r.exit_code == 0, r.output
    cfg = json.loads(config_path.read_text())
    assert {k: v for k, v in cfg.items() if k != "harnesses"} == {
        k: v for k, v in original.items() if k != "harnesses"
    }
    assert cfg["harnesses"]["pi"] == original["harnesses"]["pi"]
    assert cfg["harnesses"]["codex"]["retainTags"] == ["agent:codex"]


def test_agents_install_reuses_a_working_token_already_on_the_endpoint(agents_box):
    config_path, accepts = agents_box
    config_path.write_text(
        json.dumps({"harnesses": {"codex": {"apiUrl": _COMPAT, "apiToken": "hal0bk_codex.old"}}})
    )
    r = runner.invoke(memory_commands.app, ["agents", "install", "codex"])
    assert r.exit_code == 0, r.output
    assert accepts["seen"] == [(_COMPAT, "hal0bk_codex.old")]
    assert json.loads(config_path.read_text())["harnesses"]["codex"]["apiToken"] == (
        "hal0bk_codex.old"
    )


@pytest.mark.parametrize(
    ("section", "verdict"),
    [
        ({}, True),  # no token at all
        ({"apiUrl": _COMPAT, "apiToken": "hal0bk_codex.old"}, False),  # rejected
        ({"apiUrl": _COMPAT, "apiToken": "hal0bk_codex.old"}, None),  # cannot be checked
        ({"apiUrl": "http://127.0.0.1:9177", "apiToken": "engine"}, True),  # not the endpoint
    ],
)
def test_agents_install_without_a_working_key_stops_before_running_anything(
    agents_box, monkeypatch, section, verdict
):
    config_path, accepts = agents_box
    accepts["value"] = verdict
    text = json.dumps({"harnesses": {"codex": section}})
    config_path.write_text(text)
    ran: list[object] = []
    monkeypatch.setattr(f"{_M}.subprocess.run", lambda *a, **k: ran.append(a))

    r = runner.invoke(memory_commands.app, ["agents", "install", "codex"])

    assert r.exit_code != 0
    out = " ".join(r.output.split())  # the error panel wraps lines
    assert "hal0 bridge add codex --scope memory" in out
    assert "--bridge-key" in out and "hal0 bridge rotate codex" in out
    assert ran == [] and config_path.read_text() == text


def test_agents_install_refuses_a_bridge_key_the_endpoint_rejects(agents_box):
    config_path, accepts = agents_box
    accepts["value"] = False
    r = runner.invoke(
        memory_commands.app, ["agents", "install", "codex", "--bridge-key", "hal0bk_codex.bad"]
    )
    assert r.exit_code != 0 and "rejected" in r.output
    assert not config_path.exists()


def test_agents_install_dry_run_names_the_endpoint_and_writes_nothing(agents_box):
    config_path, _ = agents_box
    r = runner.invoke(memory_commands.app, ["agents", "install", "codex", "--dry-run", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert payload["api_url"] == _COMPAT and payload["dry_run"] is True
    # The upstream installer is not told an address: given one, it writes the
    # flat apiUrl every harness reads.
    assert payload["command"] == "echo install codex"
    assert "--api-url" not in payload["command"] and "--server" not in payload["command"]
    assert not config_path.exists()


def test_agents_install_reads_the_key_from_a_file_or_stdin(agents_box, tmp_path):
    config_path, _ = agents_box
    key_file = tmp_path / "key"
    key_file.write_text("hal0bk_codex.fromfile\n")
    r = runner.invoke(
        memory_commands.app, ["agents", "install", "codex", "--bridge-key-file", str(key_file)]
    )
    assert r.exit_code == 0, r.output
    assert json.loads(config_path.read_text())["harnesses"]["codex"]["apiToken"] == (
        "hal0bk_codex.fromfile"
    )
    r = runner.invoke(
        memory_commands.app,
        ["agents", "install", "cursor", "--bridge-key-file", "-"],
        input="hal0bk_cursor.fromstdin\n",
    )
    assert r.exit_code == 0, r.output
    assert json.loads(config_path.read_text())["harnesses"]["cursor"]["apiToken"] == (
        "hal0bk_cursor.fromstdin"
    )
    assert "hal0bk_" not in r.output


def test_agents_install_refuses_both_key_options(agents_box, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("hal0bk_codex.k")
    r = runner.invoke(
        memory_commands.app,
        ["agents", "install", "codex", "--bridge-key", "x", "--bridge-key-file", str(key_file)],
    )
    assert r.exit_code != 0 and "not both" in r.output


def test_agents_install_bridge_key_help_warns_about_the_process_list():
    r = runner.invoke(memory_commands.app, ["agents", "install", "--help"])
    assert "process list" in " ".join(r.output.split())


def test_agents_install_runs_the_installer_with_stdin_closed(agents_box, monkeypatch):
    import subprocess as sp

    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return sp.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(f"{_M}.subprocess.run", fake_run)
    r = runner.invoke(
        memory_commands.app, ["agents", "install", "codex", "--bridge-key", "hal0bk_codex.k"]
    )
    assert r.exit_code == 0, r.output
    assert seen["stdin"] is sp.DEVNULL


@pytest.mark.parametrize(
    ("harness", "expected", "absent"),
    [
        ("pi", "hal0 bridge add pi-memory --scope memory", "hal0 bridge add pi "),
        ("hermes", "hal0 bridge add hermes-memory --scope memory", "hal0 bridge add hermes "),
        ("hal0-cli", "hal0 bridge add agent-hal0-cli --scope", "hal0 bridge add hal0-cli "),
        ("Claude.Code", "hal0 bridge add claude-code-memory", "hal0 bridge add Claude"),
    ],
)
def test_the_refusal_never_suggests_a_client_name_the_bridge_refuses(
    agents_box, harness, expected, absent
):
    r = runner.invoke(memory_commands.app, ["agents", "install", harness])
    assert r.exit_code != 0
    out = " ".join(r.output.split())
    assert expected in out and absent not in out


def test_a_symlinked_config_leaving_the_home_is_refused(agents_box, tmp_path):
    config_path, _ = agents_box
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text("{}")
    config_path.symlink_to(outside)
    try:
        r = runner.invoke(
            memory_commands.app, ["agents", "install", "codex", "--bridge-key", "hal0bk_codex.k"]
        )
        assert r.exit_code != 0 and "outside" in " ".join(r.output.split())
        assert outside.read_text() == "{}"
    finally:
        outside.unlink()


@pytest.mark.parametrize("how", ["flag", "file"])
def test_agents_install_refuses_a_key_that_is_not_a_bridge_key(agents_box, tmp_path, how):
    # The endpoint answers 200 for the admin key too: shape decides, before any probe.
    config_path, accepts = agents_box
    key_file = tmp_path / "key"
    key_file.write_text("the-admin-key\n")
    opt = (
        ["--bridge-key", "the-admin-key"] if how == "flag" else ["--bridge-key-file", str(key_file)]
    )
    r = runner.invoke(memory_commands.app, ["agents", "install", "codex", *opt])
    assert r.exit_code != 0 and "not a bridge key" in " ".join(r.output.split())
    assert accepts["seen"] == [] and not config_path.exists()


def test_agents_install_does_not_reuse_an_admin_key_in_the_section(agents_box):
    config_path, accepts = agents_box
    text = json.dumps({"harnesses": {"codex": {"apiUrl": _COMPAT, "apiToken": "admin"}}})
    config_path.write_text(text)
    r = runner.invoke(memory_commands.app, ["agents", "install", "codex"])
    assert r.exit_code != 0 and accepts["seen"] == [] and config_path.read_text() == text


def test_agents_install_refuses_both_key_options_even_with_dry_run(agents_box, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("hal0bk_codex.k")
    r = runner.invoke(
        memory_commands.app,
        [
            "agents",
            "install",
            "codex",
            "--dry-run",
            "--bridge-key",
            "hal0bk_codex.k",
            "--bridge-key-file",
            str(key_file),
        ],
    )
    assert r.exit_code != 0 and "not both" in r.output


@pytest.mark.parametrize("name", ["-rf", "--help-me", "a b", "a/b", "x;y", "", "é"])
def test_agents_install_refuses_an_unsafe_harness_name(agents_box, monkeypatch, name):
    ran: list[object] = []
    monkeypatch.setattr(f"{_M}.subprocess.run", lambda *a, **k: ran.append(a))
    r = runner.invoke(
        memory_commands.app, ["agents", "install", "--bridge-key", "hal0bk_x.k", "--", name]
    )
    assert r.exit_code != 0 and ran == []


def test_agents_install_masks_keys_in_the_installer_output(agents_box, monkeypatch):
    config_path, _ = agents_box
    config_path.write_text(json.dumps({"apiToken": "flat-secret"}))

    class _Done:
        returncode = 0
        stdout = "wrote hal0bk_codex.k and flat-secret and hal0bk_other.zz9\n"
        stderr = ""

    monkeypatch.setattr(f"{_M}.subprocess.run", lambda *a, **k: _Done())
    for extra in ([], ["--json"]):
        r = runner.invoke(
            memory_commands.app,
            ["agents", "install", "codex", "--bridge-key", "hal0bk_codex.k", *extra],
        )
        assert r.exit_code == 0, r.output
        assert "hal0bk_" not in r.output and "flat-secret" not in r.output
        assert "***" in r.output
