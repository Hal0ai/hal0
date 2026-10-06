"""Tests for :mod:`hal0.mcp.hermes_join` — ADR-0015 §Decision 2.

Every test runs under ``tmp_hal0_home`` so real ``/etc/hal0``/``/var/lib/hal0``
are never touched (see the ``fix(mcp): make hermes_join HAL0_HOME-aware``
commit — the first cut of this module didn't thread paths through and
would have written to the real filesystem here).
"""

from __future__ import annotations

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
    assert (
        "github" in "".join(report["hermes"].get("errors", [])) or report["hermes"]["applied"] == 0
    )
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
        "[mcp.servers.operator-added]\nbuiltin = false\nenabled = true\n"
        '[mcp.servers.operator-added.tools]\nallow = ["hand_tool"]\n'
        "gated = []\nblocked = []\n"
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
        "[mcp.servers.operator-added]\nbuiltin = false\nenabled = true\n"
        "[mcp.servers.operator-added.tools]\nallow = []\ngated = []\nblocked = []\n"
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


def test_allow_insecure_http_renders_and_warns_on_every_render(
    tmp_hal0_home: str, monkeypatch
) -> None:
    """#2304: the explicit override is honoured, and never silently."""
    from structlog.testing import capture_logs

    monkeypatch.setenv("GITHUB_MCP_TOKEN", "shh-secret-value")
    _install(
        "github",
        url="http://192.0.2.10:8765/mcp",
        exposure=installed.ExposureConfig(hermes=True),
        secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"},
        allow_insecure_http=True,
    )
    for _ in range(2):
        with capture_logs() as logs:
            entries = hermes_join._desired_entries("hermes")
        assert entries["github"]["headers"]["AUTHORIZATION"] == "shh-secret-value"
        warnings = [e for e in logs if e["event"] == "hal0.mcp.hermes_join.insecure_http"]
        assert len(warnings) == 1
        assert warnings[0]["log_level"] == "warning"
        assert warnings[0]["server_id"] == "github"
        assert warnings[0]["host"] == "192.0.2.10"
        assert warnings[0]["header_keys"] == ["AUTHORIZATION"]


def test_https_record_renders_without_insecure_warning(tmp_hal0_home: str, monkeypatch) -> None:
    from structlog.testing import capture_logs

    monkeypatch.setenv("GITHUB_MCP_TOKEN", "shh-secret-value")
    _install(
        "github",
        exposure=installed.ExposureConfig(hermes=True),
        secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"},
    )
    with capture_logs() as logs:
        entries = hermes_join._desired_entries("hermes")
    assert entries["github"]["headers"]["AUTHORIZATION"] == "shh-secret-value"
    assert not [e for e in logs if e["event"] == "hal0.mcp.hermes_join.insecure_http"]


# ── #2304 review: Hermes's own client + upgrade reconciliation ──────────────


def test_entries_carrying_header_values_skip_hermes_preflight(tmp_hal0_home: str) -> None:
    """Hermes's preflight re-sends the headers with follow_redirects=True and no
    stripping at all; `skip_preflight` (honoured by the pinned Hermes) turns it off."""
    _install(
        "github",
        exposure=installed.ExposureConfig(hermes=True),
        secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"},
    )
    _install("envonly", exposure=installed.ExposureConfig(hermes=True), env={"X_API_KEY": "v"})
    _install("plain", exposure=installed.ExposureConfig(hermes=True), env={"X_API_KEY": ""})
    entries = hermes_join._desired_entries("hermes")
    assert entries["github"]["skip_preflight"] is True
    assert entries["envonly"]["skip_preflight"] is True
    assert "skip_preflight" not in entries["plain"]


def test_skip_preflight_reaches_hermes_config_and_brain_profile(
    tmp_hal0_home: str, monkeypatch
) -> None:
    import yaml

    from hal0.agents import hermes_provision

    hermes_bin = cfg_paths.var_lib() / "venvs" / "hermes" / "bin" / "hermes"
    hermes_bin.parent.mkdir(parents=True, exist_ok=True)
    hermes_bin.write_text("", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        hermes_provision.subprocess, "run", lambda argv, **kw: calls.append(list(argv))
    )
    brain_cfg = cfg_paths.var_lib() / ".hermes" / "profiles" / "hal0-brain" / "config.yaml"
    brain_cfg.parent.mkdir(parents=True, exist_ok=True)
    brain_cfg.write_text("mcp_servers: {}\n", encoding="utf-8")
    _install(
        "github",
        exposure=installed.ExposureConfig(hermes=True, brain=True),
        secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"},
    )

    hermes_join.sync_exposure()

    assert [str(hermes_bin), "config", "set", "mcp_servers.github.skip_preflight", "true"] in calls
    brain = yaml.safe_load(brain_cfg.read_text(encoding="utf-8"))
    assert brain["mcp_servers"]["github"]["skip_preflight"] is True


def _write_quarantined_record(server_id: str) -> None:
    """A record that loaded before #2304 and is now refused on load."""
    path = installed._registry_path(server_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'id = "{server_id}"\nname = "{server_id}"\nspec = "https://x.example.com/m.json"\n'
        'transport = "streamable-http"\nurl = "http://192.0.2.10:8765/mcp"\n'
        '[secrets]\nAUTHORIZATION = "GITHUB_MCP_TOKEN"\n'
        "[exposure]\nhermes = true\n",
        encoding="utf-8",
    )


def test_reconcile_stale_joins_evicts_quarantined_record(tmp_hal0_home: str) -> None:
    """Upgrade path: the join hal0 wrote earlier is removed at startup, not at
    the next unrelated MCP mutation."""
    import yaml

    hermes_cfg = cfg_paths.var_lib() / ".hermes" / "config.yaml"
    hermes_cfg.parent.mkdir(parents=True, exist_ok=True)
    hermes_cfg.write_text(
        yaml.safe_dump(
            {
                "mcp_servers": {
                    "github": {
                        "type": "http",
                        "url": "http://192.0.2.10:8765/mcp",
                        "headers": {"AUTHORIZATION": "shh-secret-value"},
                    },
                    "operator-added": {"type": "http", "url": "https://op.example.com/mcp"},
                }
            }
        ),
        encoding="utf-8",
    )
    hermes_join._write_manifest({"hermes": ["github"], "brain": []})
    _write_quarantined_record("github")
    assert installed.list_installed() == []

    assert hermes_join.reconcile_stale_joins() == ["github"]

    servers = yaml.safe_load(hermes_cfg.read_text(encoding="utf-8"))["mcp_servers"]
    assert "github" not in servers
    assert "operator-added" in servers
    assert hermes_join._load_manifest()["hermes"] == []
    # Converged: a second boot does nothing.
    assert hermes_join.reconcile_stale_joins() == []


def test_reconcile_stale_joins_is_a_noop_when_nothing_is_stale(
    tmp_hal0_home: str, monkeypatch
) -> None:
    _install("github", exposure=installed.ExposureConfig(hermes=True))
    hermes_join._write_manifest({"hermes": ["github"], "brain": []})

    def _no_sync(**kwargs: object) -> dict:
        raise AssertionError("sync_exposure must not run when nothing is stale")

    monkeypatch.setattr(hermes_join, "sync_exposure", _no_sync)
    assert hermes_join.reconcile_stale_joins() == []
