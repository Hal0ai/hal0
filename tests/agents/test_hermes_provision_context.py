"""Linear-installer plumbing: InstallIO / _StepCtx / InstallReport.

Replaces the retired PhaseIO / PhaseContext / Phase-graph contract. Pins:

* ``InstallIO`` defaults bind the real IO seams (default-constructed = production)
  and it is frozen.
* ``_StepCtx.output_of(name)`` returns an earlier step's details, empty when the
  step hasn't run — no needs-graph enforcement.
* the ``_INSTALL_STEPS`` order (mcp_wire before config_write) and the
  ``report.converged`` signal.
* RELOCATE(brain-lane) landed: persona_seed, namespace_register,
  brain_profile_seed, brain_profile_mcp_wire, self_report no longer appear
  in ``_INSTALL_STEPS`` at all — they run from the hal0-api boot lifespan
  instead (src/hal0/api/__init__.py). The step functions stay importable
  and directly callable for unit coverage + lifespan reuse.
"""

from __future__ import annotations

import inspect

import pytest

from hal0.agents import hermes_provision as hp

# ── InstallIO ────────────────────────────────────────────────────────────────


def test_installio_defaults_bind_real_seams() -> None:
    io = hp.InstallIO()
    assert io.http_get is hp._http_get
    assert io.fetch_slots is hp._fetch_slots
    assert io.fetch_model_contexts is hp._fetch_model_contexts
    assert io.probe_mcp_server is hp._probe_mcp_server
    assert io.mcp_memory_call is hp._mcp_memory_call
    assert io.install_venv is hp._install_venv
    assert io.read_env_probe is hp._read_env_probe
    assert io.load_config is hp._load_hal0_config
    assert io.run is hp.subprocess.run


def test_installio_is_frozen() -> None:
    io = hp.InstallIO()
    with pytest.raises(AttributeError):
        io.fetch_slots = lambda: []  # type: ignore[misc]


# ── _StepCtx.output_of ───────────────────────────────────────────────────────


def test_output_of_returns_prior_step_details() -> None:
    ctx = hp._StepCtx(
        state=hp.BootstrapState(),
        io=hp.InstallIO(),
        _prior={"mcp_wire": {"rendered_servers": [{"name": "hal0-admin"}]}},
    )
    assert ctx.output_of("mcp_wire") == {"rendered_servers": [{"name": "hal0-admin"}]}


def test_output_of_empty_when_step_has_not_run() -> None:
    ctx = hp._StepCtx(state=hp.BootstrapState(), io=hp.InstallIO())
    assert ctx.output_of("mcp_wire") == {}


# ── pipeline shape ───────────────────────────────────────────────────────────


def test_install_steps_ordering() -> None:
    names = [name for name, _fn in hp._INSTALL_STEPS]
    assert names.index("mcp_wire") < names.index("config_write")
    # model_automap is gone; ownership_reconcile is gone.
    assert "model_automap" not in names
    assert "ownership_reconcile" not in names


def test_retired_machinery_is_gone() -> None:
    for attr in (
        "PHASES",
        "Phase",
        "PhaseContext",
        "PhaseIO",
        "PhaseNeedError",
        "context_for",
        "RunResult",
        "run",
        "_validate_phase_graph",
    ):
        assert not hasattr(hp, attr), f"{attr} should be deleted"


_RELOCATED_BRAIN_LANE_STEPS = frozenset(
    {
        "persona_seed",
        "namespace_register",
        "brain_profile_seed",
        "brain_profile_mcp_wire",
        "self_report",
    }
)


def test_brain_lane_steps_relocated_out_of_install() -> None:
    """RELOCATE(brain-lane) landed: the 5 brain-lane steps no longer run as
    part of the linear install pipeline — they moved into the hal0-api boot
    lifespan (``_boot_seeds`` for persona_seed/brain_profile_mcp_wire, the
    terminal ``_boot_brain_lane`` phase for namespace_register/
    brain_profile_seed/self_report; see src/hal0/api/__init__.py).

    The step FUNCTIONS themselves stay put and importable — the lifespan
    phases call them directly (no copy-pasted body) via the same
    InstallIO/_StepCtx seam this test module's other tests already use to
    call them in isolation. Only their ``_INSTALL_STEPS`` membership moved.
    """
    names = {name for name, _fn in hp._INSTALL_STEPS}
    assert _RELOCATED_BRAIN_LANE_STEPS.isdisjoint(names), (
        f"still in _INSTALL_STEPS after relocation: {_RELOCATED_BRAIN_LANE_STEPS & names}"
    )
    assert callable(hp._phase_persona_seed)
    assert callable(hp._phase_namespace_register)
    assert callable(hp._phase_brain_profile_seed)
    assert callable(hp._phase_brain_profile_mcp_wire)
    assert callable(hp._phase_self_report)
    # The _INSTALL_STEPS table + the InstallIO docstring above it record the
    # relocation explicitly (RELOCATE(brain-lane) — LANDED), so a future
    # reader can't miss it.
    src = inspect.getsource(hp)
    assert src.count("RELOCATE(brain-lane)") >= len(_RELOCATED_BRAIN_LANE_STEPS)
    # _BRAIN_LANE_STEPS (the convergence-exemption set) is retired along with
    # the markers — nothing in _INSTALL_STEPS needs the exemption anymore.
    assert not hasattr(hp, "_BRAIN_LANE_STEPS")


def test_relocated_steps_no_longer_special_cased_in_step_changed() -> None:
    """_step_changed has no brain-lane exemption branch left to test — a
    result carrying details["changed"]=True for one of the relocated names
    now just follows the normal path (there's no caller left that would
    ever pass one of these names in, since they're gone from
    _INSTALL_STEPS, but the function itself should not silently resurrect
    a special case for them)."""
    result = hp.PhaseResult(status=hp.PhaseStatus.OK, details={"changed": True})
    for name in _RELOCATED_BRAIN_LANE_STEPS:
        assert hp._step_changed(name, result) is True
    assert hp._step_changed("config_write", result) is True


# ── InstallReport ────────────────────────────────────────────────────────────


def test_install_report_converged_and_failed() -> None:
    report = hp.InstallReport(hermes_home="/hh", venv="/v", agent_id="hermes")
    report.steps = [
        hp.InstallStep("preflight", "ok"),
        hp.InstallStep("install", "ok", changed=True),
        hp.InstallStep("voice_wire", "skip"),
    ]
    assert report.ok is True
    assert report.failed == []
    assert report.mutated == ["install"]
    assert report.converged is False

    report.steps.append(hp.InstallStep("config_write", "fail", reason="boom"))
    assert report.ok is False
    assert report.failed == ["config_write"]
    assert report.step("config_write").reason == "boom"


# ── repair reaches persona_seed ──────────────────────────────────────────────


def test_persona_seed_overwrites_on_repair(tmp_path) -> None:
    from hal0.agents import personas as _personas

    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    ctx = hp._StepCtx(state=state, io=hp.InstallIO())
    out = hp._phase_persona_seed(ctx)
    assert out.status == hp.PhaseStatus.OK

    persona_path = tmp_path / "hh" / "personas" / "hermes.toml"
    persona_path.write_text('[persona]\nid = "hermes"\ndisplay_name = "Custom"\n', encoding="utf-8")
    # No repair → operator edit survives.
    hp._phase_persona_seed(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert _personas.load_persona("hermes", root=persona_path.parent).display_name == "Custom"
    # repair → seeds rewritten.
    hp._phase_persona_seed(hp._StepCtx(state=state, io=hp.InstallIO(), repair=True))
    assert _personas.load_persona("hermes", root=persona_path.parent).display_name == "Hermes"
