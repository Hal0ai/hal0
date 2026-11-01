"""Tests for hal0.registry.tombstones — the ignored-model-paths store (ADR-0026)."""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import stat
from pathlib import Path

import pytest

from hal0.config import locking, paths
from hal0.registry import tombstones


@pytest.fixture(autouse=True)
def _home(tmp_hal0_home: str) -> None:
    """Every test writes the tombstone file under an isolated HAL0_HOME."""


def test_load_is_empty_when_file_absent() -> None:
    assert tombstones.load() == set()


@pytest.mark.parametrize("umask", [0o022, 0o077])
def test_clear_creates_group_writable_lock_despite_umask(tmp_path: Path, umask: int) -> None:
    previous = os.umask(umask)
    try:
        assert tombstones.clear(tmp_path / "model.gguf") == []
    finally:
        os.umask(previous)
    lock = paths.model_tombstones_path().with_suffix(".json.lock")
    assert stat.S_IMODE(lock.stat().st_mode) == 0o664


def test_root_created_lock_adopts_directory_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = paths.model_tombstones_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    real_fstat = os.fstat

    def service_owned_directory(fd: int) -> os.stat_result:
        observed = real_fstat(fd)
        if stat.S_ISDIR(observed.st_mode):
            fields = list(observed)
            fields[4:6] = [12345, 12346]
            return os.stat_result(fields)
        return observed

    adopted: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "fstat", service_owned_directory)
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: adopted.append((uid, gid)))

    tombstones.clear(tmp_path / "model.gguf")

    assert adopted == [(12345, 12346)]


def test_existing_lock_is_flocked_without_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lock = paths.model_tombstones_path().with_suffix(".json.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch(mode=0o664)
    acquired: list[int] = []
    real_flock = fcntl.flock

    def no_chmod(fd: int, mode: int) -> None:
        raise PermissionError(errno.EPERM, "not the lock owner")

    def record_flock(fd: int, operation: int) -> None:
        real_flock(fd, operation)
        acquired.append(operation)

    monkeypatch.setattr(os, "fchmod", no_chmod)
    monkeypatch.setattr(fcntl, "flock", record_flock)
    tombstones.clear(tmp_path / "model.gguf")

    assert acquired == [fcntl.LOCK_EX, fcntl.LOCK_UN]
    assert "tombstones.lock_failed" not in caplog.text


@pytest.mark.parametrize("failure", ["open", "flock"])
def test_lock_failure_remains_a_warning_and_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise PermissionError(errno.EACCES, "Permission denied")

    if failure == "open":
        monkeypatch.setattr(locking, "_open_lock_file", denied)
    else:
        monkeypatch.setattr(fcntl, "flock", denied)
    with caplog.at_level(logging.WARNING):
        assert tombstones.add([tmp_path / "model.gguf"]) == [str(tmp_path / "model.gguf")]
    assert tombstones.load() == {str(tmp_path / "model.gguf")}
    assert "tombstones.lock_failed" in caplog.text
    assert "Permission denied" in caplog.text


def test_add_persists_resolved_paths(tmp_path: Path) -> None:
    real = tmp_path / "models" / "a.gguf"
    real.parent.mkdir()
    real.write_bytes(b"x")
    link = tmp_path / "link.gguf"
    link.symlink_to(real)

    added = tombstones.add([link])

    assert added == [str(real)]
    assert tombstones.load() == {str(real)}
    assert paths.model_tombstones_path().is_file()


def test_add_is_idempotent(tmp_path: Path) -> None:
    f = tmp_path / "a.gguf"
    assert tombstones.add([f]) == [str(f)]
    assert tombstones.add([f]) == []
    assert tombstones.load() == {str(f)}


def test_is_tombstoned_matches_path_and_descendants(tmp_path: Path) -> None:
    d = tmp_path / "flm-model"
    entries = {str(d), str(tmp_path / "x.gguf")}
    assert tombstones.is_tombstoned(tmp_path / "x.gguf", entries)
    assert tombstones.is_tombstoned(d / "weights" / "w.q4nx", entries)
    assert not tombstones.is_tombstoned(tmp_path / "y.gguf", entries)
    assert not tombstones.is_tombstoned(tmp_path / "x.gguf", set())


def test_clear_drops_entry_and_entries_below(tmp_path: Path) -> None:
    d = tmp_path / "dir"
    keep = tmp_path / "other.gguf"
    tombstones.add([d / "a.gguf", d / "sub" / "b.gguf", keep])

    removed = tombstones.clear(d)

    assert removed == sorted([str(d / "a.gguf"), str(d / "sub" / "b.gguf")])
    assert tombstones.load() == {str(keep)}


def test_clear_matches_symlinked_form(tmp_path: Path) -> None:
    real = tmp_path / "real.gguf"
    real.write_bytes(b"x")
    link = tmp_path / "via-link.gguf"
    link.symlink_to(real)
    tombstones.add([real])

    assert tombstones.clear(link) == [str(real)]
    assert tombstones.load() == set()


def test_corrupt_file_reads_as_empty_and_is_overwritten(tmp_path: Path) -> None:
    target = paths.model_tombstones_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")

    assert tombstones.load() == set()
    tombstones.add([tmp_path / "a.gguf"])
    assert tombstones.load() == {str(tmp_path / "a.gguf")}


def _slow_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """Widen the read-modify-write window so an unlocked race would lose data."""
    import time

    real_load = tombstones.load

    def slow() -> set[str]:
        current = real_load()
        time.sleep(0.02)
        return current

    monkeypatch.setattr(tombstones, "load", slow)


def test_concurrent_adds_in_threads_keep_every_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    _slow_load(monkeypatch)
    workers = [
        threading.Thread(target=tombstones.add, args=([tmp_path / f"t{i}.gguf"],)) for i in range(8)
    ]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert tombstones.load() == {str(tmp_path / f"t{i}.gguf") for i in range(8)}


def test_concurrent_adds_across_processes_keep_every_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flock sidecar serialises a second process (CLI next to the API)."""
    import multiprocessing

    _slow_load(monkeypatch)
    ctx = multiprocessing.get_context("fork")
    procs = [
        ctx.Process(target=tombstones.add, args=([tmp_path / f"p{i}.gguf"],)) for i in range(6)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    assert tombstones.load() == {str(tmp_path / f"p{i}.gguf") for i in range(6)}
