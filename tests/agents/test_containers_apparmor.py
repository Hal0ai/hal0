"""RATIFIED 2026-07-18 (deliverable 4) — convergent podman AppArmor preflight.

Detect the unconfined-LXC apparmor profile-load failure from the podman SMOKE
FAILURE (halo150 R4), write the containers.conf fix once, retry — all against
recorded fakes, no real podman or /etc writes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hal0.agents import containers_apparmor as ca


def _proc(returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


_APPARMOR_STDERR = (
    "Error: default OCI runtime spec: install profile containers-default apparmor: exit status 243"
)


def _recorder(sequence: list[subprocess.CompletedProcess]):
    calls: list[list[str]] = []
    it = iter(sequence)

    def _run(argv, **_kw):
        calls.append(list(argv))
        return next(it)

    return _run, calls


def test_smoke_passes_leaves_config_untouched(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    run, calls = _recorder([_proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    assert result.outcome == "ok"
    assert not result.wrote_config
    assert not conf.exists()
    assert len(calls) == 1  # only the initial smoke


def test_apparmor_failure_writes_config_and_retries(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    run, calls = _recorder([_proc(243, _APPARMOR_STDERR), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    assert result.outcome == "fixed"
    assert result.wrote_config
    assert conf.exists()
    assert 'apparmor_profile = "unconfined"' in conf.read_text()
    assert "[containers]" in conf.read_text()
    assert len(calls) == 2  # smoke, then retry after the write


def test_unrelated_failure_is_not_touched(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    run, _calls = _recorder([_proc(125, "Error: unable to pull image: not found")])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    assert result.outcome == "unrelated"
    assert not result.wrote_config
    assert not conf.exists()


def test_idempotent_when_already_unconfined(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    conf.write_text('[containers]\napparmor_profile = "unconfined"\n')
    run, _calls = _recorder([_proc(243, _APPARMOR_STDERR), _proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run, conf_path=conf)
    # Config already carried the fix → no rewrite, but retry still attempted.
    assert result.outcome == "already"
    assert not result.wrote_config


def test_second_run_after_fix_is_ok(tmp_path: Path) -> None:
    # Convergence: after the box is fixed, a rerun re-smokes, passes, no-ops.
    conf = tmp_path / "containers.conf"
    run1, _ = _recorder([_proc(243, _APPARMOR_STDERR), _proc(0)])
    ca.ensure_podman_apparmor_usable(run=run1, conf_path=conf)
    run2, calls2 = _recorder([_proc(0)])
    result = ca.ensure_podman_apparmor_usable(run=run2, conf_path=conf)
    assert result.outcome == "ok"
    assert len(calls2) == 1


def test_write_preserves_existing_containers_section(tmp_path: Path) -> None:
    conf = tmp_path / "containers.conf"
    conf.write_text('[containers]\nlog_driver = "journald"\n[network]\ndns = ["1.1.1.1"]\n')
    ca._write_apparmor_unconfined(conf)
    text = conf.read_text()
    assert 'log_driver = "journald"' in text
    assert 'apparmor_profile = "unconfined"' in text
    assert "[network]" in text
    assert ca._apparmor_already_unconfined(conf)


def test_no_podman_reports_cleanly(tmp_path: Path) -> None:
    def _run(argv, **_kw):
        raise FileNotFoundError("podman")

    result = ca.ensure_podman_apparmor_usable(run=_run, conf_path=tmp_path / "c.conf")
    assert result.outcome == "no_podman"
