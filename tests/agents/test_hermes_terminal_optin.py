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
    # The only terminal.* key hal0 writes on a fresh box is the sentinel
    # backend that drops the tool for every agent in the process (#2878).
    assert doc["terminal"] == {"backend": hp.TERMINAL_OFF_BACKEND}, doc.get("terminal")


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


def test_legacy_etc_hal0_cwd_survives_an_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Shape of a box provisioned between v0.7.3-nightly.20260621 (commit
    # ce4f9ded) and v0.9.8 (commit cda957c3 moved it): hal0 wrote
    # terminal.cwd: /etc/hal0. That agent has a working shell today and an
    # update must not silently take it away.
    home = tmp_path / "hh"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {"default": "hal0/agent"},
                "terminal": {"backend": "local", "cwd": "/etc/hal0"},
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


def test_operator_custom_cwd_survives_an_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An operator who pointed the local terminal at their own directory made a
    # choice hermes' migration could never have made for them. Any cwd that is
    # not hermes' own default is evidence of a deliberate, working setup.
    home = tmp_path / "hh"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"terminal": {"backend": "local", "cwd": "/srv/agent-work"}}, sort_keys=False
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    out, doc = _run_config_write(tmp_path, monkeypatch, home=home)

    assert out.details["terminal_tool_reason"] == "existing-config"
    assert (doc.get("agent") or {}).get("disabled_toolsets") == []


@pytest.mark.parametrize("cwd", [None, "", ".", "./", " . "])
def test_upstream_default_shape_is_never_read_as_consent(cwd: str | None) -> None:
    # hermes' own `config migrate` writes backend: local with cwd "." (or leaves
    # cwd unset). That is upstream's default, not hal0's overlay, so it can
    # never be operator consent — those boxes stay default-OFF.
    terminal: dict[str, str] = {"backend": "local"}
    if cwd is not None:
        terminal["cwd"] = cwd
    config_text = yaml.safe_dump({"terminal": terminal}, sort_keys=False)

    assert hp.terminal_tool_enabled(config_text=config_text, env={}) == (False, "default-off")


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


# ── delegation cannot be used to mint a shell the box said no to ───────────


def test_terminal_off_also_subtracts_delegation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `delegate_task` builds a child agent from toolsets named in the CALL, and
    # the pinned hermes-agent passes only enabled_toolsets to that child — its
    # DELEGATE_BLOCKED_TOOLS blocks execute_code but not terminal/process. So a
    # prompt-injected agent on a terminal-OFF box could call
    # delegate_task(toolsets=["terminal"]) and get the shell back one hop away.
    # With the terminal off, `delegation` goes too.
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    _out, doc = _run_config_write(tmp_path, monkeypatch)

    disabled = (doc.get("agent") or {}).get("disabled_toolsets") or []
    assert "delegation" in disabled, disabled


def test_terminal_on_keeps_delegation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An operator who opted the terminal IN has accepted host execution, so
    # subagents are theirs to spawn — nothing is subtracted.
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "1")
    _out, doc = _run_config_write(tmp_path, monkeypatch)

    assert (doc.get("agent") or {}).get("disabled_toolsets") == []


def test_opting_in_later_clears_the_delegation_subtraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The posture is convergent in both directions: turning the terminal on
    # must not leave a stale `delegation` subtraction behind.
    home = tmp_path / "hh"
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    _run_config_write(tmp_path, monkeypatch, home=home)
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "1")
    _out, doc = _run_config_write(tmp_path, monkeypatch, home=home)

    assert (doc.get("agent") or {}).get("disabled_toolsets") == []


def test_delegation_config_block_is_still_written_when_the_terminal_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only the TOOLSET is subtracted. The `delegation:` block routes subagents
    # to the `agent` role slot; leaving it written keeps the posture a pure
    # toolset decision and keeps re-enabling a one-key change.
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "1")
    _out, on_doc = _run_config_write(tmp_path, monkeypatch, home=tmp_path / "on")
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    _out2, off_doc = _run_config_write(tmp_path, monkeypatch, home=tmp_path / "off")

    assert off_doc.get("delegation") == on_doc.get("delegation")


def test_readback_requires_delegation_gone_too() -> None:
    # The readback answers "can this agent reach command execution?", so it has
    # to include every route. `delegate_task` left live is a shell one hop
    # away, exactly as `execute_code` left live is a shell under another name —
    # reporting that shape as a clean "off" would be the same mistake the
    # execution pair already refuses to make.
    execution_only = "agent:\n  disabled_toolsets: [terminal, code_execution]\n"
    all_three = "agent:\n  disabled_toolsets: [terminal, code_execution, delegation]\n"
    assert hp.terminal_enabled_in_config(execution_only) is True
    assert hp.terminal_enabled_in_config(all_three) is False


def test_an_override_that_restores_only_delegation_is_reported_as_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The reachable version of the above: overrides.yaml deep-merges last, so an
    # operator can hand back `delegation` alone. The status line must say the
    # tool is reachable rather than claim off.
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(
        "agent:\n  disabled_toolsets: [terminal, code_execution]\n", encoding="utf-8"
    )
    home = tmp_path / "hh"
    monkeypatch.setattr(hp, "TERMINAL_STATE_PATH", tmp_path / "state" / "hermes-terminal-tool")
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
    out = hp._phase_config_write(hp._StepCtx(state=hp.BootstrapState(hermes_home=str(home)), io=io))

    assert out.details["terminal_tool_decision"] == "off"
    assert out.details["terminal_tool"] == "on", "delegate_task is live — do not report a clean off"


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
    assert set(disabled) == {"browser", "cronjob", *hp.TERMINAL_OFF_TOOLSETS}

    # And opting in removes ONLY hal0's own entries.
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


def test_a_working_legacy_box_is_never_disabled_by_an_unwritable_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fail-closed is for a FRESH opt-in only. A legacy box already has a
    # working terminal, and the marker is root-only /etc/hal0/agents/ — so a
    # provisioning path that runs unprivileged and skips the root prelude
    # (`hal0 agent reprovision hermes` as a non-root user) simply cannot write
    # it. Downgrading to state-unwritable there would take away a shell the
    # operator has been using, on a run that asked for no such thing.
    home = tmp_path / "hh"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": {"backend": "local", "cwd": "/etc/hal0"}}, sort_keys=False),
        encoding="utf-8",
    )
    blocked = tmp_path / "blocked" / "hermes-terminal-tool"
    blocked.mkdir(parents=True)  # a dir at the marker path blocks the write
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    out, doc = _run_config_write(tmp_path, monkeypatch, home=home, state_path=blocked)

    assert out.details["terminal_tool_decision"] == "on"
    assert out.details["terminal_tool_reason"] == "existing-config+state-stale"
    assert out.details["terminal_state_stale"] is True
    assert (doc.get("agent") or {}).get("disabled_toolsets") == []
    # Applied and warned, never silently accepted.
    assert out.status == hp.PhaseStatus.WARN
    assert "could not be recorded" in (out.reason or "")


def test_the_stale_marker_warning_names_the_direction_that_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The two stale cases point opposite ways — an unrecorded opt-out would be
    # re-enabled next run, an unrecorded enable would be disabled next run —
    # so the operator-facing line must not describe one as the other.
    home = tmp_path / "hh"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"terminal": {"backend": "local", "cwd": "/etc/hal0"}}, sort_keys=False),
        encoding="utf-8",
    )
    blocked = tmp_path / "blocked" / "hermes-terminal-tool"
    blocked.mkdir(parents=True)
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    out, _doc = _run_config_write(tmp_path, monkeypatch, home=home, state_path=blocked)

    assert "opt-out" not in (out.reason or ""), out.reason


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


def test_optout_that_cannot_be_recorded_is_applied_but_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An opt-out from the unprivileged path cannot replace the root-owned
    # marker. Disabling now is still right, but the marker would re-enable the
    # tools at the next unflagged provision — that must not pass silently.
    state_path = tmp_path / "state" / "hermes-terminal-tool"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("1\n", encoding="utf-8")
    real_write = hp.write_terminal_state
    monkeypatch.setattr(hp, "write_terminal_state", lambda _enabled: False)
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "0")
    out, doc = _run_config_write(tmp_path, monkeypatch, state_path=state_path)
    monkeypatch.setattr(hp, "write_terminal_state", real_write)

    assert out.status == hp.PhaseStatus.WARN
    assert out.details["terminal_state_stale"] is True
    assert "re-run as root" in (out.reason or "")
    assert _terminal_is_off(doc), doc


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


def test_effective_off_requires_every_route_gone() -> None:
    # Each of the three is independently sufficient to reach command execution:
    # `execute_code` runs arbitrary Python as the same root-equivalent user, and
    # `delegate_task` hands a child agent whatever toolsets the call names. Any
    # one of them left live means the posture is not off.
    for left_live in hp.TERMINAL_OFF_TOOLSETS:
        rest = [t for t in hp.TERMINAL_OFF_TOOLSETS if t != left_live]
        partial = f"agent:\n  disabled_toolsets: [{', '.join(rest)}]\n"
        assert hp.terminal_enabled_in_config(partial) is True, left_live

    everything = f"agent:\n  disabled_toolsets: [{', '.join(hp.TERMINAL_OFF_TOOLSETS)}]\n"
    assert hp.terminal_enabled_in_config(everything) is False


def test_corrupt_config_does_not_enable_terminal() -> None:
    enabled, _why = hp.terminal_tool_enabled(config_text="terminal: [", env={})
    assert enabled is False


# ── #2878: the backend gate the side agent cannot bypass ─────────────────────
#
# Hermes v0.22.3+'s gateway-owned preview-restart side agent is built with
# ``enabled_toolsets=['terminal','file'], disabled_toolsets=None``
# (gateway/session_tui_side.py:282-283 at 4d043038), so the subtraction above
# never reaches it. What every agent in the process shares is the terminal
# tool's ``check_fn``: an unknown ``terminal.backend`` fails it and the tool
# is dropped from every model-facing schema. hal0 writes that sentinel when
# the terminal is off, and takes it back out when the operator opts in.


def test_off_writes_the_sentinel_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    _out, doc = _run_config_write(tmp_path, monkeypatch)
    assert doc["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND
    assert _terminal_is_off(doc), doc


def test_sentinel_backend_is_not_a_real_hermes_backend() -> None:
    # Hermes rejects unknown TERMINAL_ENV values in check_terminal_requirements
    # (tools/terminal_tool_backends.py:385-393); the sentinel must never collide
    # with a real or plugin-looking name, and must say who set it.
    assert hp.TERMINAL_OFF_BACKEND.startswith("hal0-")
    assert hp.TERMINAL_OFF_BACKEND not in {
        "local",
        "docker",
        "singularity",
        "modal",
        "daytona",
        "vercel_sandbox",
        "ssh",
        "managed_modal",
    }


def test_opting_in_later_replaces_the_sentinel_with_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hh"
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    _out, doc = _run_config_write(tmp_path, monkeypatch, home=home)
    assert doc["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "1")
    out, doc = _run_config_write(tmp_path, monkeypatch, home=home)
    assert out.details["terminal_tool"] == "on"
    assert doc["terminal"]["backend"] == "local"
    assert doc["terminal"]["cwd"] == str(home / "scratch")


def test_off_gates_an_operator_moved_backend_and_remembers_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off means off for every backend (#2878 review): a docker/ssh backend is set
    aside under hal0.terminal_backend_saved and the sentinel takes its place;
    opting in puts the operator's backend back and clears the memory."""
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "0")
    home = tmp_path / "hh"
    home.mkdir()
    (home / "config.yaml").write_text("terminal:\n  backend: ssh\n  ssh_host: h\n")
    _out, doc = _run_config_write(tmp_path, monkeypatch, home=home)
    assert doc["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND
    assert doc["terminal"]["ssh_host"] == "h"
    assert doc["hal0"][hp.TERMINAL_SAVED_BACKEND_KEY] == "ssh"
    assert _terminal_is_off(doc), doc
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "1")
    out, doc = _run_config_write(tmp_path, monkeypatch, home=home)
    assert out.details["terminal_tool"] == "on"
    assert doc["terminal"]["backend"] == "ssh"
    assert "cwd" not in doc["terminal"]
    assert doc["hal0"][hp.TERMINAL_SAVED_BACKEND_KEY] == ""


def test_sentinel_backend_reads_as_off_not_as_an_operator_choice() -> None:
    text = f"terminal:\n  backend: {hp.TERMINAL_OFF_BACKEND}\n  cwd: /x\n"
    assert hp._existing_terminal_choice(text, "/h") is False


def test_profile_terminal_keys_follow_the_recorded_decision() -> None:
    S, K, OFF = hp.TERMINAL_OFF_BACKEND, hp.TERMINAL_SAVED_BACKEND_KEY, hp.TERMINAL_OFF_TOOLSETS
    gate = {"terminal": {"backend": S}, "agent": {"disabled_toolsets": OFF}}
    assert hp._terminal_profile_keys({}, terminal_enabled=False) == gate
    assert (
        hp._terminal_profile_keys({"terminal": {"backend": "local"}}, terminal_enabled=False)
        == gate
    )
    # off means off for every backend; the operator's is remembered
    assert hp._terminal_profile_keys({"terminal": {"backend": "ssh"}}, terminal_enabled=False) == {
        **gate,
        "hal0": {K: "ssh"},
    }
    # the operator's own disabled entries stay, hal0's are appended once
    assert (
        hp._terminal_profile_keys(
            {"terminal": {"backend": S}, "agent": {"disabled_toolsets": ["browser", *OFF]}},
            terminal_enabled=False,
        )
        == {}
    )
    assert hp._terminal_profile_keys(
        {"terminal": {"backend": S}, "agent": {"disabled_toolsets": ["browser"]}},
        terminal_enabled=False,
    ) == {"agent": {"disabled_toolsets": ["browser", *OFF]}}
    # opting in takes only the sentinel and hal0's entries out
    assert hp._terminal_profile_keys(
        {"terminal": {"backend": S}, "agent": {"disabled_toolsets": ["browser", *OFF]}},
        terminal_enabled=True,
    ) == {"terminal": {"backend": "local"}, "agent": {"disabled_toolsets": ["browser"]}}
    assert hp._terminal_profile_keys(
        {"terminal": {"backend": S}, "hal0": {K: "docker"}}, terminal_enabled=True
    ) == {"terminal": {"backend": "docker"}, "hal0": {K: ""}}
    assert (
        hp._terminal_profile_keys({"terminal": {"backend": "docker"}}, terminal_enabled=True) == {}
    )
    # no sentinel means hal0 never gated this profile: an operator's own
    # `terminal` entry on a terminal-on box is not hal0's to remove
    assert (
        hp._terminal_profile_keys(
            {"terminal": {"backend": "local"}, "agent": {"disabled_toolsets": ["terminal"]}},
            terminal_enabled=True,
        )
        == {}
    )
    # no decision: a hal0-api reconcile must not flip a box either way
    assert hp._terminal_profile_keys({}, terminal_enabled=None) == {}


def test_wire_named_profiles_gates_clones_when_the_terminal_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml

    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: "svc-key")
    home = tmp_path / "hh"
    for name in ("coder", "lab"):
        (home / "profiles" / name).mkdir(parents=True)
    (home / "profiles" / "coder" / "config.yaml").write_text("model:\n  default: a\n")
    (home / "profiles" / "lab" / "config.yaml").write_text("terminal:\n  backend: docker\n")
    hp.wire_named_profiles(home, terminal_enabled=False)
    coder = yaml.safe_load((home / "profiles" / "coder" / "config.yaml").read_text())
    lab = yaml.safe_load((home / "profiles" / "lab" / "config.yaml").read_text())
    assert coder["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND
    assert lab["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND
    assert lab["hal0"][hp.TERMINAL_SAVED_BACKEND_KEY] == "docker"
    assert set(hp.TERMINAL_OFF_TOOLSETS) <= set(coder["agent"]["disabled_toolsets"])
    hp.wire_named_profiles(home, terminal_enabled=True)
    coder = yaml.safe_load((home / "profiles" / "coder" / "config.yaml").read_text())
    lab = yaml.safe_load((home / "profiles" / "lab" / "config.yaml").read_text())
    assert coder["terminal"]["backend"] == "local"
    assert lab["terminal"]["backend"] == "docker"


def test_wire_named_profiles_reads_the_marker_only_without_an_applied_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provisioning run passes the decision it applied; a hal0-api reconcile
    has none and falls back to the recorded marker (#2878 review)."""
    import yaml

    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: "svc-key")
    home = tmp_path / "hh"
    (home / "profiles" / "coder").mkdir(parents=True)
    (home / "profiles" / "coder" / "config.yaml").write_text("model:\n  default: a\n")
    monkeypatch.setattr(hp, "read_terminal_state", lambda: True)  # a stale root-owned marker
    hp.wire_named_profiles(home, terminal_enabled=False)  # what config_write applied
    doc = yaml.safe_load((home / "profiles" / "coder" / "config.yaml").read_text())
    assert doc["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND
    hp.wire_named_profiles(home)  # reconcile: marker says on
    doc = yaml.safe_load((home / "profiles" / "coder" / "config.yaml").read_text())
    assert doc["terminal"]["backend"] == "local"


def test_profile_wire_phase_fails_when_an_off_box_cannot_gate_a_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hp,
        "wire_named_profiles",
        lambda home, terminal_enabled=None: {
            "wired": [],
            "changed": [],
            "skipped": {},
            "errors": {"coder": "config.yaml is a symlink"},
        },
    )
    state = hp.BootstrapState(hermes_home=str(tmp_path), terminal_enabled=False)
    out = hp._phase_profile_mcp_wire(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert out.status == hp.PhaseStatus.FAIL
    assert out.reason is not None and "terminal gate" in out.reason and "coder" in out.reason
    state.terminal_enabled = True
    out = hp._phase_profile_mcp_wire(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert out.status == hp.PhaseStatus.OK and out.reason is not None


def test_update_path_carries_the_terminal_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``hal0 update`` never runs the overlay; the release invariants apply the gate."""
    import yaml

    home = tmp_path / "hh"
    home.mkdir()
    (home / "config.yaml").write_text(
        "agent:\n  system_prompt: keep\nterminal:\n  backend: local\n  cwd: /x\n"
    )
    monkeypatch.setattr(hp, "read_terminal_state", lambda: False)
    assert hp.apply_release_config_invariants(home, overrides_path=tmp_path / "none.yaml") is True
    doc = yaml.safe_load((home / "config.yaml").read_text())
    assert doc["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND
    assert doc["terminal"]["cwd"] == "/x"
    monkeypatch.setattr(hp, "read_terminal_state", lambda: None)
    assert hp.apply_release_config_invariants(home, overrides_path=tmp_path / "none.yaml") is False
    # Opting in is a provisioning act (the overlay rewrites the root config first);
    # an update never lifts a sentinel on the marker's word alone, which is also
    # what protects a non-root opt-out whose marker could not be replaced.
    monkeypatch.setattr(hp, "read_terminal_state", lambda: True)
    assert hp.apply_release_config_invariants(home, overrides_path=tmp_path / "none.yaml") is False
    doc = yaml.safe_load((home / "config.yaml").read_text())
    assert doc["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND


def test_update_path_shouts_when_an_override_displaces_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml

    home = tmp_path / "hh"
    home.mkdir()
    (home / "config.yaml").write_text("agent:\n  system_prompt: keep\n")
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("terminal:\n  backend: local\n")
    monkeypatch.setattr(hp, "read_terminal_state", lambda: False)
    events: list = []
    monkeypatch.setattr(hp.log, "error", lambda event, **kw: events.append((event, kw)))
    hp.apply_release_config_invariants(home, overrides_path=overrides)
    doc = yaml.safe_load((home / "config.yaml").read_text())
    assert doc["terminal"]["backend"] == "local"  # the override wins by design
    assert events and events[0][0] == "hermes.terminal_gate_overridden"
    assert events[0][1]["found"] == "local" and events[0][1]["expected"] == hp.TERMINAL_OFF_BACKEND


def test_box_terminal_decision_trusts_the_root_sentinel_over_a_stale_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hh"
    home.mkdir()
    monkeypatch.setattr(hp, "read_terminal_state", lambda: True)
    (home / "config.yaml").write_text(f"terminal:\n  backend: {hp.TERMINAL_OFF_BACKEND}\n")
    assert hp.box_terminal_decision(home) is False
    (home / "config.yaml").write_text("terminal:\n  backend: local\n")
    assert hp.box_terminal_decision(home) is True
    # no marker: the provisioner's own rule decides from the config
    monkeypatch.setattr(hp, "read_terminal_state", lambda: None)
    (home / "config.yaml").write_text(
        "agent:\n  disabled_toolsets: [terminal, code_execution, delegation]\n"
    )
    assert hp.box_terminal_decision(home) is False
    (home / "config.yaml").write_text("terminal:\n  backend: local\n  cwd: /srv/mine\n")
    assert hp.box_terminal_decision(home) is True  # a legacy working shell
    (home / "config.yaml").write_text("model:\n  default: x\n")
    assert hp.box_terminal_decision(home) is False  # default off
    assert hp.box_terminal_decision(tmp_path / "absent") is None


def test_reconcile_does_not_undo_an_unrecorded_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hal0-api's sweep has no applied decision; a root sentinel plus a stale
    marker saying on must not restore the clones' terminal (#2878 review)."""
    import yaml

    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: "svc-key")
    home = tmp_path / "hh"
    (home / "profiles" / "coder").mkdir(parents=True)
    (home / "config.yaml").write_text(f"terminal:\n  backend: {hp.TERMINAL_OFF_BACKEND}\n")
    (home / "profiles" / "coder" / "config.yaml").write_text(
        f"terminal:\n  backend: {hp.TERMINAL_OFF_BACKEND}\n"
    )
    monkeypatch.setattr(hp, "read_terminal_state", lambda: True)
    hp.wire_named_profiles(home)
    doc = yaml.safe_load((home / "profiles" / "coder" / "config.yaml").read_text())
    assert doc["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND


def test_saved_backend_is_cleared_only_after_a_verified_restore() -> None:
    S, K = hp.TERMINAL_OFF_BACKEND, hp.TERMINAL_SAVED_BACKEND_KEY
    # off over ssh remembers it
    assert hp._terminal_saved_layer(
        terminal_enabled=False, before_backend="ssh", after_backend=S, saved_backend=None
    ) == {"hal0": {K: "ssh"}}
    # opt-in whose config set landed: cleared
    assert hp._terminal_saved_layer(
        terminal_enabled=True, before_backend=S, after_backend="ssh", saved_backend="ssh"
    ) == {"hal0": {K: ""}}
    # opt-in whose config set failed (sentinel still there): memory kept
    assert (
        hp._terminal_saved_layer(
            terminal_enabled=True, before_backend=S, after_backend=S, saved_backend="ssh"
        )
        == {}
    )


def test_opt_in_that_leaves_the_sentinel_fails_the_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.agents._hermes_fakes import apply_hermes_config_cli

    class _Done:
        def __init__(self, rc: int) -> None:
            self.returncode, self.stdout, self.stderr = rc, "", "boom" if rc else ""

    def flaky_run(argv, *_a, env=None, **_kw):
        argv = list(argv)
        if "terminal.backend" in argv:
            return _Done(1)
        apply_hermes_config_cli(argv, env)
        return _Done(0)

    home = tmp_path / "hh"
    home.mkdir()
    (home / "config.yaml").write_text(
        f"terminal:\n  backend: {hp.TERMINAL_OFF_BACKEND}\nhal0:\n  {hp.TERMINAL_SAVED_BACKEND_KEY}: ssh\n"
    )
    monkeypatch.setenv(hp.HERMES_TERMINAL_ENV, "1")
    monkeypatch.setattr(hp, "TERMINAL_STATE_PATH", tmp_path / "state" / "hermes-terminal-tool")
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_kwargs: {"model": "p", "base_url": "u", "context_length": 8000},
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no-such-overrides.yaml")
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.InstallIO(fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=flaky_run)
    out = hp._phase_config_write(hp._StepCtx(state=hp.BootstrapState(hermes_home=str(home)), io=io))
    assert out.status == hp.PhaseStatus.FAIL
    assert out.reason is not None and "still" in out.reason
    doc = yaml.safe_load((home / "config.yaml").read_text())
    assert doc["hal0"][hp.TERMINAL_SAVED_BACKEND_KEY] == "ssh"  # memory kept for the retry


def test_profile_wire_phase_fails_on_a_served_symlink_profile_when_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hp,
        "wire_named_profiles",
        lambda home, terminal_enabled=None: {
            "wired": ["coder"],
            "changed": [],
            "skipped": {"linked": "symlink", "quiet": "opted out"},
            "errors": {},
        },
    )
    state = hp.BootstrapState(hermes_home=str(tmp_path), terminal_enabled=False)
    out = hp._phase_profile_mcp_wire(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert out.status == hp.PhaseStatus.FAIL
    assert out.reason is not None and "linked" in out.reason and "quiet" not in out.reason


def test_cli_restarts_hermes_when_the_report_says_the_gate_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    import hal0.cli.agent_commands as ac

    report = tmp_path / "provision.json"
    monkeypatch.setattr(ac, "_agent_provision_state_file", lambda name: report)
    report.write_text(
        json.dumps({"phases": {"config_write": {"details": {"terminal_backend_changed": True}}}})
    )
    assert ac._terminal_gate_changed_in_last_report() is True
    report.write_text(
        json.dumps({"phases": {"config_write": {"details": {"terminal_backend_changed": False}}}})
    )
    assert ac._terminal_gate_changed_in_last_report() is False
    report.unlink()
    assert ac._terminal_gate_changed_in_last_report() is False


def test_a_lost_sentinel_fails_the_config_write_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial `config set` that dropped the security-critical key is a FAIL, not OK."""
    from tests.agents._hermes_fakes import apply_hermes_config_cli

    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)

    class _Done:
        def __init__(self, rc: int) -> None:
            self.returncode, self.stdout, self.stderr = rc, "", "boom" if rc else ""

    def flaky_run(argv, *_a, env=None, **_kw):
        argv = list(argv)
        if "terminal.backend" in argv:
            return _Done(1)
        apply_hermes_config_cli(argv, env)
        return _Done(0)

    monkeypatch.setattr(hp, "TERMINAL_STATE_PATH", tmp_path / "state" / "hermes-terminal-tool")
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_kwargs: {"model": "p", "base_url": "u", "context_length": 8000},
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no-such-overrides.yaml")
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.InstallIO(fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=flaky_run)
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    out = hp._phase_config_write(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.FAIL
    assert out.reason is not None and "terminal.backend" in out.reason and "#2878" in out.reason


def test_wire_named_profiles_gates_an_mcp_opted_out_profile_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`hal0: {wire_mcp: false}` opts out of MCP wiring, not of the box's terminal decision."""
    import yaml

    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: "svc-key")
    home = tmp_path / "hh"
    (home / "profiles" / "quiet").mkdir(parents=True)
    (home / "profiles" / "quiet" / "config.yaml").write_text(
        "hal0:\n  wire_mcp: false\nterminal:\n  backend: ssh\n"
    )
    monkeypatch.setattr(hp, "read_terminal_state", lambda: False)
    report = hp.wire_named_profiles(home)
    doc = yaml.safe_load((home / "profiles" / "quiet" / "config.yaml").read_text())
    assert doc["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND
    assert doc["hal0"] == {"wire_mcp": False, hp.TERMINAL_SAVED_BACKEND_KEY: "ssh"}
    assert "mcp_servers" not in doc and "memory" not in doc
    assert report["skipped"].get("quiet") == "opted out"
    assert report["changed"] == ["quiet"]
    assert set(hp.TERMINAL_OFF_TOOLSETS) <= set(doc["agent"]["disabled_toolsets"])
    monkeypatch.setattr(hp, "read_terminal_state", lambda: True)
    hp.wire_named_profiles(home)
    doc = yaml.safe_load((home / "profiles" / "quiet" / "config.yaml").read_text())
    assert doc["terminal"]["backend"] == "ssh"
    assert not set(hp.TERMINAL_OFF_TOOLSETS) & set(doc["agent"]["disabled_toolsets"])


def test_brain_phase_fails_closed_when_an_off_box_cannot_gate_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: "svc-key")
    monkeypatch.setattr(
        hp,
        "_merge_profile_config",
        lambda path, desired: {"wired": False, "changed": False, "error": "read-only"},
    )
    state = hp.BootstrapState(hermes_home=str(tmp_path), terminal_enabled=False)
    out = hp._phase_brain_profile_mcp_wire(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert out.status == hp.PhaseStatus.FAIL and out.reason is not None and "#2878" in out.reason
    state.terminal_enabled = True
    out = hp._phase_brain_profile_mcp_wire(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert out.status == hp.PhaseStatus.OK


def test_brain_phase_without_an_applied_decision_trusts_the_root_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hal0-api's startup seed builds a fresh BootstrapState; a root sentinel plus a
    stale marker saying on must not restore the brain profile's terminal."""
    import yaml

    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: "svc-key")
    home = tmp_path / "hh"
    (home / "profiles" / "hal0-brain").mkdir(parents=True)
    (home / "config.yaml").write_text(f"terminal:\n  backend: {hp.TERMINAL_OFF_BACKEND}\n")
    (home / "profiles" / "hal0-brain" / "config.yaml").write_text(
        f"terminal:\n  backend: {hp.TERMINAL_OFF_BACKEND}\n"
    )
    monkeypatch.setattr(hp, "read_terminal_state", lambda: True)
    out = hp._phase_brain_profile_mcp_wire(
        hp._StepCtx(state=hp.BootstrapState(hermes_home=str(home)), io=hp.InstallIO())
    )
    assert out.status == hp.PhaseStatus.OK
    doc = yaml.safe_load((home / "profiles" / "hal0-brain" / "config.yaml").read_text())
    assert doc["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND


def test_try_restart_hermes_consumers_bounces_both_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class _Done:
        def __init__(self, rc: int) -> None:
            self.returncode, self.stderr = rc, "boom" if rc else ""

    def run(argv, **_kw):
        calls.append(list(argv))
        return _Done(1 if "hermes-gateway" in " ".join(argv) else 0)

    monkeypatch.setattr(hp.os, "geteuid", lambda: 999)
    failed = hp.try_restart_hermes_consumers(run=run, which=lambda name: "/bin/systemctl")
    assert failed == ["hermes-gateway.service"]
    assert any("try-restart" in " ".join(c) for c in calls) and len(calls) == 2
    assert hp.try_restart_hermes_consumers(run=run, which=lambda name: None) == []


def test_update_backstop_bounces_hermes_when_the_gate_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.components import hermes_arm

    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "agent:\n  system_prompt: keep\nterminal:\n  backend: local\n"
    )
    monkeypatch.setattr(hermes_arm, "var_lib", lambda: tmp_path)
    monkeypatch.setattr(hp, "read_terminal_state", lambda: False)
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "none.yaml")
    bounced: list[int] = []
    monkeypatch.setattr(hp, "try_restart_hermes_consumers", lambda: bounced.append(1) or [])
    result: dict = {}
    hermes_arm._config_backstop(result, job_id="j")
    assert bounced == [1]
    assert any("restarted to apply the terminal posture" in d for d in result["detail"])
    # a second pass changes nothing and bounces nothing
    result = {}
    hermes_arm._config_backstop(result, job_id="j")
    assert bounced == [1] and "note" not in result


def test_update_path_gates_the_brain_and_every_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release activation restarts the gateway, not hal0-api, so the invariants
    gate hal0-brain and the clones themselves (#2878 review)."""
    import yaml

    home = tmp_path / "hh"
    for name in ("hal0-brain", "coder"):
        (home / "profiles" / name).mkdir(parents=True)
        (home / "profiles" / name / "config.yaml").write_text("terminal:\n  backend: local\n")
    (home / "config.yaml").write_text("agent:\n  system_prompt: keep\n")
    monkeypatch.setattr(hp, "read_terminal_state", lambda: False)
    assert hp.apply_release_config_invariants(home, overrides_path=tmp_path / "none.yaml") is True
    for name in ("hal0-brain", "coder"):
        doc = yaml.safe_load((home / "profiles" / name / "config.yaml").read_text())
        assert doc["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND, name
        assert set(hp.TERMINAL_OFF_TOOLSETS) <= set(doc["agent"]["disabled_toolsets"]), name
    assert hp.apply_release_config_invariants(home, overrides_path=tmp_path / "none.yaml") is False


def test_update_backstop_bounces_when_only_a_profile_backend_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.components import hermes_arm

    home = tmp_path / ".hermes"
    (home / "profiles" / "hal0-brain").mkdir(parents=True)
    (home / "config.yaml").write_text(
        f"agent:\n  system_prompt: keep\nterminal:\n  backend: {hp.TERMINAL_OFF_BACKEND}\n"
    )
    (home / "profiles" / "hal0-brain" / "config.yaml").write_text("terminal:\n  backend: local\n")
    monkeypatch.setattr(hermes_arm, "var_lib", lambda: tmp_path)
    monkeypatch.setattr(hp, "read_terminal_state", lambda: False)
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "none.yaml")
    bounced: list[int] = []
    monkeypatch.setattr(hp, "try_restart_hermes_consumers", lambda: bounced.append(1) or [])
    result: dict = {}
    hermes_arm._config_backstop(result, job_id="j")
    assert bounced == [1]


def test_root_reexec_child_leaves_the_restart_to_the_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    import hal0.cli.agent_commands as ac

    monkeypatch.setattr(hp, "bootstrap_cli", lambda **_kw: 0)
    monkeypatch.setattr(ac, "_terminal_gate_changed_in_last_report", lambda: True)
    restarted: list[int] = []
    monkeypatch.setattr(ac, "_restart_hermes_after_posture_change", lambda: restarted.append(1))
    monkeypatch.setattr(os, "geteuid", lambda: 999)
    monkeypatch.setenv(ac._RESTART_OWNER_ENV, "parent")
    assert ac._provision_hermes(repair=True) == 0
    assert restarted == []
    monkeypatch.delenv(ac._RESTART_OWNER_ENV, raising=False)
    assert ac._provision_hermes(repair=True) == 0
    assert restarted == [1]


def test_update_path_fails_closed_when_a_profile_cannot_take_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "hh"
    (home / "profiles" / "coder").mkdir(parents=True)
    (home / "profiles" / "coder" / "config.yaml").write_text("terminal:\n  backend: local\n")
    (home / "config.yaml").write_text("agent:\n  system_prompt: keep\n")
    monkeypatch.setattr(hp, "read_terminal_state", lambda: False)
    monkeypatch.setattr(
        hp,
        "_merge_profile_config",
        lambda path, desired: {"wired": False, "changed": False, "error": "ro"},
    )
    with pytest.raises(hp.TerminalGateError, match="coder"):
        hp.apply_release_config_invariants(home, overrides_path=tmp_path / "none.yaml")
    from hal0.components import hermes_arm

    monkeypatch.setattr(hermes_arm, "var_lib", lambda: tmp_path)
    (tmp_path / ".hermes").symlink_to(home)
    result: dict = {}
    hermes_arm._config_backstop(result, job_id="j")
    assert any("terminal gate not applied" in d for d in result["detail"])


def test_profile_phase_records_a_terminal_backend_change_for_the_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    import hal0.cli.agent_commands as ac

    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: "svc-key")
    home = tmp_path / "hh"
    (home / "profiles" / "coder").mkdir(parents=True)
    (home / "profiles" / "coder" / "config.yaml").write_text("terminal:\n  backend: local\n")
    (home / "config.yaml").write_text(f"terminal:\n  backend: {hp.TERMINAL_OFF_BACKEND}\n")
    state = hp.BootstrapState(hermes_home=str(home), terminal_enabled=False)
    out = hp._phase_profile_mcp_wire(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert out.details["terminal_backend_changed"] is True
    out = hp._phase_profile_mcp_wire(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert out.details["terminal_backend_changed"] is False
    report = tmp_path / "provision.json"
    monkeypatch.setattr(ac, "_agent_provision_state_file", lambda name: report)
    report.write_text(
        json.dumps(
            {
                "phases": {
                    "config_write": {"details": {"terminal_backend_changed": False}},
                    "profile_mcp_wire": {"details": {"terminal_backend_changed": True}},
                }
            }
        )
    )
    assert ac._terminal_gate_changed_in_last_report() is True


def test_a_displaced_sentinel_is_reported_as_terminal_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """overrides.yaml wins by design; the status line must not call that box off."""
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("terminal:\n  backend: local\n")
    monkeypatch.delenv(hp.HERMES_TERMINAL_ENV, raising=False)
    monkeypatch.setattr(hp, "TERMINAL_STATE_PATH", tmp_path / "state" / "hermes-terminal-tool")
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
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    out = hp._phase_config_write(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.FAIL
    assert out.details["terminal_tool"] == "on"
    assert out.details["terminal_tool_decision"] == "off"
