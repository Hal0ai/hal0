"""Regression tests for the api.env sidecar lock and live-env coherence.

``/etc/hal0`` is writable by the ``hal0`` account while ``hal0 auth
reset-key`` takes :func:`hal0.api._env_store.api_env_lock` as root, so the
lock opener must never follow a planted symlink or chown a file it did not
create. The writers also update the live process env inside the same locked
section as the persist, so a concurrent set and delete cannot leave a deleted
secret live in-process (#2941, PR #2983 review).
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

import pytest

from hal0.api import _env_store
from hal0.api._env_store import delete_env_value, list_env_keys, upsert_env_value


def _lock_path(api_env: Path) -> Path:
    return api_env.parent / f".{api_env.name}.lock"


@pytest.fixture
def as_root(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int, int]]:
    """Pretend to be root and record every ``fchown`` instead of performing it."""
    calls: list[tuple[int, int, int]] = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: calls.append((fd, uid, gid)))
    return calls


@pytest.fixture
def flocks(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the descriptors ``api_env_lock`` flocks, still really locking them."""
    calls: list[int] = []
    real = _env_store.fcntl.flock

    def spy(fd: int, op: int) -> None:
        if op == _env_store.fcntl.LOCK_EX:
            calls.append(fd)
        real(fd, op)

    monkeypatch.setattr(_env_store.fcntl, "flock", spy)
    return calls


def test_symlinked_lock_is_not_followed_or_chowned(
    tmp_path: Path, as_root: list[tuple[int, int, int]], flocks: list[int]
) -> None:
    victim = tmp_path / "shadow"
    victim.write_text("root:secret\n", encoding="utf-8")
    os.chmod(victim, 0o600)
    before = victim.stat()
    etc = tmp_path / "etc"
    etc.mkdir()
    api_env = etc / "api.env"
    api_env.write_text("", encoding="utf-8")
    _lock_path(api_env).symlink_to(victim)

    upsert_env_value(api_env, "HF_TOKEN", "v")
    delete_env_value(api_env, "HF_TOKEN")

    after = victim.stat()
    assert as_root == []
    assert flocks == []
    assert (after.st_uid, after.st_gid, after.st_mode) == (
        before.st_uid,
        before.st_gid,
        before.st_mode,
    )
    assert victim.read_text(encoding="utf-8") == "root:secret\n"
    assert _lock_path(api_env).is_symlink()


def test_dangling_symlink_lock_does_not_create_its_target(
    tmp_path: Path, as_root: list[tuple[int, int, int]]
) -> None:
    api_env = tmp_path / "api.env"
    target = tmp_path / "planted"
    _lock_path(api_env).symlink_to(target)

    upsert_env_value(api_env, "HF_TOKEN", "v")

    assert not target.exists()
    assert as_root == []
    assert list_env_keys(api_env) == ["HF_TOKEN"]


def test_non_regular_lock_is_refused(
    tmp_path: Path, as_root: list[tuple[int, int, int]], flocks: list[int]
) -> None:
    api_env = tmp_path / "api.env"
    os.mkfifo(_lock_path(api_env))

    upsert_env_value(api_env, "HF_TOKEN", "v")

    assert as_root == []
    assert flocks == []
    assert stat.S_ISFIFO(os.lstat(_lock_path(api_env)).st_mode)
    assert list_env_keys(api_env) == ["HF_TOKEN"]


def test_existing_lock_is_not_rechowned(
    tmp_path: Path, as_root: list[tuple[int, int, int]], flocks: list[int]
) -> None:
    api_env = tmp_path / "api.env"
    api_env.write_text("", encoding="utf-8")
    lock = _lock_path(api_env)
    lock.write_bytes(b"")
    os.chmod(lock, 0o640)

    upsert_env_value(api_env, "HF_TOKEN", "v")

    assert as_root == []
    assert len(flocks) == 1
    assert stat.S_IMODE(lock.stat().st_mode) == 0o640


def test_created_lock_adopts_api_env_ownership_once(
    tmp_path: Path, as_root: list[tuple[int, int, int]]
) -> None:
    api_env = tmp_path / "api.env"
    api_env.write_text("", encoding="utf-8")
    st = api_env.stat()

    upsert_env_value(api_env, "A", "1")
    upsert_env_value(api_env, "B", "2")

    assert _lock_path(api_env).is_file()
    if (st.st_uid, st.st_gid) == (0, 0):
        assert as_root == []  # root-owned box: root:root is already correct
    else:
        assert [(uid, gid) for _, uid, gid in as_root] == [(st.st_uid, st.st_gid)]


class _LockCheckingEnv(dict[str, str]):
    """An environ that records whether the api.env thread lock was held at each change."""

    def __init__(self) -> None:
        super().__init__()
        self.held: list[bool] = []

    def _lock_is_held(self) -> bool:
        result: list[bool] = []

        def probe() -> None:
            got = _env_store._API_ENV_THREAD_LOCK.acquire(blocking=False)
            if got:
                _env_store._API_ENV_THREAD_LOCK.release()
            result.append(not got)

        t = threading.Thread(target=probe)
        t.start()
        t.join()
        return result[0]

    def __setitem__(self, key: str, value: str) -> None:
        self.held.append(self._lock_is_held())
        super().__setitem__(key, value)

    def pop(self, key: str, *default: object) -> object:  # type: ignore[override]
        self.held.append(self._lock_is_held())
        return super().pop(key, *default)


def test_environ_is_updated_inside_the_lock(tmp_path: Path) -> None:
    api_env = tmp_path / "api.env"
    env = _LockCheckingEnv()

    upsert_env_value(api_env, "HF_TOKEN", "v", environ=env)
    assert env == {"HF_TOKEN": "v"}
    assert delete_env_value(api_env, "HF_TOKEN", environ=env) is True
    assert env == {}
    # A key the file never held is still dropped from the live env.
    env["STALE"] = "x"
    assert delete_env_value(api_env, "STALE", environ=env) is False
    assert "STALE" not in env
    assert env.held == [True, True, False, True]


def test_concurrent_set_and_delete_leave_env_and_file_agreeing(tmp_path: Path) -> None:
    for trial in range(40):
        api_env = tmp_path / f"t{trial}" / "api.env"
        api_env.parent.mkdir()
        env: dict[str, str] = {}
        barrier = threading.Barrier(2)

        def setter(
            p: Path = api_env, e: dict[str, str] = env, b: threading.Barrier = barrier
        ) -> None:
            b.wait()
            upsert_env_value(p, "SECRET", "v", environ=e)

        def deleter(
            p: Path = api_env, e: dict[str, str] = env, b: threading.Barrier = barrier
        ) -> None:
            b.wait()
            delete_env_value(p, "SECRET", environ=e)

        ts = [threading.Thread(target=setter), threading.Thread(target=deleter)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert ("SECRET" in list_env_keys(api_env)) == ("SECRET" in env)
