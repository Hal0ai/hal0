"""Tests for :mod:`hal0.mcp.hermes_join` — ADR-0015 §Decision 2.

Every test runs under ``tmp_hal0_home`` so real ``/etc/hal0``/``/var/lib/hal0``
are never touched (see the ``fix(mcp): make hermes_join HAL0_HOME-aware``
commit — the first cut of this module didn't thread paths through and
would have written to the real filesystem here).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from hal0.config import paths as cfg_paths
from hal0.config.schema import ToolPolicy
from hal0.mcp import hermes_join, installed


@pytest.fixture(autouse=True)
def _guard_lifted(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Most of this file covers the join's rendering, which the #2358 guard
    turns off for every user-installed record until #2303. Lift the guard
    here; the #2358 tests at the end of the file put it back."""
    monkeypatch.setattr(installed, "AGENT_CALL_PATH_ENFORCED", True)
    hermes_join._unenforced_logged.clear()
    yield
    hermes_join._unenforced_logged.clear()


@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped state: hal0 does not enforce policy on Hermes's call path."""
    monkeypatch.setattr(installed, "AGENT_CALL_PATH_ENFORCED", False)


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
        tool_policy=ToolPolicy(allow=["search", "list_issues"]),
    )
    hermes_join.sync_exposure()
    seed_path = cfg_paths.etc() / "agents" / "hermes.toml"
    assert seed_path.exists()
    import tomllib

    data = tomllib.loads(seed_path.read_text())
    github_entry = data["mcp"]["servers"]["github"]
    assert github_entry["builtin"] is False
    assert github_entry["tools"]["allow"] == ["search", "list_issues"]
    assert github_entry["tools"]["gated"] == []


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


def _fake_hermes(monkeypatch) -> tuple[str, list[list[str]]]:
    """A present hermes binary whose `config set` calls are recorded, not run."""
    from hal0.agents import hermes_provision

    hermes_bin = cfg_paths.var_lib() / "venvs" / "hermes" / "bin" / "hermes"
    hermes_bin.parent.mkdir(parents=True, exist_ok=True)
    hermes_bin.write_text("", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        hermes_provision.subprocess, "run", lambda argv, **kw: calls.append(list(argv))
    )
    return str(hermes_bin), calls


def _write_hermes_config(servers: dict) -> None:
    import yaml

    path = cfg_paths.var_lib() / ".hermes" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"mcp_servers": servers}), encoding="utf-8")


def test_failed_removal_keeps_ownership_so_the_next_run_retries(tmp_hal0_home: str) -> None:
    """A stale id is only forgotten once its entry is really gone from Hermes."""
    hermes_cfg = cfg_paths.var_lib() / ".hermes" / "config.yaml"
    hermes_cfg.parent.mkdir(parents=True, exist_ok=True)
    hermes_cfg.write_text("mcp_servers: [unclosed\n", encoding="utf-8")  # malformed YAML
    hermes_join._write_manifest({"hermes": ["github"], "brain": []})
    _write_quarantined_record("github")
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        assert hermes_join.reconcile_stale_joins() == ["github"]
    assert hermes_join._load_manifest()["hermes"] == ["github"]
    # The per-target failure is surfaced, naming the target and the reason.
    resync = [e for e in logs if e["event"] == "hal0.mcp.hermes_join.startup_resync"]
    assert len(resync) == 1
    assert any(err.startswith("hermes: ") and "remove" in err for err in resync[0]["errors"]), (
        resync[0]["errors"]
    )

    # Operator repairs the file; the next boot retries and now succeeds.
    _write_hermes_config(
        {"github": {"url": "http://192.0.2.10:8765/mcp", "headers": {"AUTHORIZATION": "s"}}}
    )
    assert hermes_join.reconcile_stale_joins() == ["github"]
    import yaml

    assert "github" not in (yaml.safe_load(hermes_cfg.read_text(encoding="utf-8"))["mcp_servers"])
    assert hermes_join._load_manifest()["hermes"] == []


def test_reconcile_resyncs_existing_join_missing_skip_preflight(
    tmp_hal0_home: str, monkeypatch
) -> None:
    """Upgrade path: an owned, still-desired join written before skip_preflight
    existed is re-applied at startup, not at the next unrelated mutation."""
    hermes_bin, calls = _fake_hermes(monkeypatch)
    _install(
        "github",
        exposure=installed.ExposureConfig(hermes=True),
        secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"},
    )
    _write_hermes_config({"github": {"type": "http", "url": "https://github.example.com/mcp"}})
    hermes_join._write_manifest({"hermes": ["github"], "brain": []})

    assert hermes_join.reconcile_stale_joins() == ["github"]
    assert [hermes_bin, "config", "set", "mcp_servers.github.skip_preflight", "true"] in calls


def test_reconcile_is_a_noop_when_persisted_entries_match(tmp_hal0_home: str, monkeypatch) -> None:
    _install(
        "github",
        exposure=installed.ExposureConfig(hermes=True),
        secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"},
    )
    _write_hermes_config(
        {
            "github": {
                "type": "http",
                "url": "https://github.example.com/mcp",
                # GITHUB_MCP_TOKEN is unset here, so only the agent tag renders.
                "headers": {"X-hal0-Agent": "hermes"},
                "skip_preflight": True,
            }
        }
    )
    hermes_join._write_manifest({"hermes": ["github"], "brain": []})

    def _no_sync(**kwargs: object) -> dict:
        raise AssertionError("sync_exposure must not run on a converged box")

    monkeypatch.setattr(hermes_join, "sync_exposure", _no_sync)
    assert hermes_join.reconcile_stale_joins() == []


def test_reconcile_recreates_entries_when_main_config_was_deleted(
    tmp_hal0_home: str, monkeypatch
) -> None:
    """Hermes installed, manifest matches, but config.yaml is gone: re-sync."""
    hermes_bin, calls = _fake_hermes(monkeypatch)
    _install("github", exposure=installed.ExposureConfig(hermes=True))
    hermes_join._write_manifest({"hermes": ["github"], "brain": []})
    assert not (cfg_paths.var_lib() / ".hermes" / "config.yaml").exists()

    assert hermes_join.reconcile_stale_joins() == ["github"]
    assert [
        hermes_bin,
        "config",
        "set",
        "mcp_servers.github.url",
        "https://github.example.com/mcp",
    ] in calls


def test_reconcile_writes_a_desired_header_key_missing_on_disk(
    tmp_hal0_home: str, monkeypatch
) -> None:
    """A secret unresolved at the previous sync and set since: startup writes it."""
    monkeypatch.setenv("GITHUB_MCP_TOKEN", "shh-secret-value")
    hermes_bin, calls = _fake_hermes(monkeypatch)
    _install(
        "github",
        exposure=installed.ExposureConfig(hermes=True),
        secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"},
    )
    _write_hermes_config(
        {
            "github": {
                "url": "https://github.example.com/mcp",
                "headers": {"X-hal0-Agent": "hermes"},
                "skip_preflight": True,
            }
        }
    )
    hermes_join._write_manifest({"hermes": ["github"], "brain": []})

    assert hermes_join.reconcile_stale_joins() == ["github"]
    assert [
        hermes_bin,
        "config",
        "set",
        "mcp_servers.github.headers.AUTHORIZATION",
        "shh-secret-value",
    ] in calls


def test_malformed_main_config_is_logged_not_drift(tmp_hal0_home: str, monkeypatch) -> None:
    """Unreadable is not 'differs' (no sync), but it is surfaced — without
    PyYAML's snippet of the offending line, which can hold a header value."""
    from structlog.testing import capture_logs

    _fake_hermes(monkeypatch)
    _install("github", exposure=installed.ExposureConfig(hermes=True))
    hermes_cfg = cfg_paths.var_lib() / ".hermes" / "config.yaml"
    hermes_cfg.parent.mkdir(parents=True, exist_ok=True)
    hermes_cfg.write_text(
        "mcp_servers:\n  github:\n    headers: {AUTHORIZATION: SUPERSECRET-VALUE\n",
        encoding="utf-8",
    )
    hermes_join._write_manifest({"hermes": ["github"], "brain": []})

    def _no_sync(**kwargs: object) -> dict:
        raise AssertionError("an unreadable config must not count as drift")

    monkeypatch.setattr(hermes_join, "sync_exposure", _no_sync)
    with capture_logs() as logs:
        assert hermes_join.reconcile_stale_joins() == []
    unreadable = [
        e for e in logs if e["event"] == "hal0.mcp.hermes_join.persisted_config_unreadable"
    ]
    assert len(unreadable) == 1
    assert unreadable[0]["target"] == "hermes"
    assert "line" in unreadable[0]["error"]
    assert "SUPERSECRET-VALUE" not in repr(logs)


def test_skip_preflight_cleared_when_header_values_removed(tmp_hal0_home: str, monkeypatch) -> None:
    import yaml

    hermes_bin, calls = _fake_hermes(monkeypatch)
    brain_cfg = cfg_paths.var_lib() / ".hermes" / "profiles" / "hal0-brain" / "config.yaml"
    brain_cfg.parent.mkdir(parents=True, exist_ok=True)
    brain_cfg.write_text("mcp_servers: {}\n", encoding="utf-8")
    _install(
        "github",
        exposure=installed.ExposureConfig(hermes=True, brain=True),
        secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"},
    )
    hermes_join.sync_exposure()
    brain = yaml.safe_load(brain_cfg.read_text(encoding="utf-8"))
    assert brain["mcp_servers"]["github"]["skip_preflight"] is True

    installed.patch_config("github", secrets={})
    calls.clear()
    hermes_join.sync_exposure()

    assert [hermes_bin, "config", "set", "mcp_servers.github.skip_preflight", "false"] in calls
    brain = yaml.safe_load(brain_cfg.read_text(encoding="utf-8"))
    assert brain["mcp_servers"]["github"].get("skip_preflight", False) is False


def test_removed_header_key_is_pruned_from_hermes_config(tmp_hal0_home: str, monkeypatch) -> None:
    """#2332: `hermes config set` only adds keys, so a removed credential's header
    would stay in config.yaml (and, with skip_preflight cleared, reach the
    redirect-following preflight). The writer prunes it."""
    import yaml

    monkeypatch.setenv("GITHUB_MCP_TOKEN", "shh-secret-value")
    hermes_bin, calls = _fake_hermes(monkeypatch)
    _install(
        "github",
        exposure=installed.ExposureConfig(hermes=True),
        secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"},
        env={"X_API_KEY": "literal-value"},
    )
    hermes_cfg = cfg_paths.var_lib() / ".hermes" / "config.yaml"
    # What the first sync left on disk (the fake binary does not write it).
    _write_hermes_config(
        {
            "github": {
                "type": "http",
                "url": "https://github.example.com/mcp",
                "headers": {
                    "X-hal0-Agent": "hermes",
                    "AUTHORIZATION": "shh-secret-value",
                    "X_API_KEY": "literal-value",
                },
                "skip_preflight": True,
            },
            "operator-added": {"url": "https://op.example.com/mcp", "headers": {"K": "v"}},
        }
    )

    installed.patch_config("github", secrets={})
    hermes_join.sync_exposure()

    servers = yaml.safe_load(hermes_cfg.read_text(encoding="utf-8"))["mcp_servers"]
    assert "AUTHORIZATION" not in servers["github"]["headers"]
    assert servers["github"]["headers"]["X_API_KEY"] == "literal-value"
    assert servers["operator-added"]["headers"] == {"K": "v"}
    assert [hermes_bin, "config", "set", "mcp_servers.github.skip_preflight", "true"] in calls

    installed.patch_config("github", env={})
    calls.clear()
    hermes_join.sync_exposure()

    servers = yaml.safe_load(hermes_cfg.read_text(encoding="utf-8"))["mcp_servers"]
    assert set(servers["github"]["headers"]) <= {"X-hal0-Agent"}
    assert [hermes_bin, "config", "set", "mcp_servers.github.skip_preflight", "false"] in calls


def test_reconcile_resyncs_when_disk_has_stale_header_keys(tmp_hal0_home: str, monkeypatch) -> None:
    """Startup also catches a header key on disk that the registry no longer renders."""
    _fake_hermes(monkeypatch)
    _install("github", exposure=installed.ExposureConfig(hermes=True))
    _write_hermes_config(
        {
            "github": {
                "url": "https://github.example.com/mcp",
                "headers": {"X-hal0-Agent": "hermes", "AUTHORIZATION": "old-value"},
                "skip_preflight": False,
            }
        }
    )
    hermes_join._write_manifest({"hermes": ["github"], "brain": []})
    assert hermes_join.reconcile_stale_joins() == ["github"]


# --- #2331: the pinned Hermes picks SSE by `transport`, not `type` ----------
# (`tools/mcp_tool.py:2383` at the VETTED_HERMES_REFS commit:
# `if config.get("transport") == "sse":`; its status reader defaults the key
# to "http", `tools/mcp_tool.py:5084`.)


def test_desired_entries_carry_the_transport_key_hermes_reads(tmp_hal0_home: str) -> None:
    _install("feed", transport="sse", exposure=installed.ExposureConfig(hermes=True))
    _install("github", exposure=installed.ExposureConfig(hermes=True))
    entries = hermes_join._desired_entries("hermes")
    assert entries["feed"]["transport"] == "sse"
    assert entries["github"]["transport"] == "http"


def test_sse_transport_reaches_hermes_config_and_brain_profile(
    tmp_hal0_home: str, monkeypatch
) -> None:
    import yaml

    hermes_bin, calls = _fake_hermes(monkeypatch)
    brain_cfg = cfg_paths.var_lib() / ".hermes" / "profiles" / "hal0-brain" / "config.yaml"
    brain_cfg.parent.mkdir(parents=True, exist_ok=True)
    brain_cfg.write_text("mcp_servers: {}\n", encoding="utf-8")
    _install("feed", transport="sse", exposure=installed.ExposureConfig(hermes=True, brain=True))

    hermes_join.sync_exposure()

    assert [hermes_bin, "config", "set", "mcp_servers.feed.transport", "sse"] in calls
    brain = yaml.safe_load(brain_cfg.read_text(encoding="utf-8"))
    assert brain["mcp_servers"]["feed"]["transport"] == "sse"


def test_reconcile_resyncs_an_sse_join_written_without_transport(
    tmp_hal0_home: str, monkeypatch
) -> None:
    """Upgrade path: an SSE join written as `type: sse` alone is rewritten at boot."""
    hermes_bin, calls = _fake_hermes(monkeypatch)
    _install("feed", transport="sse", exposure=installed.ExposureConfig(hermes=True))
    _write_hermes_config(
        {
            "feed": {
                "type": "sse",
                "url": "https://feed.example.com/mcp",
                "headers": {"X-hal0-Agent": "hermes"},
            }
        }
    )
    hermes_join._write_manifest({"hermes": ["feed"], "brain": []})

    assert hermes_join.reconcile_stale_joins() == ["feed"]
    assert [hermes_bin, "config", "set", "mcp_servers.feed.transport", "sse"] in calls


def test_reconcile_resyncs_a_brain_sse_join_written_without_transport(
    tmp_hal0_home: str, monkeypatch
) -> None:
    import yaml

    _fake_hermes(monkeypatch)
    brain_cfg = cfg_paths.var_lib() / ".hermes" / "profiles" / "hal0-brain" / "config.yaml"
    brain_cfg.parent.mkdir(parents=True, exist_ok=True)
    brain_cfg.write_text(
        yaml.safe_dump(
            {
                "mcp_servers": {
                    "feed": {
                        "type": "sse",
                        "url": "https://feed.example.com/mcp",
                        "headers": {"X-hal0-Agent": "hermes"},
                        "timeout": 60,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    _install("feed", transport="sse", exposure=installed.ExposureConfig(brain=True))
    hermes_join._write_manifest({"hermes": [], "brain": ["feed"]})

    assert hermes_join.reconcile_stale_joins() == ["feed"]
    brain = yaml.safe_load(brain_cfg.read_text(encoding="utf-8"))
    assert brain["mcp_servers"]["feed"]["transport"] == "sse"
    # Converged: the next boot does nothing.
    assert hermes_join.reconcile_stale_joins() == []


def test_reconcile_treats_a_missing_transport_as_http(tmp_hal0_home: str, monkeypatch) -> None:
    """Hermes defaults an absent `transport` to HTTP, so an http join written
    before #2331 is already correct and must not force a boot-time resync."""
    _install("github", exposure=installed.ExposureConfig(hermes=True))
    _write_hermes_config(
        {
            "github": {
                "type": "http",
                "url": "https://github.example.com/mcp",
                "headers": {"X-hal0-Agent": "hermes"},
            }
        }
    )
    hermes_join._write_manifest({"hermes": ["github"], "brain": []})

    def _no_sync(**kwargs: object) -> dict:
        raise AssertionError("sync_exposure must not run on a converged box")

    monkeypatch.setattr(hermes_join, "sync_exposure", _no_sync)
    assert hermes_join.reconcile_stale_joins() == []


# --- #2358: no user-installed record is joined until #2303 -----------------
# Hermes calls the upstream URL itself (`_desired_entries` hands it url +
# headers), and a [tools] policy has no wildcard: an unlisted tool is denied
# (`AgentMCPClient.classify` -> `unknown_tool`) yet reachable on that path.
# So every user-installed record is skipped, whatever its policy.


@pytest.mark.usefixtures("guarded")
def test_desired_entries_skip_every_user_installed_record_and_log_once(
    tmp_hal0_home: str,
) -> None:
    from structlog.testing import capture_logs

    both = installed.ExposureConfig(hermes=True, brain=True)
    _install("empty", exposure=both)
    _install("allowonly", exposure=both, tool_policy=ToolPolicy(allow=["search"]))
    _install("gated", exposure=both, tool_policy=ToolPolicy(gated=["create_pr"]))
    _install("hermesonly", exposure=installed.ExposureConfig(hermes=True))

    with capture_logs() as logs:
        for _ in range(2):
            for target in hermes_join.JOIN_TARGETS:
                assert hermes_join._desired_entries(target) == {}, target
    skipped = [e for e in logs if e["event"] == "hal0.mcp.hermes_join.policy_unenforced"]
    # Once per record, not once per target or per render.
    assert sorted(e["server_id"] for e in skipped) == [
        "allowonly",
        "empty",
        "gated",
        "hermesonly",
    ]
    by_id = {e["server_id"]: e for e in skipped}
    assert by_id["empty"]["targets"] == ["hermes", "brain"]
    assert by_id["hermesonly"]["targets"] == ["hermes"]
    assert by_id["empty"]["code"] == "mcp.exposure_policy_unenforced"
    assert "#2303" in by_id["empty"]["reason"]
    assert by_id["empty"]["log_level"] == "warning"


def _write_seed_toml(servers: dict) -> None:
    from hal0.config.loader import write_toml_atomic

    path = cfg_paths.etc() / "agents" / "hermes.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_toml_atomic(path, {"mcp": {"servers": servers}}, mode=0o600)


_BUILTIN_ENTRIES = {
    "hal0-admin": {"type": "http", "url": "http://127.0.0.1:8080/mcp/admin/mcp"},
    "hal0-memory": {"type": "http", "url": "http://127.0.0.1:8080/mcp/memory/mcp"},
}


@pytest.mark.usefixtures("guarded")
def test_reconcile_removes_an_existing_join_and_keeps_the_builtins(
    tmp_hal0_home: str, monkeypatch
) -> None:
    """Upgrade path: an allow-only record joined before #2358 leaves Hermes's
    config, the brain profile, the manifest and the seed mirror at boot. The
    built-in servers, served through hal0's own /mcp mount, stay joined."""
    import tomllib

    import yaml

    _fake_hermes(monkeypatch)
    both = installed.ExposureConfig(hermes=True, brain=True)
    _install("github", exposure=both, tool_policy=ToolPolicy(allow=["search"]))
    joined = {"github": {"url": "https://github.example.com/mcp"}}
    _write_hermes_config(
        {**_BUILTIN_ENTRIES, **joined, "operator-added": {"url": "https://op.example.com/mcp"}}
    )
    brain_cfg = cfg_paths.var_lib() / ".hermes" / "profiles" / "hal0-brain" / "config.yaml"
    brain_cfg.parent.mkdir(parents=True, exist_ok=True)
    brain_cfg.write_text(yaml.safe_dump({"mcp_servers": {**_BUILTIN_ENTRIES, **joined}}))
    _write_seed_toml(
        {
            "hal0-admin": {"builtin": True, "enabled": True},
            "hal0-memory": {"builtin": True, "enabled": True},
            "github": {"builtin": False, "enabled": True, "tools": {"allow": ["search"]}},
        }
    )
    hermes_join._write_manifest({"hermes": ["github"], "brain": ["github"]})

    assert hermes_join.reconcile_stale_joins() == ["github"]

    assert hermes_join._load_manifest() == {"hermes": [], "brain": []}
    hermes_cfg = cfg_paths.var_lib() / ".hermes" / "config.yaml"
    servers = yaml.safe_load(hermes_cfg.read_text(encoding="utf-8"))["mcp_servers"]
    assert set(servers) == {"hal0-admin", "hal0-memory", "operator-added"}
    brain = yaml.safe_load(brain_cfg.read_text(encoding="utf-8"))["mcp_servers"]
    assert set(brain) == {"hal0-admin", "hal0-memory"}
    seed = tomllib.loads((cfg_paths.etc() / "agents" / "hermes.toml").read_text())
    assert set(seed["mcp"]["servers"]) == {"hal0-admin", "hal0-memory"}
    # Converged: the next boot does no work.
    assert hermes_join.reconcile_stale_joins() == []


@pytest.mark.usefixtures("guarded")
def test_builtin_servers_are_still_joined(tmp_hal0_home: str, monkeypatch) -> None:
    """The guard covers installed records only; Hermes's built-in entries
    come from hermes_provision, which never reads it."""
    from hal0.agents import hermes_provision

    names = {s["name"] for s in hermes_provision._default_mcp_servers()}
    assert {"hal0-admin", "hal0-memory"} <= names
    assert set(hermes_provision._builtin_mcp_seed_servers()) == {"hal0-admin", "hal0-memory"}

    _fake_hermes(monkeypatch)
    _write_hermes_config(dict(_BUILTIN_ENTRIES))
    report = hermes_join.sync_exposure()
    assert report["errors"] == []
    import yaml

    hermes_cfg = cfg_paths.var_lib() / ".hermes" / "config.yaml"
    servers = yaml.safe_load(hermes_cfg.read_text(encoding="utf-8"))["mcp_servers"]
    assert set(servers) == {"hal0-admin", "hal0-memory"}


def test_join_renders_again_once_the_call_path_enforces_policy(tmp_hal0_home: str) -> None:
    """With the guard lifted (#2303), an exposed record of any policy joins."""
    _install(
        "github",
        exposure=installed.ExposureConfig(hermes=True),
        tool_policy=ToolPolicy(allow=["search"], gated=["create_pr"], blocked=["delete"]),
    )
    assert set(hermes_join._desired_entries("hermes")) == {"github"}


# ── #2330: NO_PROXY in the Hermes driver env follows the exposed records ────


def _sandboxed_driver_env(monkeypatch) -> Path:
    """Point the provisioner's driver env at this sandbox and create it, as an
    installed Hermes would have it. Writes go direct (euid 0), never via sudo."""
    from hal0.agents import hermes_provision

    path = cfg_paths.etc() / "agents" / "hermes.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("HAL0_API_URL=http://127.0.0.1:8080\n")
    monkeypatch.setattr(hermes_provision, "DRIVER_ENV_PATH", path)
    monkeypatch.setattr(hermes_provision.os, "geteuid", lambda: 0)
    for name in ("NO_PROXY", "no_proxy", "HAL0_ADMIN_KEY", "HAL0_CLIENT_KEY"):
        monkeypatch.delenv(name, raising=False)
    return path


def _no_proxy(path: Path) -> list[str]:
    for line in path.read_text().splitlines():
        if line.startswith("NO_PROXY="):
            return line.split("=", 1)[1].split(",")
    raise AssertionError("no NO_PROXY line")


def test_exposing_a_loopback_server_adds_its_host_to_hermes_no_proxy(
    tmp_hal0_home: str, monkeypatch
) -> None:
    path = _sandboxed_driver_env(monkeypatch)
    _install("local", url="http://127.0.0.2:9000/mcp")
    installed.patch_config("local", exposure=installed.ExposureConfig(hermes=True))

    report = hermes_join.sync_exposure()

    assert report["driver_env"] == {"no_proxy_hosts": ["127.0.0.2"], "refreshed": True}
    assert "127.0.0.2" in _no_proxy(path)
    assert "no_proxy=" + ",".join(_no_proxy(path)) in path.read_text()


def test_driver_env_is_rewritten_only_when_the_loopback_hosts_change(
    tmp_hal0_home: str, monkeypatch
) -> None:
    from hal0.agents import hermes_provision

    _sandboxed_driver_env(monkeypatch)
    _install(
        "local", url="http://127.0.0.2:9000/mcp", exposure=installed.ExposureConfig(hermes=True)
    )
    hermes_join.sync_exposure()

    calls: list[int] = []
    real = hermes_provision.refresh_driver_env
    monkeypatch.setattr(
        hermes_provision, "refresh_driver_env", lambda **kw: (calls.append(1), real(**kw))[1]
    )
    _install("remote", exposure=installed.ExposureConfig(hermes=True))
    assert hermes_join.sync_exposure()["driver_env"]["refreshed"] is False
    assert calls == []

    _install("local2", url="http://[::1]:9001/mcp", exposure=installed.ExposureConfig(brain=True))
    _install(
        "local3", url="http://127.0.0.3:9001/mcp", exposure=installed.ExposureConfig(brain=True)
    )
    assert hermes_join.sync_exposure()["driver_env"] == {
        "no_proxy_hosts": ["127.0.0.2", "127.0.0.3", "::1"],
        "refreshed": True,
    }
    assert calls == [1]


def test_sync_never_creates_a_driver_env_for_an_uninstalled_hermes(
    tmp_hal0_home: str, monkeypatch
) -> None:
    path = _sandboxed_driver_env(monkeypatch)
    path.unlink()
    _install(
        "local", url="http://127.0.0.2:9000/mcp", exposure=installed.ExposureConfig(hermes=True)
    )

    report = hermes_join.sync_exposure()

    assert report["driver_env"]["refreshed"] is False
    assert not path.exists()


def test_sync_leaves_a_driver_env_outside_the_hal0_home_alone(
    tmp_hal0_home: str, monkeypatch
) -> None:
    """Under HAL0_HOME the provisioner still targets /etc/hal0 — never write it."""
    from hal0.agents import hermes_provision

    monkeypatch.setattr(
        hermes_provision,
        "refresh_driver_env",
        lambda **kw: (_ for _ in ()).throw(AssertionError("must not refresh")),
    )
    _install(
        "local", url="http://127.0.0.2:9000/mcp", exposure=installed.ExposureConfig(hermes=True)
    )
    assert hermes_join.sync_exposure()["driver_env"]["refreshed"] is False


def test_failed_driver_env_refresh_is_reported_and_retried(tmp_hal0_home: str, monkeypatch) -> None:
    from hal0.agents import hermes_provision

    path = _sandboxed_driver_env(monkeypatch)
    _install(
        "local", url="http://127.0.0.2:9000/mcp", exposure=installed.ExposureConfig(hermes=True)
    )

    def boom(**kw: object) -> None:
        raise RuntimeError("seam refused")

    real = hermes_provision.refresh_driver_env
    monkeypatch.setattr(hermes_provision, "refresh_driver_env", boom)
    report = hermes_join.sync_exposure()
    assert report["driver_env"]["refreshed"] is False
    assert any("seam refused" in err for err in report["errors"])

    monkeypatch.setattr(hermes_provision, "refresh_driver_env", real)
    assert hermes_join.sync_exposure()["driver_env"]["refreshed"] is True
    assert "127.0.0.2" in _no_proxy(path)


def test_driver_env_writes_the_hosts_the_join_records(tmp_hal0_home: str, monkeypatch) -> None:
    """The write uses the host list the join looked up and records: a second
    lookup inside the writer that failed would write an incomplete NO_PROXY
    while the join recorded the full set as done."""
    path = _sandboxed_driver_env(monkeypatch)
    _install(
        "local", url="http://127.0.0.2:9000/mcp", exposure=installed.ExposureConfig(hermes=True)
    )
    real_lookup = installed.exposed_loopback_hosts
    lookups: list[int] = []

    def flaky() -> list[str]:
        lookups.append(1)
        if len(lookups) > 1:
            raise RuntimeError("registry read failed")
        return real_lookup()

    monkeypatch.setattr(installed, "exposed_loopback_hosts", flaky)
    monkeypatch.setattr(hermes_join, "exposed_loopback_hosts", flaky)

    report = hermes_join.sync_exposure()

    assert report["driver_env"]["refreshed"] is True
    assert "127.0.0.2" in _no_proxy(path)


def test_reconcile_adds_loopback_hosts_to_an_upgraded_boxs_driver_env(
    tmp_hal0_home: str, monkeypatch
) -> None:
    """A box upgraded with a loopback server already exposed: the joins are
    converged, but the driver env predates #2330. Startup fixes it."""
    path = _sandboxed_driver_env(monkeypatch)
    _install(
        "local", url="http://127.0.0.2:9000/mcp", exposure=installed.ExposureConfig(hermes=True)
    )
    manifest = cfg_paths.var_lib() / "mcp" / "hermes-managed.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"hermes": ["local"], "brain": []}')

    assert hermes_join.reconcile_stale_joins() == []  # joins already converged

    assert "127.0.0.2" in _no_proxy(path)
