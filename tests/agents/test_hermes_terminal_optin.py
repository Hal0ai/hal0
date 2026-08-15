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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    home: Path | None = None,
    state_path: Path | None = None,
) -> tuple[hp.PhaseResult, dict]:
    """Run the real config_write phase offline; return (result, parsed yaml)."""
    hermes_home = home if home is not None else tmp_path / "hh"
    # The real marker lives in root-only /etc/hal0/agents/; point it at tmp.
    monkeypatch.setattr(
        hp, "TERMINAL_STATE_PATH", state_path or (tmp_path / "state" / "hermes-terminal-tool")
    )
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
    """What actually removes the tool from hermes' schema: the subtraction.

    A leftover ``terminal.backend`` in the file is harmless and deliberately not
    deleted — hal0 layers its keys onto a hermes-owned file and never strips
    hermes' own. The fresh-install cases assert separately that hal0 writes no
    ``terminal.*`` key of its own.
    """
    disabled = (doc.get("agent") or {}).get("disabled_toolsets") or []
    return "terminal" in disabled and "code_execution" in disabled


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
    # hal0 writes no terminal.* key at all on a fresh box.
    assert "terminal" not in doc, doc.get("terminal")


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

    assert out.details["terminal_tool_reason"] == "recorded"
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
                "memory": {"provider": "hal0-memory"},
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
        yaml.safe_dump(
            {"memory": {"provider": "hal0-memory"}, "terminal": {"backend": "local", "cwd": "/x"}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "0")
    _out, doc = _run_config_write(tmp_path, monkeypatch, home=home)

    disabled = (doc.get("agent") or {}).get("disabled_toolsets") or []
    assert "terminal" in disabled and "code_execution" in disabled


# ── it must not trample anything else the operator disabled ────────────────


def test_unrelated_disabled_toolsets_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hh"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"agent": {"disabled_toolsets": ["browser", "cronjob"]}}, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    _out, doc = _run_config_write(tmp_path, monkeypatch, home=home)

    disabled = (doc.get("agent") or {}).get("disabled_toolsets") or []
    assert set(disabled) == {"browser", "cronjob", "terminal", "code_execution"}

    # And opting in removes ONLY hal0's two entries.
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "1")
    _out2, doc2 = _run_config_write(tmp_path, monkeypatch, home=home)
    assert (doc2.get("agent") or {}).get("disabled_toolsets") == ["browser", "cronjob"]


# ── a half-finished provision is not consent ───────────────────────────────


def test_bare_migrate_output_is_not_read_as_an_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fresh run that died after `hermes config migrate` leaves hermes' OWN
    # defaults on disk, including terminal.backend: local. The next run must not
    # read that as the operator having opted in.
    home = tmp_path / "hh"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"model": "", "toolsets": ["hermes-cli"], "terminal": {"backend": "local", "cwd": "."}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    out, doc = _run_config_write(tmp_path, monkeypatch, home=home)

    assert out.details["terminal_tool_decision"] == "off"
    assert _terminal_is_off(doc), doc


def test_recorded_state_outranks_a_reappearing_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hh"
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    state_path = tmp_path / "state" / "hermes-terminal-tool"
    _run_config_write(tmp_path, monkeypatch, home=home, state_path=state_path)
    monkeypatch.setattr(hp, "TERMINAL_STATE_PATH", state_path)
    assert hp.read_terminal_state() is False

    # Something puts a terminal backend back (a hand edit, a hermes migration).
    doc = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    doc["terminal"] = {"backend": "local", "cwd": "."}
    doc["agent"].pop("disabled_toolsets", None)
    (home / "config.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    out, doc2 = _run_config_write(tmp_path, monkeypatch, home=home)
    assert out.details["terminal_tool_reason"] == "recorded"
    assert _terminal_is_off(doc2), doc2


# ── the reported posture is the effective one ──────────────────────────────


def test_reported_posture_follows_an_operator_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # overrides.yaml deep-merges last and wins over hal0's disabled_toolsets, so
    # the reported posture has to be read back off the final file — otherwise
    # the status line would claim "off" on a box where the tool is loaded.
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("agent:\n  disabled_toolsets: []\n", encoding="utf-8")
    home = tmp_path / "hh"
    state = hp.BootstrapState(hermes_home=str(home))
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_kwargs: {"model": "p", "base_url": "u", "context_length": 8000},
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", overrides)
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.InstallIO(
        fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=fake_hermes_run()
    )
    out = hp._phase_config_write(hp._StepCtx(state=state, io=io))

    assert out.details["terminal_tool_decision"] == "off"
    assert out.details["terminal_tool"] == "on", "the override re-enabled it — say so"


def test_an_operator_moved_backend_is_not_widened_back_to_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An operator who moved the backend to docker/ssh has a NARROWER execution
    # boundary than hal0's default. Re-asserting `local` on top would silently
    # widen it back to root-equivalent host execution.
    home = tmp_path / "hh"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"terminal": {"backend": "docker", "cwd": str(home / "scratch")}}, sort_keys=False
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    out, doc = _run_config_write(tmp_path, monkeypatch, home=home)

    assert out.details["terminal_tool_reason"] == "existing-config"
    assert doc["terminal"]["backend"] == "docker"
    assert (doc.get("agent") or {}).get("disabled_toolsets") == []


def test_non_local_backend_is_recognised_whatever_its_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # hermes' own default backend is `local`, so a docker/ssh value cannot have
    # been materialised by a migration — it is someone's choice even when the
    # cwd is theirs too, and disabling it would break their setup.
    home = tmp_path / "hh"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": {"backend": "ssh", "cwd": "/srv/work"}}, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    out, doc = _run_config_write(tmp_path, monkeypatch, home=home)

    assert out.details["terminal_tool_reason"] == "existing-config"
    assert doc["terminal"] == {"backend": "ssh", "cwd": "/srv/work"}
    assert (doc.get("agent") or {}).get("disabled_toolsets") == []


def test_unwritable_state_file_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If the decision cannot be recorded, an "on" answer must not be honoured:
    # an unrecorded opt-in is exactly the state a later run could re-derive the
    # permissive way, so refuse to enable rather than enable unrecorded.
    blocked = tmp_path / "blocked" / "hermes-terminal-tool"
    blocked.mkdir(parents=True)  # a dir at the marker path blocks the write
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "1")
    out, doc = _run_config_write(tmp_path, monkeypatch, state_path=blocked)

    assert out.details["terminal_tool_reason"] == "state-unwritable"
    assert _terminal_is_off(doc), doc


# ── the marker is out of the agent's reach ─────────────────────────────────


def test_state_marker_lives_outside_the_agent_writable_home() -> None:
    # $HERMES_HOME is owned by the same `hal0` user the agent runs as and is a
    # ReadWritePath of the agent unit, so a marker there would be agent-
    # writable: a prompt-injected agent could write "1" and have the next
    # re-provision read its own state back as operator consent. /etc/hal0/agents
    # is root:root 0755 on a real box — root writes, the provisioner reads.
    assert Path("/etc/hal0/agents/hermes-terminal-tool") == hp.TERMINAL_STATE_PATH
    assert "/.hermes" not in str(hp.TERMINAL_STATE_PATH)
    assert str(hp.TERMINAL_STATE_PATH).startswith("/etc/hal0/agents/")


def test_persist_terminal_decision_records_a_legacy_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The root-side capture: a pre-#1863 box's existing setting is resolved and
    # written to the root-only marker once, before provisioning drops to hal0.
    home = tmp_path / "hh"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"terminal": {"backend": "local", "cwd": str(home / "scratch")}}, sort_keys=False
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "state" / "hermes-terminal-tool"
    monkeypatch.setattr(hp, "TERMINAL_STATE_PATH", state_path)

    enabled, why, recorded = hp.persist_terminal_decision(home, env={})

    assert (enabled, why, recorded) == (True, "existing-config", True)
    assert hp.read_terminal_state() is True


def test_persist_terminal_decision_records_a_fresh_box_as_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "state" / "hermes-terminal-tool"
    monkeypatch.setattr(hp, "TERMINAL_STATE_PATH", state_path)

    enabled, why, recorded = hp.persist_terminal_decision(tmp_path / "nonexistent", env={})

    assert (enabled, why, recorded) == (False, "default-off", True)
    assert hp.read_terminal_state() is False


def test_marker_is_world_readable_under_a_restrictive_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Root writes it; the privilege-dropped provisioner reads it. Under a root
    # umask of 077 an inherited-mode write would land 0600 and the `hal0` child
    # would see no record — failing an operator's opt-in closed.
    import os

    state_path = tmp_path / "state" / "hermes-terminal-tool"
    monkeypatch.setattr(hp, "TERMINAL_STATE_PATH", state_path)
    old = os.umask(0o077)
    try:
        assert hp.write_terminal_state(True) is True
    finally:
        os.umask(old)

    assert state_path.stat().st_mode & 0o044 == 0o044


def test_undecodable_marker_reads_as_unrecorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Non-UTF-8 bytes must not raise out of provisioning — that would block the
    # very `HAL0_HERMES_TERMINAL=0` run that repairs the marker.
    state_path = tmp_path / "hermes-terminal-tool"
    state_path.write_bytes(b"\xff\xfe\x00")
    monkeypatch.setattr(hp, "TERMINAL_STATE_PATH", state_path)

    assert hp.read_terminal_state() is None


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


def test_effective_off_requires_both_execution_toolsets() -> None:
    # `execute_code` runs arbitrary Python as the same root-equivalent user, so
    # "terminal disabled, code_execution not" is not a safe posture and must
    # never be reported as off.
    only_terminal = "agent:\n  disabled_toolsets: [terminal]\n"
    both = "agent:\n  disabled_toolsets: [terminal, code_execution]\n"
    assert hp.terminal_enabled_in_config(only_terminal) is True
    assert hp.terminal_enabled_in_config(both) is False


def test_corrupt_config_does_not_enable_terminal() -> None:
    enabled, _why = hp.terminal_tool_enabled(config_text="terminal: [", env={})
    assert enabled is False
