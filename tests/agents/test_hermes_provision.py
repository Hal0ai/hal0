"""Unit tests for :mod:`hal0.agents.hermes_provision`.

Pins the linear ``install_hermes`` installer + its step bodies:

* the full pipeline runs every step to ok/skip and writes a last-run report;
* a failing step surfaces in ``report.failed`` and ``bootstrap_cli`` maps it to
  a non-zero exit;
* each step body (preflight / install / home_init / env_probe / config_write /
  mcp_wire / context_link / gateway_secrets_wire / voice_wire / smoke / …) plus
  the surviving pure helpers (python resolve, requirement floor, config overlay,
  slot resolvers, gateway drop-in) behave as specified.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import structlog.testing

from hal0.agents import hermes_provision as hp
from hal0.slots.state import DISPATCHABLE_STATES, SlotState

from ._hermes_fakes import fake_ensure_release, fake_hermes_run

_REAL_UV_AVAILABLE = hp._uv_available


@pytest.fixture(autouse=True)
def _isolated_install_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``INSTALL_SEED_PATH`` at an absent tmp file so host seed state
    (``[hermes].delegation_slot`` / ``command_approvals``) cannot leak into
    ``_phase_config_write`` assertions. Tests that need a seed re-patch it."""
    monkeypatch.setattr(hp, "INSTALL_SEED_PATH", tmp_path / "isolated-seed" / "hermes.toml")


@pytest.fixture(autouse=True)
def _hermetic_uv(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let the host's uv into a resolver test.

    ``_uv_available`` looks next to ``sys.executable`` first. CI installs hal0
    with ``uv sync``, so a real uv sits there and the uv-managed branch (first
    in ADR-0034's order) ran against fake runners. Here only a uv the test's
    own ``prober`` names, or its ``fallback_bin``, is ever found.
    :class:`TestUvDiscovery`, which tests the discovery rule itself, opts out.
    """
    if request.cls is not None and request.cls.__name__ == "TestUvDiscovery":
        return

    def _uv_available(prober=shutil.which, *, fallback_bin: Path | None = None):  # type: ignore[no-untyped-def]
        return _REAL_UV_AVAILABLE(
            prober,
            fallback_bin=fallback_bin if fallback_bin is not None else Path("/nonexistent/uv"),
        )

    monkeypatch.setattr(hp, "_uv_available", _uv_available)


@pytest.fixture
def install_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Sandbox every host path constant under tmp_path; return (hermes_home, venv)."""
    from ._hermes_fakes import sandbox_hermes_paths

    return sandbox_hermes_paths(hp, tmp_path, monkeypatch)


@pytest.fixture
def install_fake_io() -> hp.InstallIO:
    """Deterministic InstallIO for a full hermetic install_hermes run."""
    from ._hermes_fakes import install_io

    return install_io(hp)


# ── install_hermes: the linear pipeline ──────────────────────────────────────


def test_install_hermes_marks_every_step_ok_or_skip(
    tmp_path: Path, install_target: tuple[Path, Path], install_fake_io: hp.InstallIO
) -> None:
    home, venv = install_target
    report = hp.install_hermes(
        hermes_home=home,
        venv=venv,
        agent_id="hermes-agent",
        io=install_fake_io,
        state_root=tmp_path / "state",
    )
    assert report.ok, report.failed
    for step in report.steps:
        # smoke_tests may legitimately land on "warn" (#1793): it's
        # diagnostic-only and never fails the install, but a phase that
        # recorded a real probe failure must say so rather than reporting a
        # silent "ok" — see
        # test_smoke_tests_phase_records_failures_as_warn_without_blocking
        # for the behavior this permits.
        allowed = {"ok", "skip", "warn"} if step.name == "smoke_tests" else {"ok", "skip"}
        assert step.status in allowed, f"{step.name}: {step.status}"


def test_install_hermes_writes_last_run_report(
    tmp_path: Path, install_target: tuple[Path, Path], install_fake_io: hp.InstallIO
) -> None:
    home, venv = install_target
    sr = tmp_path / "state"
    hp.install_hermes(hermes_home=home, venv=venv, io=install_fake_io, state_root=sr)
    data = json.loads((sr / "provision.json").read_text())
    assert set(data["phases"]) >= {name for name, _ in hp._INSTALL_STEPS}
    assert data["completed_at"] is not None


def test_content_hash_is_stable_and_collision_free() -> None:
    a = hp.content_hash("foo", "bar")
    b = hp.content_hash("foo", "bar")
    c = hp.content_hash("foo", "baz")
    assert a == b
    assert a != c
    d = hp.content_hash(b"foo", "bar")
    assert d == a


def test_failed_step_surfaces_and_blocks_ok(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_target: tuple[Path, Path],
    install_fake_io: hp.InstallIO,
) -> None:
    """A failing step surfaces in report.failed and flips report.ok False."""

    def _boom(_ctx: hp._StepCtx) -> hp.PhaseResult:
        return hp.PhaseResult(status=hp.PhaseStatus.FAIL, reason="forced")

    patched = tuple((n, _boom if n == "env_probe" else fn) for n, fn in hp._INSTALL_STEPS)
    monkeypatch.setattr(hp, "_INSTALL_STEPS", patched)
    home, venv = install_target
    report = hp.install_hermes(
        hermes_home=home,
        venv=venv,
        io=install_fake_io,
        state_root=tmp_path / "state",
    )
    assert "env_probe" in report.failed
    assert report.ok is False
    # The last-run report records the failure so `agent status` shows it.
    data = json.loads((tmp_path / "state" / "provision.json").read_text())
    assert data["phases"]["env_probe"]["status"] == "fail"
    assert data["completed_at"] is None


def test_bootstrap_cli_returns_zero_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_target: tuple[Path, Path],
    install_fake_io: hp.InstallIO,
) -> None:
    home, venv = install_target
    real = hp.install_hermes

    def _wrapped(**kw: Any) -> hp.InstallReport:
        kw.setdefault("hermes_home", home)
        kw.setdefault("venv", venv)
        kw.setdefault("io", install_fake_io)
        return real(**kw)

    monkeypatch.setattr(hp, "install_hermes", _wrapped)
    rc = hp.bootstrap_cli(repair=False, verbose=False, state_root=tmp_path / "state")
    assert rc == 0


def test_bootstrap_cli_returns_one_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_target: tuple[Path, Path],
    install_fake_io: hp.InstallIO,
) -> None:
    home, venv = install_target

    def _boom(_ctx: hp._StepCtx) -> hp.PhaseResult:
        return hp.PhaseResult(status=hp.PhaseStatus.FAIL, reason="boom")

    patched = tuple((n, _boom if n == "preflight" else fn) for n, fn in hp._INSTALL_STEPS)
    monkeypatch.setattr(hp, "_INSTALL_STEPS", patched)
    real = hp.install_hermes

    def _wrapped(**kw: Any) -> hp.InstallReport:
        kw.setdefault("hermes_home", home)
        kw.setdefault("venv", venv)
        kw.setdefault("io", install_fake_io)
        return real(**kw)

    monkeypatch.setattr(hp, "install_hermes", _wrapped)
    rc = hp.bootstrap_cli(repair=False, verbose=False, state_root=tmp_path / "state")
    assert rc == 1


def test_bootstrap_cli_always_writes_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_target: tuple[Path, Path],
    install_fake_io: hp.InstallIO,
) -> None:
    home, venv = install_target
    real = hp.install_hermes

    def _wrapped(**kw: Any) -> hp.InstallReport:
        kw.setdefault("hermes_home", home)
        kw.setdefault("venv", venv)
        kw.setdefault("io", install_fake_io)
        return real(**kw)

    monkeypatch.setattr(hp, "install_hermes", _wrapped)
    sr = tmp_path / "state"
    # #2445: there is no report-less "dry run" any more — every pass is a real
    # pass, so it always records what it did.
    assert hp.bootstrap_cli(repair=False, verbose=False, state_root=sr) == 0
    assert (sr / "provision.json").exists()
    with pytest.raises(TypeError):
        hp.bootstrap_cli(repair=False, dry_run=True, state_root=sr)  # type: ignore[call-arg]


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
    io = hp.InstallIO(http_get=lambda *_a, **_kw: 200)
    out = hp._phase_preflight(hp._StepCtx(state=state, io=io))
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
    io = hp.InstallIO(http_get=lambda *_a, **_kw: 0)
    out = hp._phase_preflight(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.FAIL
    assert "daemon unreachable" in (out.reason or "")


def test_preflight_fails_on_var_lib_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    io = hp.InstallIO(http_get=lambda *_a, **_kw: 200)
    state = hp.BootstrapState(venv=str(tmp_path / "nope" / "venvs" / "hermes"))
    # /var/lib (nearest existing ancestor of the default $HERMES_HOME) isn't
    # writable to a normal user — stub the probe so the test is deterministic
    # regardless of the runner's uid (CI may run as root).
    monkeypatch.setattr(hp, "path_is_writable", lambda _p: False)
    out = hp._phase_preflight(hp._StepCtx(state=state, io=io))
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
    io = hp.InstallIO(http_get=lambda *_a, **_kw: 200)

    real = hp.path_is_writable
    monkeypatch.setattr(
        hp,
        "path_is_writable",
        lambda p: False if str(p) == str(hermes_home) else real(p),
    )
    out = hp._phase_preflight(hp._StepCtx(state=state, io=io))
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


def test_home_init_creates_layout(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes_home"
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    out = hp._phase_home_init(hp._StepCtx(state=state))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["changed"] is True
    assert hermes_home.is_dir()
    for sub in ("memories", "skills", "plugins/memory", "plugins/model-providers", "logs"):
        assert (hermes_home / sub).is_dir()


def test_home_init_converges_on_second_run(tmp_path: Path) -> None:
    """The adopt/marker claim is retired — home_init is a pure convergent mkdir.

    A re-run over an existing layout reports changed=False (no dir created) and
    never refuses a pre-existing populated home (capture/adopt gone)."""
    hermes_home = tmp_path / "user_hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("# operator file")
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    first = hp._phase_home_init(hp._StepCtx(state=state))
    assert first.status == hp.PhaseStatus.OK
    assert first.details["changed"] is True  # created the standard subdirs
    second = hp._phase_home_init(hp._StepCtx(state=state))
    assert second.status == hp.PhaseStatus.OK
    assert second.details["changed"] is False
    assert (hermes_home / "config.yaml").read_text() == "# operator file"


def test_install_phase_skips_venv_when_binary_exists_and_client_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A converged venv is left alone — binary present AND client importable.

    Re-baselined for #2102: the skip used to key on ``bin/hermes`` existing
    alone, which is what let a venv with no ``mcp`` package sail through the
    install phase and fail at ``mcp_wire``. The probe is injected healthy here
    so this test still covers what it was written to cover (a converged venv is
    not rebuilt); the drift case has its own test below.
    """
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

    called: list[dict[str, Any]] = []

    out = hp._phase_install(
        hp._StepCtx(
            state=state,
            io=hp.InstallIO(
                ensure_release=fake_ensure_release(called),
                probe_mcp_client=lambda *_a, **_kw: {
                    "ok": True,
                    "stage": "import",
                    "tools": [],
                    "error": None,
                },
            ),
        )
    )
    assert out.status == hp.PhaseStatus.OK
    # Venv binary present and its MCP client imports → the builder is asked
    # without force, and it reports nothing to do.
    assert [c["force"] for c in called] == [False]
    assert out.details["release_status"] == "converged"
    assert "venv_reinstalled_reason" not in out.details
    # Canonical `hermes` wrapper is installed (the hal0-hermes back-compat
    # symlink is retired).
    assert hermes_cli_dst.is_file()
    assert not wrapper_dst.exists()
    # Both shipped plugin trees dir-drop: hal0-memory to plugins/, hal0-provider
    # to its model-providers/hal0 target (its seed landed with the HP-provider
    # lane, so the missing-source skip path no longer triggers in-tree).
    assert (hermes_home / "plugins" / "hal0-memory" / "__init__.py").is_file()
    assert (hermes_home / "plugins" / "model-providers" / "hal0" / "__init__.py").is_file()
    assert not any("hal0-provider" in s for s in out.details.get("plugins_skipped", []))


def test_install_phase_reinstalls_a_venv_whose_mcp_client_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2102: ``bin/hermes`` existing is not proof the venv is usable.

    A venv that drifted off the vetted pin has the binary and no ``mcp``
    package, so the old existence gate skipped the install, reported
    ``install: ok``, and left ``mcp_wire`` to hard-fail over the very artefact
    the previous phase had just blessed. The operator was then told
    ``bootstrap failed in steps: mcp_wire`` with no remedy named, while plain
    ``hal0 agent reprovision hermes`` could not fix it and ``--repair`` — which
    could — advertised itself as a persona/config re-run.

    The phase now re-runs the install when the venv cannot import the client,
    with no ``--repair`` needed: the provisioner already knows both the
    diagnosis and the cure.
    """
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "hermes").write_text("#!/bin/sh\nexit 0\n")
    (venv / "bin" / "hermes").chmod(0o755)

    monkeypatch.setattr(
        hp, "WRAPPER_INSTALL_PATH", tmp_path / "usr" / "local" / "bin" / "hal0-hermes"
    )
    monkeypatch.setattr(
        hp, "HERMES_CLI_INSTALL_PATH", tmp_path / "usr" / "local" / "bin" / "hermes"
    )
    state = hp.BootstrapState(venv=str(venv), hermes_home=str(tmp_path / "hermes_home"))

    install_calls: list[dict[str, Any]] = []

    out = hp._phase_install(
        hp._StepCtx(
            state=state,
            io=hp.InstallIO(
                ensure_release=fake_ensure_release(install_calls),
                probe_mcp_client=lambda *_a, **_kw: {
                    "ok": False,
                    "stage": "import",
                    "tools": [],
                    "error": "ModuleNotFoundError: No module named 'mcp'",
                },
            ),
        )
    )

    assert out.status == hp.PhaseStatus.OK
    assert [(c["venv"], c["force"], c["reason"]) for c in install_calls] == [
        (venv, True, "mcp client import failed")
    ]
    assert out.details["venv_reinstalled_reason"] == "mcp client import failed"


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

    install_calls: list[dict[str, Any]] = []

    out = hp._phase_install(
        hp._StepCtx(state=state, io=hp.InstallIO(ensure_release=fake_ensure_release(install_calls)))
    )
    assert out.status == hp.PhaseStatus.OK
    assert [(c["venv"], c["force"], c["caller"], c["wait"]) for c in install_calls] == [
        (venv, True, "provision", True)
    ]
    assert out.details["release_status"] == "activated"


@pytest.mark.parametrize("status", ["build_failed", "activation_failed", "offline", "busy"])
def test_install_phase_fails_with_the_builder_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setattr(
        hp, "HERMES_CLI_INSTALL_PATH", tmp_path / "usr" / "local" / "bin" / "hermes"
    )
    state = hp.BootstrapState(venv=str(tmp_path / "venv"), hermes_home=str(tmp_path / "home"))
    out = hp._phase_install(
        hp._StepCtx(state=state, io=hp.InstallIO(ensure_release=fake_ensure_release(status=status)))
    )
    assert out.status == hp.PhaseStatus.FAIL
    assert f"hermes release {status}" in (out.reason or "")
    assert "fake remedy" in (out.reason or "")


def test_install_phase_mirrors_plugins_even_when_the_release_build_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2884: a failed release build must not skip the plugin mirror.

    A stale ``kind: provider`` manifest under HERMES_HOME survived every update
    because the mirror sat after the build, which returns FAIL early.
    """
    monkeypatch.setattr(
        hp, "HERMES_CLI_INSTALL_PATH", tmp_path / "usr" / "local" / "bin" / "hermes"
    )
    home = tmp_path / "home"
    stale = home / "plugins" / "model-providers" / "hal0"
    stale.mkdir(parents=True)
    (stale / "plugin.yaml").write_text("name: hal0\nkind: provider\n")
    state = hp.BootstrapState(venv=str(tmp_path / "venv"), hermes_home=str(home))
    out = hp._phase_install(
        hp._StepCtx(
            state=state,
            io=hp.InstallIO(ensure_release=fake_ensure_release(status="build_failed")),
        )
    )
    assert out.status == hp.PhaseStatus.FAIL
    src = hp.REPO_ROOT_FOR_INSTALLER / "installer/agents/hermes/plugins/hal0-provider/plugin.yaml"
    assert (stale / "plugin.yaml").read_text() == src.read_text()
    assert (home / "plugins" / "hal0-memory").is_dir()


def test_install_phase_failed_build_still_reports_the_plugin_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rewritten plugin tree is a recorded change even when the build fails."""
    monkeypatch.setattr(
        hp, "HERMES_CLI_INSTALL_PATH", tmp_path / "usr" / "local" / "bin" / "hermes"
    )
    home = tmp_path / "home"
    state = hp.BootstrapState(venv=str(tmp_path / "venv"), hermes_home=str(home))
    out = hp._phase_install(
        hp._StepCtx(
            state=state,
            io=hp.InstallIO(ensure_release=fake_ensure_release(status="build_failed")),
        )
    )
    assert out.status == hp.PhaseStatus.FAIL
    assert (out.details or {}).get("changed") is True
    assert hp._step_changed("install", out) is True


def test_install_phase_plugin_copy_failure_does_not_skip_the_release_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither step starves the other: the build runs, failures are merged."""
    monkeypatch.setattr(
        hp, "HERMES_CLI_INSTALL_PATH", tmp_path / "usr" / "local" / "bin" / "hermes"
    )

    def _boom(_src: Path, _dst: Path) -> bool:
        raise OSError("read-only plugins dir")

    monkeypatch.setattr(hp, "_copy_plugin_tree", _boom)
    calls: list[dict[str, Any]] = []
    inner = fake_ensure_release()

    def _ensure(**kw: Any) -> Any:
        calls.append(kw)
        return inner(**kw)

    state = hp.BootstrapState(venv=str(tmp_path / "venv"), hermes_home=str(tmp_path / "home"))
    out = hp._phase_install(hp._StepCtx(state=state, io=hp.InstallIO(ensure_release=_ensure)))
    assert calls, "release build must still run"
    assert out.status == hp.PhaseStatus.FAIL
    assert "read-only plugins dir" in (out.reason or "")


def test_install_phase_passes_offline_and_the_pin_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2838: HAL0_HERMES_OFFLINE (written by ``--offline``) reaches the builder."""
    monkeypatch.setattr(
        hp, "HERMES_CLI_INSTALL_PATH", tmp_path / "usr" / "local" / "bin" / "hermes"
    )
    monkeypatch.setenv("HAL0_HERMES_OFFLINE", "1")
    state = hp.BootstrapState(venv=str(tmp_path / "venv"), hermes_home=str(tmp_path / "h"))
    calls: list[dict[str, Any]] = []
    hp._phase_install(
        hp._StepCtx(state=state, io=hp.InstallIO(ensure_release=fake_ensure_release(calls)))
    )
    assert calls[0]["offline"] is True
    assert calls[0]["ref"] is None, "no override: install builds the pin"


def test_persisted_hermes_python_is_atomic_and_idempotent(tmp_path: Path) -> None:
    env_path = tmp_path / "hermes-python.env"

    class _Runner:
        @staticmethod
        def run(_argv: list[str], **_kwargs: Any) -> Any:
            return type("Result", (), {"stdout": "3.14\n"})()

    hp._persist_hermes_python("/opt/python3.14", env_path, runner=_Runner)
    first = env_path.read_text()
    hp._persist_hermes_python("/opt/python3.14", env_path, runner=_Runner)
    assert env_path.read_text() == first == "HAL0_HERMES_PYTHON=/opt/python3.14\n"
    assert env_path.stat().st_mode & 0o777 == 0o644


class _VersionRunner:
    """Reports 3.14 for paths containing ``3.14`` and 3.11 otherwise.

    The SQLite probe answers 3.46.1 (Ubuntu 26.04's) for a path containing
    ``oldsqlite`` and 3.53.1 otherwise.
    """

    @staticmethod
    def run(argv: list[str], **_kwargs: Any) -> Any:
        if any("sqlite3" in str(a) for a in argv):
            out = "3.46.1\n" if "oldsqlite" in argv[0] else "3.53.1\n"
        else:
            out = "3.14\n" if "3.14" in argv[0] else "3.11\n"
        return type("Result", (), {"stdout": out})()


def _unset_hermes_python_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # delenv alone records nothing when the name is absent, so a later
    # os.environ write by the code under test would leak into other tests.
    monkeypatch.setenv("HAL0_HERMES_PYTHON", "placeholder")
    monkeypatch.delenv("HAL0_HERMES_PYTHON")


_GOOD = "/usr/bin/python3.14"
_STALE = "/opt/python3.11"


def _which_314(name: str) -> str | None:
    return _GOOD if name == "python3.14" else None


def test_resolve_discards_invalid_env_value_and_reprobes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / "hermes-python.env"
    env = {"HAL0_HERMES_PYTHON": _STALE}
    _unset_hermes_python_env(monkeypatch)
    with structlog.testing.capture_logs() as logs:
        out = hp.resolve_hermes_python(
            env=env, env_path=env_path, prober=_which_314, runner=_VersionRunner
        )
    assert out == _GOOD
    assert env["HAL0_HERMES_PYTHON"] == _GOOD
    assert env_path.read_text() == f"HAL0_HERMES_PYTHON={_GOOD}\n"
    warn = next(e for e in logs if e["event"] == "hermes-python-discarded")
    assert warn["log_level"] == "warning"
    assert warn["value"] == _STALE
    assert "exactly 3.14" in warn["reason"]
    assert warn["chosen"] == _GOOD


def test_resolve_discards_invalid_file_value_and_persists_new(tmp_path: Path) -> None:
    env_path = tmp_path / "hermes-python.env"
    env_path.write_text(f"HAL0_HERMES_PYTHON={_STALE}\n")
    env: dict[str, str] = {}
    with structlog.testing.capture_logs() as logs:
        out = hp.resolve_hermes_python(
            env=env, env_path=env_path, prober=_which_314, runner=_VersionRunner
        )
    assert out == _GOOD
    assert env_path.read_text() == f"HAL0_HERMES_PYTHON={_GOOD}\n"
    warn = next(e for e in logs if e["event"] == "hermes-python-discarded")
    assert warn["source"] == "persisted"
    assert warn["value"] == _STALE


def test_resolve_discards_malformed_file_and_reprobes(tmp_path: Path) -> None:
    env_path = tmp_path / "hermes-python.env"
    env_path.write_text("HAL0_HERMES_PYTHON=/a\nHAL0_HERMES_PYTHON=/b\n")
    with structlog.testing.capture_logs() as logs:
        out = hp.resolve_hermes_python(
            env={}, env_path=env_path, prober=_which_314, runner=_VersionRunner
        )
    assert out == _GOOD
    assert env_path.read_text() == f"HAL0_HERMES_PYTHON={_GOOD}\n"
    warn = next(e for e in logs if e["event"] == "hermes-python-discarded")
    assert "invalid Hermes Python environment file" in warn["reason"]
    assert "HAL0_HERMES_PYTHON=/a" in warn["value"]


def test_resolve_leaves_unwritable_file_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / "hermes-python.env"
    env_path.write_text(f"HAL0_HERMES_PYTHON={_STALE}\n")

    def _denied(*_a: Any, **_k: Any) -> None:
        raise PermissionError("read-only file system")

    monkeypatch.setattr(hp, "_persist_hermes_python", _denied)
    env: dict[str, str] = {}
    with structlog.testing.capture_logs() as logs:
        out = hp.resolve_hermes_python(
            env=env, env_path=env_path, prober=_which_314, runner=_VersionRunner
        )
    assert out == _GOOD
    assert env["HAL0_HERMES_PYTHON"] == _GOOD
    assert env_path.read_text() == f"HAL0_HERMES_PYTHON={_STALE}\n"
    assert any(e["event"] == "hermes-python-persist-skipped" for e in logs)


def test_persist_unwritable_dir_raises_oserror_and_leaves_no_tmp(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    d = tmp_path / "etc"
    d.mkdir()
    env_path = d / "hermes-python.env"
    env_path.write_text(f"HAL0_HERMES_PYTHON={_STALE}\n")
    d.chmod(0o555)
    try:
        with pytest.raises(OSError):
            hp._persist_hermes_python(_GOOD, env_path, runner=_VersionRunner)
    finally:
        d.chmod(0o755)
    assert env_path.read_text() == f"HAL0_HERMES_PYTHON={_STALE}\n"
    assert [p.name for p in d.iterdir()] == ["hermes-python.env"]


def test_resolve_valid_value_still_wins(tmp_path: Path) -> None:
    env_path = tmp_path / "hermes-python.env"
    env_path.write_text("HAL0_HERMES_PYTHON=/opt/py3.14-file\n")
    with structlog.testing.capture_logs() as logs:
        out = hp.resolve_hermes_python(
            env={"HAL0_HERMES_PYTHON": "/opt/py3.14-env"},
            env_path=env_path,
            prober=_which_314,
            runner=_VersionRunner,
        )
        assert out == "/opt/py3.14-env"
        out = hp.resolve_hermes_python(
            env={}, env_path=env_path, prober=_which_314, runner=_VersionRunner
        )
    assert out == "/opt/py3.14-file"
    assert env_path.read_text() == "HAL0_HERMES_PYTHON=/opt/py3.14-file\n"
    assert not any(e["event"] == "hermes-python-discarded" for e in logs)


def test_resolve_still_raises_when_reprobe_finds_nothing(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=r"no usable Python 3\.14"):
        hp.resolve_hermes_python(
            env={"HAL0_HERMES_PYTHON": _STALE},
            env_path=tmp_path / "hermes-python.env",
            prober=lambda _name: None,
            runner=_VersionRunner,
            fallback_bin=tmp_path / "no-such-uv",
        )


def test_ensure_discards_invalid_env_value_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / "hermes-python.env"
    monkeypatch.setenv("HAL0_HERMES_PYTHON", _STALE)
    out = hp._ensure_supported_python(prober=_which_314, runner=_VersionRunner, env_path=env_path)
    assert out == _GOOD
    assert os.environ["HAL0_HERMES_PYTHON"] == _GOOD
    assert env_path.read_text() == f"HAL0_HERMES_PYTHON={_GOOD}\n"


def test_ensure_discards_malformed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / "hermes-python.env"
    env_path.write_text("garbage\n")
    _unset_hermes_python_env(monkeypatch)
    out = hp._ensure_supported_python(prober=_which_314, runner=_VersionRunner, env_path=env_path)
    assert out == _GOOD
    assert env_path.read_text() == f"HAL0_HERMES_PYTHON={_GOOD}\n"


def test_ensure_unwritable_file_uses_reprobed_for_this_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / "hermes-python.env"
    env_path.write_text(f"HAL0_HERMES_PYTHON={_STALE}\n")
    _unset_hermes_python_env(monkeypatch)

    def _denied(*_a: Any, **_k: Any) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(hp, "_persist_hermes_python", _denied)
    out = hp._ensure_supported_python(prober=_which_314, runner=_VersionRunner, env_path=env_path)
    assert out == _GOOD
    assert env_path.read_text() == f"HAL0_HERMES_PYTHON={_STALE}\n"


def test_ensure_valid_persisted_value_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / "hermes-python.env"
    env_path.write_text("HAL0_HERMES_PYTHON=/opt/py3.14-file\n")
    _unset_hermes_python_env(monkeypatch)
    out = hp._ensure_supported_python(prober=_which_314, runner=_VersionRunner, env_path=env_path)
    assert out == "/opt/py3.14-file"
    assert env_path.read_text() == "HAL0_HERMES_PYTHON=/opt/py3.14-file\n"


def test_ensure_returns_none_when_reprobe_finds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_HERMES_PYTHON", _STALE)
    out = hp._ensure_supported_python(
        prober=lambda _name: None,
        runner=_VersionRunner,
        fallback_bin=tmp_path / "no-such-uv",
        env_path=tmp_path / "hermes-python.env",
    )
    assert out is None


def test_resolve_python_probes_only_the_target_minor() -> None:
    probed: list[str] = []

    def _prober(name: str) -> str | None:
        probed.append(name)
        return f"/opt/{name}" if name == "python3.14" else None

    assert hp._resolve_supported_python(prober=_prober, runner=_VersionRunner) == "/opt/python3.14"
    assert probed == ["python3.14"]
    probed.clear()
    assert hp._resolve_supported_python(prober=_prober, runner=_VersionRunner, minor="3.12") is None
    assert probed == ["python3.12"]


def test_hal0s_own_interpreter_is_never_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0034 d2: no running-interpreter branch, even when hal0 runs on 3.14."""
    monkeypatch.setattr(hp.sys, "version_info", (3, 14, 0, "final", 0))
    assert hp._resolve_supported_python(prober=lambda _n: None, runner=_VersionRunner) is None


def test_system_314_with_a_new_sqlite_is_accepted() -> None:
    out = hp._resolve_supported_python(
        prober=lambda name: "/usr/bin/python3.14" if name == "python3.14" else None,
        runner=_VersionRunner,
    )
    assert out == "/usr/bin/python3.14"


def test_system_314_with_an_old_sqlite_is_refused() -> None:
    """ADR-0034 d2: Ubuntu 26.04's python3.14 links SQLite 3.46.1 (< 3.51.3)."""
    out = hp._resolve_supported_python(
        prober=lambda name: "/usr/bin/oldsqlite/python3.14" if name == "python3.14" else None,
        runner=_VersionRunner,
    )
    assert out is None


class _UvRunner:
    """uv install/find succeed with a managed path; SQLite answers per path."""

    def __init__(self, managed: str, *, managed_sqlite: str = "3.53.1") -> None:
        self.managed = managed
        self.managed_sqlite = managed_sqlite
        self.ran: list[list[str]] = []

    def run(self, argv: list[str], **_kw: Any) -> Any:
        argv = [str(a) for a in argv]
        self.ran.append(argv)
        if any("sqlite3" in a for a in argv):
            out = (
                self.managed_sqlite
                if argv[0] == self.managed
                else ("3.46.1" if "oldsqlite" in argv[0] else "3.53.1")
            )
        elif argv[1:3] == ["python", "find"]:
            out = self.managed
        else:
            out = ""
        return type("R", (), {"stdout": out + "\n"})()


def _uv_prober(system: str | None = "/usr/bin/python3.14"):
    def prober(name: str) -> str | None:
        return {"python3.14": system, "uv": "/usr/local/bin/uv"}.get(name)

    return prober


def test_uv_managed_comes_before_a_qualifying_system_python(tmp_path: Path) -> None:
    """ADR-0034 d2 order: uv-managed first, even over a system 3.14 with a new SQLite."""
    managed = f"{hp.UV_PYTHON_INSTALL_DIR}/cpython-3.14-linux-x86_64-gnu/bin/python3.14"
    runner = _UvRunner(managed)
    out = hp._ensure_supported_python(
        prober=_uv_prober(),
        runner=runner,
        fallback_bin=Path("/nonexistent/uv"),
        env_path=tmp_path / "e.env",
    )
    assert out == managed


def test_uv_install_and_find_are_managed_only_and_sqlite_checked(tmp_path: Path) -> None:
    managed = f"{hp.UV_PYTHON_INSTALL_DIR}/cpython-3.14-linux-x86_64-gnu/bin/python3.14"
    runner = _UvRunner(managed)
    hp._provision_python_via_uv(_uv_prober(), runner, fallback_bin=Path("/nonexistent/uv"))
    install, find = (a for a in runner.ran if a[0] == "/usr/local/bin/uv")
    assert install[1:] == ["python", "install", "3.14", "--managed-python", "--no-bin"]
    assert find[1:] == ["python", "find", "3.14", "--managed-python"]
    assert any(a[0] == managed and "sqlite3" in a[-1] for a in runner.ran)


def test_a_managed_python_with_an_old_sqlite_falls_back_to_the_system(tmp_path: Path) -> None:
    managed = f"{hp.UV_PYTHON_INSTALL_DIR}/cpython-3.14/bin/python3.14"
    runner = _UvRunner(managed, managed_sqlite="3.40.0")
    out = hp._ensure_supported_python(
        prober=_uv_prober(),
        runner=runner,
        fallback_bin=Path("/nonexistent/uv"),
        env_path=tmp_path / "e.env",
    )
    assert out == "/usr/bin/python3.14"


def test_the_system_python_is_the_fallback_when_uv_cannot_fetch(tmp_path: Path) -> None:
    class _NoFetch(_UvRunner):
        def run(self, argv: list[str], **kw: Any) -> Any:
            if str(argv[0]).endswith("/uv"):
                raise subprocess.CalledProcessError(1, argv)
            return super().run(argv, **kw)

    out = hp._ensure_supported_python(
        prober=_uv_prober(),
        runner=_NoFetch("x"),
        fallback_bin=Path("/nonexistent/uv"),
        env_path=tmp_path / "e.env",
    )
    assert out == "/usr/bin/python3.14"


def test_a_system_python_with_an_old_sqlite_is_never_the_fallback(tmp_path: Path) -> None:
    class _NoFetch(_UvRunner):
        def run(self, argv: list[str], **kw: Any) -> Any:
            if str(argv[0]).endswith("/uv"):
                raise subprocess.CalledProcessError(1, argv)
            return super().run(argv, **kw)

    out = hp._ensure_supported_python(
        prober=_uv_prober("/usr/bin/oldsqlite/python3.14"),
        runner=_NoFetch("x"),
        fallback_bin=Path("/nonexistent/uv"),
        env_path=tmp_path / "e.env",
    )
    assert out is None


def test_the_resolver_targets_the_minor_it_is_given(tmp_path: Path) -> None:
    managed = f"{hp.UV_PYTHON_INSTALL_DIR}/cpython-3.12-linux-x86_64-gnu/bin/python3.12"
    runner = _UvRunner(managed)
    out = hp._ensure_supported_python(
        prober=lambda n: "/usr/local/bin/uv" if n == "uv" else None,
        runner=runner,
        fallback_bin=Path("/nonexistent/uv"),
        env_path=tmp_path / "e.env",
        minor="3.12",
    )
    assert out == managed
    assert [a[3] for a in runner.ran if a[0] == "/usr/local/bin/uv"] == ["3.12", "3.12"]


def test_the_pinned_ref_targets_314() -> None:
    """v0.22.4 (4d043038) declares requires-python ">=3.11,<3.15" (its
    pyproject.toml:15), as v0.22.2 (d923a5eb) did, so the shipped pin keeps
    hosts on 3.14 and the v0.22.2 flip-back target builds on the same minor."""
    assert hp.pinned_target_minor() == "3.14"
    assert set(hp.VETTED_HERMES_REQUIRES_PYTHON) == set(hp.VETTED_HERMES_REFS)
    for ref in (
        "4d04303818b61bf3aa09278af48356315515965b",
        "d923a5eb487a17c0a68801bba9ef0f56c81040a8",
    ):
        assert ref in hp.VETTED_HERMES_REFS
        assert hp.hermes_target_minor(ref) == "3.14"


class _MinorRunner(_UvRunner):
    """``_UvRunner`` that also answers the version probe from the path."""

    def run(self, argv: list[str], **kw: Any) -> Any:
        argv = [str(a) for a in argv]
        if len(argv) > 2 and argv[1] == "-c" and "version_info" in argv[2]:
            self.ran.append(argv)
            out = "3.14" if "3.14" in argv[0] else "3.12"
            return type("R", (), {"stdout": out + "\n"})()
        return super().run(argv, **kw)


@pytest.mark.parametrize("source", ["persisted", "environment"])
def test_the_pin_bump_repersists_the_312_interpreter_as_the_managed_314(
    tmp_path: Path, source: str
) -> None:
    """#2615 PR 4: a host whose hermes-python.env names the uv-managed 3.12 of
    the 0.19.0 pin is re-pointed at a uv-managed 3.14 on the next resolve for
    the v0.22.2 pin, and the file is rewritten. ``environment`` is hal0-api
    during `hal0 update`: its unit loads the old value through
    ``EnvironmentFile=-/etc/hal0/hermes-python.env``, and /etc/hal0 is
    hal0:hal0 2775, so the rewrite succeeds as hal0."""
    env_path = tmp_path / "hermes-python.env"
    old = f"{hp.UV_PYTHON_INSTALL_DIR}/cpython-3.12.15-linux-x86_64-gnu/bin/python3.12"
    new = f"{hp.UV_PYTHON_INSTALL_DIR}/cpython-3.14.7-linux-x86_64-gnu/bin/python3.14"
    env_path.write_text(f"HAL0_HERMES_PYTHON={old}\n")
    env = {"HAL0_HERMES_PYTHON": old} if source == "environment" else {}
    with structlog.testing.capture_logs() as logs:
        out = hp.resolve_hermes_python(
            env=env,
            env_path=env_path,
            prober=_uv_prober(system=None),
            runner=_MinorRunner(new),
            fallback_bin=Path("/nonexistent/uv"),
            minor=hp.pinned_target_minor(),
        )
    assert out == new
    assert env_path.read_text() == f"HAL0_HERMES_PYTHON={new}\n"
    warn = next(e for e in logs if e["event"] == "hermes-python-discarded")
    assert warn["value"] == old and "exactly 3.14" in warn["reason"]
    assert warn["source"] == source


def test_an_override_with_an_old_sqlite_is_used_with_a_warning(tmp_path: Path) -> None:
    override = "/opt/oldsqlite/python3.14"
    with structlog.testing.capture_logs() as logs:
        out = hp.resolve_hermes_python(
            env={"HAL0_HERMES_PYTHON": override},
            env_path=tmp_path / "hermes-python.env",
            prober=_which_314,
            runner=_VersionRunner,
        )
    assert out == override
    warn = next(e for e in logs if e["event"] == "hermes-python-old-sqlite")
    assert warn["log_level"] == "warning" and warn["sqlite"] == "3.46.1"


def test_a_stale_312_value_is_discarded_and_reprobed_to_314(tmp_path: Path) -> None:
    """A 3.12 value persisted for an old pin is re-probed when the target is 3.14."""
    env_path = tmp_path / "hermes-python.env"
    stale = "/var/lib/hal0/python/cpython-3.12.15-linux-x86_64-gnu/bin/python3.12"
    env_path.write_text(f"HAL0_HERMES_PYTHON={stale}\n")
    env: dict[str, str] = {}
    with structlog.testing.capture_logs() as logs:
        out = hp.resolve_hermes_python(
            env=env,
            env_path=env_path,
            prober=_which_314,
            runner=_VersionRunner,
            fallback_bin=Path("/nonexistent/uv"),
            minor="3.14",
        )
    assert out == _GOOD
    assert env_path.read_text() == f"HAL0_HERMES_PYTHON={_GOOD}\n"
    warn = next(e for e in logs if e["event"] == "hermes-python-discarded")
    assert warn["value"] == stale and "exactly 3.14" in warn["reason"]


def test_a_persisted_312_value_is_kept_while_the_pin_targets_312(tmp_path: Path) -> None:
    """PR 3 alone changes nothing on a 3ef6bbd2 host."""
    env_path = tmp_path / "hermes-python.env"
    kept = "/opt/python3.12"
    env_path.write_text(f"HAL0_HERMES_PYTHON={kept}\n")

    class _R:
        @staticmethod
        def run(argv: list[str], **_kw: Any) -> Any:
            out = "3.53.1" if any("sqlite3" in str(a) for a in argv) else "3.12"
            return type("R", (), {"stdout": out + "\n"})()

    out = hp.resolve_hermes_python(env={}, env_path=env_path, runner=_R, minor="3.12")
    assert out == kept
    assert env_path.read_text() == f"HAL0_HERMES_PYTHON={kept}\n"


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
    io = hp.InstallIO(http_get=lambda *_a, **_kw: 200)
    out = hp._phase_preflight(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.FAIL
    minor = hp.pinned_target_minor()
    assert f"Python {minor}" in (out.reason or "")
    assert "3.51.3" in (out.reason or "")
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
    io = hp.InstallIO(http_get=lambda *_a, **_kw: 200)
    out = hp._phase_preflight(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["uv_python_fallback"] is True


class TestUvDiscovery:
    """ADR-0034 decision 2: uv ships in hal0's venv, next to ``sys.executable``.

    The sibling is checked first, so a privilege drop that carries a deficient
    PATH (#2124) cannot hide it; ``PATH`` is the fallback for a dev venv
    without uv. The pipx lookup at ``/usr/local/bin/uv`` is gone with
    ``hermes-prereqs.sh``'s ``ensure_uv``.
    """

    @staticmethod
    def _exe(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\necho uv\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_sibling_of_the_running_interpreter_wins_over_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        uv = self._exe(tmp_path / "venv" / "bin" / "uv")
        monkeypatch.setattr(hp.sys, "executable", str(tmp_path / "venv" / "bin" / "python"))
        assert hp._uv_available(prober=lambda _name: "/opt/elsewhere/uv") == str(uv)

    def test_path_is_the_fallback_when_the_venv_has_no_uv(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(hp.sys, "executable", str(tmp_path / "bare" / "bin" / "python"))
        assert (
            hp._uv_available(prober=lambda _name: "/opt/homebrew/bin/uv") == "/opt/homebrew/bin/uv"
        )

    def test_none_when_neither_has_uv(self, tmp_path: Path) -> None:
        assert hp._uv_available(prober=lambda _name: None, fallback_bin=tmp_path / "nope") is None

    def test_a_directory_named_uv_is_not_uv(self, tmp_path: Path) -> None:
        bogus = tmp_path / "uv"
        bogus.mkdir()
        assert hp._uv_available(prober=lambda _name: None, fallback_bin=bogus) is None

    def test_fallback_bin_overrides_the_sibling(self, tmp_path: Path) -> None:
        uv = self._exe(tmp_path / "custom" / "uv")
        assert hp._uv_available(prober=lambda _name: None, fallback_bin=uv) == str(uv)

    def test_release_builder_uses_the_same_order(self, tmp_path: Path) -> None:
        from hal0.agents.hermes_release import find_uv

        uv = self._exe(tmp_path / "venv" / "bin" / "uv")
        exe = tmp_path / "venv" / "bin" / "python"
        assert find_uv(executable=exe, which=lambda _n: "/usr/bin/uv") == str(uv)
        assert (
            find_uv(executable=tmp_path / "x" / "python", which=lambda _n: "/usr/bin/uv")
            == "/usr/bin/uv"
        )
        assert find_uv(executable=tmp_path / "x" / "python", which=lambda _n: None) is None


def test_ensure_python_uses_uv_first_even_with_a_qualifying_system_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0034 d2: uv-managed first; a good system python3.14 does not win."""
    monkeypatch.delenv("HAL0_HERMES_PYTHON", raising=False)
    managed = f"{hp.UV_PYTHON_INSTALL_DIR}/cpython-3.14-linux-x86_64-gnu/bin/python3.14"
    runner = _UvRunner(managed)
    out = hp._ensure_supported_python(
        prober=_uv_prober("/usr/bin/python3.14"),
        runner=runner,
        env_path=tmp_path / "hermes-python.env",
    )
    assert out == managed
    first_uv = next(a for a in runner.ran if a[0] == "/usr/local/bin/uv")
    assert first_uv[1:3] == ["python", "install"]


def test_ensure_python_falls_back_to_a_qualifying_system_interpreter_without_uv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No uv anywhere: the system python3.14 with a current SQLite is used."""
    monkeypatch.delenv("HAL0_HERMES_PYTHON", raising=False)
    ran: list[list[str]] = []

    class _Runner:
        @staticmethod
        def run(argv: list[str], **_kw: Any) -> Any:
            ran.append(list(argv))
            return type("R", (), {"stdout": "3.53.1\n"})()

    out = hp._ensure_supported_python(
        prober=lambda name: "/usr/bin/python3.14" if name == "python3.14" else None,
        runner=_Runner,
        env_path=tmp_path / "hermes-python.env",
    )
    assert out == "/usr/bin/python3.14"
    assert all("uv" not in a[0] for a in ran)


def test_ensure_python_provisions_via_uv_when_no_system_interpreter() -> None:
    ran: list[list[str]] = []
    envs: list[dict[str, str] | None] = []

    class _Result:
        stdout = "/var/lib/hal0/python/cpython-3.14.7-linux-x86_64-gnu/bin/python3.14\n"

    class _Runner:
        @staticmethod
        def run(argv: list[str], *, env: dict[str, str] | None = None, **_kw: Any) -> _Result:
            ran.append(argv)
            envs.append(env)
            if any("sqlite3" in str(x) for x in argv):
                return type("R", (), {"stdout": "3.53.1\n"})()
            return _Result()

    out = hp._ensure_supported_python(
        prober=lambda name: "/usr/local/bin/uv" if name == "uv" else None,
        runner=_Runner,
    )
    assert out == "/var/lib/hal0/python/cpython-3.14.7-linux-x86_64-gnu/bin/python3.14"
    # install runs before find, both pinned to UV_PYTHON_FALLBACK, then the
    # found interpreter's SQLite is checked...
    uv_calls = [a for a in ran if a[0] == "/usr/local/bin/uv"]
    assert [a[1:3] for a in uv_calls] == [["python", "install"], ["python", "find"]]
    assert all(a[3] == hp.UV_PYTHON_FALLBACK for a in uv_calls)
    assert uv_calls[0][4:] == ["--managed-python", "--no-bin"]  # no ~/.local/bin link
    # ...and both redirected to the world-readable install dir (the default
    # ~/.local/share/uv under root's 0700 home would be unreachable by the
    # hal0 user the venv runs as).
    uv_envs = [e for a, e in zip(ran, envs, strict=True) if a[0] == "/usr/local/bin/uv"]
    assert all(
        e is not None and e["UV_PYTHON_INSTALL_DIR"] == str(hp.UV_PYTHON_INSTALL_DIR)
        for e in uv_envs
    )


def test_provision_via_uv_sanitizes_leaked_root_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O15: uv must not inherit a leaked HOME=/root.

    On a py3.14-only host the provision drops to hal0 and takes the uv fallback;
    with HOME=/root inherited, uv tried to open /root/uv.toml and failed
    "Permission denied" → bootstrap failed. The subprocess env must force HOME
    to the hal0 home and pin UV_CACHE_DIR under the state root so uv never
    reaches into /root.
    """
    monkeypatch.setenv("HOME", "/root")  # the leak

    envs: list[dict[str, str] | None] = []

    class _Result:
        stdout = "/var/lib/hal0/python/cpython-3.14/bin/python3.14\n"

    class _Runner:
        @staticmethod
        def run(argv: list[str], *, env: dict[str, str] | None = None, **_kw: Any) -> _Result:
            envs.append(env)
            if any("sqlite3" in str(x) for x in argv):
                return type("R", (), {"stdout": "3.53.1\n"})()
            return _Result()

    out = hp._provision_python_via_uv(
        prober=lambda name: "/usr/local/bin/uv" if name == "uv" else None,
        runner=_Runner,
    )
    assert out == "/var/lib/hal0/python/cpython-3.14/bin/python3.14"
    assert envs, "uv must have been invoked"
    for e in (e for e in envs if e is not None):
        # HOME is forced off /root to the resolved hal0 home (or the state-root
        # fallback when the hal0 account is absent, as in CI).
        assert e["HOME"] != "/root"
        assert e["HOME"] == hp._hal0_service_home()
        assert e["UV_CACHE_DIR"] == str(hp.UV_CACHE_DIR)
        assert e["UV_PYTHON_INSTALL_DIR"] == str(hp.UV_PYTHON_INSTALL_DIR)


def test_release_build_env_sanitizes_leaked_root_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """O15 (same leak class): git and uv in the release builder get a sane HOME."""
    from hal0.agents.hermes_release import build_env

    monkeypatch.setenv("HOME", "/root")
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/else")
    env = build_env()
    assert env["HOME"] == hp._hal0_service_home() != "/root"
    assert env["UV_CACHE_DIR"] == str(hp.UV_CACHE_DIR)
    assert env["UV_PYTHON_INSTALL_DIR"] == str(hp.UV_PYTHON_INSTALL_DIR)
    assert "VIRTUAL_ENV" not in env


def test_provision_via_uv_creates_install_dir_world_traversable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Under a restrictive umask, root's uv would write a 0700 tree the hal0
    # service user cannot traverse to reach the symlinked base interpreter —
    # the exact "gateway venv python won't start" failure the /var/lib/hal0
    # move was meant to fix. The install dir must be created 0o755 *before*
    # uv is invoked, so record its mode at the moment the runner is called. It is
    # 2775, matching the perms table.
    install_dir = tmp_path / "python"
    monkeypatch.setattr(hp, "UV_PYTHON_INSTALL_DIR", install_dir)

    modes_at_run: list[int] = []

    class _Result:
        stdout = f"{install_dir}/cpython-3.14/bin/python3.14\n"

    class _Runner:
        @staticmethod
        def run(argv: list[str], **_kw: Any) -> _Result:
            modes_at_run.append(install_dir.stat().st_mode & 0o7777)
            if any("sqlite3" in str(x) for x in argv):
                return type("R", (), {"stdout": "3.53.1\n"})()
            return _Result()

    old_umask = os.umask(0o077)
    try:
        out = hp._ensure_supported_python(
            prober=lambda name: "/usr/local/bin/uv" if name == "uv" else None,
            runner=_Runner,
        )
    finally:
        os.umask(old_umask)

    assert out == f"{install_dir}/cpython-3.14/bin/python3.14"
    # World-traversable despite the 077 umask (a plain mkdir would be 0700)...
    assert install_dir.is_dir()
    # 2775, the mode hal0.install.perms declares for it (setgid hal0 group).
    assert install_dir.stat().st_mode & 0o7777 == 0o2775
    # ...and already so by the time uv's first subcommand ran.
    assert modes_at_run and modes_at_run[0] == 0o2775


def test_ensure_python_returns_none_without_uv(tmp_path: Path) -> None:
    # #2151 review: a prober that reports "no uv" must not still fall through
    # to whatever /usr/local/bin/uv happens to exist on the box actually
    # running this test — pin fallback_bin somewhere guaranteed absent so the
    # assertion holds on a fleet box with a real uv install, not just in CI.
    out = hp._ensure_supported_python(
        prober=lambda _name: None,
        fallback_bin=tmp_path / "no-such-uv",
    )
    assert out is None


def test_ensure_python_returns_none_when_uv_fetch_fails() -> None:
    class _Runner:
        @staticmethod
        def run(argv: list[str], **_kw: Any) -> None:
            raise subprocess.CalledProcessError(1, argv)

    out = hp._ensure_supported_python(
        prober=lambda name: "/usr/local/bin/uv" if name == "uv" else None,
        runner=_Runner,
    )
    assert out is None


def test_install_venv_is_gone() -> None:
    # ADR-0032 decision 6: nothing writes into a release once it exists, so the
    # in-place build and its minor-version rebuild are deleted.
    assert not hasattr(hp, "_install_venv")
    assert not hasattr(hp, "_venv_python_minor")


def test_requirement_pins_a_vetted_commit() -> None:
    # The builder checks the pinned commit out of its mirror, so the shipped
    # requirement must name a full commit in hp.VETTED_HERMES_REFS. A version
    # floor no longer counts (ADR-0032 decision 3).
    text = hp.HERMES_REQUIREMENTS.read_text()
    ref = hp.hermes_pinned_ref(hp._hermes_requirement_line(text))
    assert ref is not None and ref in hp.VETTED_HERMES_REFS
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
            "4d04303818b61bf3aa09278af48356315515965b",
            True,
        ),
        # The previous pin stays vetted (flip-back, boxes not yet updated).
        (
            "hermes-agent[web] @ git+https://github.com/NousResearch/hermes-agent.git@"
            "d923a5eb487a17c0a68801bba9ef0f56c81040a8",
            True,
        ),
        # Floored+capped spec — a floor names no commit to check out.
        ("hermes-agent[web]>=0.16.0,<1.0", False),
        # A tag pin — refused: only a full 40-hex commit is vetted.
        (
            "hermes-agent[web] @ git+https://github.com/NousResearch/hermes-agent.git@v2026.7.20",
            False,
        ),
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
    # The vetted-ref protection lives in one seam; exercise the accept path
    # (vetted commit) and the reject paths (any floor, hard-pinned version, a
    # tag, an unreviewed commit) directly.
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
        "hermes-agent.git@4d04303818b61bf3aa09278af48356315515965b\n",
    )
    assert pin == "4d04303818b6"


def test_version_pin_reports_exact_floored_and_unknown_forms(monkeypatch, tmp_path) -> None:
    assert _version_pin_for(monkeypatch, tmp_path, "hermes-agent[web]==0.18.2\n") == "0.18.2"
    assert _version_pin_for(monkeypatch, tmp_path, "hermes-agent[web]>=0.16.0,<1.0\n") == ">=0.16.0"
    assert _version_pin_for(monkeypatch, tmp_path, "# only comments\n") == "unknown"


# ── #241 phase impls — env_probe / config_write ─────────────────────────────


def test_env_probe_writes_snapshot_to_hermes_home(tmp_path: Path) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    out = hp._phase_env_probe(hp._StepCtx(state=state))
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


def test_primary_pick_and_auxiliary_main_agree_without_agent_slot() -> None:
    # ADR-0023 retired `chat`/`primary`: with `brain` + `chat` and no `agent`,
    # both resolvers must take the same slot (first ready llm in pool order),
    # never one by alphabetical accident and one by legacy name (#2635).
    slots = [
        {"name": "brain", "type": "llm", "state": "ready", "model_id": "brain-model"},
        {"name": "chat", "type": "llm", "state": "ready", "model_id": "chat-model"},
    ]
    primary = hp._resolve_primary_slot(slots_fetcher=lambda: slots)
    from hal0.agents.role_slots import candidate_from_slot_mapping, resolve_role_slots

    role_map = resolve_role_slots("hermes", [candidate_from_slot_mapping(s) for s in slots])
    main = next(e for e in role_map.entries if e.role == "main")
    assert primary["model"] == "brain-model"
    assert main.model == primary["model"]


def test_resolve_primary_slot_reads_the_slot_views_effective_window() -> None:
    """ADR-0029: ``/api/slots`` carries ``ctx_effective`` (the profile's window,
    else the model's native one) — it wins over any other key."""
    fake = lambda: [  # noqa: E731
        {
            "name": "primary",
            "type": "llm",
            "state": "ready",
            "model_id": "qwen3:8b",
            "backend_url": "http://127.0.0.1:8001/v1",
            "ctx_effective": 65536,
            "ctx_source": "profile",
            "context_length": 16384,
        }
    ]
    out = hp._resolve_primary_slot(slots_fetcher=fake)
    assert out["context_length"] == 65536


def test_slot_context_length_prefers_ctx_effective() -> None:
    assert hp._slot_context_length({"ctx_effective": 65536, "context_length": 8192}) == 65536
    assert hp._slot_context_length({"context_length": 8192}) == 8192
    assert hp._slot_context_length({"name": "x"}) is None


def test_resolve_primary_slot_fallback_when_no_slots() -> None:
    out = hp._resolve_primary_slot(slots_fetcher=lambda: [])
    assert out["model"] == "primary"
    assert out["context_length"] == 32768
    # Placeholder points at hal0-api on 8080/v1, not the legacy
    # phantom on 8000.
    assert out["base_url"] == "http://127.0.0.1:8080/v1"


def test_resolve_primary_slot_skips_modelless_named_seed_for_serving_slot() -> None:
    """Regression for #1892.

    A fresh install carries an unwired ``agent`` seed slot (no model bound)
    alongside a genuinely serving llm slot under another name. The name-based
    match must not shadow the slot that is actually serving traffic.
    """
    fake = lambda: [  # noqa: E731
        {"name": "agent", "type": "llm", "state": "idle", "model_id": None},
        {
            "name": "qwen-coder",
            "type": "llm",
            "state": "serving",
            "model_id": "qwen3-coder:30b",
            "backend": "vllm",
            "backend_url": "http://127.0.0.1:8001/v1",
            "context_length": 32768,
        },
    ]
    out = hp._resolve_primary_slot(slots_fetcher=fake)
    assert out["model"] == "qwen3-coder:30b"
    assert out["placeholder"] is False
    # One selection, one answer: the alias/backend the renderers consume must
    # come from the SAME slot that supplied the model — never re-derived by
    # name (which would relabel this model `model_aliases.agent`, an alias
    # _collect_chat_slots never writes for a model-less seed).
    assert out["alias"] == "qwen-coder"
    assert out["backend"] == "vllm"


def test_resolve_primary_slot_accepts_warming_slot_with_bound_model() -> None:
    """#1892 register `expect`: a resident (warming) model with a bound
    model_id must never render as "no chat model loaded" for the whole
    duration of a large load. `model_aliases.<slot>` for it already exists —
    the gateway cold-loads/queues on demand."""
    fake = lambda: [  # noqa: E731
        {
            "name": "agent",
            "type": "llm",
            "state": "warming",
            "model_id": "qwen3:32b",
            "context_length": 16384,
        },
    ]
    out = hp._resolve_primary_slot(slots_fetcher=fake)
    assert out["model"] == "qwen3:32b"
    assert out["placeholder"] is False
    assert out["alias"] == "agent"


def test_resolve_primary_slot_prefers_dispatchable_over_warming() -> None:
    """A dispatchable slot outranks a warming one, even when the warming slot
    carries the canonical `agent` name — name preference applies only within
    a residency tier."""
    fake = lambda: [  # noqa: E731
        {"name": "agent", "type": "llm", "state": "warming", "model_id": "qwen3:32b"},
        {"name": "qwen-coder", "type": "llm", "state": "serving", "model_id": "qwen3-coder:30b"},
    ]
    out = hp._resolve_primary_slot(slots_fetcher=fake)
    assert out["model"] == "qwen3-coder:30b"
    assert out["alias"] == "qwen-coder"


def test_resolve_primary_slot_warming_without_model_is_placeholder() -> None:
    """Warming counts as resident only WITH a bound model — a model-less
    warming slot still falls through to the placeholder."""
    fake = lambda: [  # noqa: E731
        {"name": "agent", "type": "llm", "state": "warming", "model_id": None},
    ]
    out = hp._resolve_primary_slot(slots_fetcher=fake)
    assert out["placeholder"] is True


# Derived from the canonical enum, not hardcoded lists: a new SlotState
# member can never slip through untested (#1892 "closes this permanently").
@pytest.mark.parametrize("state", sorted(str(s) for s in DISPATCHABLE_STATES))
def test_is_ready_accepts_every_dispatchable_slot_state(state: str) -> None:
    """Regression for #1892: _is_ready must agree with the canonical
    dispatchable-state set in hal0.slots.state, not a stale ad-hoc vocabulary.
    """
    assert hp._is_ready({"state": state}) is True


@pytest.mark.parametrize("state", sorted(str(s) for s in set(SlotState) - DISPATCHABLE_STATES))
def test_is_ready_rejects_non_dispatchable_slot_states(state: str) -> None:
    assert hp._is_ready({"state": state}) is False


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
        live_resolve_enabled=True,
    )
    base.update(over)
    return dict(hp._build_config_overlay(**base))


def test_merge_drops_config_keys_hal0_no_longer_writes(tmp_path: Path) -> None:
    yaml = pytest.importorskip("yaml")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "agent:\n  max_turns: 60\n  system_prompt_prelude: old\nmodel:\n  default: m\n",
        encoding="utf-8",
    )
    changed = hp._merge_config_yaml_layers(
        cfg, list_keys={}, overrides_path=tmp_path / "absent.yaml"
    )
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert changed is True
    # The prelude's text moves to agent.system_prompt, which was unset.
    assert data["agent"] == {"max_turns": 60, "system_prompt": "old"}
    assert data["model"] == {"default": "m"}


@pytest.mark.parametrize(
    ("current", "reset"),
    [("Concise", True), (" hal0 brain ", True), ("pirate", False), ("", False)],
)
def test_merge_retires_a_personality_hal0_wrote(tmp_path: Path, current: str, reset: bool) -> None:
    """#2839 follow-up: a display.personality equal to a hal0 persona display
    name (what hal0 wrote before the fix) is reset, so a persona named like a
    built-in personality cannot override agent.system_prompt. An operator's
    own pick is left alone."""
    yaml = pytest.importorskip("yaml")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump({"display": {"personality": current, "streaming": True}}),
        encoding="utf-8",
    )
    hp._merge_config_yaml_layers(
        cfg,
        list_keys={},
        overrides_path=tmp_path / "absent.yaml",
        persona_names=["Concise", "hal0 Brain"],
    )
    display = yaml.safe_load(cfg.read_text(encoding="utf-8"))["display"]
    assert display["personality"] == ("" if reset else current)
    assert display["streaming"] is True


def test_persona_display_names_cover_seeds_and_the_store(tmp_path: Path) -> None:
    from hal0.agents import personas

    state = hp.BootstrapState(venv=str(tmp_path / "v"), hermes_home=str(tmp_path / "h"))
    root = hp._personas_root_for(state)
    seeded = personas._seed_coder("hermes")
    seeded.display_name = "Concise"
    personas.save_persona(seeded, root=root)
    names = hp._hal0_persona_display_names(state)
    assert {"Hermes", "Coder", "hal0 Brain", "Concise"} <= set(names)


def test_release_config_invariants_on_a_pre_2839_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    yaml = pytest.importorskip("yaml")
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "absent.yaml")
    (tmp_path / "config.yaml").write_text(
        "agent:\n  system_prompt_prelude: P\nsessions:\n  retention_days: 30\n"
        "auxiliary:\n  web_extract: {provider: main}\n"
    )
    assert hp.apply_release_config_invariants(tmp_path) is True
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert cfg["sessions"] == {"retention_days": 30, "auto_prune": False}
    # the terminal gate rides along for a marker-less default-off box (#2878)
    assert cfg["agent"]["system_prompt"] == "P"
    assert set(hp.TERMINAL_OFF_TOOLSETS) <= set(cfg["agent"]["disabled_toolsets"])
    assert cfg["auxiliary"] == {}
    # Idempotent.
    assert hp.apply_release_config_invariants(tmp_path) is False


def test_release_config_invariants_keep_a_rendered_system_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    yaml = pytest.importorskip("yaml")
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "absent.yaml")
    (tmp_path / "config.yaml").write_text(
        "agent:\n  system_prompt: NEW\n  system_prompt_prelude: OLD\n"
    )
    hp.apply_release_config_invariants(tmp_path)
    agent = yaml.safe_load((tmp_path / "config.yaml").read_text())["agent"]
    assert agent["system_prompt"] == "NEW"
    assert set(hp.TERMINAL_OFF_TOOLSETS) <= set(agent["disabled_toolsets"])
    assert set(agent) == {"system_prompt", "disabled_toolsets"}


def test_release_config_invariants_render_the_persona_when_both_keys_are_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #2853 install path left some boxes with neither agent.system_prompt
    nor the prelude; the invariants render the active persona then."""
    yaml = pytest.importorskip("yaml")
    from hal0.agents import personas

    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "absent.yaml")
    monkeypatch.setattr(personas, "PERSONAS_ROOT", tmp_path / "no-host-personas")
    personas.seed_default_personas(root=tmp_path / "personas")
    (tmp_path / "config.yaml").write_text("agent:\n  max_turns: 60\n")
    assert hp.apply_release_config_invariants(tmp_path) is True
    prompt = yaml.safe_load((tmp_path / "config.yaml").read_text())["agent"]["system_prompt"]
    assert prompt.startswith("You are Hermes") and "hal0-admin" in prompt


def test_release_config_invariants_without_a_config_do_nothing(tmp_path: Path) -> None:
    assert hp.apply_release_config_invariants(tmp_path) is False
    assert not (tmp_path / "config.yaml").exists()


def test_drop_dotted_key_tolerates_missing_and_non_dict_parents() -> None:
    data = {"agent": "scalar", "auxiliary": {"vision": {}}}
    assert hp._drop_dotted_key(data, "agent.system_prompt_prelude") is False
    assert hp._drop_dotted_key(data, "auxiliary.web_extract") is False
    assert hp._drop_dotted_key(data, "auxiliary.vision") is True
    assert data == {"agent": "scalar", "auxiliary": {}}


def test_overlay_includes_provider_and_identity_keys() -> None:
    keys = _build_overlay_keys(
        mcp_servers=[{"name": "hal0-admin", "url": "http://x/mcp", "type": "http"}],
    )
    assert keys["model.provider"] == "custom"
    assert keys["mcp_servers.hal0-admin.headers.X-hal0-Agent"] == "hermes-agent"
    # memory.graph.* dropped entirely — dead config,
    # configures hal0's own Hindsight engine, not anything hermes reads.
    assert "memory.graph.enabled" not in keys
    # model.context_length is NEVER set — per-model context comes from live
    # /v1/models discovery (see _build_config_overlay).
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
    # "idle" is a dispatchable state (hal0.slots.state.DISPATCHABLE_STATES) —
    # a container that's up with a bound model but past its idle timeout is
    # still safe to route to (#1892). Use "warming" (model still loading,
    # genuinely not yet dispatchable) as the not-ready fixture instead.
    slots = [
        *_ROLE_SLOTS[:1],
        {"name": "agent", "type": "llm", "state": "warming", "model_id": "x"},
    ]
    assert hp._resolve_delegation(slots, hal0_base_url=_HAL0_V1) is None


def test_resolve_delegation_honours_configured_slot() -> None:
    deleg = hp._resolve_delegation(_ROLE_SLOTS, hal0_base_url=_HAL0_V1, slot_name="utility")
    assert deleg is not None
    # Slot-qualified so the /v1 route pins the lane (two slots may share a model id).
    assert deleg["model"] == "hal0/utility"


def test_resolve_delegation_configured_slot_offline_is_none() -> None:
    assert (
        hp._resolve_delegation(_ROLE_SLOTS[:2], hal0_base_url=_HAL0_V1, slot_name="utility") is None
    )


def test_read_delegation_slot(tmp_path) -> None:
    seed = tmp_path / "hermes.toml"
    assert hp.read_delegation_slot(seed) == "agent"  # absent file
    seed.write_text('[hermes]\ndelegation_slot = "utility"\n')
    assert hp.read_delegation_slot(seed) == "utility"
    seed.write_text('[hermes]\ndelegation_slot = "bad slot!"\n')
    assert hp.read_delegation_slot(seed) == "agent"  # invalid -> default
    seed.write_text("not = [toml")
    assert hp.read_delegation_slot(seed) == "agent"  # unreadable -> default
    seed.write_text('[hermes]\ncommand_approvals = "manual"\n')
    assert hp.read_delegation_slot(seed) == "agent"  # key absent


def test_resolve_auxiliary_tasks_routes_utility_group_to_utility_slot() -> None:
    aux = hp._resolve_auxiliary_tasks(_ROLE_SLOTS, hal0_base_url=_HAL0_V1)
    # Utility group → custom provider on the utility slot's model.
    for task in ("compression", "title_generation", "skills_hub", "mcp"):
        assert aux[task] == {
            "provider": "custom",
            "model": "qwen3-zero-coder-v2-0.8b-f16",
            "base_url": _HAL0_V1,
        }
    # vision always stays on the main chat provider.
    assert aux["vision"] == {"provider": "custom", "model": "", "base_url": _HAL0_V1}
    # web_extract/session_search have no aux LLM since Hermes v0.22.
    assert "web_extract" not in aux
    assert "session_search" not in aux


def test_resolve_auxiliary_tasks_never_renders_main_without_utility_slot() -> None:
    aux = hp._resolve_auxiliary_tasks(_ROLE_SLOTS[:1], hal0_base_url=_HAL0_V1)
    for task in ("compression", "title_generation"):
        assert aux[task]["provider"] == "custom"
        assert aux[task]["model"] == ""
        assert aux[task]["base_url"] == _HAL0_V1


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
    for task in ("compression", "title_generation", "skills_hub", "mcp"):
        assert aux[task] == {
            "provider": "custom",
            "model": "hal0/npu",
            "base_url": _HAL0_V1,
        }
    assert aux["vision"] == {"provider": "custom", "model": "", "base_url": _HAL0_V1}


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
    assert keys["auxiliary.title_generation.model"] == "qwen3-zero-coder-v2-0.8b-f16"
    assert not any(
        k.startswith(("auxiliary.session_search.", "auxiliary.web_extract.")) for k in keys
    )
    # Session pruning is off (ADR-0034 decision 7).
    assert keys["sessions.auto_prune"] is False
    # vision stays on the main chat provider (no base_url key emitted).
    assert keys["auxiliary.vision.provider"] == "custom"
    assert keys["auxiliary.vision.base_url"] == _HAL0_V1


def test_overlay_omits_delegation_when_slot_missing() -> None:
    aux = hp._resolve_auxiliary_tasks(_ROLE_SLOTS[:1], hal0_base_url=_HAL0_V1)
    keys = _build_overlay_keys(delegation=None, auxiliary_tasks=aux)
    assert not any(k.startswith("delegation.") for k in keys)
    # No utility slot → aux compaction group falls back to an empty model (never provider "main", #2882).
    assert keys["auxiliary.compression.provider"] == "custom"
    assert keys["auxiliary.compression.model"] == ""


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
    io = hp.InstallIO(
        fetch_slots=lambda: list(_ROLE_SLOTS),
        fetch_model_contexts=lambda: {},
        run=fake_hermes_run(),
    )
    out = hp._phase_config_write(hp._StepCtx(state=state, io=io))
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


def test_config_write_renders_the_configured_delegation_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[hermes].delegation_slot`` in the seed reaches the rendered config.yaml (#2885)."""
    yaml = pytest.importorskip("yaml")
    seed = tmp_path / "hermes.toml"
    seed.write_text('[hermes]\ndelegation_slot = "utility"\n')
    monkeypatch.setattr(hp, "INSTALL_SEED_PATH", seed)
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    monkeypatch.setattr(
        hp,
        "_resolve_primary_slot",
        lambda **_k: {"model": "m", "base_url": _HAL0_V1, "context_length": 32768},
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no-overrides.yaml")
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.InstallIO(
        fetch_slots=lambda: list(_ROLE_SLOTS),
        fetch_model_contexts=lambda: {},
        run=fake_hermes_run(),
    )
    out = hp._phase_config_write(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.OK
    cfg = yaml.safe_load(Path(out.details["config_path"]).read_text())
    assert cfg["delegation"]["model"] == "hal0/utility"
    assert out.details["delegation_model"] == "hal0/utility"
    assert cfg["delegation"]["base_url"] == _HAL0_V1


def test_delegation_slot_survives_a_repair_seed_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_write_seed_toml --repair`` refreshes [agent]/[mcp] and must keep ``delegation_slot``."""
    import tomllib

    seed = tmp_path / "hermes.toml"
    seed.write_text('[agent]\nname = "hermes"\n\n[hermes]\ndelegation_slot = "utility"\n')
    monkeypatch.setattr(hp, "INSTALL_SEED_PATH", seed)
    written: list[str] = []
    monkeypatch.setattr(hp, "write_seed_toml_body", written.append)
    hp._write_seed_toml(hp.BootstrapState(hermes_home=str(tmp_path / "hh")), repair=True)
    assert tomllib.loads(written[-1])["hermes"]["delegation_slot"] == "utility"


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
    io = hp.InstallIO(
        fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=fake_hermes_run()
    )
    out1 = hp._phase_config_write(hp._StepCtx(state=state, io=io))
    assert out1.status == hp.PhaseStatus.OK
    cfg = Path(out1.details["config_path"])
    assert cfg.exists()
    # #2085 phase-level wiring: the file the PHASE writes must carry the
    # private header as a quoted string for hal0-memory. This is the only
    # guard on the mcp_servers= kwarg into _config_list_keys — without it,
    # dropping that kwarg keeps every unit test green while fresh installs
    # ship no private header at all (memory writes silently land shared).
    assert "X-hal0-Private: '1'" in cfg.read_text(encoding="utf-8")
    first_hash = out1.hash
    # Re-run is idempotent: config set re-writes the same values + the YAML
    # merge is a no-op, so the on-disk file (and its hash) is unchanged.
    out2 = hp._phase_config_write(hp._StepCtx(state=state, io=io))
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
    io = hp.InstallIO(
        fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=fake_hermes_run()
    )
    out = hp._phase_config_write(hp._StepCtx(state=state, io=io))
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
    io = hp.InstallIO(
        fetch_slots=lambda: [], fetch_model_contexts=lambda: {}, run=fake_hermes_run()
    )
    out = hp._phase_config_write(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.OK
    sites = {f["site"] for f in out.details["fallbacks"]}
    assert sites == {"primary_slot", "mcp_servers"}


def test_config_write_records_no_fallbacks_when_inputs_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    # mcp_wire runs before config_write in the linear pipeline; its live probe
    # result is threaded via output_of (formerly a cross-run checkpoint).
    prior = {"mcp_wire": {"rendered_servers": hp._default_mcp_servers()}}
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "no.yaml")
    from hal0.agents import personas as _personas

    monkeypatch.setattr(_personas, "PERSONAS_ROOT", tmp_path / "personas-empty")
    io = hp.InstallIO(
        fetch_slots=lambda: list(_ROLE_SLOTS),
        fetch_model_contexts=lambda: {},
        run=fake_hermes_run(),
    )
    out = hp._phase_config_write(hp._StepCtx(state=state, io=io, _prior=prior))
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
    io = hp.InstallIO(
        probe_mcp_server=lambda url, **_kw: {"ok": True, "tools": ["t1", "t2"], "error": None},
        probe_mcp_client=_client_probe_ok,
    )
    monkeypatch.setattr(hp, "_load_agent_allowlist", lambda *_a, **_kw: None)
    out = hp._phase_mcp_wire(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["servers"]["hal0-admin"]["status"] == "ok"
    assert out.details["servers"]["hal0-admin"]["tool_count"] == 2
    assert out.details["allowlist_present"] is False
    assert out.details["warnings"] == []


def test_mcp_wire_phase_probes_mount_root_not_double_mcp_suffixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: entry["url"] from _default_mcp_servers() is the FULL
    transport URL (".../mcp/admin/mcp" — correct as-is for config.yaml), but
    probe_mcp_server's own contract wants the MOUNT ROOT and appends "/mcp"
    itself. Passing the full URL through unstripped used to double-append
    "/mcp" and 404 every probe unconditionally — this pins the fix so it
    can't silently regress back to the double-suffixed URL."""
    seen_urls: list[str] = []

    def _fake_probe(url: str, **_kw: object) -> dict[str, object]:
        seen_urls.append(url)
        return {"ok": True, "tools": ["t1"], "error": None}

    state = hp.BootstrapState()
    io = hp.InstallIO(probe_mcp_server=_fake_probe, probe_mcp_client=_client_probe_ok)
    monkeypatch.setattr(hp, "_load_agent_allowlist", lambda *_a, **_kw: None)
    hp._phase_mcp_wire(hp._StepCtx(state=state, io=io))

    assert seen_urls == [
        "http://127.0.0.1:8080/mcp/admin",
        "http://127.0.0.1:8080/mcp/memory",
    ]
    assert not any(u.endswith("/mcp/mcp") for u in seen_urls)


def test_mcp_wire_phase_degrades_not_fails_on_unreachable_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = hp.BootstrapState()
    io = hp.InstallIO(
        probe_mcp_server=lambda url, **_kw: {
            "ok": False,
            "tools": [],
            "error": "connection refused",
        },
        probe_mcp_client=_client_probe_ok,
    )
    monkeypatch.setattr(hp, "_load_agent_allowlist", lambda *_a, **_kw: None)
    out = hp._phase_mcp_wire(hp._StepCtx(state=state, io=io))
    # Still OK — degraded is a warning, not a phase-blocker per ADR-0013.
    assert out.status == hp.PhaseStatus.OK
    assert out.details["servers"]["hal0-admin"]["status"] == "degraded"
    assert "connection refused" in out.details["warnings"][0]


# ── #2021 — mcp_wire must verify the hermes CLIENT, not just the server ──────
#
# rc.7 shipped a hermes venv with NO ``mcp`` package at all
# (``hermes-agent[web]`` without the ``mcp`` extra) and mcp_wire still
# reported ``status: ok, mcp_server_count: 2`` — every probe went through the
# bootstrap's own stdlib HTTP client, never through the client hermes actually
# uses. These tests pin the honest contract: a hermes venv whose MCP client
# cannot import or cannot connect to a provably-live server is a hard phase
# FAIL, typed in the details, never a green checkpoint.


def _client_probe_ok(_python: object, url: object, **_kw: object) -> dict[str, object]:
    """A hermes-venv client probe that imports and connects."""
    if url is None:
        return {"ok": True, "stage": "import", "tools": [], "error": None}
    return {"ok": True, "stage": "connected", "tools": ["t1", "t2"], "error": None}


def test_mcp_wire_phase_fails_when_hermes_client_cannot_import_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``mcp`` package in the hermes venv → FAIL, not ok (#2021)."""
    state = hp.BootstrapState()
    io = hp.InstallIO(
        probe_mcp_server=lambda url, **_kw: {"ok": True, "tools": ["t1"], "error": None},
        probe_mcp_client=lambda _python, _url, **_kw: {
            "ok": False,
            "stage": "import",
            "tools": [],
            "error": "ModuleNotFoundError: No module named 'mcp'",
        },
    )
    monkeypatch.setattr(hp, "_load_agent_allowlist", lambda *_a, **_kw: None)
    out = hp._phase_mcp_wire(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.FAIL
    client = out.details["mcp_client"]
    assert client["ok"] is False
    assert client["stage"] == "import"
    assert "No module named 'mcp'" in (out.reason or "")


def test_mcp_wire_phase_fails_when_client_cannot_connect_to_live_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server answers the raw HTTP probe but the hermes client cannot connect
    → the client is the defect and the phase must FAIL, not warn (#2021)."""

    def _client_probe(_python: object, url: object, **_kw: object) -> dict[str, object]:
        if url is None:
            return {"ok": True, "stage": "import", "tools": [], "error": None}
        return {
            "ok": False,
            "stage": "connect",
            "tools": [],
            "error": "mcp.client.streamable_http is not available",
        }

    state = hp.BootstrapState()
    io = hp.InstallIO(
        probe_mcp_server=lambda url, **_kw: {"ok": True, "tools": ["t1"], "error": None},
        probe_mcp_client=_client_probe,
    )
    monkeypatch.setattr(hp, "_load_agent_allowlist", lambda *_a, **_kw: None)
    out = hp._phase_mcp_wire(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.FAIL
    assert out.details["servers"]["hal0-admin"]["client"]["status"] == "failed"
    assert "streamable_http" in (out.reason or "")


def test_mcp_wire_phase_probes_client_with_venv_python_and_transport_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client probe runs the HERMES VENV interpreter against the FULL
    transport URL (the exact url config.yaml hands hermes) — probing any
    other interpreter or endpoint re-opens the #2021 blind spot."""
    seen: list[tuple[str, object]] = []

    def _client_probe(python: object, url: object, **_kw: object) -> dict[str, object]:
        seen.append((str(python), url))
        return _client_probe_ok(python, url)

    state = hp.BootstrapState()
    io = hp.InstallIO(
        probe_mcp_server=lambda url, **_kw: {"ok": True, "tools": ["t1"], "error": None},
        probe_mcp_client=_client_probe,
    )
    monkeypatch.setattr(hp, "_load_agent_allowlist", lambda *_a, **_kw: None)
    out = hp._phase_mcp_wire(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.OK
    venv_python = str(hp._venv_python(Path(state.venv)))
    assert (venv_python, None) in seen  # the standalone import assertion
    assert (venv_python, "http://127.0.0.1:8080/mcp/admin/mcp") in seen
    assert (venv_python, "http://127.0.0.1:8080/mcp/memory/mcp") in seen
    assert out.details["mcp_client"]["ok"] is True
    assert out.details["servers"]["hal0-admin"]["client"]["status"] == "ok"


def test_mcp_wire_phase_degraded_server_stays_warning_when_client_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that fails BOTH probes is still the ADR-0013 warm-up warning —
    the client cannot be blamed for a server that is provably down."""
    state = hp.BootstrapState()
    io = hp.InstallIO(
        probe_mcp_server=lambda url, **_kw: {
            "ok": False,
            "tools": [],
            "error": "connection refused",
        },
        probe_mcp_client=lambda _python, url, **_kw: (
            {"ok": True, "stage": "import", "tools": [], "error": None}
            if url is None
            else {"ok": False, "stage": "connect", "tools": [], "error": "connection refused"}
        ),
    )
    monkeypatch.setattr(hp, "_load_agent_allowlist", lambda *_a, **_kw: None)
    out = hp._phase_mcp_wire(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["servers"]["hal0-admin"]["status"] == "degraded"


def test_smoke_admin_tools_list_probes_through_hermes_client() -> None:
    """The admin_tools_list smoke goes THROUGH the hermes venv's own MCP
    client — probing the server with the bootstrap's stdlib HTTP client is
    exactly the blind spot that let #2021 read green over a dead client."""
    seen: list[tuple[str, object]] = []

    def _client_probe(python: object, url: object, **_kw: object) -> dict[str, object]:
        seen.append((str(python), url))
        return {
            "ok": True,
            "stage": "connected",
            "tools": ["a", "b", "c", "d", "e", "f"],
            "error": None,
        }

    state = hp.BootstrapState()
    io = hp.InstallIO(probe_mcp_client=_client_probe)
    passed, detail = hp._smoke_admin_tools_list(state, io)
    assert passed is True
    assert seen == [(str(hp._venv_python(Path(state.venv))), "http://127.0.0.1:8080/mcp/admin/mcp")]
    assert "6 tools" in detail


def test_smoke_admin_tools_list_fails_when_hermes_client_cannot_import() -> None:
    state = hp.BootstrapState()
    io = hp.InstallIO(
        probe_mcp_client=lambda _python, _url, **_kw: {
            "ok": False,
            "stage": "import",
            "tools": [],
            "error": "ModuleNotFoundError: No module named 'mcp'",
        }
    )
    passed, detail = hp._smoke_admin_tools_list(state, io)
    assert passed is False
    assert "import" in detail
    assert "No module named 'mcp'" in detail


def test_mcp_wire_phase_threads_entry_timeout_to_client_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each server entry carries its own RUNTIME timeout (hal0-memory: 900s
    for agentic memory_reflect; hal0-admin: slot-lifecycle-sized). A probe
    capped at a hardcoded 40s false-FAILs a slow-but-legitimate server —
    but provisioning must stay bounded too, so the probe gets
    ``min(entry timeout, _MCP_CLIENT_PROBE_CEILING_S)``."""
    seen: dict[str, object] = {}

    def _client_probe(_python: object, url: object, **kw: object) -> dict[str, object]:
        if url is not None:
            seen[str(url)] = kw.get("timeout")
        return _client_probe_ok(_python, url)

    state = hp.BootstrapState()
    io = hp.InstallIO(
        probe_mcp_server=lambda url, **_kw: {"ok": True, "tools": ["t1"], "error": None},
        probe_mcp_client=_client_probe,
    )
    monkeypatch.setattr(hp, "_load_agent_allowlist", lambda *_a, **_kw: None)
    out = hp._phase_mcp_wire(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.OK
    for entry in hp._default_mcp_servers():
        expected = min(float(entry["timeout"]), hp._MCP_CLIENT_PROBE_CEILING_S)
        assert seen[entry["url"]] == expected, entry["name"]
    # hal0-memory's 900s runtime budget MUST be capped for provisioning.
    assert seen["http://127.0.0.1:8080/mcp/memory/mcp"] == hp._MCP_CLIENT_PROBE_CEILING_S


def test_probe_mcp_client_runs_interpreter_with_probe_script_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real ``_probe_mcp_client`` path, against a fake interpreter that
    dumps its argv + env: pins the subprocess argv shape (``-c`` + the
    embedded probe script) and the env plumbing (URL + headers travel via
    env, never argv — the bearer must not show up in the process list)."""
    fake = tmp_path / "fake-python"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "print(json.dumps({\n"
        "    'ok': True, 'stage': 'connected', 'tools': [], 'error': None,\n"
        "    'argv': sys.argv[1:],\n"
        "    'url_env': os.environ.get('HAL0_MCP_PROBE_URL'),\n"
        "    'headers_env': os.environ.get('HAL0_MCP_PROBE_HEADERS'),\n"
        "}))\n"
    )
    fake.chmod(0o755)
    import hal0.service_identity as si

    monkeypatch.setattr(si, "service_key", lambda **_kw: "tok-123")
    res = hp._probe_mcp_client(
        fake, "http://127.0.0.1:8080/mcp/admin/mcp", agent_id="hermes-agent", private=True
    )
    assert res["ok"] is True
    assert res["argv"] == ["-c", hp._MCP_CLIENT_PROBE_SCRIPT]
    assert res["url_env"] == "http://127.0.0.1:8080/mcp/admin/mcp"
    headers = json.loads(res["headers_env"])
    assert headers["X-hal0-Agent"] == "hermes-agent"
    assert headers["Authorization"] == "Bearer tok-123"
    assert headers["X-hal0-Private"] == "1"


def test_probe_mcp_client_script_emits_verdict_on_real_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The embedded probe script must be valid Python that always prints a
    typed JSON verdict — the ``exec`` no-verdict fallback firing on a healthy
    interpreter would be a probe bug misreported as a client defect."""
    import hal0.service_identity as si

    monkeypatch.setattr(si, "service_key", lambda **_kw: None)
    res = hp._probe_mcp_client(Path(sys.executable), None, agent_id="hermes-agent")
    # url=None is the import-only assertion; whichever way ``import mcp``
    # resolves on THIS interpreter, the verdict must come from the script.
    assert res["stage"] == "import"
    assert set(res) >= {"ok", "stage", "tools", "error"}


def _run_probe_script_against_fake_mcp(tmp_path: Path, *, new: bool, legacy: bool) -> dict:
    """Run the embedded probe script import-only against a stub ``mcp`` package."""
    import json as _json
    import subprocess as _sp

    pkg = tmp_path / "mcp"
    (pkg / "client").mkdir(parents=True)
    (pkg / "shared").mkdir()
    (pkg / "__init__.py").write_text("class ClientSession:\n    pass\n")
    (pkg / "client" / "__init__.py").write_text("")
    (pkg / "shared" / "__init__.py").write_text("")
    (pkg / "shared" / "_httpx_utils.py").write_text("def create_mcp_http_client(**kw):\n    pass\n")
    body = ""
    if new:
        body += "def streamable_http_client(url, *, http_client=None):\n    pass\n"
    if legacy:
        body += "def streamablehttp_client(url, headers=None):\n    pass\n"
    (pkg / "client" / "streamable_http.py").write_text(body)
    out = _sp.run(
        [sys.executable, "-c", hp._MCP_CLIENT_PROBE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={"PYTHONPATH": str(tmp_path), "HAL0_MCP_PROBE_URL": ""},
    )
    return _json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    ("new", "legacy", "ok"),
    [
        (True, False, True),  # mcp 2.0 (Hermes v0.22.2 lock): legacy alias removed
        (True, True, True),  # mcp 1.24-1.x: both present
        (False, True, True),  # mcp < 1.24: legacy only
        (False, False, False),  # neither: a broken client stack
    ],
)
def test_probe_script_imports_whichever_streamable_http_client_the_sdk_has(
    tmp_path: Path, new: bool, legacy: bool, ok: bool
) -> None:
    """mcp 2.0 dropped ``streamablehttp_client``; the probe must not fail the
    ``mcp_wire`` phase on a venv that only has ``streamable_http_client``."""
    res = _run_probe_script_against_fake_mcp(tmp_path, new=new, legacy=legacy)
    assert res["stage"] == "import"
    assert res["ok"] is ok, res


def test_smoke_admin_tools_list_url_follows_server_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The smoke reads the hal0-admin URL from ``_default_mcp_servers()`` —
    the same inventory config_write renders — so it cannot drift from what
    config.yaml actually wires."""
    seen: list[object] = []

    def _client_probe(_python: object, url: object, **_kw: object) -> dict[str, object]:
        seen.append(url)
        return {"ok": True, "stage": "connected", "tools": ["a", "b", "c", "d", "e"], "error": None}

    monkeypatch.setattr(
        hp,
        "_default_mcp_servers",
        lambda: [
            {
                "name": "hal0-admin",
                "url": "http://10.9.8.7:1234/mcp/admin/mcp",
                "private": False,
                "timeout": 7,
            }
        ],
    )
    state = hp.BootstrapState()
    io = hp.InstallIO(probe_mcp_client=_client_probe)
    passed, _detail = hp._smoke_admin_tools_list(state, io)
    assert passed is True
    assert seen == ["http://10.9.8.7:1234/mcp/admin/mcp"]


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

    io = hp.InstallIO(mcp_memory_call=_fake_mcp)
    out = hp._phase_namespace_register(hp._StepCtx(state=state, io=io))
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

    io = hp.InstallIO(mcp_memory_call=_fake_mcp)
    out = hp._phase_namespace_register(hp._StepCtx(state=state, io=io))
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

    io = hp.InstallIO(mcp_memory_call=_fake_mcp)
    out = hp._phase_brain_profile_seed(hp._StepCtx(state=state, io=io))
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

    io = hp.InstallIO(mcp_memory_call=_fake_mcp)
    out = hp._phase_brain_profile_seed(hp._StepCtx(state=state, io=io))
    # Warn-as-OK: the phase never blocks bootstrap on the memory layer.
    assert out.status == hp.PhaseStatus.OK
    assert out.details["registered"] is False


# ── brain_profile_mcp_wire — reproducible profile MCP wiring ────────────────


def _brain_profile_state(tmp_path: Path) -> Any:
    return hp.BootstrapState(hermes_home=str(tmp_path / ".hermes"))


def test_brain_profile_mcp_wire_skips_when_profile_absent(tmp_path: Path) -> None:
    state = _brain_profile_state(tmp_path)
    out = hp._phase_brain_profile_mcp_wire(hp._StepCtx(state=state))
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
    out = hp._phase_brain_profile_mcp_wire(hp._StepCtx(state=state))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["wired"] is True and out.details["changed"] is True

    merged = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    servers = merged["mcp_servers"]
    # hal0-owned servers wired under the brain profile identity…
    assert servers["hal0-admin"]["headers"]["X-hal0-Agent"] == "hermes__hal0-brain"
    assert servers["hal0-memory"]["headers"]["X-hal0-Private"] == "1"  # str, not int (#2085)
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
    first = hp._phase_brain_profile_mcp_wire(hp._StepCtx(state=state))
    assert first.details["changed"] is True
    second = hp._phase_brain_profile_mcp_wire(hp._StepCtx(state=state))
    # Already-correct box: no rewrite, so its comments/formatting survive.
    assert second.details["wired"] is True and second.details["changed"] is False


def test_namespace_register_continues_on_mcp_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0013: registry failure logs + continues; bootstrap doesn't block."""
    state = hp.BootstrapState()
    io = hp.InstallIO(mcp_memory_call=lambda *a, **kw: {"ok": False, "error": "connection refused"})
    out = hp._phase_namespace_register(hp._StepCtx(state=state, io=io))
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
    io = hp.InstallIO(
        probe_mcp_server=lambda url, **_kw: {"ok": True, "tools": ["t1"], "error": None},
        probe_mcp_client=_client_probe_ok,
    )
    out = hp._phase_mcp_wire(hp._StepCtx(state=state, io=io))
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
    io = hp.InstallIO(
        fetch_slots=lambda: [],
        fetch_model_contexts=lambda: {},
        http_get=lambda *_a, **_kw: 0,
    )

    out = hp._phase_context_link(hp._StepCtx(state=state, io=io))
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

    # MCP-CLIENTS.md carries the real auth contract, not a stale "no auth"
    # claim — the accuracy fix this template exists to guard.
    assert (etc / "MCP-CLIENTS.md").exists()
    mcp_clients = (etc / "MCP-CLIENTS.md").read_text()
    assert "Authorization: Bearer" in mcp_clients
    assert "HAL0_ADMIN_KEY" in mcp_clients
    assert "no built-in" not in mcp_clients.lower()
    assert "hal0 has no auth" not in mcp_clients.lower()

    # AGENTS.md describes the full admin/memory tool surface generically
    # rather than the old five-tool memory / status-only admin summary.
    agents_md = (etc / "AGENTS.md").read_text()
    assert "profile_generate" in agents_md
    assert "reflection" in agents_md


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
    mirror = hp._mirror_bundled_skills(tmp_path / "no-src", tmp_path / "dst")
    assert mirror.linked == []
    assert any("not present" in w for w in mirror.warnings)
    # A dev install ships no bundled skills at all — "nothing to mirror" is not
    # a partial failure, so it must not colour the phase status (#1828).
    assert mirror.failed == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory write permissions")
def test_context_link_warns_when_bundled_skill_symlinks_are_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1828: a phase that produced five EACCES symlink errors must not say ``ok``.

    Reproduces the fresh-install shape exactly: the bundled-skill SOURCE exists
    (``/usr/share/hal0/skills`` with real skill dirs) but the MIRROR dir is not
    writable by the (privilege-dropped) user running bootstrap, so every
    ``os.symlink`` raises EACCES. The two pre-existing context_link tests point
    ``HAL0_BUNDLED_SKILLS`` at a nonexistent dir, so neither can ever exercise a
    real symlink denial — which is why this shipped.
    """
    hermes_home = tmp_path / "hh"
    hermes_home.mkdir()
    state = hp.BootstrapState(hermes_home=str(hermes_home))
    etc = tmp_path / "etc" / "hal0"
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", etc)
    monkeypatch.setattr(hp, "RUNTIME_SNAPSHOT_DIR", tmp_path)

    # A REAL bundled-skill source, as install.sh ships it.
    src = tmp_path / "usr" / "share" / "hal0" / "skills"
    for name in ("hal0-bench", "hal0-tune"):
        (src / name).mkdir(parents=True)
        (src / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    monkeypatch.setattr(hp, "HAL0_BUNDLED_SKILLS", src)

    # …and a mirror dir the running user cannot write (the on-box shape is
    # root:root 0755 with bootstrap re-exec'd as hal0; 0o555 is the same denial
    # without needing a second uid).
    mirror = etc / "agent-skills"
    mirror.mkdir(parents=True)
    os.chmod(mirror, 0o555)

    io = hp.InstallIO(
        fetch_slots=lambda: [],
        fetch_model_contexts=lambda: {},
        http_get=lambda *_a, **_kw: 0,
    )
    try:
        monkeypatch.setattr(hp, "ETC_HAL0_AGENT_SKILLS", mirror)
        out = hp._phase_context_link(hp._StepCtx(state=state, io=io))
    finally:
        os.chmod(mirror, 0o755)  # let tmp_path teardown clean up

    # The mirror really did fail — no links, one warning per skill.
    assert out.details["bundled_skills_linked"] == []
    assert not list(mirror.iterdir())
    denied = [w for w in out.details["warnings"] if w.startswith("symlink ")]
    assert len(denied) == 2, out.details["warnings"]
    assert all("Permission denied" in w for w in denied), denied
    # …and the phase says so instead of reporting a clean install.
    assert out.status == hp.PhaseStatus.WARN


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
    io = hp.InstallIO(
        fetch_slots=lambda: [],
        fetch_model_contexts=lambda: {},
        http_get=lambda *_a, **_kw: 0,
    )

    def _explode(name: str, **_: Any) -> str:
        if name == "SOUL.md.j2":
            raise RuntimeError("template boom")
        return "ok"

    monkeypatch.setattr(hp, "_render_template", _explode)
    out = hp._phase_context_link(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.OK
    soul = (hermes_home / "SOUL.md").read_text()
    assert "hal0 admin agent" in soul
    assert any("SOUL.md render" in w for w in out.details["warnings"])
    # #702: the inline-default fallback is observable, not silent.
    assert any(f["site"] == "soul_md" for f in out.details["fallbacks"])


# ── voice_wire ─────────────────────────────────────────────────────────────


def test_voice_wire_skips_when_no_voice_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    io = hp.InstallIO(fetch_slots=lambda: [])
    out = hp._phase_voice_wire(hp._StepCtx(state=state, io=io))
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
    io = hp.InstallIO(
        fetch_slots=lambda: slots,
        fetch_model_contexts=lambda: {},
    )
    out = hp._phase_voice_wire(hp._StepCtx(state=state, io=io))
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
    io = hp.InstallIO(
        fetch_slots=lambda: slots,
        fetch_model_contexts=lambda: {},
    )
    out = hp._phase_voice_wire(hp._StepCtx(state=state, io=io))
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
    io = hp.InstallIO(
        fetch_slots=lambda: slots,
        fetch_model_contexts=lambda: {},
    )
    out = hp._phase_voice_wire(hp._StepCtx(state=state, io=io))
    # TTS is ready so we get OK (not SKIP), but STT must be absent.
    assert out.status in (hp.PhaseStatus.OK, hp.PhaseStatus.SKIP)
    if secrets_env.exists():
        env_text = secrets_env.read_text()
        assert "STT_OPENAI_BASE_URL=" not in env_text, (
            f"STT URL must not be written when anchor is offline. env:\n{env_text}"
        )


# ── #246 phase impls — smoke_tests + self_report ────────────────────────────


def _write_ready_chat_config(
    hermes_home: Path,
    model_name: str = "m1",
    base_url: str = "http://127.0.0.1:8080/v1",
) -> None:
    """Drop a config.yaml + matching gateway model so :func:`hp._chat_model_ready`
    reports ready — the precondition the model-dependent probes need to
    actually run instead of self-skipping (#1793). ``base_url`` is written
    because the preflight resolves the anchor against THAT endpoint, the one
    the chat probe dispatches to (#1831)."""
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text(
        f"model:\n  default: {model_name}\n  base_url: {base_url}\n"
    )


@pytest.fixture(autouse=True)
def _memory_engine_llm_off_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The memory gate's default seam reads hindsight-api's live environment
    (``systemctl show`` + ``/proc``). Keep every test in this module off the
    host's engine: report the engine LLM as unknown unless a test injects one."""
    monkeypatch.setattr("hal0.memory.extraction_env.effective_engine_llm", lambda **_kw: {})


#: A slot's own llama-server (``HAL0_HERMES_LIVE_RESOLVE=0``), not hal0's gateway.
_DIRECT_BACKEND = "http://127.0.0.1:8081/v1"

#: hal0-api's slot list, as the smoke guard reads it.
_SLOTS_URL = "http://127.0.0.1:8080/api/slots"


def _slot_detail_body(name: str, *, drifted: bool = False, **drift: Any) -> dict[str, Any]:
    """A ``GET /api/slots/{name}`` body carrying the slot's ``config_drift``."""
    return {"name": name, "config_drift": {"drifted": drifted, "diffs": [], **drift}}


def _detail_routes(*names: str) -> dict[str, Any]:
    """Fake-network routes for each slot's detail, none with a pending change."""
    return {f"{_SLOTS_URL}/{name}": _slot_detail_body(name) for name in names}


def _slot_row(name: str, state: str, model: str = "", **extra: Any) -> dict[str, Any]:
    """One ``GET /api/slots`` row in the shape the route serves (``slot_view``):
    the FSM ``state``, plus the config-lifted ``type`` and ``model_default``."""
    loaded = state in ("ready", "serving", "idle")
    row: dict[str, Any] = {
        "name": name,
        "kind": "local",
        "type": "llm",
        "state": state,
        "status": state,
        "model_id": model if (model and loaded) else None,
    }
    if model:
        row["model_default"] = model
    row.update(extra)
    return row


#: A box whose agent (``m1``) and utility (``u1``) slots are both loaded, so
#: every gated probe may run.
_WARM_SLOTS = [_slot_row("agent", "ready", "m1"), _slot_row("utility", "ready", "u1")]


def _memory_cfg(**graph: Any) -> Any:
    """A ``load_config()`` stand-in carrying just the ``[memory]`` table."""
    from types import SimpleNamespace

    from hal0.config.schema import MemoryGraphConfig

    engine = graph.pop("engine", "hindsight")
    enabled = graph.pop("enabled", True)
    return SimpleNamespace(
        memory=SimpleNamespace(enabled=enabled, engine=engine, graph=MemoryGraphConfig(**graph))
    )


def _box_io(
    rows: list[dict[str, Any]] | None = _WARM_SLOTS,
    *,
    status: int | None = 200,
    engine_llm: dict[str, Any] | None = None,
    memory: Any = None,
    calls: list[str] | None = None,
    drifted: tuple[str, ...] = (),
    detail: Any = None,
    **kw: Any,
) -> hp.InstallIO:
    """``InstallIO`` for a box whose hal0-api answers its slot list with
    ``(status, rows)``, whose memory engine calls ``hal0/memory`` on the
    gateway (unless ``engine_llm`` says otherwise), and whose ``[memory]`` is
    the schema default (unless ``memory``). Each slot's detail reports no
    pending config change unless it is named in ``drifted``; ``detail``
    replaces the detail answer outright. ``calls`` records each slot-list read
    (``"slots"``) and detail read (``"detail:<name>"``)."""

    def _fetch() -> hp.SlotListAnswer:
        if calls is not None:
            calls.append("slots")
        return (status, rows)

    def _fetch_detail(name: str) -> hp.SlotDetailAnswer:
        if calls is not None:
            calls.append(f"detail:{name}")
        if detail is not None:
            return detail
        return (200, _slot_detail_body(name, drifted=name in drifted))

    seams: dict[str, Any] = {
        "fetch_slot_list": _fetch,
        "fetch_slot_detail": _fetch_detail,
        "read_memory_engine_llm": lambda: dict(engine_llm or {}),
        "load_config": lambda: memory if memory is not None else _memory_cfg(),
        # The anchor probe's route preflight: routable unless a test says not,
        # so it never reaches a real gateway.
        "fetch_model_route_ready": lambda _model_id, _base_url="": True,
    }
    return hp.InstallIO(**{**seams, **kw})


def test_smoke_tests_phase_runs_each_probe_collecting_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hh = tmp_path / "hh"
    _write_ready_chat_config(hh)
    state = hp.BootstrapState(hermes_home=str(hh))
    io = _box_io()
    # All six probes return (True, "...") so we exercise the rollup
    # without depending on a real Hermes binary or HTTP listener.
    monkeypatch.setattr(hp, "_smoke_wrapper_ready", lambda s, io: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_hermes_doctor", lambda s, io: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_chat_completions", lambda s, io: (True, "ready"))
    monkeypatch.setattr(hp, "_smoke_memory_roundtrip", lambda s, io: (True, "1 item"))
    monkeypatch.setattr(hp, "_smoke_admin_tools_list", lambda s, io: (True, "8 tools"))
    monkeypatch.setattr(hp, "_smoke_hermes_md_contains_primary", lambda s, io: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_anchor_context_window", lambda s, io: (True, "65,536 ≥ floor"))
    out = hp._phase_smoke_tests(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["failures"] == []
    assert out.details["skipped"] == []
    assert set(out.details["results"].keys()) == {
        "wrapper_ready",
        "hermes_doctor",
        "anchor_context_window",
        "chat_completions",
        "memory_roundtrip",
        "admin_tools_list",
        "hermes_md_contains_primary",
    }


def test_smoke_tests_phase_records_failures_as_warn_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuinely failing probe flips the phase to ``warn`` (#1793) — visible,
    but still non-gating: the overall install must not fail over a smoke miss."""
    hh = tmp_path / "hh"
    _write_ready_chat_config(hh)
    state = hp.BootstrapState(hermes_home=str(hh))
    io = _box_io()
    monkeypatch.setattr(hp, "_smoke_wrapper_ready", lambda s, io: (False, "wrapper missing"))
    monkeypatch.setattr(hp, "_smoke_hermes_doctor", lambda s, io: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_chat_completions", lambda s, io: (False, "503"))
    monkeypatch.setattr(hp, "_smoke_memory_roundtrip", lambda s, io: (True, "1 item"))
    monkeypatch.setattr(hp, "_smoke_admin_tools_list", lambda s, io: (True, "8 tools"))
    monkeypatch.setattr(hp, "_smoke_hermes_md_contains_primary", lambda s, io: (True, "ok"))
    out = hp._phase_smoke_tests(hp._StepCtx(state=state, io=io))
    assert out.status == hp.PhaseStatus.WARN  # visible, but not a blocker
    assert len(out.details["failures"]) == 2
    assert any("wrapper_ready" in f for f in out.details["failures"])
    assert any("chat_completions" in f for f in out.details["failures"])


def test_smoke_tests_phase_skips_model_dependent_probes_without_chat_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No routable chat model at smoke time (e.g. install-time run, before any
    model has ever been loaded) → chat_completions reports ``skipped``, not a
    timeout recorded as an opaque failure (#1793). memory_roundtrip has no
    chat dependency and must still run (#1831)."""
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))  # no config.yaml at all
    calls: list[str] = []

    def _boom(_s: hp.BootstrapState, _io: hp.InstallIO) -> tuple[bool, str]:
        calls.append("called")
        return (False, "should not have run")

    monkeypatch.setattr(hp, "_smoke_wrapper_ready", lambda s, io: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_hermes_doctor", lambda s, io: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_chat_completions", _boom)
    monkeypatch.setattr(hp, "_smoke_memory_roundtrip", lambda s, io: (True, "1 item"))
    monkeypatch.setattr(hp, "_smoke_admin_tools_list", lambda s, io: (True, "8 tools"))
    monkeypatch.setattr(hp, "_smoke_hermes_md_contains_primary", lambda s, io: (True, "ok"))
    # The memory engine's model (utility, in _WARM_SLOTS) is loaded, so its gate
    # is open and the only question is the chat one.
    out = hp._phase_smoke_tests(hp._StepCtx(state=state, io=_box_io()))
    assert calls == []  # the chat-gated probe never ran
    assert out.status == hp.PhaseStatus.OK  # skips aren't failures
    assert out.details["failures"] == []
    # Both model-dependent probes skip: chat_completions and #1867's
    # anchor_context_window, which has no advertised window to measure either.
    assert {s.split(":")[0] for s in out.details["skipped"]} == {
        "chat_completions",
        "anchor_context_window",
    }
    assert out.details["results"]["chat_completions"]["skipped"] is True
    assert "chat model not loaded" in out.details["results"]["chat_completions"]["detail"]
    assert out.details["results"]["memory_roundtrip"] == {"passed": True, "detail": "1 item"}


class TestChatModelReady:
    """Direct coverage of the :func:`hp._chat_model_ready` preflight (#1793)."""

    def test_missing_config_not_ready(self, tmp_path: Path) -> None:
        state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
        ready, reason = hp._chat_model_ready(state, hp.InstallIO())
        assert ready is False
        assert "config.yaml missing" in reason

    def test_unset_model_default_not_ready(self, tmp_path: Path) -> None:
        hh = tmp_path / "hh"
        hh.mkdir()
        (hh / "config.yaml").write_text("{}\n")
        state = hp.BootstrapState(hermes_home=str(hh))
        ready, reason = hp._chat_model_ready(state, hp.InstallIO())
        assert ready is False
        assert "model.default unset" in reason

    # The route-probe rules below are the DIRECT-backend path (a slot's own
    # llama-server never cold-loads on a request); hal0's gateway is gated by
    # its slot list instead — see TestSmokeColdLoadGuard.

    def test_model_with_no_route_not_ready(self, tmp_path: Path) -> None:
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "m1", base_url=_DIRECT_BACKEND)
        state = hp.BootstrapState(hermes_home=str(hh))
        io = hp.InstallIO(fetch_model_route_ready=lambda model_id, base_url="": False)
        ready, reason = hp._chat_model_ready(state, io)
        assert ready is False
        assert f"has no route at {_DIRECT_BACKEND}" in reason

    def test_routable_model_ready(self, tmp_path: Path) -> None:
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "m1", base_url=_DIRECT_BACKEND)
        state = hp.BootstrapState(hermes_home=str(hh))
        io = hp.InstallIO(fetch_model_route_ready=lambda model_id, base_url="": model_id == "m1")
        ready, reason = hp._chat_model_ready(state, io)
        assert ready is True
        assert reason == "ready"

    def test_preflight_asks_about_the_configured_base_url(self, tmp_path: Path) -> None:
        """The endpoint under test is ``model.base_url`` — whatever the chat
        probe will POST to — not a hardcoded gateway (#1831)."""
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "raw-model-id", base_url="http://127.0.0.1:8081/v1")
        state = hp.BootstrapState(hermes_home=str(hh))
        asked: list[tuple[str, str]] = []

        def _record(model_id: str, base_url: str = "") -> bool:
            asked.append((model_id, base_url))
            return True

        hp._chat_model_ready(state, hp.InstallIO(fetch_model_route_ready=_record))
        assert asked == [("raw-model-id", "http://127.0.0.1:8081/v1")]

    def test_inconclusive_route_runs_the_probe(self, tmp_path: Path) -> None:
        """``None`` means the endpoint settled nothing — run the probe. A skip
        is a claim and must be earned (#1831)."""
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "m1", base_url=_DIRECT_BACKEND)
        state = hp.BootstrapState(hermes_home=str(hh))
        io = hp.InstallIO(fetch_model_route_ready=lambda model_id, base_url="": None)
        ready, reason = hp._chat_model_ready(state, io)
        assert ready is True
        assert "unverifiable" in reason

    def test_probe_exception_does_not_skip(self, tmp_path: Path) -> None:
        """A broken preflight must not silently suppress the probe it gates."""
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "m1", base_url=_DIRECT_BACKEND)
        state = hp.BootstrapState(hermes_home=str(hh))

        def _boom(_model_id: str, _base_url: str = "") -> bool:
            raise RuntimeError("gateway unreachable")

        io = hp.InstallIO(fetch_model_route_ready=_boom)
        ready, reason = hp._chat_model_ready(state, io)
        assert ready is True
        assert "route probe errored" in reason

    def test_shipped_virtual_default_ready_when_its_slot_is_loaded(self, tmp_path: Path) -> None:
        """The shipped ``model.default: hal0/agent`` on the gateway is ready
        when the agent slot is loaded — read off hal0-api's slot list, never
        off ``GET /v1/models`` (whose list never advertises the canonical
        virtuals, #1153) and never off the by-id route, which answers 200 for
        a COLD slot too and so cannot tell a warm anchor from a cold load."""
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "hal0/agent")
        state = hp.BootstrapState(hermes_home=str(hh))

        def fetch_model_route_ready(_model_id: str, _base_url: str = "") -> bool:
            raise AssertionError("the gateway path must not use the route probe")

        io = _box_io(fetch_model_route_ready=fetch_model_route_ready)
        ready, reason = hp._chat_model_ready(state, io)
        assert ready is True
        assert reason == "ready (slot agent is ready)"


# ── #1831 follow-up: the preflight must interrogate the endpoint the chat
# probe actually dispatches against, and must never skip on a guess ─────────


class _FakeHTTPResponse:
    """Minimal stand-in for what ``urlopen`` yields (context manager + read)."""

    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _fake_http(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    seen: list[str] | None = None,
    *,
    unreachable: set[str] | None = None,
) -> None:
    """Patch the real network boundary (``urllib.request.urlopen``).

    Patching HTTP rather than the ``InstallIO`` seam is deliberate: these
    tests must exercise the PRODUCTION wiring end to end, so a regression
    shows up as wrong behaviour instead of a signature mismatch on a fake.
    Any URL not in ``routes`` 404s; anything in ``unreachable`` raises a
    connection error.
    """
    import urllib.request
    from urllib.error import HTTPError, URLError

    def _urlopen(req: Any, timeout: float | None = None) -> _FakeHTTPResponse:
        url = getattr(req, "full_url", None) or str(req)
        if seen is not None:
            seen.append(url)
        for prefix in unreachable or set():
            if url.startswith(prefix):
                raise URLError("connection refused")
        if url in routes:
            return _FakeHTTPResponse(200, routes[url])
        raise HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr("hal0.service_identity.open_url", _urlopen)


def _stub_non_chat_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hp, "_smoke_wrapper_ready", lambda s, io: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_hermes_doctor", lambda s, io: (True, "ok"))
    monkeypatch.setattr(hp, "_smoke_memory_roundtrip", lambda s, io: (True, "1 item"))
    monkeypatch.setattr(hp, "_smoke_admin_tools_list", lambda s, io: (True, "8 tools"))
    monkeypatch.setattr(hp, "_smoke_hermes_md_contains_primary", lambda s, io: (True, "ok"))


def test_pinned_backend_config_does_not_skip_chat_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``HAL0_HERMES_LIVE_RESOLVE=0`` pins ``model.base_url`` at the chat
    slot's own llama-server and ``model.default`` at that slot's RAW physical
    model id (``_config_pairs``). The gateway's catalog suppresses exactly
    that raw id in favour of the slot's alias row (``hal0_chat_slot_model_ids``)
    and its by-id route only resolves ``hal0/*`` virtuals, so asking the
    GATEWAY about the id 404s even though the route is perfectly live —
    the probe is skipped forever with the false reason "not loaded on the
    gateway" (#1831). The preflight must interrogate the endpoint the chat
    probe itself POSTs to (``model.base_url``), which does advertise the id.

    Production wiring only: no ``InstallIO`` fake, just a fake network.
    """
    hh = tmp_path / "hh"
    hh.mkdir()
    (hh / "config.yaml").write_text(
        "model:\n  default: qwen3-30b-a3b-instruct\n  base_url: http://127.0.0.1:8081/v1\n"
    )
    seen: list[str] = []
    _fake_http(
        monkeypatch,
        {
            # The pinned llama-server advertises its own loaded model...
            "http://127.0.0.1:8081/v1/models": {"data": [{"id": "qwen3-30b-a3b-instruct"}]},
            # ...while the gateway catalog folds it into the alias row, so
            # neither the list nor the by-id route mentions it.
            "http://127.0.0.1:8080/v1/models": {"data": [{"id": "brain"}]},
        },
        seen,
    )
    _stub_non_chat_probes(monkeypatch)
    ran: list[str] = []
    monkeypatch.setattr(
        hp, "_smoke_chat_completions", lambda s, io: (ran.append("x"), (True, "ready"))[1]
    )
    state = hp.BootstrapState(hermes_home=str(hh))
    out = hp._phase_smoke_tests(hp._StepCtx(state=state))
    assert ran == ["x"], (
        f"chat_completions was skipped on a live pinned backend: "
        f"{out.details['results']['chat_completions']} (probed: {seen})"
    )
    # Scoped to the chat probe: #1867's anchor_context_window legitimately
    # skips here (this fake catalog advertises no context_length), and a
    # blanket "nothing skipped" would make that skip look like a chat bug.
    assert not any(s.startswith("chat_completions") for s in out.details["skipped"])


def test_unverifiable_route_runs_the_probe_instead_of_skipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 from a backend that simply doesn't implement the by-id route and
    doesn't list the id either is INCONCLUSIVE, not proof of "no model". The
    preflight exists to avoid a doomed 60 s round trip when nothing is
    loaded; when it cannot tell, running the probe (and reporting whatever
    really happens) beats a skip with an invented reason."""
    hh = tmp_path / "hh"
    hh.mkdir()
    (hh / "config.yaml").write_text(
        "model:\n  default: some-physical-id\n  base_url: http://127.0.0.1:8081/v1\n"
    )
    # The backend answers the list route but advertises a different id.
    _fake_http(monkeypatch, {"http://127.0.0.1:8081/v1/models": {"data": [{"id": "other"}]}})
    _stub_non_chat_probes(monkeypatch)
    ran: list[str] = []
    monkeypatch.setattr(
        hp, "_smoke_chat_completions", lambda s, io: (ran.append("x"), (False, "503"))[1]
    )
    state = hp.BootstrapState(hermes_home=str(hh))
    out = hp._phase_smoke_tests(hp._StepCtx(state=state))
    assert ran == ["x"]
    # Scoped to the chat probe: #1867's anchor_context_window legitimately
    # skips here (this fake catalog advertises no context_length), and a
    # blanket "nothing skipped" would make that skip look like a chat bug.
    assert not any(s.startswith("chat_completions") for s in out.details["skipped"])
    assert any("chat_completions" in f for f in out.details["failures"])


def test_virtual_anchor_with_no_live_slot_still_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #1793 install-time skip must survive: an agent slot that is not
    loaded skips the chat probe rather than burning its timeout — and, since
    the gateway would LOAD that slot to answer, rather than cold-loading it."""
    hh = tmp_path / "hh"
    hh.mkdir()
    (hh / "config.yaml").write_text(
        "model:\n  default: hal0/agent\n  base_url: http://127.0.0.1:8080/v1\n"
    )
    _fake_http(
        monkeypatch,
        {
            "http://127.0.0.1:8080/v1/models": {"data": [{"id": "brain"}]},
            _SLOTS_URL: [
                _slot_row("agent", "offline", "big-27b"),
                _slot_row("utility", "ready", "u1"),
            ],
            **_detail_routes("utility"),
        },
    )
    _stub_non_chat_probes(monkeypatch)
    monkeypatch.setattr(
        hp, "_smoke_chat_completions", lambda s, io: pytest.fail("probe must not run")
    )
    state = hp.BootstrapState(hermes_home=str(hh))
    out = hp._phase_smoke_tests(hp._StepCtx(state=state))
    assert out.details["results"]["chat_completions"]["skipped"] is True
    assert "slot agent is offline" in out.details["results"]["chat_completions"]["detail"]
    assert {s.split(":")[0] for s in out.details["skipped"]} == {
        "chat_completions",
        "anchor_context_window",
    }


def test_live_resolve_virtual_anchor_runs_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shipped default: ``hal0/agent`` against the gateway runs the probe
    when hal0-api reports the agent slot loaded, even though the list route
    never advertises the virtual."""
    hh = tmp_path / "hh"
    hh.mkdir()
    (hh / "config.yaml").write_text(
        "model:\n  default: hal0/agent\n  base_url: http://127.0.0.1:8080/v1\n"
    )
    _fake_http(
        monkeypatch,
        {
            "http://127.0.0.1:8080/v1/models/hal0/agent": {"id": "hal0/agent"},
            "http://127.0.0.1:8080/v1/models": {"data": [{"id": "brain"}]},
            _SLOTS_URL: [
                _slot_row("agent", "ready", "big-27b"),
                _slot_row("utility", "ready", "u1"),
            ],
            **_detail_routes("agent", "utility"),
        },
    )
    _stub_non_chat_probes(monkeypatch)
    ran: list[str] = []
    monkeypatch.setattr(
        hp, "_smoke_chat_completions", lambda s, io: (ran.append("x"), (True, "ready"))[1]
    )
    state = hp.BootstrapState(hermes_home=str(hh))
    out = hp._phase_smoke_tests(hp._StepCtx(state=state))
    assert ran == ["x"]
    # Scoped to the chat probe: #1867's anchor_context_window legitimately
    # skips here (this fake catalog advertises no context_length), and a
    # blanket "nothing skipped" would make that skip look like a chat bug.
    assert not any(s.startswith("chat_completions") for s in out.details["skipped"])


# ── a Hermes install or repair never cold-loads a slot to smoke-test it ─────
#
# Through hal0's gateway a request for a cold model loads its slot first
# (``api/routes/v1.py::_ensure_backend_for_model``). A dashboard repair whose
# smoke chat cold-loaded a 27B agent model stalled the host for minutes and the
# agent's systemd watchdog SIGABRTed it. The gated probes now run only when
# hal0-api's slot list shows the serving slot already loaded.

#: Every slot state that is not dispatchable (offline, pulling, starting,
#: warming, unloading, error): a request would have to load the slot.
_COLD_STATES = sorted(s.value for s in SlotState if s not in DISPATCHABLE_STATES)
_LOADED_STATES = sorted(s.value for s in DISPATCHABLE_STATES)

_CHAT_URL = "http://127.0.0.1:8080/v1/chat/completions"
_READY_REPLY = {"choices": [{"message": {"content": "ready"}}]}


def _gateway_chat_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seen: list[str], model: str = "hal0/agent"
) -> hp.BootstrapState:
    """A live-resolve config and a fake network recording every URL. The REAL
    chat probe runs, so any attempt at ``/chat/completions`` shows in ``seen``."""
    hh = tmp_path / "hh"
    _write_ready_chat_config(hh, model)
    _fake_http(monkeypatch, {_CHAT_URL: _READY_REPLY}, seen)
    _stub_non_chat_probes(monkeypatch)
    monkeypatch.setattr(hp, "_smoke_anchor_context_window", lambda s, io: (True, "window ok"))
    return hp.BootstrapState(hermes_home=str(hh))


def _chat_attempted(seen: list[str]) -> bool:
    return any("chat/completions" in url for url in seen)


class TestSmokeColdLoadGuard:
    """The chat probe's gate on hal0's gateway: loaded slot or no request."""

    @pytest.mark.parametrize("slot_state", _LOADED_STATES)
    def test_a_loaded_slot_runs_the_chat_probe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slot_state: str
    ) -> None:
        seen: list[str] = []
        state = _gateway_chat_box(tmp_path, monkeypatch, seen)
        rows = [_slot_row("agent", slot_state, "big-27b"), _slot_row("utility", "ready", "u1")]
        out = hp._phase_smoke_tests(hp._StepCtx(state=state, io=_box_io(rows)))
        assert out.details["results"]["chat_completions"] == {"passed": True, "detail": "ready"}
        assert out.details["results"]["anchor_context_window"]["passed"] is True
        assert _chat_attempted(seen)
        assert out.status == hp.PhaseStatus.OK

    @pytest.mark.parametrize("slot_state", _COLD_STATES)
    def test_a_cold_slot_skips_and_never_posts_a_chat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slot_state: str
    ) -> None:
        seen: list[str] = []
        state = _gateway_chat_box(tmp_path, monkeypatch, seen)
        rows = [_slot_row("agent", slot_state, "big-27b"), _slot_row("utility", "ready", "u1")]
        out = hp._phase_smoke_tests(hp._StepCtx(state=state, io=_box_io(rows)))
        assert not _chat_attempted(seen), seen
        chat = out.details["results"]["chat_completions"]
        assert chat == {
            "passed": None,
            "skipped": True,
            "detail": (
                f"skipped: chat model not loaded (slot agent is {slot_state}; "
                "skipped so the smoke test does not cold-load it)"
            ),
        }
        # The anchor-window probe only reads (GETs never load a slot), so it
        # keeps its route preflight and still runs on the cold slot.
        assert out.details["results"]["anchor_context_window"] == {
            "passed": True,
            "detail": "window ok",
        }
        # A cold slot is not a fault: the phase stays ok and nothing failed.
        assert out.status == hp.PhaseStatus.OK
        assert out.details["failures"] == []

    def test_a_route_that_answers_for_a_cold_slot_is_not_readiness(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The incident: ``GET /v1/models/hal0/agent`` answers 200 for a COLD
        agent slot (the resolver logs ``not_loaded_loading``), so the old route
        preflight said "ready" and the chat probe cold-loaded the slot. The
        slot list says offline and wins. Production wiring: real fetchers over
        a fake network."""
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "hal0/agent")
        seen: list[str] = []
        _fake_http(
            monkeypatch,
            {
                "http://127.0.0.1:8080/v1/models/hal0/agent": {"id": "hal0/agent"},
                _CHAT_URL: _READY_REPLY,
                _SLOTS_URL: [
                    _slot_row("agent", "offline", "big-27b"),
                    _slot_row("utility", "ready", "u1"),
                ],
                **_detail_routes("utility"),
            },
            seen,
        )
        _stub_non_chat_probes(monkeypatch)
        out = hp._phase_smoke_tests(hp._StepCtx(state=hp.BootstrapState(hermes_home=str(hh))))
        assert _SLOTS_URL in seen
        assert not _chat_attempted(seen), seen
        assert out.details["results"]["chat_completions"]["skipped"] is True
        assert out.status == hp.PhaseStatus.OK

    @pytest.mark.parametrize(
        ("status", "rows", "expected"),
        [
            (None, None, "hal0-api did not answer the slot list"),
            (401, None, "hal0-api answered the slot list with HTTP 401"),
            (500, None, "hal0-api answered the slot list with HTTP 500"),
            (200, None, "hal0-api's slot list was unreadable"),
            (
                200,
                [_slot_row("embed", "ready", "e1", type="embedding")],
                "cannot tell which slot serves 'hal0/agent'",
            ),
        ],
        ids=["unreachable", "http-401", "http-500", "unparseable", "slot-not-listed"],
    )
    def test_a_slot_list_that_cannot_answer_skips_and_never_posts_a_chat(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        status: int | None,
        rows: list[dict[str, Any]] | None,
        expected: str,
    ) -> None:
        """Never fall back to probing: probing IS the cold load."""
        seen: list[str] = []
        state = _gateway_chat_box(tmp_path, monkeypatch, seen)
        out = hp._phase_smoke_tests(hp._StepCtx(state=state, io=_box_io(rows, status=status)))
        assert not _chat_attempted(seen), seen
        detail = out.details["results"]["chat_completions"]["detail"]
        assert detail.startswith("skipped: chat model not loaded (")
        assert expected in detail
        assert detail.endswith("skipped so the smoke test does not cold-load a slot)")
        assert out.status == hp.PhaseStatus.OK

    def test_a_drifted_slot_skips_and_never_posts_a_chat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A loaded slot whose running config lags its TOML (``hal0 update``
        re-renders units without bouncing them) is restarted by any load the
        chat request triggers — a full model reload — so the probe skips."""
        seen: list[str] = []
        state = _gateway_chat_box(tmp_path, monkeypatch, seen)
        rows = [_slot_row("agent", "ready", "big-27b"), _slot_row("utility", "ready", "u1")]
        out = hp._phase_smoke_tests(hp._StepCtx(state=state, io=_box_io(rows, drifted=("agent",))))
        assert not _chat_attempted(seen), seen
        assert out.details["results"]["chat_completions"] == {
            "passed": None,
            "skipped": True,
            "detail": (
                "skipped: chat model not loaded (slot agent has a pending config change; "
                "a load would restart it)"
            ),
        }
        assert out.status == hp.PhaseStatus.OK
        assert out.details["failures"] == []

    @pytest.mark.parametrize(
        "body",
        [
            _slot_detail_body("agent", drifted=False),
            _slot_detail_body("agent", drifted=True, render_error="template missing"),
            {"name": "agent"},
        ],
        ids=["not-drifted", "drift-that-cannot-re-render", "drift-unknown"],
    )
    def test_no_restartable_drift_runs_the_chat_probe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
    ) -> None:
        """Mirrors ``SlotManager._should_converge``: only ``drifted`` without a
        ``render_error`` restarts a loaded slot; unknown drift (no
        ``config_drift``) does not."""
        seen: list[str] = []
        state = _gateway_chat_box(tmp_path, monkeypatch, seen)
        out = hp._phase_smoke_tests(hp._StepCtx(state=state, io=_box_io(detail=(200, body))))
        assert _chat_attempted(seen)
        assert out.details["results"]["chat_completions"] == {"passed": True, "detail": "ready"}

    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            ((None, None), "hal0-api did not answer for slot agent's config"),
            ((401, None), "hal0-api answered slot agent's config with HTTP 401"),
            ((500, None), "hal0-api answered slot agent's config with HTTP 500"),
            ((200, None), "hal0-api's config for slot agent was unreadable"),
        ],
        ids=["unreachable", "http-401", "http-500", "unparseable"],
    )
    def test_a_slot_detail_that_cannot_answer_skips_and_never_posts_a_chat(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        answer: hp.SlotDetailAnswer,
        expected: str,
    ) -> None:
        seen: list[str] = []
        state = _gateway_chat_box(tmp_path, monkeypatch, seen)
        out = hp._phase_smoke_tests(hp._StepCtx(state=state, io=_box_io(detail=answer)))
        assert not _chat_attempted(seen), seen
        detail = out.details["results"]["chat_completions"]["detail"]
        assert expected in detail
        assert detail.endswith("skipped so the smoke test does not restart it)")
        assert out.status == hp.PhaseStatus.OK

    def test_a_drifted_fallback_candidate_skips_even_when_all_are_loaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        state = _gateway_chat_box(tmp_path, monkeypatch, seen)
        rows = [
            _slot_row("agent", "offline"),
            _slot_row("brain", "ready", "b1"),
            _slot_row("utility", "idle", "u1"),
        ]
        out = hp._phase_smoke_tests(
            hp._StepCtx(state=state, io=_box_io(rows, drifted=("utility",)))
        )
        assert not _chat_attempted(seen), seen
        assert (
            "slot utility has a pending config change"
            in (out.details["results"]["chat_completions"]["detail"])
        )

    def test_the_drift_read_goes_through_the_slot_detail_route(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Production wiring: the drift answer comes from ``GET
        /api/slots/{name}`` (the list route computes no drift)."""
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "hal0/agent")
        seen: list[str] = []
        _fake_http(
            monkeypatch,
            {
                _CHAT_URL: _READY_REPLY,
                _SLOTS_URL: [
                    _slot_row("agent", "ready", "big-27b"),
                    _slot_row("utility", "ready", "u1"),
                ],
                f"{_SLOTS_URL}/agent": _slot_detail_body("agent", drifted=True),
                **_detail_routes("utility"),
            },
            seen,
        )
        _stub_non_chat_probes(monkeypatch)
        out = hp._phase_smoke_tests(hp._StepCtx(state=hp.BootstrapState(hermes_home=str(hh))))
        assert f"{_SLOTS_URL}/agent" in seen
        assert not _chat_attempted(seen), seen
        assert (
            "slot agent has a pending config change"
            in (out.details["results"]["chat_completions"]["detail"])
        )

    def test_the_gateway_path_ignores_the_route_probe(self, tmp_path: Path) -> None:
        """A route probe saying "routable" settles nothing on the gateway."""
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "hal0/agent")
        io = _box_io(
            [_slot_row("agent", "offline", "big-27b")],
            fetch_model_route_ready=lambda _m, _b="": True,
        )
        ready, reason = hp._chat_model_ready(hp.BootstrapState(hermes_home=str(hh)), io)
        assert ready is False
        assert reason == "slot agent is offline; skipped so the smoke test does not cold-load it"

    def test_a_direct_llama_server_keeps_the_route_probe(self, tmp_path: Path) -> None:
        """``HAL0_HERMES_LIVE_RESOLVE=0``: a slot's own llama-server never loads
        on request, so the route probe decides and the slot list is not read —
        even when hal0-api would report every slot cold."""
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "raw-model-id", base_url=_DIRECT_BACKEND)
        calls: list[str] = []
        io = _box_io(
            [_slot_row("agent", "offline", "raw-model-id")],
            calls=calls,
            fetch_model_route_ready=lambda m, _b="": m == "raw-model-id",
        )
        ready, reason = hp._chat_model_ready(hp.BootstrapState(hermes_home=str(hh)), io)
        assert (ready, reason) == (True, "ready")
        assert calls == []

    def test_the_anchor_preflight_keeps_the_route_probe_on_the_gateway(
        self, tmp_path: Path
    ) -> None:
        """anchor_context_window only GETs the catalog, which never loads a
        slot, so its preflight is the #1831 route probe on the gateway too —
        never the slot list — and a cold agent slot does not hold it back."""
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "hal0/agent")
        calls: list[str] = []
        asked: list[tuple[str, str]] = []

        def _route(model_id: str, base_url: str = "") -> bool:
            asked.append((model_id, base_url))
            return True

        io = _box_io(
            [_slot_row("agent", "offline", "big-27b")], calls=calls, fetch_model_route_ready=_route
        )
        state = hp.BootstrapState(hermes_home=str(hh))
        assert hp._anchor_route_ready(state, io) == (True, "ready")
        assert asked == [("hal0/agent", "http://127.0.0.1:8080/v1")]
        assert calls == []

    def test_only_skips_leave_the_phase_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A healthy box whose model slots are simply cold: every gated probe
        skips, nothing fails, and the phase is ``ok`` — so ``provision.json``
        carries no ``failure_count`` and the overview raises no
        ``provision_warn``."""
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "hal0/agent")
        for name in ("_smoke_chat_completions", "_smoke_memory_roundtrip"):
            monkeypatch.setattr(hp, name, lambda s, io: pytest.fail("gated probe ran"))
        monkeypatch.setattr(hp, "_smoke_wrapper_ready", lambda s, io: (True, "ok"))
        monkeypatch.setattr(hp, "_smoke_hermes_doctor", lambda s, io: (True, "ok"))
        monkeypatch.setattr(hp, "_smoke_admin_tools_list", lambda s, io: (True, "8 tools"))
        monkeypatch.setattr(hp, "_smoke_hermes_md_contains_primary", lambda s, io: (True, "ok"))
        # The anchor probe runs on a cold slot and finds no window to judge.
        monkeypatch.setattr(
            hp, "_smoke_anchor_context_window", lambda s, io: (None, "no advertised window")
        )
        rows = [_slot_row("agent", "offline", "big-27b"), _slot_row("utility", "offline", "u1")]
        out = hp._phase_smoke_tests(
            hp._StepCtx(state=hp.BootstrapState(hermes_home=str(hh)), io=_box_io(rows))
        )
        assert out.status == hp.PhaseStatus.OK
        assert out.details["failures"] == []
        assert {s.split(":")[0] for s in out.details["skipped"]} == {
            "chat_completions",
            "anchor_context_window",
            "memory_roundtrip",
        }

    def test_the_slot_list_is_read_once_per_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        state = _gateway_chat_box(tmp_path, monkeypatch, seen)
        calls: list[str] = []
        hp._phase_smoke_tests(hp._StepCtx(state=state, io=_box_io(calls=calls)))
        # One list read shared by the chat and memory gates, then each gate's
        # slot detail (chat: agent, memory: utility).
        assert calls == ["slots", "detail:agent", "detail:utility"]

    def test_a_slot_both_gates_check_has_its_detail_read_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        state = _gateway_chat_box(tmp_path, monkeypatch, seen)
        calls: list[str] = []
        engine_llm = {"base_url": "http://127.0.0.1:8080/v1", "model": "hal0/agent"}
        hp._phase_smoke_tests(
            hp._StepCtx(state=state, io=_box_io(calls=calls, engine_llm=engine_llm))
        )
        assert calls == ["slots", "detail:agent"]


class TestFetchSlotList:
    """``GET /api/slots`` keeps "said no", "said nothing" and "not a list" apart."""

    @staticmethod
    def _serve(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> list[tuple[Any, Any]]:
        sent: list[tuple[Any, Any]] = []

        def _open(req: Any, timeout: float | None = None) -> Any:
            sent.append((req, timeout))
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr("hal0.service_identity.open_url", _open)
        return sent

    def test_it_sends_the_box_key_and_keeps_only_dict_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``open_url`` sends no key of its own; the read attaches the box
        identity so it survives auth enforcement."""
        monkeypatch.setenv("HAL0_ADMIN_KEY", "box-key")
        row = _slot_row("agent", "ready", "m1")
        sent = self._serve(monkeypatch, _FakeHTTPResponse(200, [row, "junk"]))
        assert hp._fetch_slot_list() == (200, [row])
        req, timeout = sent[0]
        assert req.full_url == _SLOTS_URL
        assert req.get_header("Authorization") == "Bearer box-key"
        assert timeout == hp._SLOT_LIST_TIMEOUT_S

    def test_a_wrapped_list_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _slot_row("agent", "ready", "m1")
        self._serve(monkeypatch, _FakeHTTPResponse(200, {"slots": [row]}))
        assert hp._fetch_slot_list() == (200, [row])

    def test_a_body_that_is_not_a_list_is_unreadable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._serve(monkeypatch, _FakeHTTPResponse(200, {"detail": "nope"}))
        assert hp._fetch_slot_list() == (200, None)

    def test_an_http_error_keeps_its_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from urllib.error import HTTPError

        self._serve(monkeypatch, HTTPError(_SLOTS_URL, 401, "Unauthorized", None, None))  # type: ignore[arg-type]
        assert hp._fetch_slot_list() == (401, None)

    def test_unreachable_is_no_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from urllib.error import URLError

        self._serve(monkeypatch, URLError("connection refused"))
        assert hp._fetch_slot_list() == (None, None)


class TestFetchSlotDetail:
    """``GET /api/slots/{name}``, the route that computes ``config_drift``."""

    def test_it_sends_the_box_key_to_the_quoted_detail_route(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HAL0_ADMIN_KEY", "box-key")
        body = _slot_detail_body("my slot", drifted=True)
        sent = TestFetchSlotList._serve(monkeypatch, _FakeHTTPResponse(200, body))
        assert hp._fetch_slot_detail("my slot") == (200, body)
        req, timeout = sent[0]
        assert req.full_url == f"{_SLOTS_URL}/my%20slot"
        assert req.get_header("Authorization") == "Bearer box-key"
        assert timeout == hp._SLOT_LIST_TIMEOUT_S

    def test_a_non_object_body_is_unreadable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        TestFetchSlotList._serve(monkeypatch, _FakeHTTPResponse(200, ["not", "a", "slot"]))
        assert hp._fetch_slot_detail("agent") == (200, None)

    def test_an_http_error_keeps_its_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from urllib.error import HTTPError

        err = HTTPError(f"{_SLOTS_URL}/agent", 404, "Not Found", None, None)  # type: ignore[arg-type]
        TestFetchSlotList._serve(monkeypatch, err)
        assert hp._fetch_slot_detail("agent") == (404, None)


class TestSlotServingModel:
    """Which slots a gateway request for ``model.default`` could land on."""

    @staticmethod
    def _names(model_id: str, rows: list[dict[str, Any]]) -> list[str]:
        candidates, _why = hp._slots_serving_model(model_id, rows)
        return [str(r["name"]) for r in candidates]

    def test_a_canonical_virtual_maps_to_its_bound_slot(self) -> None:
        rows = [_slot_row("agent", "ready", "big"), _slot_row("utility", "offline", "small")]
        assert self._names("hal0/utility", rows) == ["utility"]

    def test_an_unbound_chain_head_falls_to_the_next_entry(self) -> None:
        """``hal0/utility`` with no model on utility lands on the agent anchor
        (the 27B case the memory gate must not cold-load)."""
        rows = [_slot_row("agent", "offline", "big"), _slot_row("utility", "offline")]
        assert self._names("hal0/utility", rows) == ["agent"]

    def test_any_bound_llm_slot_is_addressable_as_a_virtual(self) -> None:
        rows = [_slot_row("agent", "ready", "big"), _slot_row("coder", "idle", "c1")]
        assert self._names("hal0/coder", rows) == ["coder"]

    def test_the_npu_virtual_matches_an_flm_slot_by_device(self) -> None:
        rows = [
            _slot_row("agent", "ready", "big"),
            _slot_row("flm", "offline", "gemma-FLM", declared_backend="flm"),
        ]
        assert self._names("hal0/npu", rows) == ["flm"]

    def test_a_single_bound_slot_pins_the_primary_fallback(self) -> None:
        rows = [_slot_row("agent", "offline"), _slot_row("brain", "ready", "b1")]
        assert self._names("hal0/agent", rows) == ["brain"]

    def test_an_ambiguous_fallback_returns_every_bound_slot(self) -> None:
        """No chain entry is bound: the gateway takes the first bound slot in
        its own order, so every bound slot is a candidate."""
        rows = [
            _slot_row("agent", "offline"),
            _slot_row("brain", "ready", "b1"),
            _slot_row("utility", "offline", "u1"),
        ]
        assert self._names("hal0/agent", rows) == ["brain", "utility"]

    def test_a_physical_id_or_alias_maps_to_its_owning_slot(self) -> None:
        rows = [_slot_row("agent", "ready", "big"), _slot_row("utility", "ready", "small")]
        for model_id in ("small", "utility"):
            assert self._names(model_id, rows) == ["utility"]

    def test_an_id_two_slots_carry_is_not_pinned(self) -> None:
        rows = [_slot_row("brain", "ready", "same"), _slot_row("nano", "offline", "same")]
        candidates, why = hp._slots_serving_model("same", rows)
        assert candidates == [] and "2 slots serve 'same'" in why

    def test_unknown_ids_are_not_pinned(self) -> None:
        rows = [_slot_row("agent", "ready", "big")]
        assert self._names("hal0/nope", rows) == []
        assert self._names("cloud-model", rows) == []

    def test_synthetic_and_non_llm_rows_never_serve_chat(self) -> None:
        rows = [
            _slot_row("agent", "ready", "big", _synthetic=True, kind="remote"),
            _slot_row("embed", "ready", "e1", type="embedding"),
        ]
        candidates, why = hp._slots_serving_model("hal0/agent", rows)
        assert candidates == [] and "has a model bound" in why


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("", True),
        ("http://127.0.0.1:8080/v1", True),
        ("http://localhost:8080/v1/", True),
        ("http://[::1]:8080/v1", True),
        (_DIRECT_BACKEND, False),
        ("http://127.0.0.1:8080", False),
        ("https://api.example.invalid/v1", False),
        ("http://evil@127.0.0.1:8080/v1", True),
        ("http://hal0.lan:8080/v1", True),
        ("http://10.0.0.5:8080/v1/", True),
        ("http://hal0.lan:8081/v1", False),
    ],
)
def test_is_gateway_base_url(url: str, expected: bool) -> None:
    assert hp._is_gateway_base_url(url) is expected


class TestMemoryEngineGate:
    """memory_roundtrip's ``memory_add`` makes hindsight extract with its own
    LLM call to ``hal0/memory`` on the gateway, which loads the extraction slot
    (``utility`` by default, the agent anchor when utility has no model) if it
    is cold. The REAL probe runs against a recording memory seam, so any
    attempt shows in ``calls``."""

    @staticmethod
    def _box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> hp.BootstrapState:
        hh = tmp_path / "hh"
        _write_ready_chat_config(hh, "hal0/agent")
        for name in (
            "_smoke_wrapper_ready",
            "_smoke_hermes_doctor",
            "_smoke_chat_completions",
            "_smoke_admin_tools_list",
            "_smoke_hermes_md_contains_primary",
            "_smoke_anchor_context_window",
        ):
            monkeypatch.setattr(hp, name, lambda s, io: (True, "ok"))
        return hp.BootstrapState(hermes_home=str(hh))

    @staticmethod
    def _memory_seam(calls: list[str]) -> Any:
        def _call(_method: str, params: dict[str, Any], **_kw: Any) -> dict[str, Any]:
            calls.append(params["name"])
            if params["name"] == "memory_search":
                return {"ok": True, "result": {"items": [{"id": "1"}]}}
            return {"ok": True, "result": {"id": "1"}}

        return _call

    def _run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: Any, **io_kw: Any
    ) -> tuple[dict[str, Any], list[str]]:
        calls: list[str] = []
        state = self._box(tmp_path, monkeypatch)
        io = _box_io(rows, mcp_memory_call=self._memory_seam(calls), **io_kw)
        out = hp._phase_smoke_tests(hp._StepCtx(state=state, io=io))
        assert out.status == hp.PhaseStatus.OK
        return out.details["results"]["memory_roundtrip"], calls

    def test_a_cold_extraction_slot_skips_and_never_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [_slot_row("agent", "ready", "big"), _slot_row("utility", "offline", "u1")]
        result, calls = self._run(tmp_path, monkeypatch, rows)
        assert calls == []
        assert result == {
            "passed": None,
            "skipped": True,
            "detail": (
                "skipped: memory engine's model not loaded (hal0/utility: slot utility is "
                "offline; skipped so the smoke test does not cold-load it)"
            ),
        }

    def test_a_loaded_extraction_slot_runs_the_roundtrip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [_slot_row("agent", "offline", "big"), _slot_row("utility", "idle", "u1")]
        result, calls = self._run(tmp_path, monkeypatch, rows)
        assert calls == ["memory_add", "memory_search"]
        assert result == {"passed": True, "detail": "1 item(s) returned"}

    def test_an_unbound_utility_guards_the_agent_anchor_it_falls_to(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [_slot_row("agent", "offline", "big-27b"), _slot_row("utility", "offline")]
        result, calls = self._run(tmp_path, monkeypatch, rows)
        assert calls == []
        assert "hal0/utility: slot agent is offline" in result["detail"]

    def test_a_drifted_extraction_slot_skips_and_never_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [_slot_row("agent", "ready", "big"), _slot_row("utility", "ready", "u1")]
        result, calls = self._run(tmp_path, monkeypatch, rows, drifted=("utility",))
        assert calls == []
        assert result["detail"] == (
            "skipped: memory engine's model not loaded (hal0/utility: slot utility has a "
            "pending config change; a load would restart it)"
        )

    def test_an_upstream_extraction_model_runs_with_every_slot_cold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [_slot_row("agent", "offline", "big"), _slot_row("utility", "offline", "u1")]
        memory = _memory_cfg(extraction_model="MiniMax-M2.5")
        _result, calls = self._run(tmp_path, monkeypatch, rows, memory=memory)
        assert calls == ["memory_add", "memory_search"]

    def test_an_engine_llm_pointed_off_hal0_runs_with_every_slot_cold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``hindsight-llm.env`` override that points the engine straight at
        a cloud provider: the retain never reaches hal0, so nothing loads."""
        rows = [_slot_row("agent", "offline", "big"), _slot_row("utility", "offline", "u1")]
        engine_llm = {"base_url": "https://api.example.invalid/v1", "model": "m"}
        _result, calls = self._run(tmp_path, monkeypatch, rows, engine_llm=engine_llm)
        assert calls == ["memory_add", "memory_search"]

    def test_an_engine_pinned_to_another_virtual_guards_that_slot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pre-#2919 drop-in still naming ``hal0/agent`` on the gateway."""
        rows = [_slot_row("agent", "offline", "big"), _slot_row("utility", "ready", "u1")]
        engine_llm = {"base_url": "http://127.0.0.1:8080/v1", "model": "hal0/agent"}
        result, calls = self._run(tmp_path, monkeypatch, rows, engine_llm=engine_llm)
        assert calls == []
        assert "hal0/agent: slot agent is offline" in result["detail"]

    @pytest.mark.parametrize(
        "memory",
        [_memory_cfg(engine="pgvector"), _memory_cfg(enabled=False)],
        ids=["pgvector", "disabled"],
    )
    def test_no_engine_model_call_means_no_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory: Any
    ) -> None:
        rows = [_slot_row("agent", "offline", "big"), _slot_row("utility", "offline", "u1")]
        _result, calls = self._run(tmp_path, monkeypatch, rows, memory=memory)
        assert calls == ["memory_add", "memory_search"]

    def test_an_unanswerable_slot_list_skips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, calls = self._run(tmp_path, monkeypatch, None, status=None)
        assert calls == []
        assert "hal0-api did not answer the slot list" in result["detail"]

    def test_an_unreadable_hal0_toml_skips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> Any:
            raise OSError("permission denied")

        result, calls = self._run(tmp_path, monkeypatch, _WARM_SLOTS, load_config=_boom)
        assert calls == []
        assert "could not read [memory] from hal0.toml" in result["detail"]


class TestAnchorContextWindowPreflight:
    """#1867 — the anchor's window is checked before Hermes ever sees it.

    Reproduces the ct150 box exactly: the model behind the agent slot declares
    96000, but the slot runs a profile whose ``ctx_size`` is 4096 (ADR-0029: the
    window is the profile's, not a slot key), so Hermes is handed 4,096 and —
    raising below ``MINIMUM_CONTEXT_LENGTH`` rather than degrading — refuses
    every turn. Before this preflight nothing named the slot or the profile; the
    operator saw only an opaque upstream error ~1.6 s into the first turn.

    Production wiring only: the real ``_phase_smoke_tests``, the real
    ``_fetch_anchor_models`` over a fake network, a real slot TOML on disk.
    """

    @staticmethod
    def _ct150_box(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_hal0_home: str,
        *,
        slot_ceiling: int,
        advertised: int,
    ) -> hp.BootstrapState:
        hh = tmp_path / "hh"
        hh.mkdir()
        (hh / "config.yaml").write_text(
            "model:\n  default: hal0/agent\n  base_url: http://127.0.0.1:8080/v1\n"
        )
        etc = tmp_path / "etc-hal0"
        (etc / "slots").mkdir(parents=True)
        profiles = Path(tmp_hal0_home) / "etc" / "hal0" / "profiles.toml"
        profiles.parent.mkdir(parents=True, exist_ok=True)
        profiles.write_text(f"[profile.agent-window]\nctx_size = {slot_ceiling}\n")
        (etc / "slots" / "agent.toml").write_text(
            'profile = "agent-window"\n[model]\ndefault = "hal0-brain-sft-q8-rocmfpx"\n'
        )
        monkeypatch.setattr(hp, "ETC_HAL0_DIR", etc)
        # The by-id row is the RESOLVED slot's catalog row with ``id`` rewritten
        # back to the requested handle — the gateway's own construction
        # (``api/routes/v1.py::_resolve_virtual_model_entry``), captured off a
        # live box in ``_CT152_*`` below.
        agent_row = {
            "id": "agent",
            "object": "model",
            "owned_by": "hal0",
            "name": "agent · hal0-brain-sft-q8-rocmfpx",
            "context_length": advertised,
        }
        _fake_http(
            monkeypatch,
            {
                # The anchor routes fine — this box is not broken in the way the
                # #1831 routability preflight looks for. What it hands Hermes is
                # min(96000 model window, the profile's own window).
                "http://127.0.0.1:8080/v1/models/hal0/agent": {**agent_row, "id": "hal0/agent"},
                "http://127.0.0.1:8080/v1/models": {
                    "data": [
                        agent_row,
                        {"id": "brain", "owned_by": "hal0", "context_length": 96000},
                    ]
                },
                # The agent slot is loaded, so the cold-load guard lets the
                # anchor probe run.
                _SLOTS_URL: [
                    _slot_row("agent", "ready", "hal0-brain-sft-q8-rocmfpx"),
                    _slot_row("utility", "ready", "u1"),
                ],
                **_detail_routes("agent", "utility"),
            },
        )
        _stub_non_chat_probes(monkeypatch)
        monkeypatch.setattr(hp, "_smoke_chat_completions", lambda s, io: (True, "ready"))
        # No provisioned Hermes venv under a unit test, so the floor comes from
        # hal0's pinned copy of MINIMUM_CONTEXT_LENGTH.
        return hp.BootstrapState(hermes_home=str(hh), venv=str(tmp_path / "no-venv"))

    def test_a_profile_window_under_the_floor_fails_loudly_with_the_fix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
    ) -> None:
        state = self._ct150_box(
            tmp_path, monkeypatch, tmp_hal0_home, slot_ceiling=4096, advertised=4096
        )
        out = hp._phase_smoke_tests(hp._StepCtx(state=state))

        result = out.details["results"]["anchor_context_window"]
        assert result["passed"] is False, out.details["results"]
        assert out.status == hp.PhaseStatus.WARN
        detail = result["detail"]
        # Everything an operator needs, without opening a source file.
        assert "agent" in detail
        assert "4,096" in detail
        assert "64,000" in detail
        assert "agent.toml" in detail
        assert "profile 'agent-window' sets ctx_size = 4,096" in detail
        assert "raise ctx_size to 65536 on profile 'agent-window'" in detail
        assert "hal0 slot restart agent" in detail
        assert any("anchor_context_window" in f for f in out.details["failures"])

    def test_a_profile_window_above_the_floor_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
    ) -> None:
        state = self._ct150_box(
            tmp_path, monkeypatch, tmp_hal0_home, slot_ceiling=65536, advertised=65536
        )
        out = hp._phase_smoke_tests(hp._StepCtx(state=state))
        assert out.details["results"]["anchor_context_window"]["passed"] is True
        assert out.status == hp.PhaseStatus.OK

    def test_no_routable_anchor_skips_instead_of_inventing_a_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Install-time run, before any model was ever loaded: there is no
        advertised window to judge, so the probe reports ``skipped`` — the same
        rule the chat probe follows (#1793)."""
        hh = tmp_path / "hh"
        hh.mkdir()
        (hh / "config.yaml").write_text(
            "model:\n  default: hal0/agent\n  base_url: http://127.0.0.1:8080/v1\n"
        )
        _fake_http(monkeypatch, {"http://127.0.0.1:8080/v1/models": {"data": []}})
        _stub_non_chat_probes(monkeypatch)
        monkeypatch.setattr(
            hp, "_smoke_chat_completions", lambda s, io: pytest.fail("probe must not run")
        )
        state = hp.BootstrapState(hermes_home=str(hh))
        out = hp._phase_smoke_tests(hp._StepCtx(state=state))
        assert out.details["results"]["anchor_context_window"]["skipped"] is True
        assert out.status == hp.PhaseStatus.OK

    #: ct152 as captured: the agent slot is offline with no model bound, while
    #: brain and utility are loaded.
    _CT152_SLOTS = (
        _slot_row("agent", "offline"),
        _slot_row("brain", "ready", "hal0-brain-sft-fpx8"),
        _slot_row("utility", "ready", "u1"),
    )

    @classmethod
    def _ct152_box(
        cls,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_hal0_home: str,
        seen: list[str] | None = None,
        slots: tuple[dict[str, Any], ...] | None = None,
    ) -> hp.BootstrapState:
        slots = cls._CT152_SLOTS if slots is None else slots
        hh = tmp_path / "hh"
        hh.mkdir()
        (hh / "config.yaml").write_text(
            "model:\n  default: hal0/agent\n  base_url: http://127.0.0.1:8080/v1\n"
        )
        etc = tmp_path / "etc-hal0"
        (etc / "slots").mkdir(parents=True)
        # The agent slot's TOML still exists on that box; it is simply not what
        # the anchor resolves to, so its profile must not be blamed.
        profiles = Path(tmp_hal0_home) / "etc" / "hal0" / "profiles.toml"
        profiles.parent.mkdir(parents=True, exist_ok=True)
        profiles.write_text("[profile.brain-window]\nctx_size = 32768\n")
        (etc / "slots" / "agent.toml").write_text('profile = "agent"\n')
        (etc / "slots" / "brain.toml").write_text('profile = "brain-window"\n')
        monkeypatch.setattr(hp, "ETC_HAL0_DIR", etc)
        _fake_http(
            monkeypatch,
            {
                "http://127.0.0.1:8080/v1/models/hal0/agent": _CT152_BY_ID,
                "http://127.0.0.1:8080/v1/models": {"data": _CT152_CATALOG},
                _CHAT_URL: _READY_REPLY,
                _SLOTS_URL: list(slots),
                **_detail_routes("brain", "utility"),
            },
            seen,
        )
        # The REAL chat probe stays in: any attempt at it shows in ``seen``.
        _stub_non_chat_probes(monkeypatch)
        return hp.BootstrapState(hermes_home=str(hh), venv=str(tmp_path / "no-venv"))

    def test_ct152_the_anchor_falls_back_to_another_slot_and_must_not_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
    ) -> None:
        """THE false negative, off the live ct152 box (rc.5).

        The agent slot is offline (``model_id: null``) so ``hal0/agent``
        resolves through the routing fallback chain to the BRAIN slot's
        32,768-token window — under Hermes' 64,000 floor, i.e. a box that
        cannot chat. But ``/v1/models`` lists only ``brain`` and ``utility``:
        there is no row spelled ``agent`` or ``hal0/agent`` anywhere in it, so
        a probe that looks the anchor up by its spelled alias sees nothing,
        calls the window unknown, and reports a PASS.

        Payloads are verbatim captures from ct152 — the expectation comes
        from the gateway itself, not from anything hal0's resolver computes.
        The agent slot is cold, but the anchor probe only reads the catalog, so
        it keeps its route preflight and still runs here.
        """
        state = self._ct152_box(tmp_path, monkeypatch, tmp_hal0_home)
        out = hp._phase_smoke_tests(hp._StepCtx(state=state))
        result = out.details["results"]["anchor_context_window"]
        assert result["passed"] is not True, result  # the whole point of #1867
        assert result["passed"] is False
        detail = result["detail"]
        assert "32,768" in detail
        assert "64,000" in detail
        # The serving slot is brain, not the alias the anchor is spelled with.
        assert "'brain'" in detail
        assert "raise ctx_size to 65536 on profile 'brain-window'" in detail
        assert "hal0 slot restart brain" in detail
        assert "agent.toml" not in detail
        assert out.status == hp.PhaseStatus.WARN

    def test_ct152_the_chat_probe_runs_when_every_fallback_slot_is_loaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
    ) -> None:
        """Same box: agent has no model, so the gateway falls back to one of
        the two bound slots in its own order, which the slot list cannot pin.
        Both are loaded with no pending config change, so wherever the request
        lands nothing loads, and the chat probe runs."""
        seen: list[str] = []
        state = self._ct152_box(tmp_path, monkeypatch, tmp_hal0_home, seen)
        out = hp._phase_smoke_tests(hp._StepCtx(state=state))
        assert _chat_attempted(seen), seen
        assert out.details["results"]["chat_completions"] == {"passed": True, "detail": "ready"}
        # Both fallback candidates had their config checked first.
        assert f"{_SLOTS_URL}/brain" in seen and f"{_SLOTS_URL}/utility" in seen

    def test_ct152_shape_with_a_cold_fallback_slot_skips_chat_and_never_posts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_hal0_home: str
    ) -> None:
        """The ct152 shape with utility offline: the fallback could land on the
        cold utility slot, so the chat probe skips and nothing reaches
        ``/chat/completions`` — while the anchor probe, which only reads, still
        fails loudly on brain's window."""
        seen: list[str] = []
        slots = (
            _slot_row("agent", "offline"),
            _slot_row("brain", "ready", "hal0-brain-sft-fpx8"),
            _slot_row("utility", "offline", "u1"),
        )
        state = self._ct152_box(tmp_path, monkeypatch, tmp_hal0_home, seen, slots)
        out = hp._phase_smoke_tests(hp._StepCtx(state=state))
        assert not _chat_attempted(seen), seen
        chat = out.details["results"]["chat_completions"]
        assert chat["skipped"] is True and chat["passed"] is None
        assert chat["detail"] == (
            "skipped: chat model not loaded ('hal0/agent' falls back to one of 2 slots "
            "and slot utility is offline; skipped so the smoke test does not cold-load a slot)"
        )
        assert not any(f.startswith("chat_completions") for f in out.details["failures"])
        assert out.details["results"]["anchor_context_window"]["passed"] is False

    def test_an_unreadable_window_skips_rather_than_passing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A routable anchor whose backend advertises no ``context_length``
        (the ``HAL0_HERMES_LIVE_RESOLVE=0`` shape against a plain llama-server)
        is unknown — and unknown is reported as SKIPPED, never as a pass."""
        hh = tmp_path / "hh"
        hh.mkdir()
        (hh / "config.yaml").write_text(
            "model:\n  default: qwen3-30b-a3b-instruct\n  base_url: http://127.0.0.1:8081/v1\n"
        )
        _fake_http(
            monkeypatch,
            {
                "http://127.0.0.1:8081/v1/models": {
                    "data": [{"id": "qwen3-30b-a3b-instruct", "object": "model"}]
                }
            },
        )
        _stub_non_chat_probes(monkeypatch)
        monkeypatch.setattr(hp, "_smoke_chat_completions", lambda s, io: (True, "ready"))
        state = hp.BootstrapState(hermes_home=str(hh), venv=str(tmp_path / "no-venv"))

        out = hp._phase_smoke_tests(hp._StepCtx(state=state))
        result = out.details["results"]["anchor_context_window"]
        assert result["passed"] is None
        assert result["skipped"] is True
        assert "NOT a pass" in result["detail"]
        # A slot TOML path is never invented for a raw physical model id.
        assert ".toml" not in result["detail"]
        assert out.details["failures"] == []


#: Verbatim ``GET /v1/models`` from ct152 (rc.5, agent slot
#: offline). Note what is NOT here: any row spelled ``agent`` or ``hal0/agent``.
_CT152_CATALOG: list[dict[str, Any]] = [
    {
        "id": "brain",
        "object": "model",
        "created": 1786589347,
        "owned_by": "hal0",
        "name": "brain · hal0-brain-sft-f16",
        "downloaded": True,
        "context_length": 32768,
        "max_context_window": 32768,
        "labels": ["chat"],
        "recipe": "chat",
    },
    {
        "id": "utility",
        "object": "model",
        "created": 1786589347,
        "owned_by": "hal0",
        "name": "utility · Qwen3.5-0.8B",
        "downloaded": True,
        "context_length": 32768,
        "max_context_window": 32768,
        "labels": ["chat"],
        "checkpoint": "Q4_K_XL",
    },
]

#: Verbatim ``GET /v1/models/hal0/agent`` from the same box — the routing
#: fallback chain resolved to the brain slot, so this is the brain row with
#: ``id`` rewritten back to the handle Hermes asked about.
_CT152_BY_ID: dict[str, Any] = {**_CT152_CATALOG[0], "id": "hal0/agent"}


class TestFetchModelRouteReady:
    """Direct coverage of the real ``_fetch_model_route_ready`` — the seam's
    DEFAULT binding, which nothing exercised before (the fakes all replaced
    it, so a wiring or URL-shape mistake shipped silently)."""

    def test_installio_default_binds_the_real_implementation(self) -> None:
        assert hp.InstallIO().fetch_model_route_ready is hp._fetch_model_route_ready

    def test_by_id_hit_preserves_slashes_in_virtual_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        _fake_http(monkeypatch, {"http://gw/v1/models/hal0/agent": {"id": "hal0/agent"}}, seen)
        assert hp._fetch_model_route_ready("hal0/agent", "http://gw/v1") is True
        assert seen[0] == "http://gw/v1/models/hal0/agent"

    def test_list_fallback_when_by_id_route_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_http(monkeypatch, {"http://be/v1/models": {"data": [{"id": "m1"}]}})
        assert hp._fetch_model_route_ready("m1", "http://be/v1") is True

    def test_unknown_physical_id_is_inconclusive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_http(monkeypatch, {"http://be/v1/models": {"data": [{"id": "other"}]}})
        assert hp._fetch_model_route_ready("m1", "http://be/v1") is None

    def test_unresolvable_virtual_is_conclusively_not_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gw = f"{hp.HAL0_API_URL}/v1"
        _fake_http(monkeypatch, {f"{gw}/models": {"data": [{"id": "brain"}]}})
        assert hp._fetch_model_route_ready("hal0/agent", gw) is False

    def test_hal0_prefix_is_only_authoritative_on_the_gateway(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``hal0/`` only means "virtual" to the hal0 gateway. On a pinned
        backend it is just characters in a model id — a backend with no by-id
        route that lists a different alias proves nothing, so this must not
        harden into a skip."""
        _fake_http(monkeypatch, {"http://127.0.0.1:8081/v1/models": {"data": [{"id": "brain"}]}})
        assert hp._fetch_model_route_ready("hal0/agent", "http://127.0.0.1:8081/v1") is None

    def test_non_canonical_hal0_prefixed_id_is_inconclusive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A physical registry id may legitimately start with ``hal0/`` (the
        id constraint is only non-emptiness), and the catalog suppresses raw
        chat ids in favour of their alias row — while chat dispatch still
        resolves the exact physical id to its owning slot. Only the resolver's
        CANONICAL virtual names earn a conclusive negative."""
        gw = f"{hp.HAL0_API_URL}/v1"
        _fake_http(monkeypatch, {f"{gw}/models": {"data": [{"id": "brain"}]}})
        assert hp._fetch_model_route_ready("hal0/some-physical-gguf", gw) is None
        # ...but the advertised virtuals still are conclusive.
        from hal0.normalize.resolver import DEFAULT_CHAINS

        for virtual in DEFAULT_CHAINS:
            assert hp._fetch_model_route_ready(virtual, gw) is False, virtual

    def test_empty_catalog_is_conclusive_but_malformed_one_is_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 2xx list response with no usable ``data`` is not evidence of an
        EMPTY catalog — it is no evidence at all, so it must not produce a
        skip. A genuinely empty ``data`` list is a real answer."""
        gw = f"{hp.HAL0_API_URL}/v1"
        _fake_http(monkeypatch, {f"{gw}/models": {"data": []}})
        assert hp._fetch_model_route_ready("hal0/agent", gw) is False
        for junk in ({"error": "nope"}, {"data": "not-a-list"}, {}):
            _fake_http(monkeypatch, {f"{gw}/models": junk})
            assert hp._fetch_model_route_ready("hal0/agent", gw) is None, junk

    def test_unreachable_endpoint_is_inconclusive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_http(monkeypatch, {}, unreachable={"http://be/"})
        assert hp._fetch_model_route_ready("m1", "http://be/v1") is None

    def test_empty_base_url_falls_back_to_the_gateway(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        _fake_http(monkeypatch, {f"{hp.HAL0_API_URL}/v1/models/hal0/agent": {"id": "x"}}, seen)
        assert hp._fetch_model_route_ready("hal0/agent", "") is True
        assert seen[0] == f"{hp.HAL0_API_URL}/v1/models/hal0/agent"


def test_write_run_report_surfaces_failure_and_skipped_counts(tmp_path: Path) -> None:
    """`hal0 agent status` reads `failure_count`/`skipped_count` off the
    persisted phase entry — this pins the aggregation `_write_run_report`
    derives from `details["failures"]`/`details["skipped"]` (#1793), for any
    phase, not just smoke_tests."""
    report = hp.InstallReport(
        hermes_home=str(tmp_path / "hh"),
        venv=str(tmp_path / "venv"),
        agent_id="hermes-agent",
        steps=[
            hp.InstallStep(
                name="smoke_tests",
                status=hp.PhaseStatus.WARN,
                details={
                    "results": {},
                    "failures": ["wrapper_ready: wrapper missing"],
                    "skipped": ["chat_completions: skipped: no chat model loaded (...)"],
                },
            ),
            hp.InstallStep(name="env_probe", status=hp.PhaseStatus.OK, details={}),
        ],
    )
    state_root = tmp_path / "state"
    hp._write_run_report(report, state_root)
    data = json.loads((state_root / "provision.json").read_text())
    smoke = data["phases"]["smoke_tests"]
    assert smoke["status"] == "warn"
    assert smoke["failure_count"] == 1
    assert smoke["skipped_count"] == 1
    env_probe = data["phases"]["env_probe"]
    assert "failure_count" not in env_probe
    assert "skipped_count" not in env_probe


def test_record_converged_release_is_a_no_op_without_a_report_or_change(tmp_path: Path) -> None:
    """#2914: the converge refresh never invents a report and never rewrites an
    ``install`` phase already ``ok`` on the same release."""
    state_root = tmp_path / "state"
    assert hp.record_converged_release("r1", state_root=state_root) is False
    assert not (state_root / hp._STATE_FILE_NAME).exists()

    state_root.mkdir()
    target = state_root / hp._STATE_FILE_NAME
    target.write_text("{not json", encoding="utf-8")
    assert hp.record_converged_release("r1", state_root=state_root) is False
    assert target.read_text(encoding="utf-8") == "{not json"

    report = hp.InstallReport(hermes_home="/h", venv="/v", agent_id="hermes")
    report.steps.append(
        hp.InstallStep(name="install", status="ok", details={"release": "r1", "changed": True})
    )
    hp._write_run_report(report, state_root)
    before = target.read_text(encoding="utf-8")
    assert hp.record_converged_release("r1", state_root=state_root) is False
    assert target.read_text(encoding="utf-8") == before
    # A different live release is recorded.
    assert hp.record_converged_release("r2", state_root=state_root, changed=True) is True
    install = json.loads(target.read_text(encoding="utf-8"))["phases"]["install"]
    assert install["details"] == {
        "release": "r2",
        "source": hp.CONVERGE_REPORT_SOURCE,
        "changed": True,
    }
    assert install["changed"] is True


def test_record_converged_release_keeps_the_venv_uninstall_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2914 review: the refresh overlays the install details; it must not drop
    the ``venv`` the bootstrap recorded, or uninstall silently skips cleanup."""
    from hal0.agents import hermes_layout
    from hal0.agents.hermes.driver import HermesDriver

    state_root = tmp_path / "state"
    venv = tmp_path / "venvs" / "hermes"
    report = hp.InstallReport(hermes_home="/h", venv=str(venv), agent_id="hermes")
    report.steps.append(
        hp.InstallStep(
            name="install",
            status="fail",
            details={"venv": str(venv), "hermes_bin": "/usr/local/bin/hermes", "release": "r1"},
        )
    )
    hp._write_run_report(report, state_root)
    assert hp.record_converged_release("r2", state_root=state_root) is True
    data = json.loads((state_root / hp._STATE_FILE_NAME).read_text(encoding="utf-8"))
    details = data["phases"]["install"]["details"]
    assert details["venv"] == str(venv)
    assert details["hermes_bin"] == "/usr/local/bin/hermes"
    assert details["release"] == "r2"

    removed: list[Path] = []
    monkeypatch.setattr(hermes_layout, "remove_hermes_install", removed.append)
    HermesDriver()._remove_venv(data)
    assert removed == [venv]


def test_self_report_writes_summary_memory_and_handles_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"))
    # smoke_tests runs immediately before self_report; its rollup arrives via
    # output_of (formerly a same-run checkpoint need).
    prior = {"smoke_tests": {"failures": ["chat_completions: 503"]}}
    # Pre-render config so primary alias gets picked up.
    (tmp_path / "hh").mkdir()
    (tmp_path / "hh" / "config.yaml").write_text("model:\n  default: qwen3:8b\n", encoding="utf-8")
    captured: list[tuple[str, dict[str, Any]]] = []

    def _fake_mcp(method: str, params: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        captured.append((method, params))
        return {"ok": True, "result": {"id": "mem_xyz"}}

    io = hp.InstallIO(mcp_memory_call=_fake_mcp)
    out = hp._phase_self_report(hp._StepCtx(state=state, io=io, _prior=prior))
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
    io = hp.InstallIO(mcp_memory_call=lambda *a, **kw: {"ok": False, "error": "connection refused"})
    out = hp._phase_self_report(hp._StepCtx(state=state, io=io))
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
) -> tuple[Path, _FakeSystemctl, hp.InstallIO]:
    """Point the gateway drop-in dir at tmp_path + fake systemctl + root euid.

    Hermeticity (orchestrator note): the step also calls
    ``ensure_gateway_api_server_key``, which reads the REAL secrets vault
    (``HERMES_SECRETS_ENV``) and would key-gen + write it on a bare CI runner
    (host mutation + an extra seam call). Stub it to a stable "present" result
    so these drop-in tests never touch the host vault — the API-key wiring is
    covered hermetically by test_hermes_security_deliverables.py and the
    double-run convergence test."""
    dropin_dir = tmp_path / "etc" / "systemd" / "system" / "hermes-gateway.service.d"
    dropin_file = dropin_dir / "10-hal0-secrets.conf"
    monkeypatch.setattr(hp, "GATEWAY_SYSTEMD_DROPIN_DIR", dropin_dir)
    monkeypatch.setattr(hp, "GATEWAY_SYSTEMD_DROPIN_FILE", dropin_file)
    monkeypatch.setattr(
        hp,
        "ensure_gateway_api_server_key",
        lambda **_: hp.ApiServerKeyResult(outcome="present", key_len=43),
    )
    # Pretend we're root so the phase doesn't SKIP on the non-root guard.
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)
    fake = _FakeSystemctl()
    return dropin_file, fake, hp.InstallIO(run=fake.run)


def test_gateway_secrets_wire_warns_when_the_key_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed API_SERVER_KEY write is a WARN with a reason, not an ok row (#2908)."""
    _, _, io = _patch_dropin_to_tmp(tmp_path, monkeypatch)

    def _boom(**_: object) -> hp.ApiServerKeyResult:
        raise PermissionError(13, "Permission denied", "/var/lib/hal0/secrets/agents/hermes.env")

    monkeypatch.setattr(hp, "ensure_gateway_api_server_key", _boom)
    out = hp._phase_gateway_secrets_wire(hp._StepCtx(state=hp.BootstrapState(), io=io))
    assert out.status == hp.PhaseStatus.WARN
    assert out.reason is not None and "API_SERVER_KEY" in out.reason
    assert "hermes.env" in out.reason
    assert out.details["api_server_key"]["outcome"] == "failed"


def test_gateway_secrets_wire_writes_dropin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dropin_file, fake, io = _patch_dropin_to_tmp(tmp_path, monkeypatch)
    state = hp.BootstrapState()

    out = hp._phase_gateway_secrets_wire(hp._StepCtx(state=state, io=io))

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

    first = hp._phase_gateway_secrets_wire(hp._StepCtx(state=state, io=io))
    assert first.status == hp.PhaseStatus.OK
    mtime_after_first = dropin_file.stat().st_mtime_ns
    body_after_first = dropin_file.read_text(encoding="utf-8")
    assert fake.calls == [["systemctl", "daemon-reload"]]

    second = hp._phase_gateway_secrets_wire(hp._StepCtx(state=state, io=io))
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
    # Hermetic vault: the phase also runs ensure_gateway_api_server_key, whose
    # outcome must not depend on the host's real /var/lib/hal0 state (on a bare
    # CI runner a missing vault triggers key-gen → an extra merge-secrets seam
    # call). A strong key on disk → outcome "present", no write.
    vault = tmp_path / "hermes.env"
    vault.write_text(f"API_SERVER_KEY={'k' * 43}\n", encoding="utf-8")
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", vault)

    seam_calls: list[tuple[list[str], Any]] = []

    def _fake_run(argv: list[str], **kwargs: Any) -> Any:
        seam_calls.append((list(argv), kwargs.get("input")))

        class _C:
            returncode = 0

        return _C()

    monkeypatch.setattr(hp.subprocess, "run", _fake_run)
    state = hp.BootstrapState()

    out = hp._phase_gateway_secrets_wire(hp._StepCtx(state=state, io=io))

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
        hp._StepCtx(state=hp.BootstrapState(), io=hp.InstallIO(run=_boom))
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


# ── canonical home / wrapper ────────────────────────────────────────────────


def test_bootstrap_default_home_is_dot_hermes() -> None:
    # The default install target is the NORMAL hermes default `/var/lib/hal0/.hermes`.
    assert hp.BootstrapState().hermes_home == "/var/lib/hal0/.hermes"


def test_install_phase_installs_canonical_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the canonical /usr/local/bin/hermes is installed — the hal0-hermes
    back-compat symlink is retired."""
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
        hp._StepCtx(state=state, io=hp.InstallIO(ensure_release=fake_ensure_release()))
    )

    assert out.status == hp.PhaseStatus.OK
    assert hermes_cli_dst.is_file()
    assert os.access(hermes_cli_dst, os.X_OK)
    assert not wrapper_dst.exists()  # no back-compat symlink
    assert out.details["hermes_cli"] == str(hermes_cli_dst)
    assert "wrapper" not in out.details


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


def test_hal0_admin_mcp_timeout_clears_the_slot_lifecycle_budget():
    """The bundled hal0-admin MCP client must out-wait its own blocking tools.

    The admin catalog exposes slot_load / slot_restart / model_swap /
    npu_backend_load, all of which block on the slot state machine. A 60s
    per-server client cap aborted an agent-driven load long before the server
    gave up — the outer half of #1832. Floor read from the server modules.
    """
    from hal0.providers.container import _HEALTH_TIMEOUT_S
    from hal0.slots.manager import SlotManager

    terminate = float(SlotManager._terminate_timeout_s)
    floor = terminate + float(_HEALTH_TIMEOUT_S) + 2 * terminate

    by_name = {s["name"]: s for s in hp._default_mcp_servers()}
    assert by_name["hal0-admin"]["timeout"] >= floor
    assert hp._build_brain_profile_mcp_servers()["hal0-admin"]["timeout"] >= floor


def test_hal0_admin_mcp_timeout_covers_the_widest_bridge_tool():
    """The outer MCP client must out-wait the WIDEST tool it fronts.

    ``_HAL0_ADMIN_MCP_TIMEOUT_S`` was derived from the restart shape while the
    catalog it fronts also exposes ``stack_apply``, whose fan-out budget is
    several times larger — so the outer client truncated the single biggest
    blocking call it is responsible for. It cannot import the bridge's table
    directly (hal0.mcp.admin imports hal0.agents, so that is a circular import
    that breaks MCP mounting), hence this drift pin.
    """
    from hal0.mcp.admin import max_slot_lifecycle_timeout_s

    widest = max_slot_lifecycle_timeout_s()
    assert widest <= hp._HAL0_ADMIN_MCP_TIMEOUT_S, (
        f"outer MCP client caps at {hp._HAL0_ADMIN_MCP_TIMEOUT_S}s while the "
        f"widest tool it fronts budgets {widest}s"
    )


# ── _atomic_write keeps the owner on a root run (#2615) ─────────────────────


def test_atomic_write_as_root_keeps_the_owner_of_the_replaced_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root caller (install.sh's post-activation pass reaching the Hermes
    config backstop) must not leave config.yaml root-owned."""
    target = tmp_path / "config.yaml"
    target.write_text("a: 1\n")
    st = target.stat()
    chowned: list[tuple[int, int]] = []
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)
    monkeypatch.setattr(hp.os, "chown", lambda *_a: pytest.fail("chown takes a swappable path"))
    monkeypatch.setattr(hp.os, "fchown", lambda _fd, u, g: chowned.append((u, g)))
    hp._atomic_write(target, "a: 2\n")
    assert chowned == [(st.st_uid, st.st_gid)]
    assert target.read_text() == "a: 2\n"


def test_atomic_write_as_root_never_replaces_with_a_wrong_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("a: 1\n")

    def deny(*_a: object) -> None:
        raise PermissionError("chown denied")

    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)
    monkeypatch.setattr(hp.os, "fchown", deny)
    with pytest.raises(PermissionError):
        hp._atomic_write(target, "a: 2\n")
    assert target.read_text() == "a: 1\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]  # no temp left


def test_atomic_write_unprivileged_never_chowns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("a: 1\n")
    monkeypatch.setattr(hp.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(hp.os, "chown", lambda *_a: pytest.fail("no chown off root"))
    monkeypatch.setattr(hp.os, "fchown", lambda *_a: pytest.fail("no chown off root"))
    hp._atomic_write(target, "a: 2\n")
    assert target.read_text() == "a: 2\n"


@pytest.mark.parametrize("euid", [0, 1000])
def test_atomic_write_never_follows_a_planted_tmp_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, euid: int
) -> None:
    """#2903: the service account can plant ``<name>.tmp`` in ``$HERMES_HOME``;
    a root repair writing through it would truncate and re-own the link target."""
    import stat

    victim = tmp_path / "root-only-file"
    victim.write_text("precious\n")
    home = tmp_path / "home"
    home.mkdir()
    target = home / "config.yaml"
    target.write_text("a: 1\n")
    (home / "config.yaml.tmp").symlink_to(victim)
    monkeypatch.setattr(hp.os, "geteuid", lambda: euid)
    monkeypatch.setattr(hp.os, "chown", lambda *_a: pytest.fail("chown takes a swappable path"))
    monkeypatch.setattr(hp.os, "fchown", lambda *_a: None)
    hp._atomic_write(target, "a: 2\n", mode=0o640)
    assert victim.read_text() == "precious\n"
    assert target.read_text() == "a: 2\n"
    assert not target.is_symlink()
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert (home / "config.yaml.tmp").is_symlink()  # untouched
    assert sorted(p.name for p in home.iterdir()) == ["config.yaml", "config.yaml.tmp"]


def test_atomic_write_new_file_gets_the_umask_default(tmp_path: Path) -> None:
    """No mode and no file to inherit from: the process umask still decides."""
    import stat

    old = os.umask(0o027)
    try:
        hp._atomic_write(tmp_path / "fresh.md", "x\n")
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "fresh.md").stat().st_mode) == 0o640


def test_write_run_report_never_follows_a_planted_tmp_symlink(tmp_path: Path) -> None:
    """#2903: the state root is service-writable; the run report must not write
    through a planted ``provision.json.tmp``."""
    victim = tmp_path / "root-only-file"
    victim.write_text("precious\n")
    root = tmp_path / "state"
    root.mkdir()
    (root / "provision.json.tmp").symlink_to(victim)
    report = hp.InstallReport(
        hermes_home=str(tmp_path / "hh"), venv=str(tmp_path / "venv"), agent_id="hermes-agent"
    )
    hp._write_run_report(report, root)
    assert victim.read_text() == "precious\n"
    assert "phases" in json.loads((root / "provision.json").read_text())


# ── root writers never follow a swapped parent directory (#2975) ───────────


def _service_root(tmp_path: Path) -> Path:
    """A stand-in for /var/lib/hal0: 2775, so the walk treats it as untrusted."""
    root = tmp_path / "var-lib-hal0"
    root.mkdir()
    root.chmod(0o2775)
    return root


@pytest.mark.parametrize("depth", ["parent", "grandparent"])
def test_atomic_write_refuses_a_symlinked_ancestor(tmp_path: Path, depth: str) -> None:
    """The service account renames ``.hermes`` and plants a link to a root dir."""
    from hal0.security.nofollow import UnsafePathError

    root = _service_root(tmp_path)
    victim = tmp_path / "etc"
    (victim / "profiles").mkdir(parents=True)
    (root / ".hermes").symlink_to(victim)
    target = root / ".hermes" / "config.yaml"
    if depth == "grandparent":
        target = root / ".hermes" / "profiles" / "SOUL.md"
    with pytest.raises(UnsafePathError):
        hp._atomic_write(target, "pwn: 1\n")
    assert sorted(p.name for p in victim.rglob("*")) == ["profiles"]


def test_atomic_write_if_changed_never_reads_through_a_link(tmp_path: Path) -> None:
    from hal0.security.nofollow import UnsafePathError

    root = _service_root(tmp_path)
    (tmp_path / "shadow").write_text("same\n")
    (root / "SOUL.md").symlink_to(tmp_path / "shadow")
    # A link is not "unchanged": it is replaced by a real file.
    with pytest.raises(UnsafePathError):
        hp.nofollow.read_text(root / "SOUL.md")
    _h, wrote = hp._atomic_write_if_changed(root / "SOUL.md", "same\n")
    assert wrote and not (root / "SOUL.md").is_symlink()
    assert (tmp_path / "shadow").read_text() == "same\n"


def test_merge_env_file_refuses_a_secrets_dir_the_service_account_owns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``secrets/`` swapped for a real tree the (non-root) service account owns."""
    from hal0.security import nofollow
    from hal0.security.nofollow import UnsafePathError

    root = _service_root(tmp_path)
    fake = root / "secrets" / "agents"
    fake.mkdir(parents=True)
    fake.chmod(0o700)
    (fake / "hermes.env").write_text("PLANTED=1\n")
    monkeypatch.setattr(nofollow, "_trusted_uids", lambda: frozenset({0}))
    with pytest.raises(UnsafePathError):
        hp._merge_env_file(fake / "hermes.env", {"API_SERVER_KEY": "s3cret"})
    assert (fake / "hermes.env").read_text() == "PLANTED=1\n"


def test_merge_env_file_refuses_a_symlinked_secrets_dir(tmp_path: Path) -> None:
    from hal0.security.nofollow import UnsafePathError

    root = _service_root(tmp_path)
    victim = tmp_path / "etc-cron"
    (victim / "agents").mkdir(parents=True)
    (root / "secrets").symlink_to(victim)
    with pytest.raises(UnsafePathError):
        hp._merge_env_file(root / "secrets" / "agents" / "hermes.env", {"K": "v"})
    assert list((victim / "agents").iterdir()) == []


def test_merge_env_file_happy_path_is_0600_and_merges(tmp_path: Path) -> None:
    import stat

    root = _service_root(tmp_path)
    vault = root / "secrets" / "agents" / "hermes.env"
    hp._merge_env_file(vault, {"A": "1"})
    hp._merge_env_file(vault, {"B": "2", "A": "3"})
    assert vault.read_text() == "A=3\nB=2\n"
    assert stat.S_IMODE(vault.stat().st_mode) == 0o600
    for d in (vault.parent, vault.parent.parent):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert sorted(p.name for p in vault.parent.iterdir()) == ["hermes.env"]


def test_persist_hermes_python_refuses_a_symlinked_config_dir(tmp_path: Path) -> None:
    from hal0.security.nofollow import UnsafePathError

    etc_hal0 = _service_root(tmp_path)
    victim = tmp_path / "victim"
    victim.mkdir()
    (etc_hal0 / "sub").symlink_to(victim)

    class _Runner:
        @staticmethod
        def run(_argv: list[str], **_kwargs: Any) -> Any:
            return type("Result", (), {"stdout": "3.14\n"})()

    with pytest.raises(UnsafePathError):
        hp._persist_hermes_python(
            "/opt/python3.14", etc_hal0 / "sub" / "hermes-python.env", runner=_Runner
        )
    assert list(victim.iterdir()) == []


# ── profile_api_keys phase ──────────────────────────────────────────────────


def _profile_env(home: Path, name: str, *, keyed: bool) -> None:
    pdir = home / "profiles" / name
    pdir.mkdir(parents=True)
    (pdir / ".env").write_text("API_SERVER_KEY=" + "k" * 40 + "\n" if keyed else "OTHER=1\n")


@pytest.fixture
def _no_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the root-only vault at a tmp path (absent unless a test writes it)."""
    vault = tmp_path / "vault" / "hermes.env"
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", vault)
    return vault


def test_phase_profile_api_keys_writes_missing_only(tmp_path: Path, _no_vault: Path) -> None:
    home = tmp_path / ".hermes"
    _profile_env(home, "coder", keyed=True)
    _profile_env(home, "nokey", keyed=False)
    (home / ".env").write_text("API_SERVER_KEY=" + "d" * 43 + "\n")
    state = hp.BootstrapState(venv=str(tmp_path / "venv"), hermes_home=str(home))
    ctx = hp._StepCtx(state=state, io=hp.InstallIO())

    out = hp._phase_profile_api_keys(ctx)
    assert out.status == hp.PhaseStatus.OK
    assert out.details["wrote"] == ["nokey"]
    assert out.details["present"] == ["coder"]
    assert out.details["default_key"] == "present"
    assert "API_SERVER_KEY=" in (home / "profiles" / "nokey" / ".env").read_text()

    again = hp._phase_profile_api_keys(ctx)
    assert again.status == hp.PhaseStatus.OK
    assert again.details["wrote"] == []
    assert again.details["present"] == ["coder", "nokey"]


def test_phase_profile_api_keys_mirrors_vault_key_into_default_env(
    tmp_path: Path, _no_vault: Path
) -> None:
    """Codex P1: a readable vault key (root --repair) lands in $HERMES_HOME/.env."""
    home = tmp_path / ".hermes"
    home.mkdir()
    _no_vault.parent.mkdir(parents=True)
    _no_vault.write_text("API_SERVER_KEY=" + "v" * 43 + "\n")
    state = hp.BootstrapState(venv=str(tmp_path / "venv"), hermes_home=str(home))
    out = hp._phase_profile_api_keys(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert out.status == hp.PhaseStatus.OK
    assert out.details == {"wrote": ["default"], "present": [], "default_key": "present"}
    assert (home / ".env").read_text() == "API_SERVER_KEY=" + "v" * 43 + "\n"


def test_phase_profile_api_keys_warns_when_default_key_unreadable(
    tmp_path: Path, _no_vault: Path
) -> None:
    """No readable default key (non-root pass, vault 0600 root): WARN naming the fix."""
    state = hp.BootstrapState(venv=str(tmp_path / "venv"), hermes_home=str(tmp_path / ".hermes"))
    out = hp._phase_profile_api_keys(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert out.status == hp.PhaseStatus.WARN
    assert out.reason is not None and "sudo hal0 agent reprovision hermes --repair" in out.reason
    assert out.details == {"wrote": [], "present": [], "default_key": "unreadable"}
    assert not (tmp_path / ".hermes" / ".env").exists()  # never mints a default key


def test_gateway_secrets_wire_mirrors_minted_key_into_hermes_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P1: the key minted for the vault is mirrored into $HERMES_HOME/.env
    while the provisioner (usually hal0, which cannot read the vault) still has it."""
    real_ensure = hp.ensure_gateway_api_server_key  # the helper below stubs it
    _dropin, _fake, io = _patch_dropin_to_tmp(tmp_path, monkeypatch)
    vault = tmp_path / "vault" / "hermes.env"
    vault.parent.mkdir()
    monkeypatch.setattr(hp, "HERMES_SECRETS_ENV", vault)
    monkeypatch.setattr(hp, "ensure_gateway_api_server_key", real_ensure)
    monkeypatch.setattr(hp, "_generate_api_server_key", lambda: "m" * 43)
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / ".env").write_text("OTHER=1\n")
    state = hp.BootstrapState(hermes_home=str(home))

    out = hp._phase_gateway_secrets_wire(hp._StepCtx(state=state, io=io))

    assert out.status == hp.PhaseStatus.OK
    assert out.details["api_server_key"]["outcome"] == "generated"
    assert out.details["api_server_key"]["mirrored"] is True
    assert "API_SERVER_KEY=" + "m" * 43 in vault.read_text()
    assert (home / ".env").read_text() == "OTHER=1\nAPI_SERVER_KEY=" + "m" * 43 + "\n"

    # Rerun: the vault key is present, nothing is minted, .env is untouched.
    again = hp._phase_gateway_secrets_wire(hp._StepCtx(state=state, io=io))
    assert again.details["api_server_key"]["outcome"] == "present"
    assert "mirrored" not in again.details["api_server_key"]
    assert (home / ".env").read_text().count("API_SERVER_KEY=") == 1


def test_phase_profile_api_keys_oserror_is_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hal0.agents.hermes_api as ha

    def _boom(_home: Path) -> list[str]:
        raise OSError("disk gone")

    monkeypatch.setattr(ha, "ensure_profile_keys", _boom)
    state = hp.BootstrapState(venv=str(tmp_path / "venv"), hermes_home=str(tmp_path / ".hermes"))
    out = hp._phase_profile_api_keys(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert out.status == hp.PhaseStatus.WARN
    assert out.details["errors"] == ["disk gone"]


def test_install_steps_order_has_profile_api_keys_after_gateway_secrets_wire() -> None:
    names = [n for n, _fn in hp._INSTALL_STEPS]
    assert names.index("profile_api_keys") == names.index("gateway_secrets_wire") + 1


# ── #2891: hal0's MCP servers in every named profile ─────────────────────────


def _profiles(tmp_path: Path, **profiles: str | None) -> Path:
    home = tmp_path / ".hermes"
    for name, text in profiles.items():
        d = home / "profiles" / name
        d.mkdir(parents=True)
        if text is not None:
            (d / "config.yaml").write_text(text, encoding="utf-8")
    return home


def test_profile_agent_id_is_sanitised_for_the_mcp_identity_charset() -> None:
    import re

    assert hp.profile_agent_id("coder") == "hermes__coder"
    rewritten = hp.profile_agent_id("my profile.v2")
    assert (
        rewritten.startswith("hermes__my_profile_v2-")
        and len(rewritten) == len("hermes__my_profile_v2-") + 8
    )
    assert len(hp.profile_agent_id("x" * 90)) == 64
    for name in ("coder", "my profile.v2", "x" * 90, "foo.bar", "foo_bar"):
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", hp.profile_agent_id(name))


def test_profile_agent_id_never_collides_after_normalisation() -> None:
    """Codex P1: ``foo.bar`` and ``foo_bar`` must not share one private memory bank."""
    ids = {hp.profile_agent_id(n) for n in ("foo.bar", "foo_bar", "foo-bar", "x" * 90, "x" * 91)}
    assert len(ids) == 5
    assert hp.profile_agent_id("foo_bar") == "hermes__foo_bar"  # a clean name keeps its id


def test_wire_named_profiles_wires_each_profile_as_its_own_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml

    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: "svc-key")
    home = _profiles(
        tmp_path,
        coder="model:\n  default: agent\nmcp_servers:\n  mine:\n    type: http\n    url: http://x\n",
        research="model:\n  default: agent\n",
    )
    (home / "profiles" / "hal0-brain").mkdir()
    (home / "profiles" / "hal0-brain" / "config.yaml").write_text("model:\n  default: a\n")

    report = hp.wire_named_profiles(home)
    assert report == {
        "wired": ["coder", "research"],
        "changed": ["coder", "research"],
        "skipped": {},
        "errors": {},
    }
    coder = yaml.safe_load((home / "profiles" / "coder" / "config.yaml").read_text())
    assert coder["mcp_servers"]["hal0-admin"]["headers"] == {
        "X-hal0-Agent": "hermes__coder",
        "Authorization": "Bearer svc-key",
    }
    assert coder["mcp_servers"]["hal0-memory"]["headers"]["X-hal0-Private"] == "1"
    assert coder["mcp_servers"]["hal0-memory"]["headers"]["X-hal0-Agent"] == "hermes__coder"
    assert coder["memory"]["provider"] == "hal0-memory"
    assert coder["mcp_servers"]["mine"] == {"type": "http", "url": "http://x"}  # kept
    assert coder["model"] == {"default": "agent"}
    research = yaml.safe_load((home / "profiles" / "research" / "config.yaml").read_text())
    assert research["mcp_servers"]["hal0-admin"]["headers"]["X-hal0-Agent"] == "hermes__research"
    # hal0-brain keeps its own phase and identity: untouched here.
    assert (
        home / "profiles" / "hal0-brain" / "config.yaml"
    ).read_text() == "model:\n  default: a\n"

    again = hp.wire_named_profiles(home)
    assert again["wired"] == ["coder", "research"] and again["changed"] == []


def test_wire_named_profiles_skips_absent_config_and_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, bare=None, lab="hal0:\n  wire_mcp: false\nmodel:\n  default: x\n")
    before = (home / "profiles" / "lab" / "config.yaml").read_bytes()
    report = hp.wire_named_profiles(home)
    assert report["wired"] == [] and report["errors"] == {}
    assert report["skipped"] == {"bare": "profile config absent", "lab": "opted out"}
    assert (home / "profiles" / "lab" / "config.yaml").read_bytes() == before
    assert hp.wire_named_profiles(tmp_path / "nowhere") == {
        "wired": [],
        "changed": [],
        "skipped": {},
        "errors": {},
    }


def test_wire_named_profiles_reports_a_broken_config_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, bad="model: [unclosed\n", listy="- not\n- a mapping\n", ok="a: 1\n")
    report = hp.wire_named_profiles(home)
    assert set(report["errors"]) == {"bad", "listy"}
    assert report["wired"] == ["ok"]


def test_phase_profile_mcp_wire_runs_after_profile_api_keys(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    names = [n for n, _fn in hp._INSTALL_STEPS]
    assert names.index("profile_mcp_wire") == names.index("profile_api_keys") + 1
    home = _profiles(tmp_path, coder="model:\n  default: agent\n")
    state = hp.BootstrapState(venv=str(tmp_path / "venv"), hermes_home=str(home))
    out = hp._phase_profile_mcp_wire(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert out.status == hp.PhaseStatus.OK
    assert out.details["changed"] is True and out.details["changed_profiles"] == ["coder"]
    assert hp._step_changed("profile_mcp_wire", out) is True


def test_phase_profile_mcp_wire_keeps_the_change_signal_on_a_partial_error(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex P2: a rewritten profile next to a broken one still counts as a change."""
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, bad="model: [unclosed\n", coder="model:\n  default: agent\n")
    state = hp.BootstrapState(venv=str(tmp_path / "venv"), hermes_home=str(home))
    out = hp._phase_profile_mcp_wire(hp._StepCtx(state=state, io=hp.InstallIO()))
    assert out.status == hp.PhaseStatus.OK  # warn-as-OK
    assert out.reason is not None and "bad" in out.reason
    assert out.details["errors"] == {"bad": out.details["errors"]["bad"]}
    assert hp._step_changed("profile_mcp_wire", out) is True


def test_wire_named_profiles_skips_dirs_hermes_would_not_serve(tmp_path: Path, monkeypatch) -> None:
    """A directory that breaks Hermes's profile id rule is never wired."""
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, **{"My Work": "a: 1\n", "ok": "a: 1\n", "-lead": "a: 1\n"})
    report = hp.wire_named_profiles(home)
    assert report["wired"] == ["ok"]
    assert report["skipped"] == {"My Work": "invalid profile name", "-lead": "invalid profile name"}
    assert (home / "profiles" / "My Work" / "config.yaml").read_text() == "a: 1\n"


def test_wire_named_profiles_keeps_comments_of_an_already_wired_profile(
    tmp_path: Path, monkeypatch
) -> None:
    """CodeRabbit: decide on parsed content, so Hermes's own formatting and comments survive."""
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, coder="model:\n  default: agent\n")
    assert hp.wire_named_profiles(home)["changed"] == ["coder"]
    cfg = home / "profiles" / "coder" / "config.yaml"
    commented = "# operator note\n" + cfg.read_text().replace("hal0-admin:", "hal0-admin:  # ours")
    cfg.write_text(commented)
    assert hp.wire_named_profiles(home)["changed"] == []
    assert cfg.read_text() == commented


def test_wire_named_profiles_refuses_a_falsy_non_mapping_document(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex P2: ``[]`` / ``false`` / ``0`` are errors, not an empty mapping to overwrite."""
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, a="[]\n", b="false\n", c="0\n", empty="")
    report = hp.wire_named_profiles(home)
    assert set(report["errors"]) == {"a", "b", "c"}
    assert report["wired"] == ["empty"]  # an empty file is an empty mapping
    assert (home / "profiles" / "a" / "config.yaml").read_text() == "[]\n"


def test_wire_named_profiles_locks_and_replaces_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2: one read-merge-write transaction under file_lock, replaced via
    tmp + rename with the mode kept, so Hermes's reconcile never sees a torn file."""
    import fcntl
    import os
    import stat

    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, coder="model:\n  default: agent\n")
    cfg = home / "profiles" / "coder" / "config.yaml"
    os.chmod(cfg, 0o640)
    locked: list[str] = []
    real_flock = fcntl.flock

    def spy(fd, op):
        if op == fcntl.LOCK_EX:
            locked.append(os.readlink(f"/proc/self/fd/{fd}"))
        return real_flock(fd, op)

    monkeypatch.setattr(fcntl, "flock", spy)
    replaced: list[tuple] = []
    real_replace = os.replace

    def spy_replace(src, dst, **kw):
        replaced.append((src, dst, sorted(kw)))
        return real_replace(src, dst, **kw)

    monkeypatch.setattr(hp.os, "replace", spy_replace)
    report = hp.wire_named_profiles(home)
    assert report["changed"] == ["coder"]
    assert locked == [str(cfg.parent / ".config.yaml.lock")]  # lock inside the verified dir
    # Renamed through the directory descriptor, from a random dot-name.
    assert len(replaced) == 1 and replaced[0][1] == "config.yaml"
    assert replaced[0][0].startswith(".config.yaml.")
    assert replaced[0][2] == ["dst_dir_fd", "src_dir_fd"]
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o640


def test_wire_named_profiles_never_follows_a_planted_tmp_symlink(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex P1: a root repair must not truncate whatever ``config.yaml.tmp`` points at."""
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, coder="model:\n  default: agent\n")
    victim = tmp_path / "root-only-file"
    victim.write_text("precious\n")
    (home / "profiles" / "coder" / "config.yaml.tmp").symlink_to(victim)
    assert hp.wire_named_profiles(home)["changed"] == ["coder"]
    assert victim.read_text() == "precious\n"
    assert (home / "profiles" / "coder" / "config.yaml.tmp").is_symlink()  # untouched
    leftovers = [
        p.name for p in (home / "profiles" / "coder").iterdir() if p.name.startswith(".config")
    ]
    assert leftovers == [".config.yaml.lock"]  # the lock file is the only hal0 artefact


def test_wire_named_profiles_redacts_yaml_errors(tmp_path: Path, monkeypatch) -> None:
    """Codex P2: a YAML error near an Authorization line must not quote the bearer."""
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(
        tmp_path,
        bad="mcp_servers:\n  hal0-admin:\n    headers:\n      Authorization: Bearer SECRET-TOKEN-VALUE\n   broken: [\n",
    )
    report = hp.wire_named_profiles(home)
    assert "bad" in report["errors"]
    assert "SECRET-TOKEN-VALUE" not in report["errors"]["bad"]
    assert "line" in report["errors"]["bad"]


def test_wire_named_profiles_contains_invalid_utf8_to_that_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, ok="a: 1\n", zed="a: 1\n")
    (home / "profiles" / "bad").mkdir()
    (home / "profiles" / "bad" / "config.yaml").write_bytes(b"model: \xff\xfe\n")
    report = hp.wire_named_profiles(home)
    assert "bad" in report["errors"] and report["wired"] == ["ok", "zed"]


def test_wire_named_profiles_never_follows_symlinked_profiles_or_configs(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex P1: a root repair must not rewrite a YAML file outside HERMES_HOME."""
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, ok="a: 1\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.yaml").write_text("root: owned\n")
    (home / "profiles" / "planted").symlink_to(outside, target_is_directory=True)
    (home / "profiles" / "linked").mkdir()
    (home / "profiles" / "linked" / "config.yaml").symlink_to(outside / "config.yaml")
    report = hp.wire_named_profiles(home)
    assert report["wired"] == ["ok"]
    assert report["skipped"]["planted"] == "symlink"
    assert report["errors"]["linked"] == "config.yaml is a symlink"
    assert (outside / "config.yaml").read_text() == "root: owned\n"


def test_wire_named_profiles_reports_an_unreadable_profile_root(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex P2: an enumeration failure is an error, not a clean empty report."""
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, ok="a: 1\n")

    import os

    real_open = os.open

    def boom(path, flags, *a, **kw):
        if path == "profiles" and "dir_fd" in kw:  # opened relative to the home fd
            raise PermissionError("denied")
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(hp.os, "open", boom)
    report = hp.wire_named_profiles(home)
    assert report["errors"] == {"profiles/": "denied"} and report["wired"] == []
    assert hp.wire_named_profiles(tmp_path / "absent")["errors"] == {}


def test_named_profile_memory_timeout_matches_the_default_profile(monkeypatch) -> None:
    """Codex P1: memory_reflect runs minutes; a 30 s client budget cancelled it."""
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    canonical = next(s["timeout"] for s in hp._default_mcp_servers() if s["name"] == "hal0-memory")
    assert canonical >= 900
    assert hp._build_profile_mcp_servers("hermes__coder")["hal0-memory"]["timeout"] == canonical
    assert hp._build_brain_profile_mcp_servers()["hal0-memory"]["timeout"] == canonical


def test_wire_named_profiles_refuses_a_symlinked_profiles_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    outside = tmp_path / "outside" / "victim"
    outside.mkdir(parents=True)
    (outside / "config.yaml").write_text("root: owned\n")
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "profiles").symlink_to(tmp_path / "outside", target_is_directory=True)
    report = hp.wire_named_profiles(home)
    assert report == {
        "wired": [],
        "changed": [],
        "skipped": {},
        "errors": {"profiles/": "profiles/ is a symlink"},
    }
    assert (outside / "config.yaml").read_text() == "root: owned\n"


def test_replace_profile_config_sets_metadata_on_the_descriptor(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex P1: chmod/chown on the open fd, never on the temp path."""
    import os

    cfg = tmp_path / "config.yaml"
    cfg.write_text("a: 1\n")
    os.chmod(cfg, 0o640)
    calls: list[str] = []
    monkeypatch.setattr(hp.os, "chmod", lambda *a, **k: calls.append("chmod-path"))
    monkeypatch.setattr(hp.os, "chown", lambda *a, **k: calls.append("chown-path"))
    real_fchmod = os.fchmod
    monkeypatch.setattr(
        hp.os, "fchmod", lambda fd, mode: (calls.append("fchmod"), real_fchmod(fd, mode))
    )
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)
    monkeypatch.setattr(hp.os, "fchown", lambda fd, uid, gid: calls.append("fchown"))
    dir_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        hp._replace_profile_config_at(dir_fd, "a: 2\n", cfg.stat())
    finally:
        os.close(dir_fd)
    assert cfg.read_text() == "a: 2\n"
    assert calls == ["fchmod", "fchown"]
    assert (cfg.stat().st_mode & 0o777) == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["config.yaml"]  # no temp file left


def test_wire_named_profiles_refuses_a_fifo_config(tmp_path: Path, monkeypatch) -> None:
    """Codex P2: a FIFO config.yaml would block the sweep for good."""
    import os

    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, ok="a: 1\n", pipe=None)
    os.mkfifo(home / "profiles" / "pipe" / "config.yaml")
    report = hp.wire_named_profiles(home)
    assert report["errors"]["pipe"] == "config.yaml is not a regular file"
    assert report["wired"] == ["ok"]


def test_wire_named_profiles_contains_a_stat_failure_to_that_profile(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex P2: one untraversable entry must not end the sweep for the others."""
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, bad="a: 1\n", ok="a: 1\n")
    import os

    real_open = os.open

    def flaky(path, flags, *a, **kw):
        if path == "bad" and "dir_fd" in kw:
            raise PermissionError("denied")
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(hp.os, "open", flaky)
    report = hp.wire_named_profiles(home)
    assert report["errors"] == {"bad": "denied"} and report["wired"] == ["ok"]


def test_wire_named_profiles_refuses_a_symlinked_hermes_home(tmp_path: Path, monkeypatch) -> None:
    """Codex P1: a home replaced by a link must not steer a root repair elsewhere."""
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    real_home = _profiles(tmp_path / "real", coder="a: 1\n")
    link = tmp_path / ".hermes"
    link.symlink_to(real_home, target_is_directory=True)
    report = hp.wire_named_profiles(link)
    assert report["errors"] == {"profiles/": "HERMES_HOME is a symlink"} and report["wired"] == []
    assert (real_home / "profiles" / "coder" / "config.yaml").read_text() == "a: 1\n"


def test_wire_named_profiles_rereads_a_config_edited_under_the_sweep(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex P2: an operator save between read and replace is merged, not overwritten."""
    import yaml

    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, coder="model:\n  default: agent\n")
    cfg = home / "profiles" / "coder" / "config.yaml"
    real_open = hp._open_config_nofollow
    opens: list[int] = []

    def racing_open(dir_fd):
        opens.append(dir_fd)
        if len(opens) == 2:  # the pre-replace probe: an operator saved meanwhile
            cfg.write_text("model:\n  default: agent\noperator: kept\n")
        return real_open(dir_fd)

    monkeypatch.setattr(hp, "_open_config_nofollow", racing_open)
    report = hp.wire_named_profiles(home)
    assert report["changed"] == ["coder"] and report["errors"] == {}
    data = yaml.safe_load(cfg.read_text())
    assert data["operator"] == "kept"
    assert data["mcp_servers"]["hal0-admin"]["headers"]["X-hal0-Agent"] == "hermes__coder"


def test_wire_named_profiles_gives_up_on_a_config_that_keeps_changing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, coder="model:\n  default: agent\n")
    cfg = home / "profiles" / "coder" / "config.yaml"
    real_open = hp._open_config_nofollow
    n = [0]

    def churn(dir_fd):
        n[0] += 1
        if n[0] % 2 == 0:
            cfg.write_text(f"model:\n  default: agent\nrev: {n[0]}\n")
        return real_open(dir_fd)

    monkeypatch.setattr(hp, "_open_config_nofollow", churn)
    report = hp.wire_named_profiles(home)
    assert report["errors"] == {"coder": "config.yaml kept changing during the sweep"}


def test_wire_named_profiles_refuses_to_stamp_a_renamed_profile(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex P2: a sweep that opened ``alpha`` must not write hermes__alpha into ``beta``."""
    monkeypatch.setattr("hal0.service_identity.service_key", lambda prefer=None: None)
    home = _profiles(tmp_path, alpha="model:\n  default: agent\n")
    monkeypatch.setattr(hp, "_same_inode", lambda root_fd, name, dir_fd: False)
    report = hp.wire_named_profiles(home)
    assert report["errors"] == {"alpha": "profile renamed during the sweep"}
    assert "mcp_servers" not in (home / "profiles" / "alpha" / "config.yaml").read_text()


def test_profile_lock_created_by_root_is_owned_like_the_directory(
    tmp_path: Path, monkeypatch
) -> None:
    """Codex P2: a root pass must not leave a lock the hal0-api sweeps cannot open."""
    import os

    d = tmp_path / "coder"
    d.mkdir()
    dir_fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    real_fstat = os.fstat
    fake_dir = os.stat_result((0o40700, 1, 1, 1, 4242, 4343, 0, 0, 0, 0))
    chowned: list[tuple] = []
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)
    monkeypatch.setattr(hp.os, "fstat", lambda fd: fake_dir if fd == dir_fd else real_fstat(fd))
    monkeypatch.setattr(hp.os, "fchown", lambda fd, uid, gid: chowned.append((uid, gid)))
    try:
        with hp._profile_lock(dir_fd):
            pass
    finally:
        os.close(dir_fd)
    assert chowned == [(4242, 4343)]
    assert (d / ".config.yaml.lock").exists()


# ---------------------------------------------------------------------------
# hermes_doctor smoke probe: provider-key advisories are not failures (#2951)
# ---------------------------------------------------------------------------

_DOCTOR_TAIL = """
◆ Memory Provider

────────────────────────────────────────────────────────────
  Found {n} issue(s) to address:

{issues}
  Tip: run 'hermes doctor --fix' to auto-fix what's possible.

"""


def _doctor_output(*issues: str) -> str:
    numbered = "".join(f"  {i}. {issue}\n" for i, issue in enumerate(issues, 1))
    return _DOCTOR_TAIL.format(n=len(issues), issues=numbered)


def _doctor_probe(tmp_path: Path, returncode: int, stdout: str) -> tuple[bool, str]:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "hermes").write_text("")
    state = hp.BootstrapState(hermes_home=str(tmp_path / "hh"), venv=str(venv))

    def _run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        assert argv[1:] == ["doctor"]  # never --live: that would load a model
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    return hp._smoke_hermes_doctor(state, hp.InstallIO(run=_run))


def test_smoke_hermes_doctor_passes_on_rc0(tmp_path: Path) -> None:
    ok, detail = _doctor_probe(tmp_path, 0, "  All checks passed! 🎉\n")
    assert ok
    assert detail == "rc=0"


def test_smoke_hermes_doctor_records_upstream_issues_as_advisory(tmp_path: Path) -> None:
    """Upstream exits 1 whenever it lists an issue, and on hal0 boxes it always
    lists some that describe a standalone install (#2951): a readable list is
    recorded in the detail, never a failure."""
    issues = (
        "Run 'hermes setup' to configure API keys",
        "Missing ~/.local/bin/hermes symlink — run 'hermes doctor --fix'",
        "Fix terminal.backend in config.yaml, or install/enable the plugin that provides it",
        "web workspace has 4 npm vulnerabilities; wait for an upstream lockfile fix",
    )
    ok, detail = _doctor_probe(tmp_path, 1, _doctor_output(*issues))
    assert ok
    assert detail == "rc=1, 4 advisory issue(s): " + "; ".join(issues)


def test_smoke_hermes_doctor_strips_colour_codes(tmp_path: Path) -> None:
    out = _doctor_output("\x1b[33mRun 'hermes setup' to configure API keys\x1b[0m")
    ok, detail = _doctor_probe(tmp_path, 1, out)
    assert ok
    assert detail == "rc=1, 1 advisory issue(s): Run 'hermes setup' to configure API keys"


@pytest.mark.parametrize("stdout", ["", "Traceback (most recent call last):\n  boom\n"])
def test_smoke_hermes_doctor_fails_without_an_issue_list(tmp_path: Path, stdout: str) -> None:
    """rc 1 is also what a crash returns; without the summary it is a failure."""
    ok, detail = _doctor_probe(tmp_path, 1, stdout)
    assert not ok
    assert "no issue list" in detail


def test_smoke_hermes_doctor_fails_on_other_exit_codes(tmp_path: Path) -> None:
    out = _doctor_output("Run 'hermes setup' to configure API keys")
    ok, detail = _doctor_probe(tmp_path, 2, out)
    assert not ok
    assert detail == "rc=2"


def test_smoke_hermes_doctor_fails_when_the_list_is_shorter_than_its_count(tmp_path: Path) -> None:
    out = _doctor_output("Run 'hermes setup' to configure API keys", "", "another issue")
    ok, detail = _doctor_probe(tmp_path, 1, out)
    assert not ok
    assert "no issue list" in detail


def test_smoke_hermes_doctor_reads_the_last_summary(tmp_path: Path) -> None:
    echoed = _doctor_output("echoed issue")
    real = _doctor_output("real one", "real two")
    ok, detail = _doctor_probe(tmp_path, 1, echoed + real)
    assert ok
    assert detail == "rc=1, 2 advisory issue(s): real one; real two"


# -- hermes_md_contains_primary: evidence, not a substring (#2947) -----------


def _render_hermes_md(model_default: str, primary: dict[str, Any] | None) -> str:
    return hp._render_template(
        "HERMES.md.j2",
        env={},
        hal0_version="1.0",
        hermes_version="0.1",
        primary=primary,
        model_default=model_default,
        chat_slots=[],
        peer_agents=[],
        dashboard_url="http://hal0.local:8080",
    )


def _md_probe_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, default: str | None
) -> hp.BootstrapState:
    import yaml

    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "HERMES.md").write_text(body, encoding="utf-8")
    monkeypatch.setattr(hp, "ETC_HAL0_DIR", etc)
    hh = tmp_path / "hh"
    hh.mkdir()
    model = {"default": default} if default else {}
    (hh / "config.yaml").write_text(yaml.safe_dump({"model": model}), encoding="utf-8")
    return hp.BootstrapState(hermes_home=str(hh))


def test_hermes_md_primary_probe_rejects_body_with_only_the_skills_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``hal0/agent`` is a substring of ``/etc/hal0/agent-skills/``; that prose is
    not evidence the file names the primary model."""
    body = "# Where you are\n\nSkills live in /etc/hal0/agent-skills/\n"
    assert "hal0/agent" in body  # the old substring test would have passed
    state = _md_probe_setup(tmp_path, monkeypatch, body, "hal0/agent")
    passed, detail = hp._smoke_hermes_md_contains_primary(state, _box_io())
    assert passed is False
    assert "Primary model" in detail


def test_hermes_md_primary_probe_skips_on_none_wired_yet_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No dispatchable LLM slot: config.yaml still says ``hal0/agent`` but the
    real render says ``_none wired yet_``. Unknown state skips, never fails."""
    body = _render_hermes_md("", None)
    assert "- Primary model: _none wired yet_" in body
    state = _md_probe_setup(tmp_path, monkeypatch, body, "hal0/agent")
    passed, detail = hp._smoke_hermes_md_contains_primary(state, _box_io())
    assert passed is None
    assert "no primary" in detail


def test_hermes_md_primary_probe_passes_on_rendered_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary = {"alias": "agent", "model_id": "m", "backend_url": "http://x"}
    body = _render_hermes_md("hal0/agent", primary)
    state = _md_probe_setup(tmp_path, monkeypatch, body, "hal0/agent")
    passed, _ = hp._smoke_hermes_md_contains_primary(state, _box_io())
    assert passed is True


def test_hermes_md_primary_probe_fails_on_stale_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary = {"alias": "agent", "model_id": "old", "backend_url": "http://x"}
    body = _render_hermes_md("old-model", primary)
    state = _md_probe_setup(tmp_path, monkeypatch, body, "new-model")
    passed, detail = hp._smoke_hermes_md_contains_primary(state, _box_io())
    assert passed is False
    assert "old-model" in detail


def test_hermes_md_primary_probe_skips_without_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _md_probe_setup(tmp_path, monkeypatch, _render_hermes_md("", None), None)
    passed, detail = hp._smoke_hermes_md_contains_primary(state, _box_io())
    assert passed is None
    assert "no primary" in detail


def test_install_phase_rebuilds_the_update_target_override_not_the_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`reprovision --repair` after `hal0 update component hermes --target` keeps the override."""
    from hal0.components import hermes_arm

    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        hp, "HERMES_CLI_INSTALL_PATH", tmp_path / "usr" / "local" / "bin" / "hermes"
    )
    prev = "d923a5eb487a17c0a68801bba9ef0f56c81040a8"
    hermes_arm.override_path().parent.mkdir(parents=True, exist_ok=True)
    hermes_arm.override_path().write_text(prev + "\n", encoding="utf-8")
    state = hp.BootstrapState(venv=str(tmp_path / "venv"), hermes_home=str(tmp_path / "h"))
    calls: list[dict[str, Any]] = []
    hp._phase_install(
        hp._StepCtx(state=state, io=hp.InstallIO(ensure_release=fake_ensure_release(calls)))
    )
    assert calls[0]["ref"] == prev
    # An override this release no longer vets fails the step instead of building the pin.
    calls.clear()
    monkeypatch.setattr(
        hp, "VETTED_HERMES_REFS", frozenset({"4d04303818b61bf3aa09278af48356315515965b"})
    )
    out = hp._phase_install(
        hp._StepCtx(state=state, io=hp.InstallIO(ensure_release=fake_ensure_release(calls)))
    )
    assert out.status == hp.PhaseStatus.FAIL and "--clear-override" in (out.reason or "")
    assert calls == []
