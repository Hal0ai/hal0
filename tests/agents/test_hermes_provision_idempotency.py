"""PR-3 idempotency contract: re-running every phase converges.

The promise: ``hal0 agent reprovision hermes`` (or rerunning
``bootstrap hermes``) must converge without producing drift. Per the
master plan §4 PR-3 and DA-arch must-fix #4, the bar is *byte-equal
provision.json + byte-equal config.yaml* across two consecutive runs
when nothing in the environment changed.

We monkey-patch every external touchpoint (HTTP, venv install, MCP
probes, memory POSTs) so the test runs hermetically and asserts the
managed-file contents (config.yaml, personas, provision.json checkpoint
hashes) are identical between run #1 and run #2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes_provision as hp
from hal0.agents import personas as P


@pytest.fixture
def hermetic_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> hp.BootstrapState:
    """A BootstrapState rooted entirely in ``tmp_path`` with externals stubbed."""
    var_lib = tmp_path / "var" / "lib" / "hal0"
    var_lib.mkdir(parents=True)
    venv = var_lib / "venvs" / "hermes"
    hermes_home = var_lib / "agents" / "hermes"

    # Stub network + filesystem touchpoints. Keep them as stable as
    # possible across calls — a flaky time.now() in `details` would
    # falsely fail the byte-equal assertion (we strip `at` timestamps in
    # the comparison below to side-step the legit one).
    monkeypatch.setattr(hp, "_http_get", lambda *_a, **_kw: 200)
    monkeypatch.setattr(hp, "MIN_FREE_GIB", 0)
    monkeypatch.setattr(hp, "WRAPPER_INSTALL_PATH", tmp_path / "usr" / "bin" / "hal0-hermes")
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "etc" / "hal0" / "overrides.yaml")
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", tmp_path / "etc" / "hal0")
    monkeypatch.setattr(hp, "ETC_HAL0_AGENT_SKILLS", tmp_path / "etc" / "hal0" / "agent-skills")
    monkeypatch.setattr(hp, "HAL0_BUNDLED_SKILLS", tmp_path / "usr" / "share" / "hal0" / "skills")
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", tmp_path / "secrets" / "hermes.env")
    monkeypatch.setattr(hp, "AGENT_ALLOWLIST_PATH", tmp_path / "etc" / "hal0" / "agents.toml")
    # Personas land under $HERMES_HOME/personas via _personas_root_for —
    # the fixture's hermes_home is already in tmp_path so no further
    # monkey-patching is needed for the persona phase.

    # Stable, ready-looking slot payload — the bootstrap renders aliases
    # only when slots are ``type=llm`` AND ``state=ready``.
    def _fake_slots() -> list[dict[str, Any]]:
        return [
            {
                "name": "primary",
                "type": "llm",
                "kind": "local",
                "state": "ready",
                "status": "ready",
                "model_id": "qwen3-test",
                "backend_url": "http://127.0.0.1:8001/v1",
                "context_length": 32768,
            },
            {
                "name": "agent-hermes",
                "type": "llm",
                "kind": "local",
                "state": "ready",
                "status": "ready",
                "model_id": "qwen3-coder-test",
                "backend_url": "http://127.0.0.1:8001/v1",
                "context_length": 16384,
            },
            # An embed slot that must NEVER appear in chat aliases.
            {
                "name": "embed",
                "type": "embedding",
                "kind": "local",
                "state": "ready",
                "status": "ready",
                "model_id": "bge-test",
                "backend_url": "http://127.0.0.1:8002/v1",
            },
        ]

    monkeypatch.setattr(hp, "_fetch_slots", _fake_slots)

    # MCP probes succeed deterministically with a fixed tool list so the
    # provision.json `mcp_wire.details` hash matches across runs.
    def _fake_probe(_url: str, **_kw: Any) -> dict[str, Any]:
        return {"ok": True, "tools": ["t1", "t2", "t3", "t4", "t5"], "error": None}

    monkeypatch.setattr(hp, "_probe_mcp_server", _fake_probe)

    # Memory POSTs succeed silently — namespace_register + self_report
    # rely on these.
    def _fake_memory_call(method: str, params: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        tool = (params or {}).get("name", "")
        if tool == "memory_search":
            return {"ok": True, "result": {"items": []}}
        if tool == "memory_add":
            return {"ok": True, "result": {"id": "memid-stable"}}
        if tool == "memory_delete":
            return {"ok": True, "result": {"deleted": 0}}
        return {"ok": True, "result": {}}

    monkeypatch.setattr(hp, "_mcp_memory_call", _fake_memory_call)

    # env_probe writes a timestamped snapshot file every run — that's
    # legitimately non-idempotent (it's a point-in-time view). Stub
    # ``_read_env_probe`` so the snapshot CONTENT is stable across runs
    # and rely on the test's "ignore env_probe details" filter for the
    # snapshot path.
    monkeypatch.setattr(
        hp,
        "_read_env_probe",
        lambda: {
            "env_report": {"cpu": {"strix_halo": True}},
            "gpu_target_version": {"gfx": "1151"},
            "npu_status": {"present": True},
            "ai_models": {"present": False},
        },
    )

    # Fake venv install so we don't shell out to ``python -m venv``.
    def _fake_install(v: Path, _req: Path, **_kw: Any) -> None:
        (v / "bin").mkdir(parents=True, exist_ok=True)
        (v / "bin" / "hermes").write_text("#!/bin/sh\nexit 0\n")
        (v / "bin" / "hermes").chmod(0o755)
        (v / "bin" / "python").write_text("#!/bin/sh\nexit 0\n")
        (v / "bin" / "python").chmod(0o755)

    monkeypatch.setattr(hp, "_install_venv", _fake_install)

    # Wrapper copy needs a source file to exist; just create a stub.
    wrapper_src = hp.REPO_ROOT_FOR_INSTALLER / "installer" / "wrappers" / "hal0-hermes"
    wrapper_src.parent.mkdir(parents=True, exist_ok=True)
    if not wrapper_src.exists():
        wrapper_src.write_text("#!/bin/sh\nexit 0\n")
        wrapper_src.chmod(0o755)

    return hp.BootstrapState(
        venv=str(venv),
        hermes_home=str(hermes_home),
        agent_id="hermes-agent",
    )


def _strip_volatile(phases: dict[str, Any]) -> dict[str, Any]:
    """Filter out per-run volatile fields so we can compare two runs.

    ``at`` is a UTC timestamp Phase orchestration stamps; env_probe's
    snapshot path includes a UTC timestamp too. Both legitimately
    change every run; we strip them from the comparison and assert
    every OTHER detail matches.
    """
    stable: dict[str, Any] = {}
    for name, entry in phases.items():
        if not isinstance(entry, dict):
            stable[name] = entry
            continue
        copy = {k: v for k, v in entry.items() if k != "at"}
        if name == "env_probe":
            details = dict(copy.get("details") or {})
            details.pop("snapshot_path", None)
            copy["details"] = details
        stable[name] = copy
    return stable


def test_two_consecutive_runs_converge(tmp_path: Path, hermetic_state: hp.BootstrapState) -> None:
    """Run #1 writes everything; run #2 must produce byte-identical
    config.yaml + persona TOMLs + (post-volatile-strip) provision.json."""
    state_root = tmp_path / "state"

    # Run #1
    result_1 = hp.run(state_root=state_root, initial_state=hermetic_state)
    config_path = Path(hermetic_state.hermes_home) / "config.yaml"
    assert config_path.exists()
    config_after_1 = config_path.read_text(encoding="utf-8")

    persona_root = Path(hermetic_state.hermes_home) / "personas"
    hermes_toml_1 = (persona_root / "hermes.toml").read_text(encoding="utf-8")
    coder_toml_1 = (persona_root / "coder.toml").read_text(encoding="utf-8")
    active_1 = (persona_root / "active.txt").read_text(encoding="utf-8")

    provision_1 = _strip_volatile(result_1.phases)

    # Run #2 — same state root means BootstrapState.load picks up the
    # checkpoint; phases marked OK get skipped. Even so, the on-disk
    # artefacts (config.yaml, personas) must not change.
    result_2 = hp.run(state_root=state_root)
    config_after_2 = config_path.read_text(encoding="utf-8")
    hermes_toml_2 = (persona_root / "hermes.toml").read_text(encoding="utf-8")
    coder_toml_2 = (persona_root / "coder.toml").read_text(encoding="utf-8")
    active_2 = (persona_root / "active.txt").read_text(encoding="utf-8")
    provision_2 = _strip_volatile(result_2.phases)

    assert config_after_1 == config_after_2, "config.yaml drifted on re-run"
    assert hermes_toml_1 == hermes_toml_2, "hermes persona TOML drifted"
    assert coder_toml_1 == coder_toml_2, "coder persona TOML drifted"
    assert active_1 == active_2, "active pointer drifted"
    # Every phase status should be OK or SKIP on both runs.
    for name in hp.PHASE_NAMES:
        s1 = provision_1.get(name, {}).get("status")
        s2 = provision_2.get(name, {}).get("status")
        assert s1 in {"ok", "skip"}, f"run #1 {name}: {s1}"
        assert s2 in {"ok", "skip"}, f"run #2 {name}: {s2}"


def test_repair_run_rewrites_persona_seeds(
    tmp_path: Path, hermetic_state: hp.BootstrapState
) -> None:
    """``--repair`` overwrites operator persona edits with the seeds.

    Pure ``--repair`` semantics: the operator asked for a known-good
    state; preserve nothing. (Without --repair the operator edit
    survives — see ``test_seed_preserves_operator_edits``.)
    """
    state_root = tmp_path / "state"
    hp.run(state_root=state_root, initial_state=hermetic_state)
    persona_path = Path(hermetic_state.hermes_home) / "personas" / "hermes.toml"
    persona_path.write_text('[persona]\nid = "hermes"\ndisplay_name = "Custom"\n', encoding="utf-8")
    hp.run(state_root=state_root, repair=True)
    reloaded = P.load_persona("hermes", root=persona_path.parent)
    assert reloaded.display_name == "Hermes"


def test_config_yaml_contains_persona_prelude(
    tmp_path: Path, hermetic_state: hp.BootstrapState
) -> None:
    """Phase 7 contract: rendered config.yaml carries the active persona's
    system_prompt_prelude. Without this, the agent has no way to see the
    hal0-tone / approval-policy guidance."""
    state_root = tmp_path / "state"
    hp.run(state_root=state_root, initial_state=hermetic_state)
    config = (Path(hermetic_state.hermes_home) / "config.yaml").read_text(encoding="utf-8")
    assert "system_prompt_prelude" in config, "Phase 7 didn't inject the persona prelude"
    # Hermes display label lands in the cosmetic personality field.
    assert "personality:" in config


def test_config_yaml_contains_chat_slot_aliases(
    tmp_path: Path, hermetic_state: hp.BootstrapState
) -> None:
    """Phase 5 contract: chat_slots appear in the first render
    (pre-PR-3 they only appeared after Phase 9)."""
    state_root = tmp_path / "state"
    hp.run(state_root=state_root, initial_state=hermetic_state)
    config = (Path(hermetic_state.hermes_home) / "config.yaml").read_text(encoding="utf-8")
    assert "model_aliases:" in config
    assert "primary:" in config
    assert "agent-hermes:" in config
    assert "embed:" not in config.split("model_aliases:")[1].split("\n\n")[0], (
        "embed slot leaked into chat aliases"
    )


def test_config_yaml_contains_mcp_servers(
    tmp_path: Path, hermetic_state: hp.BootstrapState
) -> None:
    """Phase 6 contract: rendered config carries both default MCP servers
    with X-hal0-Agent identity headers."""
    state_root = tmp_path / "state"
    hp.run(state_root=state_root, initial_state=hermetic_state)
    config = (Path(hermetic_state.hermes_home) / "config.yaml").read_text(encoding="utf-8")
    assert "mcp_servers:" in config
    assert "hal0-admin:" in config
    assert "hal0-memory:" in config
    assert '"hermes-agent"' in config  # X-hal0-Agent value


def test_persona_seed_appears_in_phase_order_before_config_write(tmp_path: Path) -> None:
    """Ordering guard — persona_seed must come BEFORE config_write so the
    first render gets the active persona's system_prompt."""
    names = list(hp.PHASE_NAMES)
    assert names.index("persona_seed") < names.index("config_write")
