"""Convergence contract: ``install_hermes`` is idempotent.

The promise: re-running ``install_hermes`` (or ``hal0 agent reprovision hermes``)
over an already-provisioned box converges without drift — byte-equal config.yaml
+ persona TOMLs, and a *second run that mutates nothing* (every host-mutating
step reports ``changed=False`` → ``report.converged``). This replaces the old
byte-equal-provision.json checkpoint contract, which is gone with the pipeline.

Every external touchpoint (HTTP, venv install, MCP probes, memory POSTs,
subprocess) is faked so the runs are hermetic and the two-run comparison is
byte-exact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes_provision as hp
from hal0.agents import personas as P

from ._hermes_fakes import install_io, sandbox_hermes_paths


@pytest.fixture
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Sandbox every host path constant under tmp_path; return (hermes_home, venv)."""
    return sandbox_hermes_paths(hp, tmp_path, monkeypatch)


def _install(target: tuple[Path, Path], *, io=None, repair=False, record=None, state_root=None):
    home, venv = target
    # RELOCATE(brain-lane): persona seeding now runs in the hal0-api boot
    # lifespan's _boot_seeds phase, which in production always runs before
    # `hal0 agent install hermes` (install talks to the already-running API
    # over loopback HTTP). Mirror that ordering hermetically so
    # install_hermes sees the personas it now expects to already exist —
    # idempotent (overwrite=False), so calling it again before a second
    # _install() in the same test is a no-op once seeded.
    P.seed_default_personas(agent_id="hermes-agent", root=home / "personas")
    return hp.install_hermes(
        hermes_home=home,
        venv=venv,
        agent_id="hermes-agent",
        io=io if io is not None else install_io(hp, record=record),
        repair=repair,
        state_root=state_root,
    )


def _config(target: tuple[Path, Path]) -> str:
    return (target[0] / "config.yaml").read_text(encoding="utf-8")


# ── the double-run convergence contract ──────────────────────────────────────


def test_double_run_zero_mutating_steps(target: tuple[Path, Path], tmp_path: Path) -> None:
    """Run #1 provisions; run #2 (recorded fakes) mutates ZERO steps.

    The convergence contract: a second ``install_hermes`` over the converged box
    reports every host-mutating step ``changed=False`` (``report.mutated == []``)
    and leaves config.yaml + personas byte-identical.
    """
    state_root = tmp_path / "state"
    io = install_io(hp)

    r1 = _install(target, io=io, state_root=state_root)
    assert r1.ok, r1.failed
    # Run #1 genuinely provisions — several steps mutate.
    assert set(r1.mutated) >= {"install", "config_write", "context_link"}
    config_1 = _config(target)

    r2 = _install(target, io=io, state_root=state_root)
    assert r2.ok, r2.failed
    assert r2.mutated == [], f"second run mutated {r2.mutated}"
    assert r2.converged
    assert _config(target) == config_1, "config.yaml drifted on re-run"


def test_two_consecutive_runs_converge(target: tuple[Path, Path]) -> None:
    """config.yaml + persona TOMLs + active pointer are byte-identical run-to-run."""
    io = install_io(hp)
    r1 = _install(target, io=io)
    config_1 = _config(target)

    persona_root = target[0] / "personas"
    hermes_1 = (persona_root / "hermes.toml").read_text(encoding="utf-8")
    brain_1 = (persona_root / "hal0-brain.toml").read_text(encoding="utf-8")
    active_1 = (persona_root / "active.txt").read_text(encoding="utf-8")

    r2 = _install(target, io=io)
    assert r1.ok and r2.ok
    assert _config(target) == config_1
    assert (persona_root / "hermes.toml").read_text(encoding="utf-8") == hermes_1
    assert (persona_root / "hal0-brain.toml").read_text(encoding="utf-8") == brain_1
    assert (persona_root / "active.txt").read_text(encoding="utf-8") == active_1
    # The retired coder seed must not reappear.
    assert not (persona_root / "coder.toml").exists()
    # Every step ends ok or skip on both runs — except smoke_tests, which may
    # legitimately land on "warn" (#1793): it's diagnostic-only and never
    # fails the install, but a phase that recorded a real probe failure must
    # say so rather than reporting a silent "ok".
    for step in r2.steps:
        allowed = {"ok", "skip", "warn"} if step.name == "smoke_tests" else {"ok", "skip"}
        assert step.status in allowed, f"{step.name}: {step.status}"


def test_report_written_for_agent_status(target: tuple[Path, Path], tmp_path: Path) -> None:
    """install_hermes drops a flat last-run report `hal0 agent status` can render."""
    import json

    state_root = tmp_path / "state"
    _install(target, state_root=state_root)
    report = state_root / "provision.json"
    assert report.exists()
    data = json.loads(report.read_text())
    assert "phases" in data and "install" in data["phases"]
    assert data["phases"]["install"]["status"] == "ok"
    assert data["completed_at"]  # ok run stamps it


# ── repair semantics ─────────────────────────────────────────────────────────
#
# RELOCATE(brain-lane) — LANDED: persona_seed no longer runs as part of
# `install_hermes()`/`--repair` at all (it moved to the hal0-api boot
# lifespan's _boot_seeds phase, which never forces an overwrite — an
# operator-chosen edit only gets reset by removing the persona TOML and
# restarting hal0-api). The direct-call unit coverage for
# `_phase_persona_seed`'s own overwrite=True/False behavior lives in
# tests/agents/test_hermes_provision_context.py::
# test_persona_seed_overwrites_on_repair — that function is unchanged and
# still exercises the real overwrite logic, just without going through
# `install_hermes()`.


# ── config.yaml content contracts ────────────────────────────────────────────


def test_config_yaml_contains_persona_prelude(target: tuple[Path, Path]) -> None:
    """The render carries the active persona's system_prompt_prelude + label."""
    _install(target)
    config = _config(target)
    assert "system_prompt_prelude" in config
    assert "personality:" in config


def test_config_yaml_contains_chat_slot_aliases(target: tuple[Path, Path]) -> None:
    """chat_slots appear as model_aliases routed through the STABLE gateway."""
    yaml = pytest.importorskip("yaml")
    _install(target)
    config = _config(target)
    assert "model_aliases:" in config
    cfg = yaml.safe_load(config)
    assert "chat" in cfg["model_aliases"] and "agent" in cfg["model_aliases"]
    assert "embed" not in cfg["model_aliases"], "embed slot leaked into chat aliases"
    for alias, entry in cfg["model_aliases"].items():
        assert entry["base_url"] == "http://127.0.0.1:8080/v1", (
            f"alias {alias} should route through the gateway, got {entry['base_url']}"
        )


def test_config_yaml_contains_mcp_servers(target: tuple[Path, Path]) -> None:
    """Rendered config carries both default MCP servers + the identity header."""
    _install(target)
    config = _config(target)
    assert "mcp_servers:" in config
    assert "hal0-admin:" in config and "hal0-memory:" in config
    assert "X-hal0-Agent: hermes-agent" in config


def test_config_yaml_contains_role_slot_blocks(target: tuple[Path, Path]) -> None:
    """delegation ← agent slot; auxiliary compaction group ← utility slot."""
    yaml = pytest.importorskip("yaml")
    _install(target)
    cfg = yaml.safe_load(_config(target))
    assert cfg["delegation"]["model"] == "qwen3-coder-test"
    assert cfg["delegation"]["provider"] == "custom"
    assert cfg["delegation"]["base_url"] == "http://127.0.0.1:8080/v1"
    for task in ("compression", "session_search", "title_generation"):
        assert cfg["auxiliary"][task]["model"] == "qwen3-utility-test"
        assert cfg["auxiliary"][task]["provider"] == "custom"
    assert cfg["auxiliary"]["vision"]["provider"] == "main"
    # No global model.context_length override; no custom_providers block.
    assert "context_length" not in cfg["model"]
    assert "custom_providers" not in cfg
    assert "bge-test" not in cfg.get("model_aliases", {})


# ── namespace_register dedup guards (#448 / #446) ────────────────────────────


def _register(state, io):
    return hp._phase_namespace_register(hp._StepCtx(state=state, io=io))


def test_namespace_register_skips_add_on_delete_count_mismatch() -> None:
    """A delete that pruned fewer ids than requested must NOT re-add the card."""
    state = hp.BootstrapState(agent_id="hermes-agent")
    add_calls: list[dict[str, Any]] = []

    def _mismatch(method, params, **_kw):
        tool = (params or {}).get("name", "")
        if tool == "memory_search":
            return {
                "ok": True,
                "result": {"items": [{"id": "prior-1", "metadata": {"agent_id": state.agent_id}}]},
            }
        if tool == "memory_add":
            add_calls.append(params)
            return {"ok": True, "result": {"id": "x"}}
        if tool == "memory_delete":
            return {"ok": True, "result": {"deleted": 0}}
        return {"ok": True, "result": {}}

    result = _register(state, hp.InstallIO(mcp_memory_call=_mismatch))
    assert result.status == hp.PhaseStatus.OK
    assert result.details["registered"] is False
    assert result.details["refreshed_existing"] is False
    assert not add_calls, "card re-added despite a delete-count mismatch"
    assert any(f["site"] == "memory_layer" for f in result.details["fallbacks"])


def test_namespace_register_rewrites_when_delete_count_matches() -> None:
    """A matching delete count proceeds to the refresh (card re-added)."""
    state = hp.BootstrapState(agent_id="hermes-agent")
    add_calls: list[dict[str, Any]] = []

    def _matching(method, params, **_kw):
        tool = (params or {}).get("name", "")
        if tool == "memory_search":
            return {
                "ok": True,
                "result": {"items": [{"id": "prior-1", "metadata": {"agent_id": state.agent_id}}]},
            }
        if tool == "memory_add":
            add_calls.append(params)
            return {"ok": True, "result": {"id": "x"}}
        if tool == "memory_delete":
            return {"ok": True, "result": {"deleted": 1}}
        return {"ok": True, "result": {}}

    result = _register(state, hp.InstallIO(mcp_memory_call=_matching))
    assert result.status == hp.PhaseStatus.OK
    assert result.details["registered"] is True
    assert result.details["refreshed_existing"] is True
    assert len(add_calls) == 1


# ── pipeline ordering ────────────────────────────────────────────────────────


def test_persona_seed_relocated_out_of_install_pipeline() -> None:
    """RELOCATE(brain-lane): persona_seed no longer runs inside install_hermes
    at all — config_write's active-persona read now depends on the hal0-api
    boot lifespan having already seeded personas (see _install()'s
    pre-seed call in this file's fixtures, which mirrors that ordering)."""
    names = [name for name, _fn in hp._INSTALL_STEPS]
    assert "persona_seed" not in names


def test_mcp_wire_runs_before_config_write() -> None:
    """mcp_wire probes before config_write renders, so the render sees live probes."""
    names = [name for name, _fn in hp._INSTALL_STEPS]
    assert names.index("mcp_wire") < names.index("config_write")
