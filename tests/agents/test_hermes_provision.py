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

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes_provision as hp

from ._hermes_fakes import fake_hermes_run


@pytest.fixture
def state_with_tmp_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> hp.BootstrapState:
    """Seed a :class:`BootstrapState` rooted in ``tmp_path``.

    Path CONSTANTS are redirected here (they intentionally stay
    module-level per #702); behavioural IO is faked via the companion
    ``pipeline_io`` fixture's :class:`hp.PhaseIO` instead of
    monkeypatching module globals.
    """
    var_lib = tmp_path / "var" / "lib" / "hal0"
    var_lib.mkdir(parents=True)
    venv = var_lib / "venvs" / "hermes"
    hermes_home = var_lib / "agents" / "hermes"
    monkeypatch.setattr(hp, "MIN_FREE_GIB", 0)  # /tmp may be tmpfs with little headroom
    monkeypatch.setattr(
        hp, "WRAPPER_INSTALL_PATH", tmp_path / "usr" / "local" / "bin" / "hal0-hermes"
    )
    monkeypatch.setattr(
        hp, "HERMES_CLI_INSTALL_PATH", tmp_path / "usr" / "local" / "bin" / "hermes"
    )
    # #437 gateway_secrets_wire: redirect the SYSTEM drop-in dir under
    # tmp_path so a pipeline run never touches the live /etc/systemd/system
    # — even when the test runner is root.
    _dropin_dir = tmp_path / "etc" / "systemd" / "system" / "hermes-gateway.service.d"
    monkeypatch.setattr(hp, "GATEWAY_SYSTEMD_DROPIN_DIR", _dropin_dir)
    monkeypatch.setattr(hp, "GATEWAY_SYSTEMD_DROPIN_FILE", _dropin_dir / "10-hal0-secrets.conf")
    # Foreign-gateway preflight scans user systemd dirs; point them at an empty
    # tmp dir so a run on a real host (which may have a live hermes-gateway) stays
    # hermetic. The pipeline_io `run` seam also stubs systemctl is-active/pgrep.
    monkeypatch.setattr(hp, "_USER_SYSTEMD_SCAN_GLOBS", (str(tmp_path / "no-such-user-systemd"),))
    return hp.BootstrapState(venv=str(venv), hermes_home=str(hermes_home))


@pytest.fixture
def pipeline_io() -> hp.PhaseIO:
    """The IO seam bundle for pipeline-level runs (#702).

    The real preflight + install phases reach for ``/var/lib/hal0/*``,
    spawn ``python -m venv``, and HTTP-poke ``127.0.0.1:8080``; the
    config/mcp/memory phases would block on real urlopens. Fake every
    seam through ``PhaseIO`` so the orchestrator's behaviour is what's
    under test, not the LXC.

    ``run`` intercepts ONLY ``systemctl daemon-reload`` (the live-systemd
    action the gateway phase would issue). Everything else — env_probe's
    ``systemd-detect-virt``, smoke-test exec — passes through to the real
    subprocess so those phases behave as before.
    """
    real_run = subprocess.run

    class _NoopCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    class _InactiveCompleted:
        returncode = 3
        stdout = "inactive\n"
        stderr = ""

    class _NoMatchCompleted:
        returncode = 1
        stdout = ""
        stderr = ""

    def _guarded_run(argv: Any, *a: Any, **kw: Any) -> Any:
        head = list(argv[:2]) if isinstance(argv, (list, tuple)) else []
        if head == ["systemctl", "daemon-reload"]:
            return _NoopCompleted()
        # Foreign-gateway preflight probes — stub them so a run on a real host
        # (which may have a live system hermes-gateway) doesn't false-positive.
        if head == ["systemctl", "is-active"]:
            return _InactiveCompleted()
        if isinstance(argv, (list, tuple)) and argv and argv[0] == "pgrep":
            return _NoMatchCompleted()
        return real_run(argv, *a, **kw)

    def _fake_install(v: Path, _req: Path, **_kwargs: Any) -> None:
        (v / "bin").mkdir(parents=True, exist_ok=True)
        (v / "bin" / "hermes").write_text("#!/bin/sh\nexit 0\n")
        (v / "bin" / "hermes").chmod(0o755)

    return hp.PhaseIO(
        http_get=lambda *_a, **_kw: 200,
        fetch_slots=lambda: [],
        fetch_model_contexts=lambda: {},
        probe_mcp_server=lambda _url, **_kw: {"ok": True, "tools": ["t1"], "error": None},
        mcp_memory_call=lambda *_a, **_kw: {"ok": True, "result": {"items": [], "id": "x"}},
        install_venv=_fake_install,
        run=_guarded_run,
    )


def test_phase_names_in_planned_order() -> None:
    """The planned phases stay in the documented order.

    Mirrors `docs/internal/hermes-bootstrap-plan-2026-05-23.md` §3 +
    PR-3's persona_seed insertion — if a slice re-orders or drops a
    phase, this guard catches it before the integration scenario
    notices.
    """
    expected = (
        "preflight",
        "install",
        "env_probe",
        "home_init",
        # #432: install_artifacts writes the manager seed + driver env +
        # runtime.json right after $HERMES_HOME exists and before mcp_wire
        # reads the seed allow-list.
        "install_artifacts",
        # PR-3 (v0.3): persona_seed inserted before config_write so the
        # first render carries the active persona's system_prompt
        # prelude (Phase 7) on the same pass that lands chat_slots.
        "persona_seed",
        "config_write",
        "mcp_wire",
        "context_link",
        "namespace_register",
        "brain_profile_seed",
        "brain_profile_mcp_wire",
        "model_automap",
        "voice_wire",
        # (ownership_reconcile removed — §7.4 F.7: born hal0:hal0, nothing to
        # reconcile.)
        # #437 (SYSTEM scope): the gateway secrets drop-in lands after
        # voice_wire (which may write the vault it references) and before
        # smoke_tests.
        "gateway_secrets_wire",
        "smoke_tests",
        "self_report",
    )
    assert expected == hp.PHASE_NAMES


def test_run_marks_every_phase_ok_on_fresh(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState, pipeline_io: hp.PhaseIO
) -> None:
    result = hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths, io=pipeline_io)
    # voice_wire legitimately returns SKIP when no STT/TTS slots are
    # configured (most CI envs); gateway_secrets_wire SKIPs when the test
    # runner is non-root (can't write /etc/systemd/system). Accept both
    # OK and SKIP for those phases.
    # #432: install_artifacts SKIPs under the pytest sandbox guard when the
    # /etc seed/env paths aren't monkeypatched (same posture as
    # gateway_secrets_wire); its write path is covered by
    # test_hermes_provision_install_artifacts.py.
    skip_ok = {"voice_wire", "gateway_secrets_wire", "install_artifacts"}
    for name in hp.PHASE_NAMES:
        status = result.phases[name]["status"]
        allowed = {hp.PhaseStatus.OK.value} | (
            {hp.PhaseStatus.SKIP.value} if name in skip_ok else set()
        )
        assert status in allowed, f"{name}: unexpected {status}"
    assert result.failed == []


def test_state_file_written_and_round_trips(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState, pipeline_io: hp.PhaseIO
) -> None:
    hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths, io=pipeline_io)
    state_file = tmp_path / "provision.json"
    assert state_file.exists()
    loaded = hp.BootstrapState.load(tmp_path)
    assert loaded is not None
    assert loaded.schema_version == hp.SCHEMA_VERSION
    assert set(loaded.phases.keys()) >= set(hp.PHASE_NAMES)
    assert loaded.completed_at is not None


def test_rerun_is_noop_when_all_phases_ok(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState, pipeline_io: hp.PhaseIO
) -> None:
    hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths, io=pipeline_io)
    second = hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths, io=pipeline_io)
    # All phases skipped because their checkpoint is already ok — no always_run
    # phase remains after the ownership_reconcile removal (§7.4 F.7).
    assert set(second.skipped) == set(hp.PHASE_NAMES)
    assert second.failed == []


def test_repair_flag_forces_rerun(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState, pipeline_io: hp.PhaseIO
) -> None:
    hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths, io=pipeline_io)
    second = hp.run(
        state_root=tmp_path, initial_state=state_with_tmp_paths, repair=True, io=pipeline_io
    )
    # Repair re-runs everything → nothing was skipped via checkpoint.
    assert second.skipped == []
    # voice_wire legitimately returns SKIP when no STT/TTS slots exist;
    # gateway_secrets_wire SKIPs when the test runner is non-root (can't
    # write /etc/systemd/system). Accept both OK and SKIP for those phases
    # (same posture as the fresh-run test above).
    # #432: install_artifacts SKIPs under the pytest sandbox guard when the
    # /etc seed/env paths aren't monkeypatched (same posture as
    # gateway_secrets_wire); its write path is covered by
    # test_hermes_provision_install_artifacts.py.
    skip_ok = {"voice_wire", "gateway_secrets_wire", "install_artifacts"}
    for name in hp.PHASE_NAMES:
        status = second.phases[name]["status"]
        allowed = {hp.PhaseStatus.OK.value} | (
            {hp.PhaseStatus.SKIP.value} if name in skip_ok else set()
        )
        assert status in allowed, f"{name}: unexpected {status}"


def test_skip_phase_records_skip_reason(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState, pipeline_io: hp.PhaseIO
) -> None:
    result = hp.run(
        state_root=tmp_path,
        initial_state=state_with_tmp_paths,
        skip_phases=("voice_wire", "smoke_tests"),
        io=pipeline_io,
    )
    assert result.phases["voice_wire"]["status"] == hp.PhaseStatus.SKIP.value
    assert result.phases["voice_wire"]["reason"] == "--skip-phase"
    assert result.phases["smoke_tests"]["status"] == hp.PhaseStatus.SKIP.value
    # Other phases run as normal.
    assert result.phases["preflight"]["status"] == hp.PhaseStatus.OK.value


def test_dry_run_skips_state_persistence(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState, pipeline_io: hp.PhaseIO
) -> None:
    hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths, dry_run=True, io=pipeline_io)
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
    pipeline_io: hp.PhaseIO,
) -> None:
    def _failing(_ctx: hp.PhaseContext) -> hp.PhaseResult:
        return hp.PhaseResult(status=hp.PhaseStatus.FAIL, reason="forced")

    new_phases = [
        dataclasses.replace(p, fn=_failing) if p.name == "env_probe" else p for p in hp.PHASES
    ]
    monkeypatch.setattr(hp, "PHASES", new_phases)

    result = hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths, io=pipeline_io)
    assert "env_probe" in result.failed
    assert result.state.completed_at is None
    assert any("env_probe" in e for e in result.state.errors)


def test_cli_entry_returns_zero_on_success(
    tmp_path: Path,
    state_with_tmp_paths: hp.BootstrapState,
    monkeypatch: pytest.MonkeyPatch,
    pipeline_io: hp.PhaseIO,
) -> None:
    # bootstrap_cli doesn't take an initial_state kwarg directly — wrap
    # `run` so the test still threads the tmp-rooted state through.
    real_run = hp.run

    def _wrapped(**kwargs: Any) -> hp.RunResult:
        kwargs.setdefault("initial_state", state_with_tmp_paths)
        kwargs.setdefault("io", pipeline_io)
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
    pipeline_io: hp.PhaseIO,
) -> None:
    def _failing(_ctx: hp.PhaseContext) -> hp.PhaseResult:
        return hp.PhaseResult(status=hp.PhaseStatus.FAIL, reason="boom")

    new_phases = [
        dataclasses.replace(p, fn=_failing) if p.name == "preflight" else p for p in hp.PHASES
    ]
    monkeypatch.setattr(hp, "PHASES", new_phases)

    real_run = hp.run

    def _wrapped(**kwargs: Any) -> hp.RunResult:
        kwargs.setdefault("initial_state", state_with_tmp_paths)
        kwargs.setdefault("io", pipeline_io)
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
    # Pin hermes_home under the tmp tree too — preflight now write-probes the
    # real $HERMES_HOME, and the default points at the live /var/lib/hal0/.hermes.
    state = hp.BootstrapState(venv=str(venv), hermes_home=str(var_lib / ".hermes"))
    monkeypatch.setattr(hp, "MIN_FREE_GIB", 0)
    io = hp.PhaseIO(http_get=lambda *_a, **_kw: 200)
    out = hp._phase_preflight(hp.context_for("preflight", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["python_version"]
    assert out.details["daemon_http_status"] == 200


def test_preflight_fails_on_unreachable_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    var_lib = tmp_path / "var" / "lib" / "hal0"
    var_lib.mkdir(parents=True)
    state = hp.BootstrapState(
        venv=str(var_lib / "venvs" / "hermes"), hermes_home=str(var_lib / ".hermes")
    )
    io = hp.PhaseIO(http_get=lambda *_a, **_kw: 0)
    out = hp._phase_preflight(hp.context_for("preflight", state, io=io))
    assert out.status == hp.PhaseStatus.FAIL
    assert "daemon unreachable" in (out.reason or "")


def test_preflight_fails_on_var_lib_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    io = hp.PhaseIO(http_get=lambda *_a, **_kw: 200)
    state = hp.BootstrapState(venv=str(tmp_path / "nope" / "venvs" / "hermes"))
    # /var/lib (nearest existing ancestor of the default $HERMES_HOME) isn't
    # writable to a normal user — stub the probe so the test is deterministic
    # regardless of the runner's uid (CI may run as root).
    monkeypatch.setattr(hp, "path_is_writable", lambda _p: False)
    out = hp._phase_preflight(hp.context_for("preflight", state, io=io))
    assert out.status == hp.PhaseStatus.FAIL
    assert "not writable" in (out.reason or "")


def test_preflight_fails_when_hermes_home_unwritable_but_var_lib_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Fedora bug: /var/lib/hal0 is writable (venv provisions fine) but a
    pre-existing root-owned $HERMES_HOME isn't — a var_lib-only check sailed
    past this and detonated at env_probe. Preflight must now catch it."""
    var_lib = tmp_path / "var" / "lib" / "hal0"
    var_lib.mkdir(parents=True)
    venv = var_lib / "venvs" / "hermes"
    hermes_home = var_lib / ".hermes"
    hermes_home.mkdir()  # exists, "owned by root" → unwritable to us
    state = hp.BootstrapState(venv=str(venv), hermes_home=str(hermes_home))
    monkeypatch.setattr(hp, "MIN_FREE_GIB", 0)
    io = hp.PhaseIO(http_get=lambda *_a, **_kw: 200)

    real = hp.path_is_writable
    monkeypatch.setattr(
        hp,
        "path_is_writable",
        lambda p: False if str(p) == str(hermes_home) else real(p),
    )
    out = hp._phase_preflight(hp.context_for("preflight", state, io=io))
    assert out.status == hp.PhaseStatus.FAIL
    assert "not writable" in (out.reason or "")
    assert str(hermes_home) in (out.reason or "")


def test_path_is_writable_probes_real_filesystem(tmp_path: Path) -> None:
    """Unit-cover the probe helper: writable dir → True; non-existent target
    resolves to its nearest existing ancestor."""
    writable = tmp_path / "writable"
    writable.mkdir()
    assert hp.path_is_writable(writable) is True
    # A target that doesn't exist yet resolves up to tmp_path (writable).
    assert hp.path_is_writable(writable / "deep" / "nested" / "venv") is True


def test_home_init_creates_layout_with_marker(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes_home"
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    out = hp._phase_home_init(hp.context_for("home_init", state))
    assert out.status == hp.PhaseStatus.OK
    assert hermes_home.is_dir()
    assert (hermes_home / ".hal0-managed").is_file()
    for sub in ("memories", "skills", "plugins/memory", "plugins/model-providers", "logs"):
        assert (hermes_home / sub).is_dir()


def test_home_init_idempotent_on_managed_dir(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes_home"
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    hp._phase_home_init(hp.context_for("home_init", state))
    marker_before = (hermes_home / ".hal0-managed").read_text()
    out2 = hp._phase_home_init(hp.context_for("home_init", state))
    assert out2.status == hp.PhaseStatus.OK
    assert (hermes_home / ".hal0-managed").read_text() == marker_before


def test_home_init_refuses_to_clobber_non_managed_dir(tmp_path: Path) -> None:
    hermes_home = tmp_path / "user_hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("# user file")
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    out = hp._phase_home_init(hp.context_for("home_init", state))
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
    hermes_cli_dst = tmp_path / "usr" / "local" / "bin" / "hermes"
    monkeypatch.setattr(hp, "WRAPPER_INSTALL_PATH", wrapper_dst)
    monkeypatch.setattr(hp, "HERMES_CLI_INSTALL_PATH", hermes_cli_dst)
    hermes_home = tmp_path / "hermes_home"
    state = hp.BootstrapState(venv=str(venv), hermes_home=str(hermes_home))

    called: list[Any] = []

    def _no_install(*args: Any, **kwargs: Any) -> None:
        called.append(args)

    out = hp._phase_install(
        hp.context_for("install", state, io=hp.PhaseIO(install_venv=_no_install))
    )
    assert out.status == hp.PhaseStatus.OK
    assert called == []
    # Canonical `hermes` is a real file; `hal0-hermes` is a back-compat
    # symlink to it (#437 wrapper consolidation).
    assert hermes_cli_dst.is_file()
    assert wrapper_dst.is_symlink()
    assert wrapper_dst.resolve() == hermes_cli_dst.resolve()
    # PR-1-bundle: the legacy hal0 model-provider plugin is no longer
    # copied — it hardcoded an :8000 base_url that has no listener and
    # the composite ``hal0`` upstream in hal0.api supersedes it.
    assert not (hermes_home / "plugins" / "model-providers" / "hal0").exists()
    assert (hermes_home / "plugins" / "hal0-memory" / "__init__.py").is_file()


def test_install_phase_runs_venv_install_when_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "venv"
    wrapper_dst = tmp_path / "usr" / "local" / "bin" / "hal0-hermes"
    hermes_cli_dst = tmp_path / "usr" / "local" / "bin" / "hermes"
    monkeypatch.setattr(hp, "WRAPPER_INSTALL_PATH", wrapper_dst)
    monkeypatch.setattr(hp, "HERMES_CLI_INSTALL_PATH", hermes_cli_dst)
    hermes_home = tmp_path / "hermes_home"
    state = hp.BootstrapState(venv=str(venv), hermes_home=str(hermes_home))

    install_calls: list[Path] = []

    def _fake_install(v: Path, _req: Path, **_kwargs: Any) -> None:
        install_calls.append(v)
        (v / "bin").mkdir(parents=True, exist_ok=True)
        (v / "bin" / "hermes").write_text("#!/bin/sh\nexit 0\n")
        (v / "bin" / "hermes").chmod(0o755)

    out = hp._phase_install(
        hp.context_for("install", state, io=hp.PhaseIO(install_venv=_fake_install))
    )
    assert out.status == hp.PhaseStatus.OK
    assert install_calls == [venv]


def test_resolve_python_prefers_newest_explicit_in_range() -> None:
    # All of 3.11-3.13 on PATH → the newest wins; 3.14 is never probed.
    probed: list[str] = []

    def _prober(name: str) -> str | None:
        probed.append(name)
        return f"/opt/{name}/bin/{name}"

    out = hp._resolve_supported_python(prober=_prober)
    assert out == "/opt/python3.13/bin/python3.13"
    assert "python3.14" not in probed


def test_resolve_python_walks_down_to_oldest_supported() -> None:
    out = hp._resolve_supported_python(
        prober=lambda name: "/usr/bin/python3.11" if name == "python3.11" else None
    )
    assert out == "/usr/bin/python3.11"


def test_resolve_python_falls_back_to_sys_executable_in_range() -> None:
    out = hp._resolve_supported_python(prober=lambda _name: None, running=(3, 12))
    assert out == sys.executable


def test_resolve_python_rejects_unsupported_running_interpreter() -> None:
    # The Ubuntu 26.04 case (#1248): only a 3.14 interpreter exists. The old
    # ">= 3.11" fallback accepted it and pip resolved the broken
    # hermes-agent 0.15.2; now the resolver must return None so the
    # preflight fails with the actionable range message.
    assert hp._resolve_supported_python(prober=lambda _name: None, running=(3, 14)) is None
    assert hp._resolve_supported_python(prober=lambda _name: None, running=(3, 10)) is None


def test_preflight_fails_actionably_when_no_supported_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    var_lib = tmp_path / "var" / "lib" / "hal0"
    var_lib.mkdir(parents=True)
    state = hp.BootstrapState(
        venv=str(var_lib / "venvs" / "hermes"), hermes_home=str(var_lib / ".hermes")
    )
    monkeypatch.setattr(hp, "MIN_FREE_GIB", 0)
    monkeypatch.setattr(hp, "_resolve_supported_python", lambda *a, **k: None)
    monkeypatch.setattr(hp, "_uv_available", lambda *a, **k: None)
    io = hp.PhaseIO(http_get=lambda *_a, **_kw: 200)
    out = hp._phase_preflight(hp.context_for("preflight", state, io=io))
    assert out.status == hp.PhaseStatus.FAIL
    assert "3.11-3.13" in (out.reason or "")
    assert "deadsnakes" in (out.reason or "")
    assert "uv" in (out.reason or "")


def test_preflight_passes_when_uv_can_provision_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 3.14-only host WITH uv: preflight must pass (availability check only,
    # no download) and flag the fallback — the install phase does the fetch.
    var_lib = tmp_path / "var" / "lib" / "hal0"
    var_lib.mkdir(parents=True)
    state = hp.BootstrapState(
        venv=str(var_lib / "venvs" / "hermes"), hermes_home=str(var_lib / ".hermes")
    )
    monkeypatch.setattr(hp, "MIN_FREE_GIB", 0)
    monkeypatch.setattr(hp, "_resolve_supported_python", lambda *a, **k: None)
    monkeypatch.setattr(hp, "_uv_available", lambda *a, **k: "/usr/local/bin/uv")
    io = hp.PhaseIO(http_get=lambda *_a, **_kw: 200)
    out = hp._phase_preflight(hp.context_for("preflight", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["uv_python_fallback"] is True


def test_ensure_python_prefers_system_interpreter_over_uv() -> None:
    # A qualifying system Python must win without uv ever being invoked.
    ran: list[list[str]] = []

    class _Runner:
        @staticmethod
        def run(argv: list[str], **_kw: Any) -> None:
            ran.append(argv)

    out = hp._ensure_supported_python(
        prober=lambda name: "/usr/bin/python3.12" if name == "python3.12" else None,
        runner=_Runner,
    )
    assert out == "/usr/bin/python3.12"
    assert ran == []


def test_ensure_python_provisions_via_uv_when_no_system_interpreter() -> None:
    ran: list[list[str]] = []
    envs: list[dict[str, str] | None] = []

    class _Result:
        stdout = "/var/lib/hal0/python/cpython-3.13.5-linux-x86_64-gnu/bin/python3.13\n"

    class _Runner:
        @staticmethod
        def run(argv: list[str], *, env: dict[str, str] | None = None, **_kw: Any) -> _Result:
            ran.append(argv)
            envs.append(env)
            return _Result()

    out = hp._ensure_supported_python(
        prober=lambda name: "/usr/local/bin/uv" if name == "uv" else None,
        runner=_Runner,
        running=(3, 14),
    )
    assert out == "/var/lib/hal0/python/cpython-3.13.5-linux-x86_64-gnu/bin/python3.13"
    # install runs before find, both pinned to UV_PYTHON_FALLBACK...
    assert [a[1:3] for a in ran] == [["python", "install"], ["python", "find"]]
    assert all(a[3] == hp.UV_PYTHON_FALLBACK for a in ran)
    # ...and both redirected to the world-readable install dir (the default
    # ~/.local/share/uv under root's 0700 home would be unreachable by the
    # hal0 user the venv runs as).
    assert all(
        e is not None and e["UV_PYTHON_INSTALL_DIR"] == str(hp.UV_PYTHON_INSTALL_DIR) for e in envs
    )


def test_provision_via_uv_creates_install_dir_world_traversable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Under a restrictive umask, root's uv would write a 0700 tree the hal0
    # service user cannot traverse to reach the symlinked base interpreter —
    # the exact "gateway venv python won't start" failure the /var/lib/hal0
    # move was meant to fix. The install dir must be created 0o755 *before*
    # uv is invoked, so record its mode at the moment the runner is called.
    install_dir = tmp_path / "python"
    monkeypatch.setattr(hp, "UV_PYTHON_INSTALL_DIR", install_dir)

    modes_at_run: list[int] = []

    class _Result:
        stdout = f"{install_dir}/cpython-3.13/bin/python3.13\n"

    class _Runner:
        @staticmethod
        def run(argv: list[str], **_kw: Any) -> _Result:
            modes_at_run.append(install_dir.stat().st_mode & 0o777)
            return _Result()

    old_umask = os.umask(0o077)
    try:
        out = hp._ensure_supported_python(
            prober=lambda name: "/usr/local/bin/uv" if name == "uv" else None,
            runner=_Runner,
            running=(3, 14),
        )
    finally:
        os.umask(old_umask)

    assert out == f"{install_dir}/cpython-3.13/bin/python3.13"
    # World-traversable despite the 077 umask (a plain mkdir would be 0700)...
    assert install_dir.is_dir()
    assert install_dir.stat().st_mode & 0o777 == 0o755
    # ...and already so by the time uv's first subcommand ran.
    assert modes_at_run and modes_at_run[0] == 0o755


def test_ensure_python_returns_none_without_uv() -> None:
    out = hp._ensure_supported_python(prober=lambda _name: None, running=(3, 14))
    assert out is None


def test_ensure_python_returns_none_when_uv_fetch_fails() -> None:
    class _Runner:
        @staticmethod
        def run(argv: list[str], **_kw: Any) -> None:
            raise subprocess.CalledProcessError(1, argv)

    out = hp._ensure_supported_python(
        prober=lambda name: "/usr/local/bin/uv" if name == "uv" else None,
        runner=_Runner,
        running=(3, 14),
    )
    assert out is None


def test_install_venv_rebuilds_venv_on_unsupported_interpreter(tmp_path: Path) -> None:
    # A venv already built on 3.14 (the pre-guard fallback) can never
    # converge by pip alone — _install_venv must detect it from the
    # lib/pythonX.Y layout and rebuild on the resolved interpreter.
    venv = tmp_path / "venv"
    (venv / "lib" / "python3.14" / "site-packages").mkdir(parents=True)
    stale_marker = venv / "lib" / "python3.14" / "site-packages" / "hermes_cli"
    stale_marker.mkdir()
    calls: list[list[str]] = []

    class _Runner:
        @staticmethod
        def run(argv: list[str], check: bool) -> None:
            calls.append(argv)

    hp._install_venv(
        venv, tmp_path / "req.txt", runner=_Runner, python_resolver=lambda: "/usr/bin/python3.13"
    )
    assert not stale_marker.exists()  # stale 3.14 tree removed
    assert calls[0][:3] == ["/usr/bin/python3.13", "-m", "venv"]


def test_install_venv_keeps_supported_venv(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    (venv / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
    calls: list[list[str]] = []

    class _Runner:
        @staticmethod
        def run(argv: list[str], check: bool) -> None:
            calls.append(argv)

    hp._install_venv(
        venv, tmp_path / "req.txt", runner=_Runner, python_resolver=lambda: "/usr/bin/python3.12"
    )
    # No `-m venv` call — straight to pip into the existing venv.
    assert all(argv[2] != "venv" for argv in calls if len(argv) > 2)


def test_requirements_floor_blocks_broken_hermes_agent() -> None:
    # Regression guard for #1247: hermes-agent 0.15.2's wheel is broken
    # (imports hermes_cli.dashboard_auth, ships without it). The shipped
    # requirement must foreclose that broken build by ONE of two *reviewed*
    # means — never an arbitrary/unreviewed pin:
    #   * a version floor >= 0.16.0, so no resolver (incl. old-Python wheel
    #     fallbacks) can select 0.15.2; OR
    #   * a pin to a commit/tag in hp.VETTED_HERMES_REFS — the production
    #     posture, where the ref was built from vetted upstream source.
    text = hp.HERMES_REQUIREMENTS.read_text()
    line = hp._hermes_requirement_line(text)
    floor = hp.hermes_requirement_floor(line)
    ref = hp.hermes_pinned_ref(line)
    floored = floor is not None and floor >= hp.HERMES_MIN_VERSION
    vetted_pin = ref is not None and ref in hp.VETTED_HERMES_REFS
    assert floored or vetted_pin, (
        f"hermes requirement neither floored >= {hp.HERMES_MIN_VERSION} nor pinned "
        f"to a vetted ref — a broken/unreviewed build could be selected: {line!r}"
    )
    # Protection intent: a VCS pin that is NOT on the reviewed allowlist must
    # never satisfy the guard (that is exactly how a broken 0.15.2 commit, or
    # any unvetted ref, would sneak past a floor-only check).
    if ref is not None and not floored:
        assert ref in hp.VETTED_HERMES_REFS, f"unreviewed hermes ref pinned: {line!r}"
    # Single source of truth for the allowlist + broken-build floor.
    assert hp.hermes_requirement_is_vetted(text)


def test_shipped_requirement_is_the_vetted_commit_pin() -> None:
    # The requirement string landed on this branch pins the reviewed
    # NousResearch commit; confirm the classifier recognizes it as a VCS pin
    # (no numeric floor) whose ref is on the allowlist.
    line = hp._hermes_requirement_line(hp.HERMES_REQUIREMENTS.read_text())
    assert hp.hermes_requirement_floor(line) is None
    assert hp.hermes_pinned_ref(line) in hp.VETTED_HERMES_REFS


@pytest.mark.parametrize(
    ("line", "vetted"),
    [
        # Vetted commit pin — the production posture.
        (
            "hermes-agent[web] @ git+https://github.com/NousResearch/hermes-agent.git@"
            "9de9c25f620ff7f1ce0fd5457d596052d5159596",
            True,
        ),
        # Floored+capped spec — blocks 0.15.2 numerically.
        ("hermes-agent[web]>=0.16.0,<1.0", True),
        # Broken build hard-pinned by version — rejected (below floor, no ref).
        ("hermes-agent[web]==0.15.2", False),
        # Sub-floor floor — 0.15.x still selectable, rejected.
        ("hermes-agent[web]>=0.15.0,<1.0", False),
        # Unreviewed commit pin — NOT on the allowlist, rejected.
        (
            "hermes-agent[web] @ git+https://github.com/NousResearch/hermes-agent.git@"
            "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            False,
        ),
    ],
)
def test_requirement_vetted_classifier_enforces_protections(line: str, vetted: bool) -> None:
    # The broken-build protection lives in one seam; exercise both accept paths
    # (vetted ref / >=0.16.0 floor) and the reject paths (sub-floor, hard-pinned
    # broken version, unreviewed commit) directly.
    assert hp.hermes_requirement_is_vetted(line) is vetted


def _version_pin_for(monkeypatch, tmp_path: Path, contents: str) -> str:
    """Run hp._hermes_version_pin() against a synthetic requirements.txt."""
    req = tmp_path / "installer" / "agents" / "hermes" / "requirements.txt"
    req.parent.mkdir(parents=True, exist_ok=True)
    req.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(hp, "REPO_ROOT_FOR_INSTALLER", tmp_path)
    return hp._hermes_version_pin()


def test_version_pin_reports_commit_ref_without_crashing(monkeypatch, tmp_path) -> None:
    # #3: a git-commit pin has no PEP 440 version. The runtime identifier path
    # (identity card / self-report) must degrade to the short ref, not crash or
    # go blank, on the shipped commit-pinned requirement.
    pin = _version_pin_for(
        monkeypatch,
        tmp_path,
        "# comment\nhermes-agent[web] @ git+https://github.com/NousResearch/"
        "hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596\n",
    )
    assert pin == "9de9c25f620f"


def test_version_pin_reports_exact_floored_and_unknown_forms(monkeypatch, tmp_path) -> None:
    assert _version_pin_for(monkeypatch, tmp_path, "hermes-agent[web]==0.18.2\n") == "0.18.2"
    assert _version_pin_for(monkeypatch, tmp_path, "hermes-agent[web]>=0.16.0,<1.0\n") == ">=0.16.0"
    assert _version_pin_for(monkeypatch, tmp_path, "# only comments\n") == "unknown"


# ── #241 phase impls — env_probe / config_write ─────────────────────────────


def test_env_probe_writes_snapshot_to_hermes_home(tmp_path: Path) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    out = hp._phase_env_probe(hp.context_for("env_probe", state))
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
    # exposes the OpenAI surface at `/v1`.
    assert out["base_url"] == "http://127.0.0.1:8080/v1"
    assert out["context_length"] == 16384


def test_resolve_primary_slot_fallback_when_no_slots() -> None:
    out = hp._resolve_primary_slot(slots_fetcher=lambda: [])
    assert out["model"] == "primary"
    assert out["context_length"] == 32768
    # Placeholder points at hal0-api on 8080/v1, not the legacy
    # phantom on 8000.
    assert out["base_url"] == "http://127.0.0.1:8080/v1"


def _build_overlay_keys(**over):
    """Helper: run _build_config_overlay with sane defaults → ``{key: value}``."""
    base = dict(
        primary={
            "model_id": "qwen3:8b",
            "backend_url": "http://127.0.0.1:8080/v1",
            "context_length": 16384,
        },
        chat_slots=[],
        delegation=None,
        auxiliary_tasks={},
        mcp_servers=[],
        agent_id="hermes-agent",
        system_prompt="",
        personality_name="",
        live_resolve_enabled=True,
    )
    base.update(over)
    return dict(hp._build_config_overlay(**base))


def test_overlay_includes_provider_and_identity_keys() -> None:
    keys = _build_overlay_keys(
        mcp_servers=[{"name": "hal0-admin", "url": "http://x/mcp", "type": "http"}],
    )
    assert keys["model.provider"] == "custom"
    assert keys["mcp_servers.hal0-admin.headers.X-hal0-Agent"] == "hermes-agent"
    # feat/honcho-memory: memory.graph.* dropped entirely — dead config,
    # configures hal0's own Hindsight engine, not anything hermes reads.
    assert "memory.graph.enabled" not in keys
    # model.context_length is NEVER set — hermes treats it as a global override
    # that bleeds onto cloud models.
    assert "model.context_length" not in keys


def test_overlay_no_primary_under_live_resolve_uses_virtual() -> None:
    # No ready slot → _resolve_primary_slot hands a placeholder primary, but
    # under live-resolve the overlay still points at the hal0/chat virtual
    # against the gateway (not a dead default).
    keys = _build_overlay_keys(
        primary={
            "model_id": "primary",
            "backend_url": "http://127.0.0.1:8080/v1",
            "context_length": 32768,
        },
    )
    # ADR-0023: the canonical default virtual is hal0/agent (was hal0/chat).
    assert keys["model.default"] == "hal0/agent"
    assert "127.0.0.1:8080/v1" in keys["model.base_url"]


def test_overlay_chat_slots_become_aliases() -> None:
    keys = _build_overlay_keys(
        chat_slots=[{"alias": "coder", "model_id": "qwen-coder", "backend_url": "http://x"}],
    )
    assert keys["model_aliases.coder.model"] == "qwen-coder"
    assert keys["model_aliases.coder.provider"] == "custom"
    assert keys["model_aliases.coder.base_url"] == "http://x"


# ── feat/hermes-role-slots: per-model context via custom_providers ───────────


def test_collect_chat_slots_carries_context_length() -> None:
    slots = [
        {
            "name": "primary",
            "type": "llm",
            "state": "ready",
            "model_id": "m1",
            "backend_url": "http://127.0.0.1:8001/v1",
            "context_length": 65536,
        },
        # ctx_size is the alternate key — must still resolve.
        {
            "name": "utility",
            "type": "llm",
            "state": "ready",
            "model_id": "m2",
            "backend_url": "http://127.0.0.1:8002/v1",
            "ctx_size": 8192,
        },
        # No context at all → None (degrade-safe).
        {
            "name": "agent-hermes",
            "type": "llm",
            "state": "ready",
            "model_id": "m3",
            "backend_url": "http://127.0.0.1:8003/v1",
        },
    ]
    collected = hp._collect_chat_slots(slots)
    by_model = {s["model_id"]: s["context_length"] for s in collected}
    assert by_model == {"m1": 65536, "m2": 8192, "m3": None}


# custom_providers (per-model context_length block) was dropped in the
# config-set redesign: it is a YAML LIST that `hermes config set` can't
# express, and under live-resolve + discover_models hal0-api's /v1/models
# already serves per-model context_length. Its tests were removed with it.


# ── feat/hermes-role-slots: delegation + auxiliary role→slot wiring ──────────

_ROLE_SLOTS = [
    {
        "name": "chat",
        "type": "llm",
        "state": "ready",
        "model_id": "qwen3-coder-next-reap-40b-a3b-q4kxl",
        "backend_url": "http://127.0.0.1:8001/v1",
        "context_length": 32768,
    },
    {
        "name": "agent",
        "type": "llm",
        "state": "ready",
        "model_id": "hermes-4-14b-q5km",
        "backend_url": "http://127.0.0.1:8001/v1",
        "context_length": 65536,
    },
    {
        "name": "utility",
        "type": "llm",
        "state": "ready",
        "model_id": "qwen3-zero-coder-v2-0.8b-f16",
        "backend_url": "http://127.0.0.1:8001/v1",
        "context_length": 16384,
    },
]
_HAL0_V1 = "http://127.0.0.1:8080/v1"


def test_resolve_delegation_picks_agent_hermes_slot() -> None:
    deleg = hp._resolve_delegation(_ROLE_SLOTS, hal0_base_url=_HAL0_V1)
    assert deleg == {
        "model": "hermes-4-14b-q5km",
        "provider": "custom",
        "base_url": _HAL0_V1,
    }


def test_resolve_delegation_none_when_slot_absent() -> None:
    # Only primary present → no subagent slot → degrade to inherit-chat.
    assert hp._resolve_delegation(_ROLE_SLOTS[:1], hal0_base_url=_HAL0_V1) is None


def test_resolve_delegation_none_when_slot_not_ready() -> None:
    slots = [
        *_ROLE_SLOTS[:1],
        {"name": "agent", "type": "llm", "state": "idle", "model_id": "x"},
    ]
    assert hp._resolve_delegation(slots, hal0_base_url=_HAL0_V1) is None


def test_resolve_auxiliary_tasks_routes_utility_group_to_utility_slot() -> None:
    aux = hp._resolve_auxiliary_tasks(_ROLE_SLOTS, hal0_base_url=_HAL0_V1)
    # Utility group → custom provider on the utility slot's model.
    for task in ("compression", "session_search", "title_generation", "skills_hub", "mcp"):
        assert aux[task] == {
            "provider": "custom",
            "model": "qwen3-zero-coder-v2-0.8b-f16",
            "base_url": _HAL0_V1,
        }
    # vision/web_extract always stay on the main chat provider.
    for task in ("vision", "web_extract"):
        assert aux[task] == {"provider": "main", "model": "", "base_url": ""}


def test_resolve_auxiliary_tasks_degrades_to_main_without_utility_slot() -> None:
    aux = hp._resolve_auxiliary_tasks(_ROLE_SLOTS[:1], hal0_base_url=_HAL0_V1)
    for task in ("compression", "session_search", "title_generation"):
        assert aux[task]["provider"] == "main"
        assert aux[task]["model"] == ""


def test_resolve_auxiliary_tasks_routes_to_npu_virtual_when_utility_on_npu() -> None:
    # Utility role lives on the NPU slot (name 'npu', role not surfaced by
    # /api/slots) and there is NO slot named 'utility'. Aux group degrades
    # hal0/npu (the NPU virtual) so the gateway routes to the NPU slot.
    slots = [
        _ROLE_SLOTS[0],  # chat
        {
            "name": "npu",
            "type": "llm",
            "state": "ready",
            "device_class": "npu",
            "model_id": "gemma4-it-e2b-FLM",
            "context_length": 18000,
        },
    ]
    aux = hp._resolve_auxiliary_tasks(slots, hal0_base_url=_HAL0_V1)
    for task in ("compression", "session_search", "title_generation", "skills_hub", "mcp"):
        assert aux[task] == {
            "provider": "custom",
            "model": "hal0/npu",
            "base_url": _HAL0_V1,
        }
    for task in ("vision", "web_extract"):
        assert aux[task] == {"provider": "main", "model": "", "base_url": ""}


def test_overlay_emits_delegation_and_auxiliary_keys() -> None:
    deleg = hp._resolve_delegation(_ROLE_SLOTS, hal0_base_url=_HAL0_V1)
    aux = hp._resolve_auxiliary_tasks(_ROLE_SLOTS, hal0_base_url=_HAL0_V1)
    keys = _build_overlay_keys(
        primary={"model_id": "chat-m", "backend_url": _HAL0_V1, "context_length": 32768},
        chat_slots=hp._collect_chat_slots(_ROLE_SLOTS),
        delegation=deleg,
        auxiliary_tasks=aux,
    )
    # delegation → agent slot model at the hal0 /v1 endpoint.
    assert keys["delegation.model"] == "hermes-4-14b-q5km"
    assert keys["delegation.provider"] == "custom"
    assert keys["delegation.base_url"] == _HAL0_V1
    # auxiliary compaction/search/title → utility model at hal0 /v1.
    assert keys["auxiliary.compression.provider"] == "custom"
    assert keys["auxiliary.compression.model"] == "qwen3-zero-coder-v2-0.8b-f16"
    assert keys["auxiliary.compression.base_url"] == _HAL0_V1
    assert keys["auxiliary.session_search.model"] == "qwen3-zero-coder-v2-0.8b-f16"
    # vision stays on the main chat provider (no base_url key emitted).
    assert keys["auxiliary.vision.provider"] == "main"
    assert "auxiliary.vision.base_url" not in keys


def test_overlay_omits_delegation_when_slot_missing() -> None:
    aux = hp._resolve_auxiliary_tasks(_ROLE_SLOTS[:1], hal0_base_url=_HAL0_V1)
    keys = _build_overlay_keys(delegation=None, auxiliary_tasks=aux)
    assert not any(k.startswith("delegation.") for k in keys)
    # No utility slot → aux compaction group falls back to provider:"main".
    assert keys["auxiliary.compression.provider"] == "main"


def test_config_write_renders_role_slots_from_live_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    yaml = pytest.importorskip("yaml")
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_k: {
            "model": "qwen3-coder-next-reap-40b-a3b-q4kxl",
            "base_url": _HAL0_V1,
            "context_length": 32768,
        },
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no-overrides.yaml")
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.PhaseIO(
        fetch_slots=lambda: list(_ROLE_SLOTS),
        fetch_model_contexts=lambda: {},
        run=fake_hermes_run(),
    )
    out = hp._phase_config_write(hp.context_for("config_write", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["delegation_model"] == "hermes-4-14b-q5km"
    assert out.details["auxiliary_utility_model"] == "qwen3-zero-coder-v2-0.8b-f16"
    cfg = yaml.safe_load(Path(out.details["config_path"]).read_text())
    assert cfg["delegation"]["model"] == "hermes-4-14b-q5km"
    assert cfg["delegation"]["base_url"] == _HAL0_V1
    assert cfg["auxiliary"]["compression"]["model"] == "qwen3-zero-coder-v2-0.8b-f16"
    # No global model.context_length override (per-model context comes from
    # live /v1/models discovery now, not a custom_providers block).
    assert "context_length" not in cfg["model"]
    assert "custom_providers" not in cfg
    # The two irreducible list keys land via the targeted YAML merge.
    assert cfg["skills"]["external_dirs"] == hp.SKILLS_EXTERNAL_DIRS
    assert cfg["hooks"]["on_session_start"] == [hp.SESSION_START_HOOK]


# ── #661/#635: reasoning wiring — chat slot + display flags ──────────────────
#
# #635 wires the `chat` slot (Qwen3-class thinking-on model) as the top-level
# model with reasoning visible in the TUI. Three Hermes gotchas apply:
#   1. model.base_url MUST be set (provider:custom requires it) ← already done.
#   2. model.max_tokens MUST be set — else Qwen3 spends the full budget on
#      <think> and the content field comes back empty (silent TUI).
#   3. display.streaming AND display.show_reasoning BOTH required; without
#      streaming the TUI hangs on the reasoning block.
#
# Reconciliation with #661: delegation→`agent` (ace-saber MoE, thinking-off)
# is already wired via _DELEGATION_SLOT_NAME="agent". Hermes has one delegation
# config (no per-subagent-type routing), so the reasoning-ON path is the top-
# level chat conversation, not a separate subagent slot. The agent MoE stays
# thinking-off by design.


def test_overlay_has_show_reasoning_true() -> None:
    """display.show_reasoning: true is required for thinking-model TUI visibility
    (#635 gotcha: without it, reasoning output is silently suppressed)."""
    keys = _build_overlay_keys()
    assert keys["display.show_reasoning"] is True


def test_overlay_has_streaming_true() -> None:
    """display.streaming: true is required alongside show_reasoning — without it
    the TUI hangs on the <think> block waiting for the full response (#635)."""
    keys = _build_overlay_keys()
    assert keys["display.streaming"] is True


def test_overlay_has_model_max_tokens() -> None:
    """model.max_tokens must be a positive int — Qwen3 thinking models silently
    drain the budget in <think> and return empty content otherwise (#635)."""
    keys = _build_overlay_keys()
    max_tokens = keys["model.max_tokens"]
    assert isinstance(max_tokens, int) and max_tokens > 0


def test_overlay_model_base_url_set() -> None:
    """model.base_url is always set. Hermes's bare ``provider: custom`` requires
    it or it falls back to OpenRouter and 400s '... is not a valid model ID'
    (#635, memory hermes_bare_custom_needs_model_base_url). Under live-resolve
    the no-slot fallback still points at the gateway."""
    keys = _build_overlay_keys(
        primary={"model_id": "qwen3-27b", "backend_url": _HAL0_V1, "context_length": 32768},
    )
    assert keys["model.base_url"]
    # No-slot fallback (placeholder primary) still wires the gateway.
    fb = _build_overlay_keys(
        primary={"model_id": "primary", "backend_url": _HAL0_V1, "context_length": 32768},
    )
    assert fb["model.base_url"]


def test_delegation_targets_agent_slot_not_chat() -> None:
    """Delegation → `agent` MoE slot (thinking-off); chat stays on main model.

    This validates the #661/#635 reconciliation: #635 asked for
    'advanced-reasoning subagents → chat-27b' but Hermes has a single
    delegation config. Reasoning lives on the top-level chat conversation
    (show_reasoning + streaming); the agent MoE handles delegation.
    The chat slot model must NOT appear as the delegation model.
    """
    deleg = hp._resolve_delegation(_ROLE_SLOTS, hal0_base_url=_HAL0_V1)
    assert deleg is not None, "delegation must be set when the agent slot is live"
    # The delegation model is the agent MoE, not the chat-27b model.
    assert deleg["model"] == "hermes-4-14b-q5km", (
        "delegation.model must be the agent slot model "
        "(ace-saber MoE, thinking-off) — not the chat slot"
    )
    chat_model = "qwen3-coder-next-reap-40b-a3b-q4kxl"
    assert deleg["model"] != chat_model, (
        "delegation must NOT be the chat-slot model; "
        "reasoning runs on the top-level chat, not in subagents"
    )
    assert deleg["base_url"] == _HAL0_V1
    assert deleg["provider"] == "custom"


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
    # PR-3: _phase_config_write also fetches slots + renders the persona.
    # Fake both seams so the test stays offline.
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.PhaseIO(fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=fake_hermes_run())
    out1 = hp._phase_config_write(hp.context_for("config_write", state, io=io))
    assert out1.status == hp.PhaseStatus.OK
    cfg = Path(out1.details["config_path"])
    assert cfg.exists()
    first_hash = out1.hash
    # Re-run is idempotent: config set re-writes the same values + the YAML
    # merge is a no-op, so the on-disk file (and its hash) is unchanged.
    out2 = hp._phase_config_write(hp.context_for("config_write", state, io=io))
    assert out2.status == hp.PhaseStatus.OK
    assert out2.details["list_merge_changed"] is False
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
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.PhaseIO(fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=fake_hermes_run())
    out = hp._phase_config_write(hp.context_for("config_write", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    cfg = Path(out.details["config_path"]).read_text()
    assert "999" in cfg


# ── #702: silent fallbacks become observable ─────────────────────────────────


def test_config_write_records_fallbacks_for_placeholder_primary_and_default_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First-run posture: no ready slot + no mcp_wire checkpoint → both
    fallback sites land in details["fallbacks"]. Behaviour is unchanged
    — the fallbacks are recorded, not different."""
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no.yaml")
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.PhaseIO(fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=fake_hermes_run())
    out = hp._phase_config_write(hp.context_for("config_write", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    sites = {f["site"] for f in out.details["fallbacks"]}
    assert sites == {"primary_slot", "mcp_servers"}


def test_config_write_records_no_fallbacks_when_inputs_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    # A persisted mcp_wire checkpoint from a previous run.
    state.phases["mcp_wire"] = {
        "status": "ok",
        "details": {"rendered_servers": hp._default_mcp_servers()},
    }
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no.yaml")
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.PhaseIO(
        fetch_slots=lambda: list(_ROLE_SLOTS),
        fetch_model_contexts=lambda: {},
        run=fake_hermes_run(),
    )
    out = hp._phase_config_write(hp.context_for("config_write", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["fallbacks"] == []


def test_resolve_primary_slot_marks_placeholder() -> None:
    assert hp._resolve_primary_slot(slots_fetcher=lambda: [])["placeholder"] is True
    live = hp._resolve_primary_slot(
        slots_fetcher=lambda: [
            {
                "name": "chat",
                "type": "llm",
                "state": "ready",
                "model_id": "qwen3-test",
                "backend_url": "http://127.0.0.1:8001/v1",
                "context_length": 32768,
            }
        ]
    )
    assert live["placeholder"] is False


def test_deep_merge_recurses() -> None:
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    overlay = {"a": {"c": 99, "e": 4}}
    merged = hp._deep_merge(base, overlay)
    assert merged == {"a": {"b": 1, "c": 99, "e": 4}, "d": 3}


def test_legacy_hal0_profile_plugin_removed() -> None:
    """The legacy ``hal0`` model-provider plugin is gone (PR-1-bundle R4 H4).

    It hardcoded ``base_url=http://127.0.0.1:8000/api/v1`` which has no
    listener on a real install; the composite ``hal0`` upstream in
    :mod:`hal0.api` supersedes it.
    """
    repo_root = hp.REPO_ROOT_FOR_INSTALLER
    legacy = repo_root / "installer" / "agents" / "hermes" / "plugins" / "hal0"
    assert not legacy.exists(), f"Legacy broken plugin still on disk at {legacy}"


# ── #242 phase impl — mcp_wire + Hal0MemoryProvider plugin ──────────────────


def test_hal0_memory_provider_plugin_file_present() -> None:
    repo_root = hp.REPO_ROOT_FOR_INSTALLER
    plugin_dir = repo_root / "installer" / "agents" / "hermes" / "plugins" / "hal0-memory"
    # The plugin is a package (__init__ re-export + register, provider.py,
    # _client.py); read the combined source so lifecycle methods (defined in
    # provider.py) are found.
    body = "\n".join(p.read_text() for p in sorted(plugin_dir.glob("*.py")))
    assert "Hal0MemoryProvider" in body
    assert "MemoryProvider" in body
    assert "register" in body  # entry point; graph forwarding is server-side now (ADR-0023)
    assert "private:" in body
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
    io = hp.PhaseIO(
        probe_mcp_server=lambda url, **_kw: {"ok": True, "tools": ["t1", "t2"], "error": None}
    )
    monkeypatch.setattr(hp, "_load_agent_allowlist", lambda *_a, **_kw: None)
    out = hp._phase_mcp_wire(hp.context_for("mcp_wire", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["servers"]["hal0-admin"]["status"] == "ok"
    assert out.details["servers"]["hal0-admin"]["tool_count"] == 2
    assert out.details["allowlist_present"] is False
    assert out.details["warnings"] == []


def test_mcp_wire_phase_degrades_not_fails_on_unreachable_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = hp.BootstrapState()
    io = hp.PhaseIO(
        probe_mcp_server=lambda url, **_kw: {
            "ok": False,
            "tools": [],
            "error": "connection refused",
        }
    )
    monkeypatch.setattr(hp, "_load_agent_allowlist", lambda *_a, **_kw: None)
    out = hp._phase_mcp_wire(hp.context_for("mcp_wire", state, io=io))
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

    io = hp.PhaseIO(mcp_memory_call=_fake_mcp)
    out = hp._phase_namespace_register(hp.context_for("namespace_register", state, io=io))
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
                "result": {"items": [{"id": "old_mem_id", "metadata": {"agent_id": "hermes"}}]},
            }
        if name == "memory_delete":
            return {"ok": True, "result": {"deleted": 1}}
        if name == "memory_add":
            return {"ok": True, "result": {"id": "new_mem_id"}}
        return {"ok": True, "result": {}}

    io = hp.PhaseIO(mcp_memory_call=_fake_mcp)
    out = hp._phase_namespace_register(hp.context_for("namespace_register", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["refreshed_existing"] is True


# ── brain_profile_seed — hal0-brain as a first-class profile identity ───────


def test_build_brain_identity_card_targets_profile_agent_id() -> None:
    from hal0.agents.personas import BRAIN_PROFILE_AGENT_ID

    card = hp._build_brain_identity_card()
    assert card["dataset"] == hp.AGENTS_DATASET
    assert hp.AGENT_IDENTITY_TAG in card["tags"]
    md = card["metadata"]
    assert md["agent_id"] == BRAIN_PROFILE_AGENT_ID == "hermes__hal0-brain"
    assert md["namespace"] == "private:hermes__hal0-brain"
    assert md["hal0_state"]["bootstrap_version"] == 1
    assert md["hal0_state"]["registered_at"]


def test_brain_profile_seed_registers_card_under_profile_agent_id() -> None:
    state = hp.BootstrapState()
    calls: list[tuple[str, dict[str, Any], str]] = []

    def _fake_mcp(
        method: str, params: dict[str, Any], *, agent_id: str, **_kw: Any
    ) -> dict[str, Any]:
        calls.append((method, params, agent_id))
        name = params.get("name")
        if name == "memory_search":
            return {"ok": True, "result": {"items": []}}
        if name == "memory_add":
            return {"ok": True, "result": {"id": "brain_mem"}}
        return {"ok": True, "result": {}}

    io = hp.PhaseIO(mcp_memory_call=_fake_mcp)
    out = hp._phase_brain_profile_seed(hp.context_for("brain_profile_seed", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["registered"] is True
    assert out.details["agent_id"] == "hermes__hal0-brain"
    assert out.details["memory_id"] == "brain_mem"
    # Every memory call is scoped to the brain profile agent-id, not the default.
    assert calls and all(agent == "hermes__hal0-brain" for _m, _p, agent in calls)


def test_brain_profile_seed_continues_on_mcp_failure() -> None:
    state = hp.BootstrapState()

    def _fake_mcp(method: str, params: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        if params.get("name") == "memory_search":
            return {"ok": True, "result": {"items": []}}
        return {"ok": False, "error": "hal0-memory unreachable"}

    io = hp.PhaseIO(mcp_memory_call=_fake_mcp)
    out = hp._phase_brain_profile_seed(hp.context_for("brain_profile_seed", state, io=io))
    # Warn-as-OK: the phase never blocks bootstrap on the memory layer.
    assert out.status == hp.PhaseStatus.OK
    assert out.details["registered"] is False


# ── brain_profile_mcp_wire — reproducible profile MCP wiring ────────────────


def _brain_profile_state(tmp_path: Path) -> Any:
    return hp.BootstrapState(hermes_home=str(tmp_path / ".hermes"))


def test_brain_profile_mcp_wire_skips_when_profile_absent(tmp_path: Path) -> None:
    state = _brain_profile_state(tmp_path)
    out = hp._phase_brain_profile_mcp_wire(hp.context_for("brain_profile_mcp_wire", state))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["wired"] is False  # upstream owns profile creation


def test_brain_profile_mcp_wire_merges_and_preserves_upstream_keys(tmp_path: Path) -> None:
    import yaml

    state = _brain_profile_state(tmp_path)
    cfg = hp._brain_profile_config_path(state)
    cfg.parent.mkdir(parents=True)
    # An existing profile config with upstream keys + an unrelated MCP server.
    cfg.write_text(
        yaml.safe_dump(
            {
                "model": {"default": "agent"},
                "mcp_servers": {"some-operator-server": {"type": "http", "url": "http://x"}},
            }
        ),
        encoding="utf-8",
    )
    out = hp._phase_brain_profile_mcp_wire(hp.context_for("brain_profile_mcp_wire", state))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["wired"] is True and out.details["changed"] is True

    merged = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    servers = merged["mcp_servers"]
    # hal0-owned servers wired under the brain profile identity…
    assert servers["hal0-admin"]["headers"]["X-hal0-Agent"] == "hermes__hal0-brain"
    assert servers["hal0-memory"]["headers"]["X-hal0-Private"] == 1
    assert servers["hal0-memory"]["headers"]["X-hal0-Agent"] == "hermes__hal0-brain"
    # memory provider is written too (scalar merge, safe)…
    assert merged["memory"]["provider"] == "hal0-memory"
    # …without clobbering upstream/operator keys.
    assert merged["model"] == {"default": "agent"}
    assert servers["some-operator-server"] == {"type": "http", "url": "http://x"}


def test_brain_profile_mcp_wire_is_idempotent(tmp_path: Path) -> None:
    state = _brain_profile_state(tmp_path)
    cfg = hp._brain_profile_config_path(state)
    cfg.parent.mkdir(parents=True)
    cfg.write_text("model:\n  default: agent\n", encoding="utf-8")
    first = hp._phase_brain_profile_mcp_wire(hp.context_for("brain_profile_mcp_wire", state))
    assert first.details["changed"] is True
    second = hp._phase_brain_profile_mcp_wire(hp.context_for("brain_profile_mcp_wire", state))
    # Already-correct box: no rewrite, so its comments/formatting survive.
    assert second.details["wired"] is True and second.details["changed"] is False


def test_namespace_register_continues_on_mcp_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0013: registry failure logs + continues; bootstrap doesn't block."""
    state = hp.BootstrapState()
    io = hp.PhaseIO(mcp_memory_call=lambda *a, **kw: {"ok": False, "error": "connection refused"})
    out = hp._phase_namespace_register(hp.context_for("namespace_register", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["registered"] is False
    assert any("memory_add" in w for w in out.details["warnings"])
    # #702: the memory-layer warn-as-OK posture is an observable fallback.
    assert any(f["site"] == "memory_layer" for f in out.details["fallbacks"])


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
    io = hp.PhaseIO(
        probe_mcp_server=lambda url, **_kw: {"ok": True, "tools": ["t1"], "error": None}
    )
    out = hp._phase_mcp_wire(hp.context_for("mcp_wire", state, io=io))
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
    # STATE.md now renders into RUNTIME_SNAPSHOT_DIR (#473); redirect it to
    # tmp_path too so render_live_context's STATE.md write doesn't hit the real
    # /var/lib/hal0 (and so its failure can't skip the HERMES.md write below).
    monkeypatch.setattr(hp, "RUNTIME_SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(hp, "ETC_HAL0_AGENT_SKILLS", etc / "agent-skills")
    monkeypatch.setattr(hp, "HAL0_BUNDLED_SKILLS", tmp_path / "no-such-skills")
    # Context-link consults /api/slots when wiring HERMES.md's primary
    # block; fake the seams so the test stays offline + deterministic.
    io = hp.PhaseIO(
        fetch_slots=lambda: [],
        fetch_model_contexts=lambda: {},
        http_get=lambda *_a, **_kw: 0,
    )

    out = hp._phase_context_link(hp.context_for("context_link", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert (hermes_home / "SOUL.md").exists()
    assert (etc / "HERMES.md").exists()
    # /etc/hal0/HERMES.md is the stable READ path but is a symlink to the
    # hal0-writable runtime copy; the real file lives under RUNTIME_SNAPSHOT_DIR.
    assert (etc / "HERMES.md").is_symlink()
    assert (etc / "HERMES.md").resolve() == (tmp_path / "HERMES.md").resolve()
    assert (tmp_path / "HERMES.md").is_file()
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


def test_relink_managed_migrates_real_file_to_symlink(tmp_path: Path) -> None:
    # Upgrade path: a pre-relocation install has a REAL /etc/hal0/HERMES.md.
    # _relink_managed must replace it in place with a symlink to the new
    # /var/lib copy (so the read path is unchanged for consumers).
    target = tmp_path / "var" / "HERMES.md"
    target.parent.mkdir()
    target.write_text("real body\n", encoding="utf-8")
    link = tmp_path / "etc" / "HERMES.md"
    link.parent.mkdir()
    link.write_text("stale real file from old install\n", encoding="utf-8")  # not a symlink

    assert hp._relink_managed(target, link) is True
    assert link.is_symlink()
    assert link.resolve() == target.resolve()
    assert link.read_text(encoding="utf-8") == "real body\n"
    # Idempotent: already points at target -> no-op, no churn.
    assert hp._relink_managed(target, link) is False


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
    monkeypatch.setattr(hp, "RUNTIME_SNAPSHOT_DIR", tmp_path)  # STATE.md target (#473)
    monkeypatch.setattr(hp, "ETC_HAL0_AGENT_SKILLS", tmp_path / "etc" / "hal0" / "agent-skills")
    monkeypatch.setattr(hp, "HAL0_BUNDLED_SKILLS", tmp_path / "no-skills")
    io = hp.PhaseIO(
        fetch_slots=lambda: [],
        fetch_model_contexts=lambda: {},
        http_get=lambda *_a, **_kw: 0,
    )

    def _explode(name: str, **_: Any) -> str:
        if name == "SOUL.md.j2":
            raise RuntimeError("template boom")
        return "ok"

    monkeypatch.setattr(hp, "_render_template", _explode)
    out = hp._phase_context_link(hp.context_for("context_link", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    soul = (hermes_home / "SOUL.md").read_text()
    assert "hal0 admin agent" in soul
    assert any("SOUL.md render" in w for w in out.details["warnings"])
    # #702: the inline-default fallback is observable, not silent.
    assert any(f["site"] == "soul_md" for f in out.details["fallbacks"])


# ── #245 phase impls — model_automap + voice_wire ───────────────────────────


def test_model_automap_writes_aliases_from_chat_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    # Pre-render config.yaml so model_automap has something to rewrite.
    (hermes_home / "config.yaml").write_text("model:\n  default: p\n", encoding="utf-8")
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no.yaml")
    # PR-1-bundle: real ``/api/slots`` payload uses ``type=="llm"`` for
    # chat slots, NOT ``capability=="chat"``. The pre-fix filter looked
    # at ``kind`` first and let the synthetic ``capability`` field through;
    # the post-fix filter is type-first to match the live shape.
    io = hp.PhaseIO(
        fetch_slots=lambda: [
            {
                "name": "primary",
                "type": "llm",
                "model_id": "qwen3:8b",
                "backend_url": "http://127.0.0.1:8001/v1",
                "state": "ready",
            },
            {
                "name": "coder",
                "type": "llm",
                "model_id": "qwen-coder",
                "backend_url": "http://127.0.0.1:8002/v1",
                "state": "ready",
            },
        ],
        fetch_model_contexts=lambda: {},
        run=fake_hermes_run(),
    )
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_kw: {"model": "p", "base_url": "u", "context_length": 8000},
    )
    out = hp._phase_model_automap(hp.context_for("model_automap", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    rendered = (hermes_home / "config.yaml").read_text()
    assert "coder" in out.details["aliases_written"]
    assert "primary" in out.details["aliases_written"]
    # config set wrote the model_aliases.* keys into the config.
    assert "qwen-coder" in rendered


def test_model_automap_idempotent_hash_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    (hermes_home / "config.yaml").write_text("model:\n  default: p\n", encoding="utf-8")
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no.yaml")
    io = hp.PhaseIO(fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=fake_hermes_run())
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_kw: {"model": "p", "base_url": "u", "context_length": 8000},
    )
    out1 = hp._phase_model_automap(hp.context_for("model_automap", state, io=io))
    out2 = hp._phase_model_automap(hp.context_for("model_automap", state, io=io))
    # config set is idempotent: re-applying the same keys leaves a byte-identical
    # config, so both runs report the same content hash.
    assert out1.status == hp.PhaseStatus.OK
    assert out1.hash == out2.hash


def test_voice_wire_skips_when_no_voice_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    io = hp.PhaseIO(fetch_slots=lambda: [])
    out = hp._phase_voice_wire(hp.context_for("voice_wire", state, io=io))
    assert out.status == hp.PhaseStatus.SKIP


def test_voice_wire_finds_local_tts_and_transcription_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: _find_slot used _slot_kind which read 'kind' before 'type'.

    Real /api/slots sets kind='local' (deployment shape) on all local slots,
    so _slot_kind returned 'local' instead of 'tts'/'transcription'.  voice_wire
    always skipped with 'no stt/tts slots ready', never writing the env vars.
    """
    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    secrets_env = tmp_path / "hermes.env"
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", secrets_env)
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no.yaml")
    (hermes_home / "config.yaml").write_text("model:\n  default: primary\n", encoding="utf-8")
    tts_url = "http://127.0.0.1:8084/v1"
    stt_url = "http://127.0.0.1:8088/v1"
    # Both slots have kind='local' (the local-vs-remote deployment field) AND
    # the functional type field.  Before the fix, _slot_kind returned 'local'
    # for both, so _find_slot(slots, 'tts') and _find_slot(slots, 'stt')
    # always returned None and voice_wire always skipped.
    slots = [
        {
            "name": "kokoro",
            "type": "tts",
            "kind": "local",
            "state": "ready",
            "backend_url": tts_url,
        },
        {
            "name": "whisper",
            "type": "transcription",
            "kind": "local",
            "state": "ready",
            "backend_url": stt_url,
        },
    ]
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    io = hp.PhaseIO(
        fetch_slots=lambda: slots,
        fetch_model_contexts=lambda: {},
    )
    out = hp._phase_voice_wire(hp.context_for("voice_wire", state, io=io))
    assert out.status == hp.PhaseStatus.OK, (
        f"expected OK but got {out.status!r}: {out.reason!r} — "
        "voice_wire skipped local tts/transcription slots (kind='local' bug)"
    )
    env_text = secrets_env.read_text()
    assert f"TTS_OPENAI_BASE_URL={tts_url}" in env_text, (
        f"TTS URL not written to secrets env. env contents:\n{env_text}"
    )
    assert f"STT_OPENAI_BASE_URL={stt_url}" in env_text, (
        f"STT URL not written to secrets env. env contents:\n{env_text}"
    )


def test_voice_wire_provisions_stt_for_npu_trio_facade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: NPU-trio stt facade (type=transcription, state=offline, served_by=anchor)
    was rejected by _find_slot because _is_ready checks state and 'offline' is not in the
    ready set. The facade has no unit of its own — the npu anchor's FLM child serves it.
    Fix: _find_slot should accept the facade when its named anchor is ready.
    """
    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    secrets_env = tmp_path / "hermes.env"
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", secrets_env)
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no.yaml")
    (hermes_home / "config.yaml").write_text("model:\n  default: primary\n", encoding="utf-8")
    tts_url = "http://127.0.0.1:8084/v1"
    # NPU trio: anchor (llm, ready) + stt facade (transcription, offline, served_by anchor)
    # The facade mirrors live container_enrichment output: served_by is the anchor name.
    slots = [
        {
            "name": "kokoro",
            "type": "tts",
            "kind": "local",
            "state": "ready",
            "backend_url": tts_url,
        },
        {
            "name": "npu",
            "type": "llm",
            "kind": "local",
            "state": "ready",
        },
        {
            "name": "stt",
            "type": "transcription",
            "kind": "local",
            "state": "offline",  # facade has no unit; routes through npu anchor
            "served_by": "npu",
        },
    ]
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    io = hp.PhaseIO(
        fetch_slots=lambda: slots,
        fetch_model_contexts=lambda: {},
    )
    out = hp._phase_voice_wire(hp.context_for("voice_wire", state, io=io))
    assert out.status == hp.PhaseStatus.OK, (
        f"expected OK but got {out.status!r}: {out.reason!r} — "
        "voice_wire skipped NPU-trio stt facade (offline state bug)"
    )
    env_text = secrets_env.read_text()
    assert f"TTS_OPENAI_BASE_URL={tts_url}" in env_text
    assert "STT_OPENAI_BASE_URL=" in env_text, (
        f"STT URL not written to secrets env. env contents:\n{env_text}"
    )
    # The facade has no explicit backend_url, so _slot_backend_url falls back
    # to the default HAL0 API gateway — voice clients send STT there, which
    # routes to the NPU trio.
    assert f"STT_OPENAI_BASE_URL={hp._DEFAULT_PRIMARY_BACKEND_URL}" in env_text, (
        f"STT URL incorrect. env contents:\n{env_text}"
    )


def test_voice_wire_does_not_provision_stt_when_npu_anchor_is_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Safety: stt facade served_by an offline anchor must NOT write STT_OPENAI_BASE_URL.

    The secondary _find_slot pass should only accept a facade when its named
    anchor is _is_ready — if the anchor is offline/error/starting the facade
    has no live backend and voice_wire must not point voice clients at it.
    TTS may still be written (independent slot), or SKIP if neither is ready.
    """
    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    secrets_env = tmp_path / "hermes.env"
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", secrets_env)
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no.yaml")
    (hermes_home / "config.yaml").write_text("model:\n  default: primary\n", encoding="utf-8")
    tts_url = "http://127.0.0.1:8084/v1"
    # NPU anchor is offline — facade must not be accepted.
    slots = [
        {
            "name": "kokoro",
            "type": "tts",
            "kind": "local",
            "state": "ready",
            "backend_url": tts_url,
        },
        {
            "name": "npu",
            "type": "llm",
            "kind": "local",
            "state": "offline",  # anchor is not ready
        },
        {
            "name": "stt",
            "type": "transcription",
            "kind": "local",
            "state": "offline",
            "served_by": "npu",
        },
    ]
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    io = hp.PhaseIO(
        fetch_slots=lambda: slots,
        fetch_model_contexts=lambda: {},
    )
    out = hp._phase_voice_wire(hp.context_for("voice_wire", state, io=io))
    # TTS is ready so we get OK (not SKIP), but STT must be absent.
    assert out.status in (hp.PhaseStatus.OK, hp.PhaseStatus.SKIP)
    if secrets_env.exists():
        env_text = secrets_env.read_text()
        assert "STT_OPENAI_BASE_URL=" not in env_text, (
            f"STT URL must not be written when anchor is offline. env:\n{env_text}"
        )


# ── #246 phase impls — smoke_tests + self_report ────────────────────────────


def test_smoke_tests_phase_runs_each_probe_collecting_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    # All six probes return (True, "...") so we exercise the rollup
    # without depending on a real Hermes binary or HTTP listener.
    monkeypatch.setattr(hp, "_smoke_wrapper_ready", lambda s, io: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_hermes_doctor", lambda s, io: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_chat_completions", lambda s, io: (True, "ready"))
    monkeypatch.setattr(hp, "_smoke_memory_roundtrip", lambda s, io: (True, "1 item"))
    monkeypatch.setattr(hp, "_smoke_admin_tools_list", lambda s, io: (True, "8 tools"))
    monkeypatch.setattr(hp, "_smoke_hermes_md_contains_primary", lambda s, io: (True, "ok"))
    out = hp._phase_smoke_tests(hp.context_for("smoke_tests", state))
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
    monkeypatch.setattr(hp, "_smoke_wrapper_ready", lambda s, io: (False, "wrapper missing"))
    monkeypatch.setattr(hp, "_smoke_hermes_doctor", lambda s, io: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_chat_completions", lambda s, io: (False, "503"))
    monkeypatch.setattr(hp, "_smoke_memory_roundtrip", lambda s, io: (True, "1 item"))
    monkeypatch.setattr(hp, "_smoke_admin_tools_list", lambda s, io: (True, "8 tools"))
    monkeypatch.setattr(hp, "_smoke_hermes_md_contains_primary", lambda s, io: (True, "ok"))
    out = hp._phase_smoke_tests(hp.context_for("smoke_tests", state))
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

    io = hp.PhaseIO(mcp_memory_call=_fake_mcp)
    out = hp._phase_self_report(hp.context_for("self_report", state, io=io))
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
    io = hp.PhaseIO(mcp_memory_call=lambda *a, **kw: {"ok": False, "error": "connection refused"})
    out = hp._phase_self_report(hp.context_for("self_report", state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["published"] is False
    assert "refused" in out.details["warning"]


# ── #437 — gateway_secrets_wire (SYSTEM scope) ──────────────────────────────
#
# The provisioner idempotently writes the gateway secrets drop-in at
# /etc/systemd/system/hermes-gateway.service.d/10-hal0-secrets.conf and
# runs `systemctl daemon-reload` ONLY when the file changed. These tests
# mirror the _merge_env_file atomic+posture test: monkeypatch the drop-in
# dir to tmp_path + capture subprocess argv. End-to-end EnvironmentFile
# loading is NOT unit-testable without a live systemd — we assert file
# presence + content + mode + the daemon-reload call, not inherited env.


class _FakeSystemctl:
    """Capture subprocess.run argv so tests can assert daemon-reload calls."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], **_kwargs: Any) -> Any:
        self.calls.append(list(argv))

        class _Completed:
            returncode = 0

        return _Completed()


def _patch_dropin_to_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, _FakeSystemctl, hp.PhaseIO]:
    """Point the gateway drop-in dir at tmp_path + fake systemctl + root euid."""
    dropin_dir = tmp_path / "etc" / "systemd" / "system" / "hermes-gateway.service.d"
    dropin_file = dropin_dir / "10-hal0-secrets.conf"
    monkeypatch.setattr(hp, "GATEWAY_SYSTEMD_DROPIN_DIR", dropin_dir)
    monkeypatch.setattr(hp, "GATEWAY_SYSTEMD_DROPIN_FILE", dropin_file)
    # Pretend we're root so the phase doesn't SKIP on the non-root guard.
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)
    fake = _FakeSystemctl()
    return dropin_file, fake, hp.PhaseIO(run=fake.run)


def test_gateway_secrets_wire_writes_dropin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dropin_file, fake, io = _patch_dropin_to_tmp(tmp_path, monkeypatch)
    state = hp.BootstrapState()

    out = hp._phase_gateway_secrets_wire(hp.context_for("gateway_secrets_wire", state, io=io))

    assert out.status == hp.PhaseStatus.OK
    assert dropin_file.exists()
    body = dropin_file.read_text(encoding="utf-8")
    # Optional (`-`): a missing vault on a fresh install must not hard-fail the
    # unit (matches hal0-agent@.service's EnvironmentFile=-).
    assert "EnvironmentFile=-/var/lib/hal0/secrets/agents/hermes.env" in body
    assert "[Service]" in body
    # Mode 0o644 — NOT 0o600, which would block systemd from reading the
    # unit fragment. The secrets themselves are in the 0600 vault.
    assert (dropin_file.stat().st_mode & 0o777) == 0o644
    # daemon-reload fired exactly once on first write.
    assert fake.calls == [["systemctl", "daemon-reload"]]
    assert out.details["daemon_reload"] is True
    assert out.details["dropin_path"] == str(dropin_file)
    assert out.details["content_hash"]


def test_gateway_secrets_wire_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dropin_file, fake, io = _patch_dropin_to_tmp(tmp_path, monkeypatch)
    state = hp.BootstrapState()

    first = hp._phase_gateway_secrets_wire(hp.context_for("gateway_secrets_wire", state, io=io))
    assert first.status == hp.PhaseStatus.OK
    mtime_after_first = dropin_file.stat().st_mtime_ns
    body_after_first = dropin_file.read_text(encoding="utf-8")
    assert fake.calls == [["systemctl", "daemon-reload"]]

    second = hp._phase_gateway_secrets_wire(hp.context_for("gateway_secrets_wire", state, io=io))
    assert second.status == hp.PhaseStatus.OK
    # Identical hash, file untouched, NO second daemon-reload (hash-skip).
    assert second.hash == first.hash
    assert dropin_file.read_text(encoding="utf-8") == body_after_first
    assert dropin_file.stat().st_mtime_ns == mtime_after_first
    assert fake.calls == [["systemctl", "daemon-reload"]]  # still only one
    assert second.details.get("daemon_reload") is False
    assert second.details.get("unchanged") is True


def test_gateway_secrets_wire_routes_through_seam_non_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7.4: a non-root (hal0) provision routes the drop-in write + daemon-reload
    through the hal0-systemctl seam instead of SKIPping."""
    dropin_file, fake, io = _patch_dropin_to_tmp(tmp_path, monkeypatch)
    # Override the root euid the helper set — emulate a non-root provision.
    monkeypatch.setattr(hp.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(hp, "_HAL0_SYSTEMCTL", "/usr/lib/hal0/bin/hal0-systemctl")

    seam_calls: list[tuple[list[str], Any]] = []

    def _fake_run(argv: list[str], **kwargs: Any) -> Any:
        seam_calls.append((list(argv), kwargs.get("input")))

        class _C:
            returncode = 0

        return _C()

    monkeypatch.setattr(hp.subprocess, "run", _fake_run)
    state = hp.BootstrapState()

    out = hp._phase_gateway_secrets_wire(hp.context_for("gateway_secrets_wire", state, io=io))

    assert out.status == hp.PhaseStatus.OK
    assert out.details["daemon_reload"] is True
    # write-gateway-dropin (body on stdin) then daemon-reload, both via the seam.
    verbs = [argv[3] for argv, _ in seam_calls]
    assert verbs == ["write-gateway-dropin", "daemon-reload"]
    assert seam_calls[0][0][:3] == ["sudo", "-n", "/usr/lib/hal0/bin/hal0-systemctl"]
    assert seam_calls[0][1] and "[Service]" in seam_calls[0][1]
    # The injected (root-only, direct-systemctl) run was NOT used; no real write.
    assert fake.calls == []
    assert not dropin_file.exists()


def test_gateway_secrets_wire_refuses_real_etc_dropin_under_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the 2026-06-04 outage: a fixture that monkeypatches
    HERMES_SECRETS_ENV but FORGETS GATEWAY_SYSTEMD_DROPIN_FILE leaves the
    drop-in pointing at the real /etc tree. When pytest runs as root (e.g.
    on an LXC) the euid!=0 guard is defeated, so the phase would write the
    host's live drop-in with a pytest-tmp EnvironmentFile path → gateway
    restart-loop once the tmp dir is reaped. The phase must refuse to touch
    the real /etc/systemd tree under pytest regardless of euid.
    """
    # Intentionally do NOT sandbox the drop-in path — it stays at the real
    # /etc default, exactly as the buggy fixture left it.
    assert str(hp.GATEWAY_SYSTEMD_DROPIN_DIR).startswith("/etc/")
    # Defeat the euid!=0 guard the way root-on-an-LXC does.
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)

    # If the phase reaches systemctl it has already escaped — fail loudly.
    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("phase invoked systemctl against the real bus")

    out = hp._phase_gateway_secrets_wire(
        hp.context_for("gateway_secrets_wire", hp.BootstrapState(), io=hp.PhaseIO(run=_boom))
    )

    assert out.status == hp.PhaseStatus.SKIP
    assert out.reason is not None
    assert "pytest" in out.reason.lower()


# ── write_gateway_secrets_dropin — extracted writer (pre-gateway-install use) ──


def test_write_gateway_secrets_dropin_writes_and_reloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dropin_file, fake, _io = _patch_dropin_to_tmp(tmp_path, monkeypatch)

    res = hp.write_gateway_secrets_dropin(run=fake.run)

    assert res.outcome == "written"
    assert res.daemon_reload is True
    assert res.content_hash
    assert dropin_file.exists()
    assert "EnvironmentFile=-/var/lib/hal0/secrets/agents/hermes.env" in dropin_file.read_text(
        encoding="utf-8"
    )
    assert (dropin_file.stat().st_mode & 0o777) == 0o644
    assert fake.calls == [["systemctl", "daemon-reload"]]


def test_write_gateway_secrets_dropin_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dropin_file, fake, _io = _patch_dropin_to_tmp(tmp_path, monkeypatch)

    first = hp.write_gateway_secrets_dropin(run=fake.run)
    mtime = dropin_file.stat().st_mtime_ns
    second = hp.write_gateway_secrets_dropin(run=fake.run)

    assert first.outcome == "written"
    assert second.outcome == "unchanged"
    assert second.daemon_reload is False
    assert second.content_hash == first.content_hash
    # Hash-skip: file untouched, only ONE daemon-reload total.
    assert dropin_file.stat().st_mtime_ns == mtime
    assert fake.calls == [["systemctl", "daemon-reload"]]


def test_write_gateway_secrets_dropin_routes_through_seam_non_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7.4: a non-root caller delegates the write + daemon-reload to the
    hal0-systemctl seam (sudo -n) rather than skipping."""
    _dropin_file, fake, _io = _patch_dropin_to_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(hp.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(hp, "_HAL0_SYSTEMCTL", "/usr/lib/hal0/bin/hal0-systemctl")

    seam_calls: list[tuple[list[str], Any]] = []

    def _fake_run(argv: list[str], **kwargs: Any) -> Any:
        seam_calls.append((list(argv), kwargs.get("input")))

        class _C:
            returncode = 0

        return _C()

    monkeypatch.setattr(hp.subprocess, "run", _fake_run)

    res = hp.write_gateway_secrets_dropin(run=fake.run)

    assert res.outcome == "written"
    assert res.daemon_reload is True
    assert res.content_hash
    verbs = [argv[3] for argv, _ in seam_calls]
    assert verbs == ["write-gateway-dropin", "daemon-reload"]
    assert seam_calls[0][0][:3] == ["sudo", "-n", "/usr/lib/hal0/bin/hal0-systemctl"]
    assert seam_calls[0][1] and "EnvironmentFile=" in seam_calls[0][1]
    assert fake.calls == []


def test_write_gateway_secrets_dropin_refuses_real_etc_under_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pytest-sandbox guard must fire BEFORE the euid check: root-under-
    pytest with the real /etc default path is a no-op, never a host write."""
    assert str(hp.GATEWAY_SYSTEMD_DROPIN_DIR).startswith("/etc/")
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("touched the real systemd bus")

    res = hp.write_gateway_secrets_dropin(run=_boom)
    assert res.outcome == "skipped"
    assert res.reason is not None and "pytest" in res.reason.lower()


# ── #437 — canonical home / wrapper consolidation ───────────────────────────


def test_bootstrap_default_home_is_dot_hermes() -> None:
    # The default the fresh bootstrap + provision.json checkpoints embed
    # must be the NORMAL hermes default `/var/lib/hal0/.hermes`, not the
    # legacy `agents/hermes` location (otherwise --repair re-claims the
    # old path via _claim_hermes_home).
    assert hp.BootstrapState().hermes_home == "/var/lib/hal0/.hermes"


def test_fresh_run_stamps_marker_under_dot_hermes_home(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState, pipeline_io: hp.PhaseIO
) -> None:
    # state_with_tmp_paths roots hermes_home under tmp; assert the
    # .hal0-managed marker lands under the configured (dot-shaped) home.
    result = hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths, io=pipeline_io)
    assert result.failed == []
    marker = Path(state_with_tmp_paths.hermes_home) / ".hal0-managed"
    assert marker.is_file()


def test_install_phase_installs_both_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "hermes").write_text("#!/bin/sh\nexit 0\n")
    (venv / "bin" / "hermes").chmod(0o755)

    hermes_cli_dst = tmp_path / "usr" / "local" / "bin" / "hermes"
    wrapper_dst = tmp_path / "usr" / "local" / "bin" / "hal0-hermes"
    monkeypatch.setattr(hp, "HERMES_CLI_INSTALL_PATH", hermes_cli_dst)
    monkeypatch.setattr(hp, "WRAPPER_INSTALL_PATH", wrapper_dst)

    state = hp.BootstrapState(venv=str(venv), hermes_home=str(tmp_path / "hh"))
    out = hp._phase_install(
        hp.context_for("install", state, io=hp.PhaseIO(install_venv=lambda *a, **kw: None))
    )

    assert out.status == hp.PhaseStatus.OK
    # Canonical `hermes` on PATH is a real executable file.
    assert hermes_cli_dst.is_file()
    assert os.access(hermes_cli_dst, os.X_OK)
    # `hal0-hermes` is a back-compat symlink to it (executable via target).
    assert wrapper_dst.is_symlink()
    assert wrapper_dst.resolve() == hermes_cli_dst.resolve()
    assert os.access(wrapper_dst, os.X_OK)
    # Details record both entry points.
    assert out.details["hermes_cli"] == str(hermes_cli_dst)
    assert out.details["wrapper"] == str(wrapper_dst)


# ── installer-root resolution (editable vs non-editable FHS install) ──────────


def test_resolve_installer_root_prefers_editable_repo_root(tmp_path):
    """When the package sits in a repo (parents[3] has installer/agents),
    that repo root wins — preserves dev/editable behaviour."""
    repo = tmp_path / "repo"
    (repo / "installer" / "agents").mkdir(parents=True)
    # …/repo/src/hal0/agents/hermes_provision.py → parents[3] == repo
    mod = repo / "src" / "hal0" / "agents" / "hermes_provision.py"
    mod.parent.mkdir(parents=True)
    mod.write_text("# stub\n")

    got = hp._resolve_installer_root(module_file=mod, prefix=str(tmp_path / "irrelevant"))
    assert got == repo


def test_resolve_installer_root_falls_back_to_fhs_current(tmp_path):
    """Non-editable FHS install: the package copy lives under the venv
    (parents[3] has no installer/), so the resolver finds the versioned
    source tree next to the venv at …/hal0/current/installer."""
    fhs = tmp_path / "usr" / "lib" / "hal0"
    venv = fhs / "venv"
    (venv).mkdir(parents=True)
    (fhs / "current" / "installer" / "agents").mkdir(parents=True)
    # site-packages copy: …/venv/lib/python3.12/site-packages/hal0/agents/<mod>
    mod = venv / "lib" / "python3.12" / "site-packages" / "hal0" / "agents" / "hermes_provision.py"
    mod.parent.mkdir(parents=True)
    mod.write_text("# stub\n")

    got = hp._resolve_installer_root(module_file=mod, prefix=str(venv))
    assert got == fhs / "current"


# ── capture / --adopt / fatal-abort (pipeline level) ─────────────────────────
#
# The claim guard used to be cosmetic: run-all meant a claim FAIL was recorded
# but config_write/context_link still clobbered the foreign config. These pin
# the fatal-abort behaviour + the --adopt capture path end-to-end.


def _seed_foreign_home(hermes_home: Path) -> tuple[bytes, bytes]:
    """Populate hermes_home as a foreign (unmarked) install; return original bytes."""
    hermes_home.mkdir(parents=True, exist_ok=True)
    cfg = hermes_home / "config.yaml"
    soul = hermes_home / "SOUL.md"
    cfg.write_bytes(b"operator: hand-written\nmodel:\n  default: their-model\n")
    soul.write_bytes(b"# Operator SOUL\nDo not clobber me.\n")
    (hermes_home / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=live-tg\nDISCORD_TOKEN=live-dc\nRANDOM_UNRELATED=keep\n"
    )
    return cfg.read_bytes(), soul.read_bytes()


def test_foreign_home_without_adopt_aborts_and_preserves_files(
    tmp_path: Path, state_with_tmp_paths: hp.BootstrapState, pipeline_io: hp.PhaseIO
) -> None:
    hermes_home = Path(state_with_tmp_paths.hermes_home)
    cfg_before, soul_before = _seed_foreign_home(hermes_home)

    result = hp.run(state_root=tmp_path, initial_state=state_with_tmp_paths, io=pipeline_io)

    # Fatal abort at install's claim guard: run stopped, exit non-zero.
    assert result.aborted is True
    assert "install" in result.failed
    assert result.state.phases["install"].get("fatal") is True
    # Every phase after the fatal one is recorded skipped with the abort reason.
    assert result.state.phases["config_write"]["status"] == hp.PhaseStatus.SKIP.value
    assert "aborted" in result.state.phases["config_write"]["reason"]
    # The foreign config.yaml + SOUL.md are byte-identical — never clobbered.
    assert (hermes_home / "config.yaml").read_bytes() == cfg_before
    assert (hermes_home / "SOUL.md").read_bytes() == soul_before
    # No hal0 marker was stamped (the home stays unclaimed).
    assert not (hermes_home / hp._HAL0_MANAGED_MARKER).exists()


def test_foreign_home_without_adopt_cli_exit_nonzero(
    tmp_path: Path,
    state_with_tmp_paths: hp.BootstrapState,
    pipeline_io: hp.PhaseIO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_foreign_home(Path(state_with_tmp_paths.hermes_home))
    real_run = hp.run

    def _wrapped(**kwargs: Any) -> hp.RunResult:
        kwargs.setdefault("initial_state", state_with_tmp_paths)
        kwargs.setdefault("io", pipeline_io)
        return real_run(**kwargs)

    monkeypatch.setattr(hp, "run", _wrapped)
    rc = hp.bootstrap_cli(
        repair=False, adopt=False, dry_run=False, skip_phases=(), verbose=False, state_root=tmp_path
    )
    assert rc == 1


def test_foreign_home_with_adopt_backs_up_imports_and_proceeds(
    tmp_path: Path,
    state_with_tmp_paths: hp.BootstrapState,
    pipeline_io: hp.PhaseIO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hermes_home = Path(state_with_tmp_paths.hermes_home)
    cfg_before, soul_before = _seed_foreign_home(hermes_home)
    vault = tmp_path / "vault" / "hermes.env"
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", vault)

    result = hp.run(
        state_root=tmp_path, initial_state=state_with_tmp_paths, adopt=True, io=pipeline_io
    )

    # No abort — the run proceeds through every phase.
    assert result.aborted is False
    assert "install" not in result.failed
    # Marker stamped so the home is now hal0-managed.
    assert (hermes_home / hp._HAL0_MANAGED_MARKER).is_file()
    # Full-tree backup sibling created.
    backups = list(hermes_home.parent.glob(f"{hermes_home.name}.pre-hal0-*"))
    assert len(backups) == 1
    assert (backups[0] / "config.yaml").read_bytes() == cfg_before
    # Sidecar snapshots preserve the operator's originals in place.
    assert (hermes_home / "config.yaml.pre-hal0").read_bytes() == cfg_before
    assert (hermes_home / "SOUL.md.pre-hal0").read_bytes() == soul_before
    # Recognized tokens imported into the vault; unrelated key skipped.
    vault_body = vault.read_text()
    assert "TELEGRAM_BOT_TOKEN=live-tg" in vault_body
    assert "DISCORD_TOKEN=live-dc" in vault_body
    assert "RANDOM_UNRELATED" not in vault_body
    # The adopt details are surfaced in the install checkpoint.
    adopted = result.state.phases["install"]["details"]["adopted"]
    assert adopted["tokens_imported"] == ["DISCORD_TOKEN", "TELEGRAM_BOT_TOKEN"]
