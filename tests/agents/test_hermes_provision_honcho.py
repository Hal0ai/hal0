"""Unit tests for the honcho memory-provider wiring (feat/honcho-memory) in
:mod:`hal0.agents.hermes_provision`.

Covers the three new pieces: :func:`_resolve_memory_provider` (hal0.toml
agent-provider routing), the honcho branch of :func:`_build_config_overlay`
(and the dropped ``memory.graph.*`` dead-config pairs), and the
``$HERMES_HOME/honcho.json`` renderer/disabler pair
(:func:`_render_honcho_json` / :func:`_disable_honcho_hermes_host`).

``cfg`` fixtures are bare ``SimpleNamespace`` trees rather than a real
``Hal0Config`` — the code under test accesses ``cfg.memory.*`` /
``cfg.honcho.*`` by plain attribute/dict access (duck-typed), so these tests
stay valid regardless of the schema module's exact field set.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from hal0.agents import hermes_provision as hp


def _overlay_keys(**over):
    """Mirrors test_hermes_provision.py's ``_build_overlay_keys`` helper."""
    base = dict(
        primary={
            "model_id": "qwen3:8b",
            "backend_url": "http://127.0.0.1:8080/v1",
            "context_length": 16384,
        },
        chat_slots=[],
        delegation=None,
        auxiliary_tasks={},
        mcp_servers=[],
        agent_id="hermes",
        system_prompt="",
        personality_name="",
        live_resolve_enabled=True,
    )
    base.update(over)
    return dict(hp._build_config_overlay(**base))


# ── _build_config_overlay: memory_provider branches ──────────────────────────


def test_overlay_hindsight_branch_default_drops_graph_keys() -> None:
    keys = _overlay_keys()  # memory_provider defaults to "hal0-memory"
    assert keys["memory.provider"] == "hal0-memory"
    assert keys["memory.memory_enabled"] is True
    assert keys["memory.user_profile_enabled"] is True
    assert keys["memory.nudge_interval"] == 10
    assert "memory.graph.enabled" not in keys
    assert "memory.graph.extraction_slot" not in keys


def test_overlay_honcho_branch_sets_provider_and_drops_graph_keys() -> None:
    keys = _overlay_keys(memory_provider="honcho")
    assert keys["memory.provider"] == "honcho"
    assert keys["memory.memory_enabled"] is True
    assert keys["memory.user_profile_enabled"] is True
    assert keys["memory.nudge_interval"] == 10
    assert "memory.graph.enabled" not in keys
    assert "memory.graph.extraction_slot" not in keys


# ── _resolve_memory_provider ─────────────────────────────────────────────────


def test_resolve_memory_provider_default_is_hindsight() -> None:
    cfg = SimpleNamespace(memory=SimpleNamespace(agent_providers={}))
    assert hp._resolve_memory_provider("hermes", cfg) == "hal0-memory"


def test_resolve_memory_provider_routed_to_honcho() -> None:
    cfg = SimpleNamespace(memory=SimpleNamespace(agent_providers={"hermes": "honcho"}))
    assert hp._resolve_memory_provider("hermes", cfg) == "honcho"


def test_resolve_memory_provider_other_agent_routed_stays_hindsight() -> None:
    cfg = SimpleNamespace(memory=SimpleNamespace(agent_providers={"other-agent": "honcho"}))
    assert hp._resolve_memory_provider("hermes", cfg) == "hal0-memory"


# ── _render_honcho_json ───────────────────────────────────────────────────────


def _fake_cfg(*, port=8000, workspace="hal0", user_peer="operator", agent_private=None):
    return SimpleNamespace(
        memory=SimpleNamespace(agent_private=agent_private or {}),
        honcho=SimpleNamespace(port=port, workspace=workspace, user_peer=user_peer),
    )


def test_render_honcho_json_creates_file_on_empty_dir(tmp_path: Path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    cfg = _fake_cfg()

    changed = hp._render_honcho_json(home, cfg, "hermes")

    assert changed is True
    honcho_json = home / "honcho.json"
    assert honcho_json.is_file()
    data = json.loads(honcho_json.read_text())
    assert data["baseUrl"] == "http://127.0.0.1:8000"
    assert data["apiKey"] == "hal0-local-noauth"
    assert data["hosts"]["hermes"] == {
        "enabled": True,
        "workspace": "hal0",
        "peerName": "operator",
        "aiPeer": "hermes",
        "sessionStrategy": "per-session",
        "pinUserPeer": True,
        "saveMessages": True,
    }


def test_render_honcho_json_second_call_is_noop(tmp_path: Path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    cfg = _fake_cfg()

    assert hp._render_honcho_json(home, cfg, "hermes") is True
    assert hp._render_honcho_json(home, cfg, "hermes") is False


def test_render_honcho_json_private_agent_gets_isolated_workspace(tmp_path: Path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    cfg = _fake_cfg(agent_private={"hermes": True})

    hp._render_honcho_json(home, cfg, "hermes")

    data = json.loads((home / "honcho.json").read_text())
    assert data["hosts"]["hermes"]["workspace"] == "hal0__private__hermes"


def test_render_honcho_json_overwrites_stale_cloud_preserves_unknown_keys(tmp_path: Path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    stale = {
        # Honcho-CLOUD leftovers: cloud api key, no baseUrl.
        "apiKey": "hch-v3-abcdef123456",
        "someTopLevelUnknown": "keep-me",
        "hosts": {
            "hermes": {
                "enabled": True,
                "peerName": "old-peer",
                "aiPeer": "old-ai",
                "workspace": "old-workspace",
                "observationMode": "active",
            },
            "hermes_research": {
                "enabled": True,
                "peerName": "old-peer",
                "aiPeer": "old-ai",
                "workspace": "old-workspace",
                "customFlag": True,
            },
        },
    }
    (home / "honcho.json").write_text(json.dumps(stale))
    cfg = _fake_cfg()

    changed = hp._render_honcho_json(home, cfg, "hermes")

    assert changed is True
    data = json.loads((home / "honcho.json").read_text())
    assert data["apiKey"] == "hal0-local-noauth"
    assert data["baseUrl"] == "http://127.0.0.1:8000"
    assert data["someTopLevelUnknown"] == "keep-me"

    hermes_host = data["hosts"]["hermes"]
    assert hermes_host["peerName"] == "operator"
    assert hermes_host["aiPeer"] == "hermes"
    assert hermes_host["workspace"] == "hal0"
    assert hermes_host["observationMode"] == "active"  # unknown key preserved

    profile_host = data["hosts"]["hermes_research"]
    assert profile_host["peerName"] == "operator"
    assert profile_host["aiPeer"] == "hermes"
    assert profile_host["workspace"] == "hal0"
    assert profile_host["customFlag"] is True  # unknown key preserved
    # "enabled" is not one of the managed keys for a profile block.
    assert profile_host["enabled"] is True


def test_render_honcho_json_does_not_invent_new_profile_blocks(tmp_path: Path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    cfg = _fake_cfg()

    hp._render_honcho_json(home, cfg, "hermes")

    data = json.loads((home / "honcho.json").read_text())
    assert set(data["hosts"]) == {"hermes"}


# ── _disable_honcho_hermes_host ──────────────────────────────────────────────


def test_disable_honcho_hermes_host_noop_when_file_absent(tmp_path: Path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()

    assert hp._disable_honcho_hermes_host(home) is False
    assert not (home / "honcho.json").exists()


def test_disable_honcho_hermes_host_flips_enabled_preserves_rest(tmp_path: Path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    existing = {
        "baseUrl": "http://127.0.0.1:8000",
        "apiKey": "hal0-local-noauth",
        "hosts": {
            "hermes": {
                "enabled": True,
                "workspace": "hal0",
                "peerName": "operator",
                "aiPeer": "hermes",
            }
        },
    }
    (home / "honcho.json").write_text(json.dumps(existing))

    changed = hp._disable_honcho_hermes_host(home)

    assert changed is True
    data = json.loads((home / "honcho.json").read_text())
    assert data["hosts"]["hermes"]["enabled"] is False
    assert data["hosts"]["hermes"]["workspace"] == "hal0"
    assert data["baseUrl"] == "http://127.0.0.1:8000"


def test_disable_honcho_hermes_host_already_disabled_no_change(tmp_path: Path) -> None:
    home = tmp_path / "hermes_home"
    home.mkdir()
    existing = {"hosts": {"hermes": {"enabled": False}}}
    (home / "honcho.json").write_text(json.dumps(existing))

    assert hp._disable_honcho_hermes_host(home) is False


def test_resolve_memory_provider_legacy_agent_id_suffix():
    """provision.json state predating #1056 says 'hermes-agent' — must still
    hit the canonical 'hermes' toggle key."""
    from types import SimpleNamespace

    from hal0.agents.hermes_provision import _resolve_memory_provider

    cfg = SimpleNamespace(memory=SimpleNamespace(agent_providers={"hermes": "honcho"}, agent_private={}))
    assert _resolve_memory_provider("hermes-agent", cfg) == "honcho"
    assert _resolve_memory_provider("hermes", cfg) == "honcho"
