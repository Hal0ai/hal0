"""Tests for :mod:`hal0.mcp.hermes_join` — ADR-0015 §Decision 2.

Every test runs under ``tmp_hal0_home`` so real ``/etc/hal0``/``/var/lib/hal0``
are never touched (see the ``fix(mcp): make hermes_join HAL0_HOME-aware``
commit — the first cut of this module didn't thread paths through and
would have written to the real filesystem here).
"""

from __future__ import annotations

from pathlib import Path

from hal0.config import paths as cfg_paths
from hal0.config.schema import ToolPolicy
from hal0.mcp import hermes_join, installed


def _install(server_id: str, **overrides: object) -> installed.InstalledServer:
    defaults: dict[str, object] = {
        "id": server_id,
        "name": server_id,
        "spec": f"https://{server_id}.example.com/manifest.json",
        "transport": "streamable-http",
        "url": f"https://{server_id}.example.com/mcp",
        "enabled": True,
    }
    defaults.update(overrides)
    return installed.install(installed.InstalledServer(**defaults))


def test_sync_exposure_noop_when_nothing_exposed(tmp_hal0_home: str) -> None:
    _install("github")  # exposure defaults to all-False
    report = hermes_join.sync_exposure()
    assert report["hermes"]["applied"] == 0
    assert report["hermes"]["removed"] == []
    assert report["errors"] == []


def test_sync_exposure_applies_exposed_server(tmp_hal0_home: str) -> None:
    _install("github", exposure=installed.ExposureConfig(hermes=True))
    report = hermes_join.sync_exposure()
    # hermes binary doesn't exist under the tmp sandbox -> apply degrades to
    # a recorded error rather than a crash, but the manifest still tracks
    # "github" as desired so a later real-hermes sync would apply it.
    assert "github" in "".join(report["hermes"].get("errors", [])) or report["hermes"]["applied"] == 0
    manifest_path = cfg_paths.var_lib() / "mcp" / "hermes-managed.json"
    assert manifest_path.exists()
    import json

    manifest = json.loads(manifest_path.read_text())
    assert manifest["hermes"] == ["github"]


def test_sync_exposure_mirrors_tools_into_seed_toml(tmp_hal0_home: str) -> None:
    _install(
        "github",
        exposure=installed.ExposureConfig(hermes=True),
        tool_policy=ToolPolicy(allow=["search"], gated=["create_pr"]),
    )
    hermes_join.sync_exposure()
    seed_path = cfg_paths.etc() / "agents" / "hermes.toml"
    assert seed_path.exists()
    import tomllib

    data = tomllib.loads(seed_path.read_text())
    github_entry = data["mcp"]["servers"]["github"]
    assert github_entry["builtin"] is False
    assert github_entry["tools"]["allow"] == ["search"]
    assert github_entry["tools"]["gated"] == ["create_pr"]


def test_sync_exposure_preserves_operator_seed_blocks(tmp_hal0_home: str) -> None:
    """An operator's hand-added [mcp.servers.*] block is never touched.

    Simulates a pre-existing seed TOML with a hand-added server that was
    never installed through this registry — the sync must leave it intact
    (it was never in hal0's own ownership manifest).
    """
    seed_path = cfg_paths.etc() / "agents" / "hermes.toml"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(
        '[mcp.servers.operator-added]\nbuiltin = false\nenabled = true\n'
        '[mcp.servers.operator-added.tools]\nallow = ["hand_tool"]\n'
        'gated = []\nblocked = []\n'
    )
    _install("github", exposure=installed.ExposureConfig(hermes=True))
    hermes_join.sync_exposure()

    import tomllib

    data = tomllib.loads(seed_path.read_text())
    assert "operator-added" in data["mcp"]["servers"]
    assert data["mcp"]["servers"]["operator-added"]["tools"]["allow"] == ["hand_tool"]


def test_sync_exposure_removes_only_previously_owned_ids(tmp_hal0_home: str) -> None:
    """Disabling exposure removes hal0's own seed entry, never an operator's."""
    seed_path = cfg_paths.etc() / "agents" / "hermes.toml"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(
        '[mcp.servers.operator-added]\nbuiltin = false\nenabled = true\n'
        '[mcp.servers.operator-added.tools]\nallow = []\ngated = []\nblocked = []\n'
    )
    _install("github", exposure=installed.ExposureConfig(hermes=True))
    hermes_join.sync_exposure()

    installed.patch_config("github", exposure=installed.ExposureConfig(hermes=False))
    hermes_join.sync_exposure()

    import tomllib

    data = tomllib.loads(seed_path.read_text())
    servers = data["mcp"]["servers"]
    assert "github" not in servers
    assert "operator-added" in servers


def test_stdio_records_excluded_from_desired_set(tmp_hal0_home: str) -> None:
    _install(
        "npmserver",
        transport="stdio",
        url="",
        spec="npm:some-mcp",
        exposure=installed.ExposureConfig(hermes=True),
    )
    entries = hermes_join._desired_entries("hermes")
    assert entries == {}


def test_headers_include_resolved_secret(tmp_hal0_home: str, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_MCP_TOKEN", "shh-secret-value")
    record = _install(
        "github",
        exposure=installed.ExposureConfig(hermes=True),
        secrets={"Authorization": "GITHUB_MCP_TOKEN"},
    )
    entries = hermes_join._desired_entries("hermes")
    assert entries["github"]["headers"]["Authorization"] == "shh-secret-value"
    assert record.secrets == {"Authorization": "GITHUB_MCP_TOKEN"}


def test_headers_omit_unresolved_secret(tmp_hal0_home: str) -> None:
    _install(
        "github",
        exposure=installed.ExposureConfig(hermes=True),
        secrets={"Authorization": "GITHUB_MCP_TOKEN_UNSET"},
    )
    entries = hermes_join._desired_entries("hermes")
    assert "Authorization" not in entries["github"]["headers"]
