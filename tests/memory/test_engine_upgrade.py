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
        # Console scripts carry pip's ABSOLUTE shebang naming the venv they
        # were built in — the shape the shebang-retarget regression rides on.
        f.write_text(f"#!{venv}/bin/python3.13\n" if name != "python" else "#!/bin/sh\n")
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


def test_swapped_scripts_shebang_points_at_the_final_venv(hs):
    """pip's absolute shebangs name .venv.new; after the rename swap they
    must name .venv or every entry point (systemd ExecStart included) execs
    a deleted interpreter — found by the component-updates engine gate."""
    result = upgrade_memory_engine(
        seam=FakeSeam(), runner=FakeRunner(), http_get=_healthy, hs_dir=hs
    )
    assert result["status"] == "upgraded"
    launcher = (hs / ".venv" / "bin" / "hindsight-api").read_text()
    assert ".venv.new" not in launcher
    assert f"#!{hs}/.venv/bin/python3.13" in launcher


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


def _wrong_version(url: str):
    if url.endswith("/version"):
        return {"api_version": "0.0.0"}
    return {"status": "healthy"}


def test_retry_replaces_a_stale_snapshot_from_an_earlier_failed_attempt(hs):
    # An earlier attempt failed and rolled back, leaving its snapshot; the
    # old engine then took writes. A second failed attempt must restore THOSE
    # writes, not the first attempt's copy (Codex review on #2612).
    stale = hs / f".pg0.pre-{OLD_VERSION}"
    stale.mkdir()
    (stale / "PG_VERSION").write_text("18\n")
    (stale / "marker").write_text("first attempt")
    (hs / ".pg0" / "marker").write_text("written after the first rollback")

    result = upgrade_memory_engine(
        seam=FakeSeam(), runner=FakeRunner(), http_get=_wrong_version, hs_dir=hs
    )
    assert result["status"] == "rolled_back"
    assert (stale / "marker").read_text() == "written after the first rollback"
    assert (hs / ".pg0" / "marker").read_text() == "written after the first rollback"
    assert not (hs / ".pg0.snapshot-new").exists()


class RestoreFails(FakeRunner):
    """``cp -a <snapshot> .pg0`` fails (the rollback restore); other copies work."""

    def __call__(self, args, **kwargs):
        args = [str(a) for a in args]
        if args[0] == "cp" and ".pg0.pre-" in args[2]:
            self.calls.append(args)
            return subprocess.CompletedProcess(args, 1, "", "Input/output error")
        return super().__call__(args, **kwargs)


def test_failed_restore_marks_the_db_and_leaves_the_engine_stopped(hs):
    # Starting an engine on a half-restored .pg0 could initialise an empty
    # database and serve it (Codex review on #2612).
    seam = FakeSeam()
    result = upgrade_memory_engine(
        seam=seam, runner=RestoreFails(), http_get=_wrong_version, hs_dir=hs
    )
    assert result["status"] == "rolled_back"
    assert result["old_engine_healthy"] is False
    assert seam.verbs == ["stop", "start", "stop"]  # no start on the bad .pg0
    marker = hs / ".pg0.restore-failed"
    assert marker.read_text().strip() == str(hs / f".pg0.pre-{OLD_VERSION}")
    assert (hs / f".pg0.pre-{OLD_VERSION}" / "PG_VERSION").exists()


def test_next_attempt_restores_the_marked_snapshot_before_anything_else(hs):
    good = hs / f".pg0.pre-{OLD_VERSION}"
    good.mkdir()
    (good / "PG_VERSION").write_text("18\n")
    (good / "marker").write_text("last known good")
    shutil.rmtree(hs / ".pg0")
    (hs / ".pg0").mkdir()  # half-restored debris
    (hs / ".pg0.restore-failed").write_text(f"{good}\n")
    runner = FakeRunner()

    result = upgrade_memory_engine(seam=FakeSeam(), runner=runner, http_get=_healthy, hs_dir=hs)
    assert result["status"] == "upgraded"
    assert not (hs / ".pg0.restore-failed").exists()
    assert (hs / ".pg0" / "marker").read_text() == "last known good"
    copies = [c for c in runner.calls if c[0] == "cp"]
    # restore first, then the fresh snapshot of the restored data dir
    assert copies[0][2:] == [str(good), str(hs / ".pg0")]
    assert copies[1][2] == str(hs / ".pg0")


def test_marked_db_with_no_snapshot_aborts_and_stays_stopped(hs):
    (hs / ".pg0.restore-failed").write_text(f"{hs / '.pg0.pre-gone'}\n")
    seam = FakeSeam()
    result = upgrade_memory_engine(seam=seam, runner=FakeRunner(), http_get=_healthy, hs_dir=hs)
    assert result["status"] == "snapshot_failed"
    assert "untrusted" in result["error"]
    assert seam.verbs == ["stop"]
    assert (hs / ".pg0.restore-failed").exists()
    assert not (hs / ".venv.new").exists()
    assert (hs / ".venv" / "bin" / "hindsight-api").exists()


def test_a_half_finished_snapshot_swap_is_recovered(hs):
    prev = hs / ".pg0.snapshot-prev"
    prev.mkdir()
    (prev / "marker").write_text("previous")
    result = upgrade_memory_engine(
        seam=FakeSeam(), runner=FakeRunner(), http_get=_wrong_version, hs_dir=hs
    )
    assert result["status"] == "rolled_back"
    assert not prev.exists()
    assert (hs / f".pg0.pre-{OLD_VERSION}" / "PG_VERSION").exists()  # fresh snapshot


def test_snapshot_copy_failure_keeps_the_previous_snapshot(hs):
    stale = hs / f".pg0.pre-{OLD_VERSION}"
    stale.mkdir()
    (stale / "marker").write_text("previous")

    class CpFails(FakeRunner):
        def __call__(self, args, **kwargs):
            if str(args[0]) == "cp":
                self.calls.append([str(a) for a in args])
                return subprocess.CompletedProcess(args, 1, "", "No space left on device")
            return super().__call__(args, **kwargs)

    seam = FakeSeam()
    result = upgrade_memory_engine(seam=seam, runner=CpFails(), http_get=_healthy, hs_dir=hs)
    assert result["status"] == "snapshot_failed"
    assert seam.verbs == ["stop", "start"]
    assert (stale / "marker").read_text() == "previous"
    assert not (hs / ".pg0.snapshot-new").exists()
    assert (hs / ".venv" / "bin" / "hindsight-api").exists()


def test_restore_marker_is_down_before_the_copy_and_gone_after_success(hs):
    seen: list[bool] = []

    class Watch(FakeRunner):
        def __call__(self, args, **kwargs):
            args = [str(a) for a in args]
            if args[0] == "cp" and ".pg0.pre-" in args[2]:
                seen.append((hs / ".pg0.restore-failed").exists())
            return super().__call__(args, **kwargs)

    result = upgrade_memory_engine(
        seam=FakeSeam(), runner=Watch(), http_get=_wrong_version, hs_dir=hs
    )
    assert result["status"] == "rolled_back"
    assert seen == [True]  # a crash mid-copy would leave the marker
    assert not (hs / ".pg0.restore-failed").exists()
