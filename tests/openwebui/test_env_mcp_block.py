"""``_mcp_env_block``, the new static flags and the ``_PRESERVE`` sentinel."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.openwebui.env_writer import (
    _DYNAMIC_ENV_KEYS,
    _OWUI_AGENTS_TOOLS,
    _OWUI_MEMORY_TOOLS,
    _image_gen_env_block,
    _mcp_env_block,
    default_openwebui_env,
    dynamic_env_overrides,
    write_openwebui_env,
)
from tests.openwebui._podman_env import parse_podman_env

BASE = "http://host.docker.internal:8080"
KEY = "hal0bk_openwebui.abc"


@pytest.fixture(autouse=True)
def _no_ambient_hal0_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_BIND_HOST", raising=False)


def _conns(scopes: frozenset[str]) -> list[dict]:
    return json.loads(_mcp_env_block(KEY, BASE, scopes)["TOOL_SERVER_CONNECTIONS"])


def _overrides(**kw):
    base = {"embed_model_id": None, "image_model_id": None, "search_provider": None}
    return dynamic_env_overrides(**{**base, **kw})


def test_static_defaults_include_task_model_and_user_headers() -> None:
    env = default_openwebui_env()
    assert env["TASK_MODEL_EXTERNAL"] == "utility"
    assert env["ENABLE_FORWARD_USER_INFO_HEADERS"] == "True"
    assert env["ENABLE_MEMORIES"] == "False"
    assert "FORWARD_USER_INFO_HEADER_JWT_SECRET" not in env


def test_image_block_enables_edit() -> None:
    b = _image_gen_env_block("sdxl")
    assert b["ENABLE_IMAGE_EDIT"] == "True" and b["IMAGE_EDIT_ENGINE"] == "openai"
    assert {"ENABLE_IMAGE_EDIT", "IMAGE_EDIT_ENGINE"} <= set(_DYNAMIC_ENV_KEYS)


def test_mcp_block_default_scopes_two_entries() -> None:
    conns = _conns(frozenset({"agents", "memory"}))
    assert [c["info"]["id"] for c in conns] == ["hal0-memory", "hal0-agents"]
    assert [c["info"]["name"] for c in conns] == ["hal0 memory", "Hermes (delegate)"]
    assert conns[0]["url"] == f"{BASE}/mcp/memory/mcp"
    assert conns[1]["url"] == f"{BASE}/mcp/agents/mcp"
    for c in conns:
        assert c["type"] == "mcp" and c["auth_type"] == "bearer" and c["path"] == ""
        assert c["key"] == KEY and c["config"]["enable"] is True


def test_mcp_block_admin_opt_in_is_unfiltered_third_entry() -> None:
    conns = _conns(frozenset({"agents", "memory", "admin"}))
    assert [c["info"]["id"] for c in conns] == ["hal0-memory", "hal0-agents", "hal0-admin"]
    assert conns[2]["info"]["name"] == "hal0 admin"
    assert conns[2]["url"] == f"{BASE}/mcp/admin/mcp"
    assert "function_name_filter_list" not in conns[2]["config"]


def test_mcp_block_filters_are_per_entry() -> None:
    conns = _conns(frozenset({"agents", "memory"}))
    assert conns[0]["config"]["function_name_filter_list"] == ",".join(_OWUI_MEMORY_TOOLS)
    assert conns[1]["config"]["function_name_filter_list"] == ",".join(_OWUI_AGENTS_TOOLS)
    assert "agent_run_wait" in _OWUI_AGENTS_TOOLS and "memory_recall" in _OWUI_MEMORY_TOOLS


def test_mcp_block_no_scopes_is_empty_list() -> None:
    assert _conns(frozenset()) == []


def test_mcp_block_nulled_when_bridge_disabled() -> None:
    o = _overrides(bridge_key=None, mcp_enabled=False)
    assert o["TOOL_SERVER_CONNECTIONS"] is None
    assert "TOOL_SERVER_CONNECTIONS" in _DYNAMIC_ENV_KEYS


def test_mcp_block_rendered_when_key_in_hand() -> None:
    o = _overrides(bridge_key=KEY, mcp_enabled=True, bridge_scopes=frozenset({"memory"}))
    assert json.loads(o["TOOL_SERVER_CONNECTIONS"])[0]["info"]["id"] == "hal0-memory"


def test_mcp_block_preserved_without_key_in_hand(tmp_path: Path) -> None:
    target = tmp_path / "openwebui.env"
    write_openwebui_env(
        target, overrides=_overrides(bridge_key=KEY, mcp_enabled=True), preserve_existing=True
    )
    first = parse_podman_env(target.read_text(encoding="utf-8"))["TOOL_SERVER_CONNECTIONS"]
    write_openwebui_env(
        target, overrides=_overrides(bridge_key=None, mcp_enabled=True), preserve_existing=True
    )
    text = target.read_text(encoding="utf-8")
    assert parse_podman_env(text)["TOOL_SERVER_CONNECTIONS"] == first
    assert "TOOL_SERVER_CONNECTIONS" in next(
        ln for ln in text.splitlines() if ln.startswith("# hal0-managed:")
    )
    # and a later disable removes it
    write_openwebui_env(
        target, overrides=_overrides(bridge_key=None, mcp_enabled=False), preserve_existing=True
    )
    assert "TOOL_SERVER_CONNECTIONS" not in parse_podman_env(target.read_text(encoding="utf-8"))


def test_preserve_writes_nothing_when_absent(tmp_path: Path) -> None:
    target = write_openwebui_env(
        tmp_path / "openwebui.env",
        overrides=_overrides(bridge_key=None, mcp_enabled=True),
        preserve_existing=True,
    )
    assert "TOOL_SERVER_CONNECTIONS" not in parse_podman_env(target.read_text(encoding="utf-8"))


def test_podman_parser_round_trips_tool_server_json(tmp_path: Path) -> None:
    target = write_openwebui_env(
        tmp_path / "openwebui.env", overrides=_overrides(bridge_key=KEY, mcp_enabled=True)
    )
    env = parse_podman_env(target.read_text(encoding="utf-8"))
    conns = json.loads(env["TOOL_SERVER_CONNECTIONS"])
    assert conns == json.loads(
        _mcp_env_block(KEY, BASE, frozenset({"agents", "memory"}))["TOOL_SERVER_CONNECTIONS"]
    )
