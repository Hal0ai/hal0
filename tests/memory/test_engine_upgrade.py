"""Unit tests for the memory-engine venv convergence pass.

``upgrade_memory_engine`` rebuilds the hindsight venv aside, snapshots the
embedded postgres dir, swaps, postchecks ``/health`` + ``/version`` and rolls
both back on failure. Everything privileged or slow is injected (seam, runner,
http_get, hs_dir) so the whole state machine runs against a tmp_path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hal0.memory.engine_upgrade import (
    HINDSIGHT_API_PIN,
    upgrade_memory_engine,
)

OLD_VERSION = "0.8.4"


class FakeSeam:
    """Record every systemctl verb; optionally fail a specific one."""

    def __init__(self, fail_verbs: set[str] | None = None):
        self.calls: list[tuple[str, ...]] = []
        self.fail_verbs = fail_verbs or set()

    def systemctl(self, *args: str, check: bool = True, timeout: float | None = None):
        self.calls.append(args)
        verb = args[1]
        if verb in self.fail_verbs and check:
            raise subprocess.CalledProcessError(1, list(args))
        return subprocess.CompletedProcess(list(args), 0, "", "")

    @property
    def verbs(self) -> list[str]:
        return [c[1] for c in self.calls]


def _make_venv_tree(venv: Path) -> None:
    (venv / "bin").mkdir(parents=True)
    for name in ("python", "pip", "hindsight-api"):
        f = venv / "bin" / name
        f.write_text("#!/bin/sh\n")
        f.chmod(0o755)


class FakeRunner:
    """Dispatch the pass's subprocess calls against the tmp tree.

    * ``python -m venv X`` materialises a venv skeleton at X;
    * version probes answer by which venv's interpreter is asked;
    * pip installs return ``pip_rc``;
    * ``cp -a`` really copies (the rollback assertions depend on it).
    """

    def __init__(self, *, pip_rc: int = 0, new_version: str = HINDSIGHT_API_PIN):
        self.pip_rc = pip_rc
        self.new_version = new_version
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        args = [str(a) for a in args]
        self.calls.append(args)
        if len(args) >= 4 and args[1:3] == ["-m", "venv"]:
            _make_venv_tree(Path(args[3]))
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "cp" and args[1] == "-a":
            shutil.copytree(args[2], args[3])
            return subprocess.CompletedProcess(args, 0, "", "")
        if len(args) >= 3 and args[1] == "-c" and "importlib.metadata" in args[2]:
            version = self.new_version if ".venv.new" in args[0] else OLD_VERSION
            return subprocess.CompletedProcess(args, 0, f"{version}\n", "")
        if len(args) >= 3 and args[1] == "-c" and "version_info" in args[2]:
            return subprocess.CompletedProcess(args, 0, "3.12\n", "")
        if "pip" in Path(args[0]).name and "install" in args:
            if "wheel" in args:  # the pip/wheel bootstrap is always best-effort
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, self.pip_rc, "", "resolver says no")
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture()
def hs(tmp_path: Path) -> Path:
    hs = tmp_path / "hindsight"
    _make_venv_tree(hs / ".venv")
    (hs / ".pg0").mkdir()
    (hs / ".pg0" / "PG_VERSION").write_text("18\n")
    return hs


def _healthy(url: str):
    if url.endswith("/version"):
        return {"api_version": HINDSIGHT_API_PIN}
    return {"status": "healthy"}


def test_skip_env_hatch(hs, monkeypatch):
    monkeypatch.setenv("HAL0_SKIP_HINDSIGHT", "1")
    result = upgrade_memory_engine(seam=FakeSeam(), runner=FakeRunner(), hs_dir=hs)
    assert result["status"] == "skipped"


def test_fresh_install_is_not_our_job(tmp_path):
    seam = FakeSeam()
    result = upgrade_memory_engine(seam=seam, runner=FakeRunner(), hs_dir=tmp_path / "empty")
    assert result["status"] == "skipped"
    assert seam.calls == []


def test_converged_prunes_all_but_newest_debris(hs):
    runner = FakeRunner()
    runner.new_version = HINDSIGHT_API_PIN

    def versioned(args, **kwargs):  # the existing venv already reports the pin
        args = [str(a) for a in args]
        if len(args) >= 3 and "importlib.metadata" in args[2]:
            return subprocess.CompletedProcess(args, 0, f"{HINDSIGHT_API_PIN}\n", "")
        return runner(args, **kwargs)

    for name, age in (
        (".venv.old-0.7.2", 100),
        (".venv.old-0.8.4", 50),
        (".pg0.pre-0.7.2", 100),
        (".pg0.pre-0.8.4", 50),
    ):
        d = hs / name
        d.mkdir()
        stamp = d.stat().st_mtime - age
        os.utime(d, (stamp, stamp))

    seam = FakeSeam()
    result = upgrade_memory_engine(seam=seam, runner=versioned, hs_dir=hs)
    assert result == {"status": "converged", "version": HINDSIGHT_API_PIN}
    assert seam.calls == []
    assert sorted(p.name for p in hs.glob(".venv.old-*")) == [".venv.old-0.8.4"]
    assert sorted(p.name for p in hs.glob(".pg0.pre-*")) == [".pg0.pre-0.8.4"]


def test_boot_mode_reports_stale_and_touches_nothing(hs):
    seam = FakeSeam()
    result = upgrade_memory_engine(upgrade=False, seam=seam, runner=FakeRunner(), hs_dir=hs)
    assert result["status"] == "stale"
    assert result["installed"] == OLD_VERSION
    assert result["pinned"] == HINDSIGHT_API_PIN
    assert seam.calls == []
    assert not (hs / ".venv.new").exists()


def test_build_failure_never_stops_the_engine(hs):
    seam = FakeSeam()
    result = upgrade_memory_engine(seam=seam, runner=FakeRunner(pip_rc=1), hs_dir=hs)
    assert result["status"] == "build_failed"
    assert seam.calls == []  # engine never stopped
    assert not (hs / ".venv.new").exists()  # partial build removed
    assert (hs / ".venv" / "bin" / "hindsight-api").exists()  # old venv intact


def test_wrong_built_version_is_a_build_failure(hs):
    seam = FakeSeam()
    runner = FakeRunner(new_version="0.9.1")  # pip "succeeded" but built the wrong thing
    result = upgrade_memory_engine(seam=seam, runner=runner, hs_dir=hs)
    assert result["status"] == "build_failed"
    assert seam.calls == []


def test_happy_path_swaps_snapshots_and_postchecks(hs):
    seam = FakeSeam()
    result = upgrade_memory_engine(seam=seam, runner=FakeRunner(), http_get=_healthy, hs_dir=hs)
    assert result["status"] == "upgraded"
    assert result["from"] == OLD_VERSION and result["to"] == HINDSIGHT_API_PIN
    assert seam.verbs == ["stop", "start"]
    assert (hs / f".pg0.pre-{OLD_VERSION}" / "PG_VERSION").exists()  # snapshot taken
    assert (hs / f".venv.old-{OLD_VERSION}" / "bin" / "hindsight-api").exists()
    assert (hs / ".venv" / "bin" / "hindsight-api").exists()
    assert not (hs / ".venv.new").exists()


class _NoFreeDisk:
    free = 0


def test_snapshot_disk_full_aborts_and_restarts_old_engine(hs, monkeypatch):
    monkeypatch.setattr("hal0.memory.engine_upgrade.shutil.disk_usage", lambda _: _NoFreeDisk())
    seam = FakeSeam()
    result = upgrade_memory_engine(seam=seam, runner=FakeRunner(), http_get=_healthy, hs_dir=hs)
    assert result["status"] == "snapshot_failed"
    assert seam.verbs == ["stop", "start"]  # old engine brought back
    assert not (hs / ".venv.new").exists()
    assert not (hs / f".pg0.pre-{OLD_VERSION}").exists()
    assert (hs / ".venv" / "bin" / "hindsight-api").exists()


def test_version_mismatch_after_start_rolls_back_venv_and_pg(hs):
    def wrong_version(url: str):
        if url.endswith("/version"):
            return {"api_version": "0.0.0"}
        return {"status": "healthy"}

    seam = FakeSeam()
    result = upgrade_memory_engine(
        seam=seam, runner=FakeRunner(), http_get=wrong_version, hs_dir=hs
    )
    assert result["status"] == "rolled_back"
    assert result["old_engine_healthy"] is True
    # stop, start (new engine), stop (rollback), start (old engine)
    assert seam.verbs == ["stop", "start", "stop", "start"]
    assert (hs / f".venv.failed-{HINDSIGHT_API_PIN}").exists()  # forensics kept
    assert (hs / ".venv" / "bin" / "hindsight-api").exists()  # old venv restored
    assert (hs / f".pg0.pre-{OLD_VERSION}").exists()  # snapshot preserved for retry
    assert (hs / ".pg0" / "PG_VERSION").exists()  # data dir restored by copy


def test_health_timeout_rolls_back(hs, monkeypatch):
    monkeypatch.setattr("hal0.memory.engine_upgrade._HEALTH_POLL_TOTAL_S", 0)

    def old_engine_only(url: str):
        return {"status": "healthy"}  # answers once the OLD engine is back

    seam = FakeSeam()
    result = upgrade_memory_engine(
        seam=seam, runner=FakeRunner(), http_get=old_engine_only, hs_dir=hs
    )
    assert result["status"] == "rolled_back"
    assert "failed /health" in result["error"]
    assert (hs / ".venv" / "bin" / "hindsight-api").exists()


def test_midswap_crash_recovers_from_venv_old(hs):
    os.rename(hs / ".venv", hs / f".venv.old-{OLD_VERSION}")  # crash left no .venv
    seam = FakeSeam()
    result = upgrade_memory_engine(seam=seam, runner=FakeRunner(), http_get=_healthy, hs_dir=hs)
    assert result["status"] == "upgraded"  # restored, then upgraded normally


def test_root_mode_chowns_artifacts(hs, monkeypatch):
    chowned: list[str] = []
    monkeypatch.setattr("hal0.memory.engine_upgrade.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "hal0.memory.engine_upgrade.shutil.chown",
        lambda p, user=None, group=None: chowned.append(str(p)),
    )
    result = upgrade_memory_engine(
        seam=FakeSeam(), runner=FakeRunner(), http_get=_healthy, hs_dir=hs
    )
    assert result["status"] == "upgraded"
    assert any(".venv.new" in p for p in chowned)  # built venv handed to hal0
    assert any(f".pg0.pre-{OLD_VERSION}" in p for p in chowned)  # snapshot too
