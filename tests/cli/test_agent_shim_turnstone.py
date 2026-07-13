"""Turnstone-specific coverage for the ``hal0-agent`` shim.

Config resolution (per-type port/bin), the server argv/env builders, and
the render-context no-op branch. cmd_serve isn't run end-to-end (it would
block on a real subprocess + HTTP poll); the building blocks are tested.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.cli import agent_shim


@pytest.fixture
def empty_conf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(agent_shim, "_AGENTS_CONF_DIR", tmp_path)
    return tmp_path


def test_builtin_turnstone_resolves_type_and_port(empty_conf: Path) -> None:
    cfg = agent_shim._load_agent_config("turnstone")
    assert cfg.agent_type == "turnstone"
    assert cfg.port == 9129  # per-type default, not hermes' 9119
    assert cfg.host == "127.0.0.1"
    assert cfg.home == Path("/var/lib/hal0/.turnstone")


def test_hermes_default_port_unchanged(empty_conf: Path) -> None:
    cfg = agent_shim._load_agent_config("hermes")
    assert cfg.port == 9119


def test_status_url_is_health_route(empty_conf: Path) -> None:
    cfg = agent_shim._load_agent_config("turnstone")
    assert cfg.status_url == "http://127.0.0.1:9129/health"


def test_bin_prefers_existing_server_binary(
    empty_conf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "turnstone-server"
    server.write_text("x")
    monkeypatch.setattr(agent_shim, "_TURNSTONE_SERVER_BINS", (server,))
    cfg = agent_shim._load_agent_config("turnstone")
    assert cfg.bin == server


def test_bin_override_from_toml_wins(empty_conf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (empty_conf / "turnstone.toml").write_text(
        'type = "turnstone"\nbin = "/opt/ts/turnstone-server"\nport = 9200\n'
    )
    cfg = agent_shim._load_agent_config("turnstone")
    assert cfg.bin == Path("/opt/ts/turnstone-server")
    assert cfg.port == 9200


def test_default_turnstone_argv(empty_conf: Path) -> None:
    cfg = agent_shim._load_agent_config("turnstone")
    argv = agent_shim._build_turnstone_argv(cfg)
    assert argv[1:] == [
        "--host",
        "127.0.0.1",
        "--port",
        "9129",
        "--base-url",
        "http://127.0.0.1:8080/v1",
    ]


def test_serve_args_override_argv(empty_conf: Path) -> None:
    (empty_conf / "turnstone.toml").write_text(
        'type = "turnstone"\nserve_args = ["serve", "--addr", "127.0.0.1:9129"]\n'
    )
    cfg = agent_shim._load_agent_config("turnstone")
    argv = agent_shim._build_turnstone_argv(cfg)
    assert argv[1:] == ["serve", "--addr", "127.0.0.1:9129"]


def test_turnstone_env_sets_config_and_drops_notify(
    empty_conf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/x")
    cfg = agent_shim._load_agent_config("turnstone")
    env = agent_shim._build_turnstone_env(cfg)
    assert env["HAL0_AGENT_ID"] == "turnstone"
    assert env["TURNSTONE_CONFIG"] == "/var/lib/hal0/.turnstone/config.toml"
    assert "NOTIFY_SOCKET" not in env


def test_render_context_noop_for_turnstone(
    empty_conf: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = agent_shim._load_agent_config("turnstone")
    rc = agent_shim.cmd_render_context(cfg)
    assert rc == 0
    assert "noop for turnstone" in capsys.readouterr().out
