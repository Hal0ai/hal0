"""Hermes' terminal tool is an explicit opt-in that DEFAULTS OFF (#1863).

The `local` terminal backend runs shell commands as the ``hal0`` service user,
which is root-equivalent on a hal0 box (rootful podman + the hal0-systemctl /
hal0-agentenv sudo wrappers). hal0 used to write ``terminal.backend: local``
unconditionally, so a fresh 1.0 install handed an LLM that ingests untrusted
web/memory content a root-equivalent shell without ever asking the operator.

These tests drive the REAL provisioning phase (``_phase_config_write``) against
a temp ``$HERMES_HOME`` and assert on the resulting ``config.yaml`` — the file
hermes actually reads — rather than re-deriving the rule under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hal0.agents import hermes_provision as hp

from ._hermes_fakes import fake_hermes_run


def _run_config_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, home: Path | None = None
) -> tuple[hp.PhaseResult, dict]:
    """Run the real config_write phase offline; return (result, parsed yaml)."""
    hermes_home = home if home is not None else tmp_path / "hh"
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_kwargs: {"model": "p", "base_url": "u", "context_length": 8000},
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no-such-overrides.yaml")
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.InstallIO(
        fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=fake_hermes_run()
    )
    out = hp._phase_config_write(hp._StepCtx(state=state, io=io))
    cfg = Path(out.details["config_path"])
    return out, yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}


def _terminal_is_off(doc: dict) -> bool:
    """Both halves of "off": no hal0-written backend, and the toolsets subtracted."""
    disabled = (doc.get("agent") or {}).get("disabled_toolsets") or []
    terminal = doc.get("terminal") or {}
    return "terminal" in disabled and "code_execution" in disabled and "backend" not in terminal


# ── fresh install ───────────────────────────────────────────────────────────


def test_fresh_install_defaults_terminal_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    out, doc = _run_config_write(tmp_path, monkeypatch)

    assert out.status == hp.PhaseStatus.OK
    assert out.details["terminal_tool"] == "off"
    assert out.details["terminal_tool_reason"] == "default-off"
    assert _terminal_is_off(doc), doc


def test_fresh_config_yaml_never_ships_a_local_terminal_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Deliberately written against ONLY the pre-existing provisioning surface
    # (the phase + the file it writes), with the env var spelled out, so this
    # case fails on the old code for a behavioural reason — not because a new
    # helper is missing.
    monkeypatch.delenv("HAL0_HERMES_TERMINAL", raising=False)
    _out, doc = _run_config_write(tmp_path, monkeypatch)

    assert (doc.get("terminal") or {}).get("backend") != "local", (
        "a fresh install must not hand the agent a root-equivalent shell"
    )
    assert "terminal" in ((doc.get("agent") or {}).get("disabled_toolsets") or [])


def test_non_interactive_install_never_enables_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A silent/headless install answers no prompt, so the opt-in env is simply
    # absent — exactly the fresh-install path, asserted separately because
    # "a silent install must never enable it" is its own contract.
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    monkeypatch.setenv("HAL0_NONINTERACTIVE", "1")
    _out, doc = _run_config_write(tmp_path, monkeypatch)

    assert _terminal_is_off(doc), doc


def test_explicit_opt_out_keeps_terminal_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "0")
    out, doc = _run_config_write(tmp_path, monkeypatch)

    assert out.details["terminal_tool_reason"] == "opt-out"
    assert _terminal_is_off(doc), doc


# ── opt-in ──────────────────────────────────────────────────────────────────


def test_opt_in_reproduces_todays_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "1")
    home = tmp_path / "hh"
    out, doc = _run_config_write(tmp_path, monkeypatch, home=home)

    assert out.details["terminal_tool"] == "on"
    assert doc["terminal"]["backend"] == "local"
    assert doc["terminal"]["cwd"] == str(home / "scratch")
    # Nothing subtracted — the agent keeps terminal/process/execute_code.
    assert (doc.get("agent") or {}).get("disabled_toolsets") == []


def test_opt_in_persists_across_a_later_reprovision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hh"
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "1")
    _run_config_write(tmp_path, monkeypatch, home=home)
    # A later run (update / --repair) with no env answer must not undo it.
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    out, doc = _run_config_write(tmp_path, monkeypatch, home=home)

    assert out.details["terminal_tool_reason"] == "existing-config"
    assert doc["terminal"]["backend"] == "local"


def test_off_is_convergent_across_reruns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "hh"
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    first, _doc = _run_config_write(tmp_path, monkeypatch, home=home)
    second, doc = _run_config_write(tmp_path, monkeypatch, home=home)

    assert second.hash == first.hash
    assert second.details["list_merge_changed"] is False
    assert _terminal_is_off(doc), doc


# ── update: an existing box's explicit setting is the operator's choice ─────


def test_existing_local_backend_survives_an_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Shape of a box provisioned by hal0 <= 1.0.0-rc.5: hal0 wrote
    # terminal.backend itself, so the agent is working today and an update
    # must not disable it out from under the operator.
    home = tmp_path / "hh"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {"default": "hal0/agent"},
                "terminal": {"backend": "local", "cwd": str(home / "scratch")},
                "agent": {"max_turns": 60},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    out, doc = _run_config_write(tmp_path, monkeypatch, home=home)

    assert out.details["terminal_tool"] == "on"
    assert out.details["terminal_tool_reason"] == "existing-config"
    assert doc["terminal"]["backend"] == "local"
    assert (doc.get("agent") or {}).get("disabled_toolsets") == []
    # Unrelated hermes-owned keys are still preserved by the merge.
    assert doc["agent"]["max_turns"] == 60


def test_existing_box_can_be_turned_off_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hh"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": {"backend": "local", "cwd": "/x"}}, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "0")
    _out, doc = _run_config_write(tmp_path, monkeypatch, home=home)

    disabled = (doc.get("agent") or {}).get("disabled_toolsets") or []
    assert "terminal" in disabled and "code_execution" in disabled


# ── the decision function itself ────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_env_opt_in_beats_a_disabled_config(value: str) -> None:
    disabled_on_disk = "agent:\n  disabled_toolsets: [terminal, code_execution]\n"
    assert hp.terminal_tool_enabled(
        config_text=disabled_on_disk, env={hp.HERMES_TERMINAL_ENV: value}
    ) == (True, "opt-in")


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_env_opt_out_beats_an_enabled_config(value: str) -> None:
    enabled_on_disk = "terminal:\n  backend: local\n"
    assert hp.terminal_tool_enabled(
        config_text=enabled_on_disk, env={hp.HERMES_TERMINAL_ENV: value}
    ) == (False, "opt-out")


def test_blank_env_is_not_an_answer() -> None:
    # An exported-but-empty variable must not be read as either answer.
    assert hp.terminal_tool_enabled(config_text=None, env={hp.HERMES_TERMINAL_ENV: ""}) == (
        False,
        "default-off",
    )


def test_post_migrate_defaults_do_not_look_like_an_opt_in() -> None:
    # `hermes config migrate` materialises hermes' OWN terminal defaults. The
    # decision therefore reads the PRE-migrate content; a missing/empty file
    # (the fresh-install case) must never read as an existing opt-in.
    assert hp.terminal_tool_enabled(config_text=None, env={}) == (False, "default-off")
    assert hp.terminal_tool_enabled(config_text="{}\n", env={}) == (False, "default-off")


def test_corrupt_config_does_not_enable_terminal() -> None:
    enabled, _why = hp.terminal_tool_enabled(config_text="terminal: [", env={})
    assert enabled is False
