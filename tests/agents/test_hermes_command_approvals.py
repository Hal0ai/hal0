"""Hermes command approvals: setting validation, read, precedence and apply path.

The setting is ``[hermes].command_approvals`` in the root-owned seed TOML
(``hp.INSTALL_SEED_PATH``); provisioning renders it to Hermes's
``approvals.mode``, and an ``approvals.mode`` in overrides.yaml wins.
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from hal0.agents import hermes_command_approvals as cmd
from hal0.agents import hermes_provision as hp
from hal0.config.schema import HermesAgentSettings
from hal0.errors import Hal0Error
from tests.agents._hermes_fakes import fake_hermes_run

SEED = """\
schema_version = 1
data_dir = "/var/lib/hal0/agents/hermes"

[agent]
name = "hermes"

[mcp.servers.hal0-admin]
builtin = true
"""


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Point every path at tmp_path and make the seed write a plain file write."""
    seed = tmp_path / "etc" / "hal0" / "agents" / "hermes.toml"
    seed.parent.mkdir(parents=True)
    seed.write_text(SEED, encoding="utf-8")
    overrides = tmp_path / "etc" / "hal0" / "agents" / "hermes" / "overrides.yaml"
    home = tmp_path / "hermes-home"
    home.mkdir()
    config = home / "config.yaml"
    config.write_text("approvals:\n  mode: smart\nmodel:\n  default: hal0/agent\n")
    monkeypatch.setattr(hp, "INSTALL_SEED_PATH", seed)
    monkeypatch.setattr(hp, "OVERRIDES_PATH", overrides)
    monkeypatch.setattr(hp, "HERMES_HOME_DEFAULT", home)

    writes: list[str] = []

    def _write(body: str) -> None:
        writes.append(body)
        seed.write_text(body, encoding="utf-8")

    monkeypatch.setattr(cmd, "_write_seed", _write)

    def _config_set(mode: str) -> list[str]:
        # The real mechanism (hermes config set) against the fake hermes CLI.
        _n, errors = hp._apply_config_set(
            [(cmd.HERMES_CONFIG_KEY, mode)],
            hermes_bin=Path("/fake/hermes"),
            hermes_home=home,
            run=fake_hermes_run(),
        )
        return errors

    monkeypatch.setattr(cmd, "_config_set", _config_set)
    return {"seed": seed, "overrides": overrides, "config": config, "writes": writes}


class _Unit:
    def __init__(
        self,
        active: bool | None = True,
        fail: bool = False,
        fail_code: str = "agent.restart_failed",
    ) -> None:
        self.active = active
        self.fail = fail
        self.fail_code = fail_code
        self.restarts = 0

    async def unit_active(self) -> bool | None:
        return self.active

    async def restart(self) -> dict[str, Any]:
        self.restarts += 1
        if self.fail:
            raise Hal0Error("systemctl restart failed", code=self.fail_code)
        return {"status": "restarted"}


def _apply(mode: Any, unit: _Unit) -> dict[str, Any]:
    return asyncio.run(
        cmd.apply_mode(
            mode, actor="hal0-dashboard", unit_active=unit.unit_active, restart=unit.restart
        )
    )


def _stored(seed: Path) -> Any:
    return tomllib.loads(seed.read_text()).get("hermes", {}).get("command_approvals")


def _rendered(config: Path) -> Any:
    return yaml.safe_load(config.read_text())["approvals"]["mode"]


# ── validation ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["smart", "manual"])
def test_schema_accepts_exactly_smart_and_manual(mode: str) -> None:
    assert HermesAgentSettings(command_approvals=mode).command_approvals == mode
    assert cmd.validate_mode(mode) == mode


@pytest.mark.parametrize("mode", ["off", "SMART", "", " manual", None, 1, True])
def test_schema_rejects_anything_else(mode: Any) -> None:
    with pytest.raises(ValidationError):
        HermesAgentSettings(command_approvals=mode)
    with pytest.raises(cmd.CommandApprovalsInvalid):
        cmd.validate_mode(mode)


def test_schema_default_is_the_provisioning_constant() -> None:
    assert HermesAgentSettings().command_approvals == hp.HAL0_APPROVALS_MODE == "smart"
    assert cmd.default_mode() == "smart"


def test_schema_forbids_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        HermesAgentSettings(command_approvals="smart", approvals_mode="off")


# ── read ─────────────────────────────────────────────────────────────────────


def test_read_setting_absent_is_default(box: dict[str, Any]) -> None:
    got = cmd.read_setting()
    assert (got.mode, got.stored, got.invalid_value) == ("smart", False, None)


def test_read_setting_missing_file_is_default(box: dict[str, Any]) -> None:
    box["seed"].unlink()
    assert cmd.read_setting().mode == "smart"


def test_read_setting_stored(box: dict[str, Any]) -> None:
    box["seed"].write_text(SEED + '\n[hermes]\ncommand_approvals = "manual"\n')
    got = cmd.read_setting()
    assert (got.mode, got.stored) == ("manual", True)


def test_read_setting_invalid_falls_back_and_reports(box: dict[str, Any]) -> None:
    box["seed"].write_text(SEED + '\n[hermes]\ncommand_approvals = "off"\n')
    got = cmd.read_setting()
    assert (got.mode, got.stored, got.invalid_value) == ("smart", False, "off")
    assert cmd.status()["invalid_value"] == "off"


def test_read_setting_corrupt_seed_is_default_and_flagged(box: dict[str, Any]) -> None:
    box["seed"].write_text(SEED + '\n[hermes]\ncommand_approvals = "manual"\n= = broken')
    got = cmd.read_setting()
    assert (got.mode, got.stored, got.unreadable) == ("smart", False, True)
    st = cmd.status()
    # Not presented as a clean "smart (default)" over the operator's manual.
    assert st["seed_unreadable"] is True and st["seed_path"] == str(box["seed"])


def test_read_setting_absent_file_is_not_unreadable(box: dict[str, Any]) -> None:
    box["seed"].unlink()
    assert cmd.read_setting().unreadable is False


def test_override_bare_off_reads_as_off(box: dict[str, Any]) -> None:
    box["overrides"].parent.mkdir(parents=True)
    box["overrides"].write_text("approvals:\n  mode: off\n")  # YAML 1.1: False
    assert cmd.read_override() == "off"


def test_status_sources(box: dict[str, Any]) -> None:
    st = cmd.status()
    assert (st["mode"], st["effective"], st["source"], st["in_sync"]) == (
        "smart",
        "smart",
        "default",
        True,
    )

    box["seed"].write_text(SEED + '\n[hermes]\ncommand_approvals = "manual"\n')
    st = cmd.status()
    assert (st["mode"], st["effective"], st["source"]) == ("manual", "manual", "setting")
    # config.yaml still says smart: drift is reported, not hidden.
    assert (st["rendered"], st["in_sync"]) == ("smart", False)

    box["overrides"].parent.mkdir(parents=True)
    box["overrides"].write_text("approvals:\n  mode: smart\n")
    st = cmd.status()
    assert (st["mode"], st["effective"], st["source"]) == ("manual", "smart", "overrides")
    assert st["overrides_path"] == str(box["overrides"])


def test_status_not_provisioned(box: dict[str, Any]) -> None:
    box["config"].unlink()
    st = cmd.status()
    assert st["rendered"] is None and st["in_sync"] is None


# ── apply ────────────────────────────────────────────────────────────────────


def test_apply_persists_renders_and_restarts(box: dict[str, Any]) -> None:
    unit = _Unit(active=True)
    out = _apply("manual", unit)
    assert _stored(box["seed"]) == "manual"
    assert _rendered(box["config"]) == "manual"
    assert unit.restarts == 1
    assert out["restart"] == "restarted" and out["changed"] is True
    assert (out["mode"], out["effective"], out["source"], out["in_sync"]) == (
        "manual",
        "manual",
        "setting",
        True,
    )
    # The rest of the seed (the MCP allow-list) survives the rewrite.
    seed = tomllib.loads(box["seed"].read_text())
    assert seed["mcp"]["servers"]["hal0-admin"]["builtin"] is True
    assert seed["agent"]["name"] == "hermes"


def test_apply_inactive_unit_is_not_started(box: dict[str, Any]) -> None:
    unit = _Unit(active=False)
    out = _apply("manual", unit)
    assert unit.restarts == 0
    assert out["restart"] == "skipped-inactive"
    assert "not running" in out["message"]


def test_apply_unknown_liveness_is_not_reported_as_stopped(box: dict[str, Any]) -> None:
    """unit_is_active() -> None (no systemd, probe timeout) is UNKNOWN, not inactive."""
    unit = _Unit(active=None)
    out = _apply("manual", unit)
    assert unit.restarts == 0
    assert out["restart"] == "unknown"
    assert "could not tell whether Hermes is running" in out["message"]
    assert "not running" not in out["message"]
    # The new mode is saved and rendered; only the restart is outstanding.
    assert _stored(box["seed"]) == "manual" and _rendered(box["config"]) == "manual"


def test_apply_restart_timeout_keeps_the_new_state(box: dict[str, Any]) -> None:
    """The unit may still be starting on the new config: never roll the files back."""
    unit = _Unit(active=True, fail=True, fail_code="agent.restart_timeout")
    out = _apply("manual", unit)
    assert out["restart"] == "timeout"
    assert "unknown" in out["message"]
    assert _stored(box["seed"]) == "manual"
    assert _rendered(box["config"]) == "manual"
    assert (out["mode"], out["in_sync"]) == ("manual", True)


def test_apply_re_merges_overrides_after_config_set(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An override that lands mid-apply must still win, as at provisioning."""
    real_set = cmd._config_set

    def _set_then_override(mode: str) -> list[str]:
        box["overrides"].parent.mkdir(parents=True, exist_ok=True)
        box["overrides"].write_text("approvals:\n  mode: smart\n")
        return real_set(mode)

    monkeypatch.setattr(cmd, "_config_set", _set_then_override)
    out = _apply("manual", _Unit())
    assert _rendered(box["config"]) == "smart"
    assert (out["mode"], out["effective"], out["source"], out["in_sync"]) == (
        "manual",
        "smart",
        "overrides",
        True,
    )


def test_apply_overrides_re_merge_failure_rolls_back(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _broken_merge() -> None:
        raise ValueError("overrides.yaml: mapping values are not allowed here")

    monkeypatch.setattr(cmd, "_merge_overrides", _broken_merge)
    seed_before, config_before = box["seed"].read_text(), box["config"].read_text()
    unit = _Unit()
    with pytest.raises(cmd.CommandApprovalsApplyFailed, match="overrides re-merge"):
        _apply("manual", unit)
    assert box["seed"].read_text() == seed_before
    assert box["config"].read_text() == config_before
    assert unit.restarts == 0


def test_apply_same_value_is_a_noop(box: dict[str, Any]) -> None:
    _apply("manual", _Unit())
    unit = _Unit()
    box["writes"].clear()
    out = _apply("manual", unit)
    assert out["changed"] is False and out["restart"] == "not-needed"
    assert unit.restarts == 0 and box["writes"] == []


def test_apply_reapplies_on_drift(box: dict[str, Any]) -> None:
    _apply("manual", _Unit())
    box["config"].write_text("approvals:\n  mode: 'off'\n")  # hand or agent edit
    unit = _Unit()
    out = _apply("manual", unit)
    assert out["changed"] is True and _rendered(box["config"]) == "manual"
    assert unit.restarts == 1


def test_apply_with_override_saves_but_leaves_hermes_alone(box: dict[str, Any]) -> None:
    box["overrides"].parent.mkdir(parents=True)
    box["overrides"].write_text("approvals:\n  mode: smart\n")
    before = box["config"].read_text()
    unit = _Unit()
    out = _apply("manual", unit)
    assert _stored(box["seed"]) == "manual"
    assert box["config"].read_text() == before
    assert unit.restarts == 0
    assert out["restart"] == "skipped-overridden"
    assert (out["effective"], out["source"]) == ("smart", "overrides")
    assert "wins" in out["message"]


def test_apply_not_provisioned_saves_only(box: dict[str, Any]) -> None:
    box["config"].unlink()
    unit = _Unit()
    out = _apply("manual", unit)
    assert _stored(box["seed"]) == "manual"
    assert unit.restarts == 0 and out["restart"] == "skipped-not-installed"


def test_apply_rejects_invalid_mode_without_writing(box: dict[str, Any]) -> None:
    with pytest.raises(cmd.CommandApprovalsInvalid):
        _apply("off", _Unit())
    assert box["writes"] == []


def test_apply_refuses_to_rewrite_a_corrupt_seed(box: dict[str, Any]) -> None:
    box["seed"].write_text("this is = = not toml")
    with pytest.raises(cmd.CommandApprovalsApplyFailed, match="not valid TOML"):
        _apply("manual", _Unit())
    assert box["seed"].read_text() == "this is = = not toml"


def test_apply_seed_write_failure_changes_nothing(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(body: str) -> None:
        raise OSError("sudo: a password is required")

    monkeypatch.setattr(cmd, "_write_seed", _boom)
    before = box["config"].read_text()
    unit = _Unit()
    with pytest.raises(cmd.CommandApprovalsApplyFailed, match="Nothing was changed"):
        _apply("manual", unit)
    assert box["config"].read_text() == before and unit.restarts == 0
    assert _stored(box["seed"]) is None


def test_apply_config_set_failure_rolls_back(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cmd, "_config_set", lambda mode: ["approvals.mode: exit 1"])
    seed_before = box["seed"].read_text()
    config_before = box["config"].read_text()
    unit = _Unit()
    with pytest.raises(cmd.CommandApprovalsApplyFailed, match="previous setting was restored"):
        _apply("manual", unit)
    assert box["seed"].read_text() == seed_before
    assert box["config"].read_text() == config_before
    assert unit.restarts == 0


def test_apply_restart_failure_rolls_back(box: dict[str, Any]) -> None:
    seed_before = box["seed"].read_text()
    config_before = box["config"].read_text()
    unit = _Unit(active=True, fail=True)
    with pytest.raises(cmd.CommandApprovalsApplyFailed, match="restart failed") as exc:
        _apply("manual", unit)
    assert box["seed"].read_text() == seed_before
    assert box["config"].read_text() == config_before
    assert exc.value.details["rollback_errors"] == []


def test_apply_reports_a_failed_rollback(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    real = cmd._write_seed

    def _write(body: str) -> None:
        calls["n"] += 1
        if calls["n"] > 1:  # the restore, not the first save
            raise OSError("seam gone")
        real(body)

    monkeypatch.setattr(cmd, "_write_seed", _write)
    with pytest.raises(cmd.CommandApprovalsApplyFailed, match="also failed") as exc:
        _apply("manual", _Unit(active=True, fail=True))
    assert exc.value.details["rollback_errors"]


# ── provisioning overlay + overrides precedence ──────────────────────────────


def _overlay(**kw: Any) -> dict[str, Any]:
    pairs = hp._build_config_overlay(
        primary={
            "model_id": "m",
            "backend_url": "http://127.0.0.1:8080/v1",
            "context_length": 8000,
        },
        chat_slots=[],
        delegation=None,
        auxiliary_tasks={},
        mcp_servers=[],
        agent_id="hermes",
        system_prompt="",
        personality_name="",
        live_resolve_enabled=True,
        **kw,
    )
    return dict(pairs)


def test_overlay_defaults_to_the_constant() -> None:
    assert _overlay()["approvals.mode"] == hp.HAL0_APPROVALS_MODE


def test_overlay_writes_the_setting() -> None:
    assert _overlay(approvals_mode="manual")["approvals.mode"] == "manual"


def test_overrides_win_over_the_setting(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("approvals:\n  mode: manual\n")  # what the overlay wrote
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("approvals:\n  mode: smart\n")
    hp._merge_config_yaml_layers(config, list_keys={}, overrides_path=overrides)
    assert _rendered(config) == "smart"


def test_config_write_phase_renders_the_setting_under_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the phase: setting -> config.yaml, then overrides win."""
    seed = tmp_path / "hermes.toml"
    seed.write_text(SEED + '\n[hermes]\ncommand_approvals = "manual"\n')
    monkeypatch.setattr(hp, "INSTALL_SEED_PATH", seed)
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_kwargs: {"model": "p", "base_url": "u", "context_length": 8000},
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no-overrides.yaml")
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.InstallIO(
        fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=fake_hermes_run()
    )
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))

    out = hp._phase_config_write(hp._StepCtx(state=state, io=io))
    assert out.details["approvals_mode"] == "manual"
    assert _rendered(Path(out.details["config_path"])) == "manual"

    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("approvals:\n  mode: smart\n")
    monkeypatch.setattr(hp, "OVERRIDES_PATH", overrides)
    out = hp._phase_config_write(hp._StepCtx(state=state, io=io))
    assert _rendered(Path(out.details["config_path"])) == "smart"


def test_seed_toml_rewrite_on_reprovision_keeps_the_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_write_seed_toml --repair`` refreshes [agent]/[mcp] and must keep [hermes]."""
    seed = tmp_path / "hermes.toml"
    seed.write_text(SEED + '\n[hermes]\ncommand_approvals = "manual"\n')
    monkeypatch.setattr(hp, "INSTALL_SEED_PATH", seed)
    written: list[str] = []
    monkeypatch.setattr(hp, "write_seed_toml_body", written.append)
    hp._write_seed_toml(hp.BootstrapState(hermes_home=str(tmp_path / "hh")), repair=True)
    assert tomllib.loads(written[-1])["hermes"]["command_approvals"] == "manual"


def test_unprivileged_seed_write_goes_through_the_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """hal0-api is not root: the write must go via hal0-agentenv, never a direct write."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(hp.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(hp, "_privileged_env_write", lambda verb, body: calls.append((verb, body)))
    hp.write_seed_toml_body("x = 1\n")
    assert calls == [("write-seed-toml", "x = 1\n")]


def test_apply_refuses_when_hermes_was_never_installed(box: dict[str, Any]) -> None:
    """No seed TOML: a body holding only [hermes] would break AgentConfig loading."""
    box["seed"].unlink()
    with pytest.raises(cmd.CommandApprovalsUnavailable, match="not installed"):
        _apply("manual", _Unit())
    assert not box["seed"].exists() and box["writes"] == []


def test_apply_recording_the_running_value_does_not_restart(box: dict[str, Any]) -> None:
    """Pinning the default explicitly: saved, but Hermes already runs it."""
    unit = _Unit()
    out = _apply("smart", unit)
    assert _stored(box["seed"]) == "smart"
    assert out["restart"] == "not-needed" and out["source"] == "setting"
    assert unit.restarts == 0


def test_apply_unreadable_config_changes_nothing(
    box: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """config.yaml is snapshotted before the seed write, so a read error writes nothing."""
    real_read = Path.read_text

    def _read(self: Path, *a: Any, **kw: Any) -> str:
        if self == box["config"]:
            raise PermissionError("config.yaml: permission denied")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _read)
    with pytest.raises(cmd.CommandApprovalsApplyFailed, match="Nothing was changed"):
        _apply("manual", _Unit())
    assert box["writes"] == []
