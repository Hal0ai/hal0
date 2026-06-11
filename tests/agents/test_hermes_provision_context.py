"""PhaseContext / PhaseIO / Phase-graph plumbing (issue #702).

Pins the explicit-pipeline contract:

* ``PhaseIO`` defaults bind the real IO seams — constructing it with no
  arguments must be production behaviour, byte-for-byte.
* ``PhaseContext.output_of(name)`` raises unless the calling phase
  declared ``name`` in its needs; declared reads return the target
  phase's checkpoint ``details`` dict (empty when absent — the
  cross-run config_write→mcp_wire read on a fresh install).
* ``_validate_phase_graph`` rejects, at import time, a PHASES ordering
  that violates a declared need.
"""

from __future__ import annotations

import pytest

from hal0.agents import hermes_provision as hp


def _ok_phase(_ctx: hp.PhaseContext) -> hp.PhaseResult:
    return hp.PhaseResult(status=hp.PhaseStatus.OK)


# ── PhaseIO ──────────────────────────────────────────────────────────────────


def test_phaseio_defaults_bind_real_seams() -> None:
    """A default-constructed PhaseIO is the production wiring."""
    io = hp.PhaseIO()
    assert io.http_get is hp._http_get
    assert io.fetch_slots is hp._fetch_slots
    assert io.fetch_model_contexts is hp._fetch_model_contexts
    assert io.probe_mcp_server is hp._probe_mcp_server
    assert io.mcp_memory_call is hp._mcp_memory_call
    assert io.install_venv is hp._install_venv
    assert io.read_env_probe is hp._read_env_probe
    assert io.run is hp.subprocess.run


def test_phaseio_is_frozen() -> None:
    io = hp.PhaseIO()
    with pytest.raises(AttributeError):
        io.fetch_slots = lambda: []  # type: ignore[misc]


# ── PhaseContext.output_of ───────────────────────────────────────────────────


def test_output_of_raises_on_undeclared_need() -> None:
    state = hp.BootstrapState()
    state.phases["mcp_wire"] = {"status": "ok", "details": {"rendered_servers": []}}
    ctx = hp.PhaseContext(state=state, phase_name="config_write")
    with pytest.raises(hp.PhaseNeedError, match=r"config_write.*mcp_wire"):
        ctx.output_of("mcp_wire")


def test_output_of_returns_declared_phase_details() -> None:
    state = hp.BootstrapState()
    state.phases["smoke_tests"] = {
        "status": "ok",
        "details": {"failures": ["chat_completions: 503"]},
    }
    ctx = hp.PhaseContext(
        state=state,
        phase_name="self_report",
        allowed_needs=frozenset({"smoke_tests"}),
    )
    assert ctx.output_of("smoke_tests") == {"failures": ["chat_completions: 503"]}


def test_output_of_returns_empty_dict_when_target_never_ran() -> None:
    """Fresh-install posture: config_write reads mcp_wire's PREVIOUS-run
    checkpoint, which doesn't exist on run #1 — that's an empty dict,
    not an error (the phase falls back to its default inventory)."""
    ctx = hp.PhaseContext(
        state=hp.BootstrapState(),
        phase_name="config_write",
        allowed_needs=frozenset({"mcp_wire"}),
    )
    assert ctx.output_of("mcp_wire") == {}


# ── Phase graph validation ───────────────────────────────────────────────────


def test_validate_phase_graph_accepts_ordered_needs() -> None:
    phases = [
        hp.Phase("a", _ok_phase),
        hp.Phase("b", _ok_phase, needs=("a",)),
        hp.Phase("c", _ok_phase, needs_previous=("d",)),
        hp.Phase("d", _ok_phase, needs=("a", "b")),
    ]
    hp._validate_phase_graph(phases)  # must not raise


def test_validate_phase_graph_rejects_need_that_follows_reader() -> None:
    phases = [
        hp.Phase("reader", _ok_phase, needs=("target",)),
        hp.Phase("target", _ok_phase),
    ]
    with pytest.raises(ValueError, match=r"reader.*target"):
        hp._validate_phase_graph(phases)


def test_validate_phase_graph_rejects_unknown_need() -> None:
    phases = [hp.Phase("reader", _ok_phase, needs=("ghost",))]
    with pytest.raises(ValueError, match="ghost"):
        hp._validate_phase_graph(phases)


def test_validate_phase_graph_rejects_unknown_previous_need() -> None:
    phases = [hp.Phase("reader", _ok_phase, needs_previous=("ghost",))]
    with pytest.raises(ValueError, match="ghost"):
        hp._validate_phase_graph(phases)


def test_validate_phase_graph_rejects_previous_need_that_precedes_reader() -> None:
    """A needs_previous target that runs BEFORE its reader is a plain
    same-run need mislabelled as a cross-run read — reject loudly."""
    phases = [
        hp.Phase("target", _ok_phase),
        hp.Phase("reader", _ok_phase, needs_previous=("target",)),
    ]
    with pytest.raises(ValueError, match="needs_previous"):
        hp._validate_phase_graph(phases)


def test_validate_phase_graph_rejects_duplicate_phase_names() -> None:
    phases = [hp.Phase("a", _ok_phase), hp.Phase("a", _ok_phase)]
    with pytest.raises(ValueError, match="duplicate"):
        hp._validate_phase_graph(phases)


# ── The real PHASES graph ────────────────────────────────────────────────────


def test_phases_declare_the_locked_needs_graph() -> None:
    """#702 locked the 4 cross-phase edges; pin them exactly."""
    by_name = {p.name: p for p in hp.PHASES}
    # config_write reads mcp_wire's PREVIOUS-run checkpoint (mcp_wire
    # runs after it in the list) — a cross-run edge, not a same-run one.
    assert by_name["config_write"].needs == ()
    assert by_name["config_write"].needs_previous == ("mcp_wire",)
    assert by_name["model_automap"].needs == ("mcp_wire",)
    assert by_name["voice_wire"].needs == ("mcp_wire",)
    assert by_name["self_report"].needs == ("smoke_tests",)
    # No other phase declares anything.
    declared = {p.name for p in hp.PHASES if p.needs or p.needs_previous}
    assert declared == {"config_write", "model_automap", "voice_wire", "self_report"}


def test_real_phases_graph_validates() -> None:
    hp._validate_phase_graph(hp.PHASES)  # import already ran this; pin it anyway


def test_phases_permutation_violating_needs_fails_loudly() -> None:
    """Moving self_report ahead of smoke_tests must be rejected."""
    permuted = sorted(hp.PHASES, key=lambda p: 0 if p.name == "self_report" else 1)
    with pytest.raises(ValueError, match=r"self_report.*smoke_tests"):
        hp._validate_phase_graph(permuted)


def test_phases_permutation_violating_needs_previous_fails_loudly() -> None:
    """Moving mcp_wire ahead of config_write flips the cross-run edge
    into a same-run one — the mislabelled declaration must fail."""
    permuted = sorted(hp.PHASES, key=lambda p: 0 if p.name == "mcp_wire" else 1)
    with pytest.raises(ValueError, match="needs_previous"):
        hp._validate_phase_graph(permuted)


# ── context_for + undeclared reads through real phases ──────────────────────


def test_context_for_carries_declared_needs() -> None:
    ctx = hp.context_for("self_report", hp.BootstrapState())
    assert ctx.allowed_needs == frozenset({"smoke_tests"})
    assert ctx.phase_name == "self_report"
    assert ctx.repair is False


def test_context_for_rejects_unknown_phase() -> None:
    with pytest.raises(KeyError, match="no_such_phase"):
        hp.context_for("no_such_phase", hp.BootstrapState())


def test_self_report_with_undeclared_needs_raises(tmp_path) -> None:
    """A phase body that reads a checkpoint without its declared needs
    must blow up loudly — the read is never silently empty."""
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    bare_ctx = hp.PhaseContext(state=state, phase_name="self_report")  # no allowed_needs
    with pytest.raises(hp.PhaseNeedError):
        hp._phase_self_report(bare_ctx)


# ── ctx.repair replaces the _repair_flag sentinel ────────────────────────────


def test_repair_flag_sentinel_is_gone() -> None:
    """The smuggled state.phases['_repair_flag'] sentinel is deleted —
    no run ever stashes or strips it again."""
    assert not hasattr(hp, "_REPAIR_FLAG")
    import inspect

    assert "_repair_flag" not in inspect.getsource(hp.run)


def test_persona_seed_overwrites_on_ctx_repair(tmp_path) -> None:
    from hal0.agents import personas as P

    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    out = hp._phase_persona_seed(hp.context_for("persona_seed", state))
    assert out.status == hp.PhaseStatus.OK
    persona_path = tmp_path / "hh" / "personas" / "hermes.toml"
    persona_path.write_text('[persona]\nid = "hermes"\ndisplay_name = "Custom"\n', encoding="utf-8")
    # No repair → operator edit survives.
    hp._phase_persona_seed(hp.context_for("persona_seed", state))
    assert P.load_persona("hermes", root=persona_path.parent).display_name == "Custom"
    # repair → seeds rewritten.
    hp._phase_persona_seed(hp.context_for("persona_seed", state, repair=True))
    assert P.load_persona("hermes", root=persona_path.parent).display_name == "Hermes"
