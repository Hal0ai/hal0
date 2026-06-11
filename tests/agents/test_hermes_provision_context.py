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
