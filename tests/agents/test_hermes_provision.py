"""Unit tests for :mod:`hal0.agents.hermes_provision` (issue #238).

The scaffold lands with no-op phase stubs; these tests pin the
state-machine invariants downstream slices rely on:

* Every phase runs in declared order.
* Successful runs produce a checkpoint with every phase marked ``ok``.
* Re-runs are no-ops (every phase is skipped because checkpoints exist).
* ``--repair`` forces re-execution of every phase.
* ``--skip-phase`` records ``skip`` for the named phase.
* The state file round-trips through ``BootstrapState.load`` ↔
  ``BootstrapState.save`` losslessly.

Phase ordering matters because downstream phases consume earlier
phases' outputs (env_probe → config_write → mcp_wire). A regression
that re-orders or drops a phase here surfaces before the integration
slice notices.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes_provision as hp


@pytest.fixture
def state_with_tmp_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> hp.BootstrapState:
    """Seed a :class:`BootstrapState` rooted in ``tmp_path`` with externals stubbed.

    The real preflight + install phases reach for ``/var/lib/hal0/*``,
    spawn ``python -m venv``, and HTTP-poke ``127.0.0.1:8080``. Every
    pipeline-level test needs these dimmed out so the orchestrator's
    behaviour is what's under test, not the LXC.
    """
    var_lib = tmp_path / "var" / "lib" / "hal0"
    var_lib.mkdir(parents=True)
    venv = var_lib / "venvs" / "hermes"
    hermes_home = var_lib / "agents" / "hermes"
    monkeypatch.setattr(hp, "_http_get", lambda *_a, **_kw: 200)
    monkeypatch.setattr(hp, "MIN_FREE_GIB", 0)  # /tmp may be tmpfs with little headroom
    monkeypatch.setattr(
        hp, "WRAPPER_INSTALL_PATH", tmp_path / "usr" / "local" / "bin" / "hal0-hermes"
    )

    def _fake_install(v: Path, _req: Path, **_kwargs: Any) -> None:
        (v / "bin").mkdir(parents=True, exist_ok=True)
        (v / "bin" / "hermes").write_text("#!/bin/sh\nexit 0\n")
        (v / "bin" / "hermes").chmod(0o755)

    monkeypatch.setattr(hp, "_install_venv", _fake_install)
    return hp.BootstrapState(venv=str(venv), hermes_home=str(hermes_home))


def test_phase_names_in_planned_order() -> None:
    """The planned 12 phases stay in the documented order.

    Mirrors `docs/internal/hermes-bootstrap-plan-2026-05-23.md` §3 —
    if a slice re-orders or drops a phase, this guard catches it
    before the integration scenario notices.
    """
    expected = (
        "preflight",
        "install",
        "env_probe",
        "home_init",
        "config_write",
        "mcp_wire",
        "context_link",
        "namespace_register",
        "model_automap",
        "voice_wire",
        "smoke_tests",
        "self_report",
    )
    assert expected == hp.PHASE_NAMES


def test_run_marks_every_phase_ok_on_fresh(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState
) -> None:
    result = hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths)
    # voice_wire legitimately returns SKIP when no STT/TTS slots are
    # configured (most CI envs); accept both OK and SKIP for that phase.
    skip_ok = {"voice_wire"}
    for name in hp.PHASE_NAMES:
        status = result.phases[name]["status"]
        allowed = {hp.PhaseStatus.OK.value} | (
            {hp.PhaseStatus.SKIP.value} if name in skip_ok else set()
        )
        assert status in allowed, f"{name}: unexpected {status}"
    assert result.failed == []


def test_state_file_written_and_round_trips(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState
) -> None:
    hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths)
    state_file = tmp_path / "provision.json"
    assert state_file.exists()
    loaded = hp.BootstrapState.load(tmp_path)
    assert loaded is not None
    assert loaded.schema_version == hp.SCHEMA_VERSION
    assert set(loaded.phases.keys()) >= set(hp.PHASE_NAMES)
    assert loaded.completed_at is not None


def test_rerun_is_noop_when_all_phases_ok(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState
) -> None:
    hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths)
    second = hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths)
    # All phases skipped because their checkpoint is already ok.
    assert set(second.skipped) == set(hp.PHASE_NAMES)
    assert second.failed == []


def test_repair_flag_forces_rerun(tmp_path: Path, state_with_tmp_paths: hp.BootstrapState) -> None:
    hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths)
    second = hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths, repair=True)
    # Repair re-runs everything → nothing was skipped via checkpoint.
    assert second.skipped == []
    # voice_wire legitimately returns SKIP when no STT/TTS slots exist
    # (same posture as the fresh-run test above).
    skip_ok = {"voice_wire"}
    for name in hp.PHASE_NAMES:
        status = second.phases[name]["status"]
        allowed = {hp.PhaseStatus.OK.value} | (
            {hp.PhaseStatus.SKIP.value} if name in skip_ok else set()
        )
        assert status in allowed, f"{name}: unexpected {status}"


def test_skip_phase_records_skip_reason(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState
) -> None:
    result = hp.run(
        state_root=tmp_path,
        initial_state=state_with_tmp_paths,
        skip_phases=("voice_wire", "smoke_tests"),
    )
    assert result.phases["voice_wire"]["status"] == hp.PhaseStatus.SKIP.value
    assert result.phases["voice_wire"]["reason"] == "--skip-phase"
    assert result.phases["smoke_tests"]["status"] == hp.PhaseStatus.SKIP.value
    # Other phases run as normal.
    assert result.phases["preflight"]["status"] == hp.PhaseStatus.OK.value


def test_dry_run_skips_state_persistence(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState
) -> None:
    hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths, dry_run=True)
    assert not (tmp_path / "provision.json").exists()


def test_load_returns_none_when_state_file_missing(tmp_path: Path) -> None:
    assert hp.BootstrapState.load(tmp_path) is None


def test_load_returns_none_when_state_file_corrupt(tmp_path: Path) -> None:
    (tmp_path / "provision.json").write_text("not-json")
    assert hp.BootstrapState.load(tmp_path) is None


def test_phase_result_to_dict_includes_optional_fields() -> None:
    r = hp.PhaseResult(
        status=hp.PhaseStatus.OK,
        details={"k": "v"},
        hash="abc",
        reason=None,
    )
    out = r.to_dict()
    assert out["status"] == "ok"
    assert out["hash"] == "abc"
    assert out["details"] == {"k": "v"}
    assert "reason" not in out  # None reasons omitted


def test_content_hash_is_stable_and_collision_free() -> None:
    a = hp.content_hash("foo", "bar")
    b = hp.content_hash("foo", "bar")
    c = hp.content_hash("foo", "baz")
    assert a == b
    assert a != c
    # Stable across the str/bytes split.
    d = hp.content_hash(b"foo", "bar")
    assert d == a


def test_failed_phase_surfaces_in_result_and_blocks_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state_with_tmp_paths: hp.BootstrapState,
) -> None:
    def _failing(_state: hp.BootstrapState) -> hp.PhaseResult:
        return hp.PhaseResult(status=hp.PhaseStatus.FAIL, reason="forced")

    new_phases = [(name, _failing if name == "env_probe" else fn) for name, fn in hp.PHASES]
    monkeypatch.setattr(hp, "PHASES", new_phases)

    result = hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths)
    assert "env_probe" in result.failed
    assert result.state.completed_at is None
    assert any("env_probe" in e for e in result.state.errors)


def test_cli_entry_returns_zero_on_success(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState, monkeypatch: pytest.MonkeyPatch
) -> None:
    # bootstrap_cli doesn't take an initial_state kwarg directly — wrap
    # `run` so the test still threads the tmp-rooted state through.
    real_run = hp.run

    def _wrapped(**kwargs: Any) -> hp.RunResult:
        kwargs.setdefault("initial_state", state_with_tmp_paths)
        return real_run(**kwargs)

    monkeypatch.setattr(hp, "run", _wrapped)
    rc = hp.bootstrap_cli(
        repair=False,
        dry_run=False,
        skip_phases=(),
        verbose=False,
        state_root=tmp_path,
    )
    assert rc == 0


def test_cli_entry_returns_one_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state_with_tmp_paths: hp.BootstrapState,
) -> None:
    def _failing(_state: hp.BootstrapState) -> hp.PhaseResult:
        return hp.PhaseResult(status=hp.PhaseStatus.FAIL, reason="boom")

    new_phases = [(name, _failing if name == "preflight" else fn) for name, fn in hp.PHASES]
    monkeypatch.setattr(hp, "PHASES", new_phases)

    real_run = hp.run

    def _wrapped(**kwargs: Any) -> hp.RunResult:
        kwargs.setdefault("initial_state", state_with_tmp_paths)
        return real_run(**kwargs)

    monkeypatch.setattr(hp, "run", _wrapped)
    rc = hp.bootstrap_cli(
        repair=False,
        dry_run=False,
        skip_phases=(),
        verbose=False,
        state_root=tmp_path,
    )
    assert rc == 1


# ── #240 phase impls — preflight / install / home_init ──────────────────────


def test_preflight_passes_when_inputs_meet_minimums(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    var_lib = tmp_path / "var" / "lib" / "hal0"
    var_lib.mkdir(parents=True)
    venv = var_lib / "venvs" / "hermes"
    state = hp.BootstrapState(venv=str(venv))
    monkeypatch.setattr(hp, "_http_get", lambda *_a, **_kw: 200)
    monkeypatch.setattr(hp, "MIN_FREE_GIB", 0)
    out = hp._phase_preflight(state)
    assert out.status == hp.PhaseStatus.OK
    assert out.details["python_version"]
    assert out.details["daemon_http_status"] == 200


def test_preflight_fails_on_unreachable_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    var_lib = tmp_path / "var" / "lib" / "hal0"
    var_lib.mkdir(parents=True)
    state = hp.BootstrapState(venv=str(var_lib / "venvs" / "hermes"))
    monkeypatch.setattr(hp, "_http_get", lambda *_a, **_kw: 0)
    out = hp._phase_preflight(state)
    assert out.status == hp.PhaseStatus.FAIL
    assert "daemon unreachable" in (out.reason or "")


def test_preflight_fails_on_var_lib_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hp, "_http_get", lambda *_a, **_kw: 200)
    state = hp.BootstrapState(venv=str(tmp_path / "nope" / "venvs" / "hermes"))
    out = hp._phase_preflight(state)
    assert out.status == hp.PhaseStatus.FAIL
    assert "not writable" in (out.reason or "")


def test_home_init_creates_layout_with_marker(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes_home"
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    out = hp._phase_home_init(state)
    assert out.status == hp.PhaseStatus.OK
    assert hermes_home.is_dir()
    assert (hermes_home / ".hal0-managed").is_file()
    for sub in ("memories", "skills", "plugins/memory", "plugins/model-providers", "logs"):
        assert (hermes_home / sub).is_dir()


def test_home_init_idempotent_on_managed_dir(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes_home"
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    hp._phase_home_init(state)
    marker_before = (hermes_home / ".hal0-managed").read_text()
    out2 = hp._phase_home_init(state)
    assert out2.status == hp.PhaseStatus.OK
    assert (hermes_home / ".hal0-managed").read_text() == marker_before


def test_home_init_refuses_to_clobber_non_managed_dir(tmp_path: Path) -> None:
    hermes_home = tmp_path / "user_hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("# user file")
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    out = hp._phase_home_init(state)
    assert out.status == hp.PhaseStatus.FAIL
    assert "not hal0-managed" in (out.reason or "")


def test_install_phase_skips_venv_when_binary_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "hermes").write_text("#!/bin/sh\nexit 0\n")
    (venv / "bin" / "hermes").chmod(0o755)

    wrapper_dst = tmp_path / "usr" / "local" / "bin" / "hal0-hermes"
    monkeypatch.setattr(hp, "WRAPPER_INSTALL_PATH", wrapper_dst)
    hermes_home = tmp_path / "hermes_home"
    state = hp.BootstrapState(venv=str(venv), hermes_home=str(hermes_home))

    called: list[Any] = []

    def _no_install(*args: Any, **kwargs: Any) -> None:
        called.append(args)

    monkeypatch.setattr(hp, "_install_venv", _no_install)

    out = hp._phase_install(state)
    assert out.status == hp.PhaseStatus.OK
    assert called == []
    assert wrapper_dst.is_file()
    assert (hermes_home / "plugins" / "model-providers" / "hal0" / "__init__.py").is_file()
    assert (hermes_home / "plugins" / "memory" / "hal0-memory" / "__init__.py").is_file()


def test_install_phase_runs_venv_install_when_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "venv"
    wrapper_dst = tmp_path / "usr" / "local" / "bin" / "hal0-hermes"
    monkeypatch.setattr(hp, "WRAPPER_INSTALL_PATH", wrapper_dst)
    hermes_home = tmp_path / "hermes_home"
    state = hp.BootstrapState(venv=str(venv), hermes_home=str(hermes_home))

    install_calls: list[Path] = []

    def _fake_install(v: Path, _req: Path, **_kwargs: Any) -> None:
        install_calls.append(v)
        (v / "bin").mkdir(parents=True, exist_ok=True)
        (v / "bin" / "hermes").write_text("#!/bin/sh\nexit 0\n")
        (v / "bin" / "hermes").chmod(0o755)

    monkeypatch.setattr(hp, "_install_venv", _fake_install)
    out = hp._phase_install(state)
    assert out.status == hp.PhaseStatus.OK
    assert install_calls == [venv]


def test_resolve_python311_prefers_explicit_when_available() -> None:
    out = hp._resolve_python311(prober=lambda _name: "/opt/python3.11/bin/python3.11")
    assert out == "/opt/python3.11/bin/python3.11"


def test_resolve_python311_falls_back_to_sys_executable() -> None:
    out = hp._resolve_python311(prober=lambda _name: None)
    # CI runs on >= 3.11 (pyproject pin); falls back to sys.executable.
    assert out is not None


# ── #241 phase impls — env_probe / config_write ─────────────────────────────


def test_env_probe_writes_snapshot_to_hermes_home(tmp_path: Path) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    out = hp._phase_env_probe(state)
    assert out.status == hp.PhaseStatus.OK
    snap = Path(out.details["snapshot_path"])
    assert snap.exists()
    import json as _json

    data = _json.loads(snap.read_text())
    for key in ("env_report", "gpu_target_version", "npu_status", "ai_models"):
        assert key in data


def test_resolve_primary_slot_picks_named_primary_slot() -> None:
    fake = lambda: [  # noqa: E731
        {
            "name": "primary",
            "type": "llm",
            "state": "ready",
            "model_id": "qwen3:8b",
            "backend_url": "http://127.0.0.1:8001/v1",
            "context_length": 16384,
        }
    ]
    out = hp._resolve_primary_slot(slots_fetcher=fake)
    assert out["model"] == "qwen3:8b"
    # Slot's llama-server URL (8001) is rewritten to the hal0 OpenAI
    # proxy so prompt-cache + dispatch stay in the loop. hal0-api
    # exposes the OpenAI surface at `/v1` — Lemonade's native
    # `/api/v1` prefix is dropped at the wrapper.
    assert out["base_url"] == "http://127.0.0.1:8080/v1"
    assert out["context_length"] == 16384


def test_resolve_primary_slot_fallback_when_no_slots() -> None:
    out = hp._resolve_primary_slot(slots_fetcher=lambda: [])
    assert out["model"] == "primary"
    assert out["context_length"] == 32768
    # Placeholder points at hal0-api on 8080/v1, not the pre-Lemonade
    # phantom on 8000.
    assert out["base_url"] == "http://127.0.0.1:8080/v1"


def test_render_config_yaml_includes_primary_block() -> None:
    rendered = hp._render_config_yaml(
        primary={
            "model_id": "qwen3:8b",
            "backend_url": "http://127.0.0.1:8001/v1",
            "context_length": 16384,
        },
        agent_id="hermes-agent",
    )
    assert '"qwen3:8b"' in rendered
    assert '"http://127.0.0.1:8001/v1"' in rendered
    assert 'X-hal0-Agent: "hermes-agent"' in rendered
    # ADR-0014: graph extraction defaults OFF.
    assert "enabled: false" in rendered


def test_render_config_yaml_no_primary_emits_safe_placeholder() -> None:
    rendered = hp._render_config_yaml(primary=None, agent_id="hermes-agent")
    assert 'default: ""' in rendered
    # Placeholder URL points at hal0-api (8080/v1) — pre-Lemonade this
    # was a phantom 8000 with no daemon behind it; the wrong-prefix
    # variant `/api/v1` exists on Lemonade but is dropped at hal0's
    # wrapper.
    assert "127.0.0.1:8080/v1" in rendered


def test_render_config_yaml_chat_slots_become_aliases() -> None:
    rendered = hp._render_config_yaml(
        primary={"model_id": "p", "backend_url": "u", "context_length": 8000},
        chat_slots=[
            {"alias": "coder", "model_id": "qwen-coder", "backend_url": "http://x"},
        ],
        agent_id="hermes-agent",
    )
    assert "model_aliases:" in rendered
    assert "coder:" in rendered
    assert '"qwen-coder"' in rendered


def test_config_write_phase_writes_yaml_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_kwargs: {"model": "p", "base_url": "u", "context_length": 8000},
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no-such-overrides.yaml")
    out1 = hp._phase_config_write(state)
    assert out1.status == hp.PhaseStatus.OK
    cfg = Path(out1.details["config_path"])
    assert cfg.exists()
    first_hash = out1.hash
    # Re-run is a no-op (hash equals on-disk).
    out2 = hp._phase_config_write(state)
    assert out2.details.get("unchanged") is True
    assert out2.hash == first_hash


def test_config_write_phase_applies_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("agent:\n  max_turns: 999\n")
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_kwargs: {"model": "p", "base_url": "u", "context_length": 8000},
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", overrides)
    out = hp._phase_config_write(state)
    assert out.status == hp.PhaseStatus.OK
    cfg = Path(out.details["config_path"]).read_text()
    assert "999" in cfg


def test_deep_merge_recurses() -> None:
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    overlay = {"a": {"c": 99, "e": 4}}
    merged = hp._deep_merge(base, overlay)
    assert merged == {"a": {"b": 1, "c": 99, "e": 4}, "d": 3}


def test_hal0_profile_plugin_file_present() -> None:
    """The plugin source file ships in the wheel + has the required hooks."""
    repo_root = hp.REPO_ROOT_FOR_INSTALLER
    src = repo_root / "installer" / "agents" / "hermes" / "plugins" / "hal0" / "__init__.py"
    body = src.read_text()
    assert "Hal0Profile" in body
    assert "register_provider" in body
    assert "hermes-on-hal0" in body  # User-Agent header marker


# ── #242 phase impl — mcp_wire + Hal0MemoryProvider plugin ──────────────────


def test_hal0_memory_provider_plugin_file_present() -> None:
    repo_root = hp.REPO_ROOT_FOR_INSTALLER
    src = repo_root / "installer" / "agents" / "hermes" / "plugins" / "hal0-memory" / "__init__.py"
    body = src.read_text()
    assert "Hal0MemoryProvider" in body
    assert "MemoryProvider" in body
    # ADR-0014: graph defaults OFF; ADR-0013-aware namespace.
    assert "graph" in body
    assert "private:" in body or "private:hermes-agent" in body
    # Lifecycle methods upstream calls.
    for method in ("system_prompt_block", "prefetch", "sync_turn"):
        assert method in body


def test_load_agent_allowlist_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert hp._load_agent_allowlist(tmp_path / "no.toml") is None


def test_load_agent_allowlist_parses_servers_section(tmp_path: Path) -> None:
    path = tmp_path / "hermes.toml"
    path.write_text(
        """schema_version = 1
[mcp.servers.hal0-admin]
builtin = true

[mcp.servers.hal0-memory]
builtin = true

[mcp.servers.filesystem]
enabled = true
""",
        encoding="utf-8",
    )
    servers = hp._load_agent_allowlist(path)
    assert servers is not None
    assert set(servers.keys()) == {"hal0-admin", "hal0-memory", "filesystem"}


def test_mcp_wire_phase_returns_ok_with_tools_when_servers_respond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = hp.BootstrapState()
    monkeypatch.setattr(
        hp,
        "_probe_mcp_server",
        lambda url, **_kw: {"ok": True, "tools": ["t1", "t2"], "error": None},
    )
    monkeypatch.setattr(hp, "_load_agent_allowlist", lambda *_a, **_kw: None)
    out = hp._phase_mcp_wire(state)
    assert out.status == hp.PhaseStatus.OK
    assert out.details["servers"]["hal0-admin"]["status"] == "ok"
    assert out.details["servers"]["hal0-admin"]["tool_count"] == 2
    assert out.details["allowlist_present"] is False
    assert out.details["warnings"] == []


def test_mcp_wire_phase_degrades_not_fails_on_unreachable_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = hp.BootstrapState()
    monkeypatch.setattr(
        hp,
        "_probe_mcp_server",
        lambda url, **_kw: {"ok": False, "tools": [], "error": "connection refused"},
    )
    monkeypatch.setattr(hp, "_load_agent_allowlist", lambda *_a, **_kw: None)
    out = hp._phase_mcp_wire(state)
    # Still OK — degraded is a warning, not a phase-blocker per ADR-0013.
    assert out.status == hp.PhaseStatus.OK
    assert out.details["servers"]["hal0-admin"]["status"] == "degraded"
    assert "connection refused" in out.details["warnings"][0]


# ── #243 phase impl — namespace_register + identity card schema ─────────────


def test_build_identity_card_matches_schema_v1() -> None:
    state = hp.BootstrapState(agent_id="hermes-agent")
    card = hp._build_identity_card(state)
    assert card["dataset"] == hp.AGENTS_DATASET
    assert hp.AGENT_IDENTITY_TAG in card["tags"]
    md = card["metadata"]
    # Required fields per ADR-0011 §4.
    for required in ("agent_id", "display_name", "namespace", "hal0_state"):
        assert required in md, f"required field missing: {required}"
    assert md["namespace"] == "private:hermes-agent"
    assert md["hal0_state"]["bootstrap_version"] == 1
    assert md["hal0_state"]["registered_at"]


def test_namespace_register_registers_card_on_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = hp.BootstrapState()
    calls: list[tuple[str, dict[str, Any]]] = []

    def _fake_mcp(method: str, params: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        calls.append((method, params))
        name = params.get("name")
        if name == "memory_search":
            return {"ok": True, "result": {"items": []}}
        if name == "memory_add":
            return {"ok": True, "result": {"id": "mem_abc"}}
        return {"ok": True, "result": {}}

    monkeypatch.setattr(hp, "_mcp_memory_call", _fake_mcp)
    out = hp._phase_namespace_register(state)
    assert out.status == hp.PhaseStatus.OK
    assert out.details["registered"] is True
    assert out.details["memory_id"] == "mem_abc"
    # First call is the search; second is the add.
    assert any(p[1]["name"] == "memory_search" for p in calls)
    assert any(p[1]["name"] == "memory_add" for p in calls)


def test_namespace_register_refreshes_existing_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = hp.BootstrapState()

    def _fake_mcp(method: str, params: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        name = params.get("name")
        if name == "memory_search":
            return {
                "ok": True,
                "result": {
                    "items": [{"id": "old_mem_id", "metadata": {"agent_id": "hermes-agent"}}]
                },
            }
        if name == "memory_delete":
            return {"ok": True, "result": {"deleted": 1}}
        if name == "memory_add":
            return {"ok": True, "result": {"id": "new_mem_id"}}
        return {"ok": True, "result": {}}

    monkeypatch.setattr(hp, "_mcp_memory_call", _fake_mcp)
    out = hp._phase_namespace_register(state)
    assert out.status == hp.PhaseStatus.OK
    assert out.details["refreshed_existing"] is True


def test_namespace_register_continues_on_mcp_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0013: registry failure logs + continues; bootstrap doesn't block."""
    state = hp.BootstrapState()
    monkeypatch.setattr(
        hp,
        "_mcp_memory_call",
        lambda *a, **kw: {"ok": False, "error": "connection refused"},
    )
    out = hp._phase_namespace_register(state)
    assert out.status == hp.PhaseStatus.OK
    assert out.details["registered"] is False
    assert any("memory_add" in w for w in out.details["warnings"])


def test_mcp_wire_phase_skips_server_not_in_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = hp.BootstrapState()
    # Allowlist has only hal0-admin; hal0-memory gets skipped + warned.
    monkeypatch.setattr(
        hp,
        "_load_agent_allowlist",
        lambda *_a, **_kw: {"hal0-admin": {"builtin": True}},
    )
    monkeypatch.setattr(
        hp,
        "_probe_mcp_server",
        lambda url, **_kw: {"ok": True, "tools": ["t1"], "error": None},
    )
    out = hp._phase_mcp_wire(state)
    assert out.status == hp.PhaseStatus.OK
    assert out.details["servers"]["hal0-memory"]["status"] == "skipped_by_allowlist"
    assert out.details["servers"]["hal0-admin"]["status"] == "ok"
    assert "hal0-memory" in out.details["warnings"][0]


# ── #244 phase impl — context_link + templates ──────────────────────────────


def test_context_link_renders_all_three_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    # Seed an env_probe snapshot the templates can consume.
    snapshot = {
        "env_report": {
            "cpu": {"model": "AMD RYZEN AI MAX+ 395", "logical_online": 16},
            "ram": {"total_bytes": 96 * 1024**3},
            "npu": {"present": True, "xdna_gen": 2, "pci_id": "1022:17F0"},
            "gpu": {"gfx": "gfx1151", "driver": "amdgpu", "pci_id": "1002:1586"},
            "container": {"layer": "container", "kind": "lxc", "apparmor": "unconfined"},
        }
    }
    (hermes_home / "env-20260523T120000Z.json").write_text(json.dumps(snapshot))
    state = hp.BootstrapState(hermes_home=str(hermes_home))

    # Redirect /etc/hal0 + bundled skills to tmp_path so we can run as
    # non-root without touching the real system.
    etc = tmp_path / "etc" / "hal0"
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", etc)
    monkeypatch.setattr(hp, "ETC_HAL0_AGENT_SKILLS", etc / "agent-skills")
    monkeypatch.setattr(hp, "HAL0_BUNDLED_SKILLS", tmp_path / "no-such-skills")
    # Context-link consults /api/slots when wiring HERMES.md's primary
    # block; stub it out so the test stays offline + deterministic.
    monkeypatch.setattr(hp, "_fetch_slots", lambda: [])

    out = hp._phase_context_link(state)
    assert out.status == hp.PhaseStatus.OK
    assert (hermes_home / "SOUL.md").exists()
    assert (etc / "HERMES.md").exists()
    assert (etc / "AGENTS.md").exists()
    soul = (hermes_home / "SOUL.md").read_text()
    # Templates reference Strix Halo signals — confirm at least one
    # variable substituted from snapshot.
    assert "RYZEN AI MAX" in soul or "gfx1151" in soul or "XDNA" in soul


def test_context_link_idempotent_symlink(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    link = tmp_path / "lnk"
    assert hp._safe_symlink(src, link) is True
    # Second call: no-op (target unchanged).
    assert hp._safe_symlink(src, link) is False


def test_context_link_skill_mirror_warns_when_src_missing(tmp_path: Path) -> None:
    linked, warnings = hp._mirror_bundled_skills(tmp_path / "no-src", tmp_path / "dst")
    assert linked == []
    assert any("not present" in w for w in warnings)


def test_context_link_falls_back_when_soul_render_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", tmp_path / "etc" / "hal0")
    monkeypatch.setattr(hp, "ETC_HAL0_AGENT_SKILLS", tmp_path / "etc" / "hal0" / "agent-skills")
    monkeypatch.setattr(hp, "HAL0_BUNDLED_SKILLS", tmp_path / "no-skills")
    monkeypatch.setattr(hp, "_fetch_slots", lambda: [])

    def _explode(name: str, **_: Any) -> str:
        if name == "SOUL.md.j2":
            raise RuntimeError("template boom")
        return "ok"

    monkeypatch.setattr(hp, "_render_template", _explode)
    out = hp._phase_context_link(state)
    assert out.status == hp.PhaseStatus.OK
    soul = (hermes_home / "SOUL.md").read_text()
    assert "hal0 admin agent" in soul
    assert any("SOUL.md render" in w for w in out.details["warnings"])


# ── #245 phase impls — model_automap + voice_wire ───────────────────────────


def test_model_automap_writes_aliases_from_chat_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    # Pre-render config.yaml so model_automap has something to rewrite.
    (hermes_home / "config.yaml").write_text(
        hp._render_config_yaml(
            primary={"model_id": "p", "backend_url": "u", "context_length": 8000},
            agent_id="hermes-agent",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no.yaml")
    monkeypatch.setattr(
        hp,
        "_fetch_slots",
        lambda: [
            {
                "name": "primary",
                "capability": "chat",
                "model_id": "qwen3:8b",
                "backend_url": "http://127.0.0.1:8001/v1",
                "state": "ready",
            },
            {
                "name": "coder",
                "capability": "chat",
                "model_id": "qwen-coder",
                "backend_url": "http://127.0.0.1:8002/v1",
                "state": "ready",
            },
        ],
    )
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_kw: {"model": "p", "base_url": "u", "context_length": 8000},
    )
    out = hp._phase_model_automap(state)
    assert out.status == hp.PhaseStatus.OK
    rendered = (hermes_home / "config.yaml").read_text()
    assert "coder" in out.details["aliases_written"]
    assert "primary" in out.details["aliases_written"]
    # YAML body actually carries the aliases.
    assert "qwen-coder" in rendered


def test_model_automap_idempotent_hash_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    (hermes_home / "config.yaml").write_text(
        hp._render_config_yaml(
            primary={"model_id": "p", "backend_url": "u", "context_length": 8000},
            agent_id="hermes-agent",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no.yaml")
    monkeypatch.setattr(hp, "_fetch_slots", lambda: [])
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_kw: {"model": "p", "base_url": "u", "context_length": 8000},
    )
    out1 = hp._phase_model_automap(state)
    out2 = hp._phase_model_automap(state)
    # First run rewrites (or doesn't, if already canonical); second run
    # observes hash equality and marks unchanged.
    assert out2.details.get("unchanged") is True
    assert out1.hash == out2.hash


def test_voice_wire_skips_when_no_voice_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    monkeypatch.setattr(hp, "_fetch_slots", lambda: [])
    out = hp._phase_voice_wire(state)
    assert out.status == hp.PhaseStatus.SKIP


# ── #246 phase impls — smoke_tests + self_report ────────────────────────────


def test_smoke_tests_phase_runs_each_probe_collecting_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    # All six probes return (True, "...") so we exercise the rollup
    # without depending on a real Hermes binary or HTTP listener.
    monkeypatch.setattr(hp, "_smoke_wrapper_ready", lambda s: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_hermes_doctor", lambda s: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_chat_completions", lambda s: (True, "ready"))
    monkeypatch.setattr(hp, "_smoke_memory_roundtrip", lambda s: (True, "1 item"))
    monkeypatch.setattr(hp, "_smoke_admin_tools_list", lambda s: (True, "8 tools"))
    monkeypatch.setattr(hp, "_smoke_hermes_md_contains_primary", lambda s: (True, "ok"))
    out = hp._phase_smoke_tests(state)
    assert out.status == hp.PhaseStatus.OK
    assert out.details["failures"] == []
    assert set(out.details["results"].keys()) == {
        "wrapper_ready",
        "hermes_doctor",
        "chat_completions",
        "memory_roundtrip",
        "admin_tools_list",
        "hermes_md_contains_primary",
    }


def test_smoke_tests_phase_records_failures_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    monkeypatch.setattr(hp, "_smoke_wrapper_ready", lambda s: (False, "wrapper missing"))
    monkeypatch.setattr(hp, "_smoke_hermes_doctor", lambda s: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_chat_completions", lambda s: (False, "503"))
    monkeypatch.setattr(hp, "_smoke_memory_roundtrip", lambda s: (True, "1 item"))
    monkeypatch.setattr(hp, "_smoke_admin_tools_list", lambda s: (True, "8 tools"))
    monkeypatch.setattr(hp, "_smoke_hermes_md_contains_primary", lambda s: (True, "ok"))
    out = hp._phase_smoke_tests(state)
    assert out.status == hp.PhaseStatus.OK  # diagnostic — not a blocker
    assert len(out.details["failures"]) == 2
    assert any("wrapper_ready" in f for f in out.details["failures"])


def test_self_report_writes_summary_memory_and_handles_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    state.phases["smoke_tests"] = {
        "status": "ok",
        "details": {"failures": ["chat_completions: 503"]},
    }
    # Pre-render config so primary alias gets picked up.
    (tmp_path / "hh").mkdir()
    (tmp_path / "hh" / "config.yaml").write_text("model:\n  default: qwen3:8b\n", encoding="utf-8")
    captured: list[tuple[str, dict[str, Any]]] = []

    def _fake_mcp(method: str, params: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        captured.append((method, params))
        return {"ok": True, "result": {"id": "mem_xyz"}}

    monkeypatch.setattr(hp, "_mcp_memory_call", _fake_mcp)
    out = hp._phase_self_report(state)
    assert out.status == hp.PhaseStatus.OK
    assert out.details["published"] is True
    assert out.details["summary_id"] == "mem_xyz"
    # Verify the memory write captures the smoke-test rollup.
    sent_text = captured[0][1]["arguments"]["text"]
    assert "qwen3:8b" in sent_text
    assert "Smoke failures: 1" in sent_text


def test_self_report_continues_when_memory_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    (tmp_path / "hh").mkdir()
    monkeypatch.setattr(
        hp,
        "_mcp_memory_call",
        lambda *a, **kw: {"ok": False, "error": "connection refused"},
    )
    out = hp._phase_self_report(state)
    assert out.status == hp.PhaseStatus.OK
    assert out.details["published"] is False
    assert "refused" in out.details["warning"]
