"""#1721 — every hal0.toml writer shares one serialized read-modify-write path.

#1717 gave ``routes/settings.py``'s ``PUT /api/settings`` and
``routes/memory.py``'s ``PUT /api/memory/graph`` a shared ``HAL0_TOML_LOCK``.
Four more whole-config read-modify-writes never joined it — the model-store
setter, ``PUT /api/config/models``, ``PUT /api/auth/require`` and the updater's
channel setter — so any of them could save its own pre-read snapshot after a
protected request and erase the section that request had just written. Several
also started from ``app.state.hal0_config``, a startup snapshot no writer
refreshed.

These tests pin the fix at three levels:

  * every writer route genuinely waits on the shared in-process lock;
  * every writer route reloads from DISK, so a stale cache can't clobber;
  * the accessor holds a cross-process advisory lock too, and the off-loop
    writers (updater migrations, installer) take the same one.
"""

from __future__ import annotations

import asyncio
import errno
import os
import re
import stat
import subprocess
import sys
import threading
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from hal0.api.routes import auth as auth_routes
from hal0.api.routes import config as config_routes
from hal0.api.routes import settings as settings_routes
from hal0.api.routes import updater as updater_routes
from hal0.config.loader import (
    HAL0_TOML_LOCK,
    ConfigLockBusy,
    hal0_config_txn,
    load_hal0_config,
    save_hal0_config,
)
from hal0.config.locking import file_lock
from hal0.config.schema import Hal0Config

# ── request doubles ───────────────────────────────────────────────────────────


class _FakeAppState:
    pass


class _FakeApp:
    def __init__(self) -> None:
        self.state = _FakeAppState()


class _FakeRequest:
    """Just enough Starlette ``Request`` surface for the writer routes."""

    def __init__(self, body: dict[str, Any] | None = None) -> None:
        self._body = body or {}
        self.app = _FakeApp()

    async def json(self) -> dict[str, Any]:
        return self._body


def _toml_path(home: str) -> Path:
    return Path(home) / "etc" / "hal0" / "hal0.toml"


def _seed_graph_section(home: str) -> Path:
    """Put a graph section on disk that no in-memory snapshot knows about.

    Stands in for "another writer just saved its section while this request
    was in flight / before it started".
    """
    path = _toml_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '[memory.graph]\nenabled = true\nextraction_slot = "agent"\n',
        encoding="utf-8",
    )
    return path


def _graph_survived(path: Path) -> bool:
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    graph = parsed.get("memory", {}).get("graph", {})
    return graph.get("enabled") is True and graph.get("extraction_slot") == "agent"


# ── per-route drivers ─────────────────────────────────────────────────────────


def _drive_settings() -> Callable[[], Awaitable[Any]]:
    req = _FakeRequest({"telemetry": {"enabled": True}})
    req.app.state.hal0_config = Hal0Config()  # stale snapshot, no graph section
    return lambda: settings_routes.update_settings(req)


def _drive_auth() -> Callable[[], Awaitable[Any]]:
    req = _FakeRequest()
    req.app.state.hal0_config = Hal0Config()
    body = auth_routes.RequireAuthRequest(require_auth=False)
    return lambda: auth_routes.set_require_auth(body, req)


def _drive_config_models(store_root: Path) -> Callable[[], Awaitable[Any]]:
    req = _FakeRequest({"roots": [str(store_root)]})
    req.app.state.hal0_config = Hal0Config()
    return lambda: config_routes.update_models_config(req)


def _drive_channel(monkeypatch: pytest.MonkeyPatch) -> Callable[[], Awaitable[Any]]:
    class _StubUpdater:
        def __init__(self, channel: str | None = None) -> None:
            self.channel = channel

        async def check(self) -> dict[str, Any]:
            return {"ok": True}

    monkeypatch.setattr(updater_routes, "Updater", _StubUpdater)
    req = _FakeRequest({"channel": "preview"})
    req.app.state.hal0_config = Hal0Config()
    return lambda: updater_routes.set_channel(req)


def _drive_model_store(store_root: Path) -> Callable[[], Awaitable[Any]]:
    req = _FakeRequest({"path": str(store_root)})
    req.app.state.hal0_config = Hal0Config()
    return lambda: settings_routes.set_model_store(req)


def _all_drivers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Callable[[], Awaitable[Any]]]:
    store_root = tmp_path / "store"
    store_root.mkdir(exist_ok=True)
    return {
        "settings": _drive_settings(),
        "auth_require": _drive_auth(),
        "config_models": _drive_config_models(store_root),
        "updater_channel": _drive_channel(monkeypatch),
        "model_store": _drive_model_store(store_root),
    }


ROUTE_IDS = ["settings", "auth_require", "config_models", "updater_channel", "model_store"]


# ── in-process serialization ──────────────────────────────────────────────────


@pytest.mark.parametrize("route_id", ROUTE_IDS)
async def test_writer_route_waits_for_the_shared_lock(
    route_id: str, tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each hal0.toml writer must block while another holds HAL0_TOML_LOCK.

    Before #1721 only the settings + memory/graph routes did; the other four
    raced straight past a held lock and could overwrite the holder's section.
    """
    driver = _all_drivers(tmp_path, monkeypatch)[route_id]

    await HAL0_TOML_LOCK.acquire()
    try:
        task = asyncio.ensure_future(driver())
        await asyncio.sleep(0.05)
        assert not task.done(), f"{route_id} must block on the shared hal0.toml lock"
    finally:
        HAL0_TOML_LOCK.release()

    await task


@pytest.mark.parametrize("route_id", ROUTE_IDS)
async def test_writer_route_reloads_disk_instead_of_a_stale_cache(
    route_id: str, tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer must never persist ``app.state.hal0_config``'s startup snapshot.

    The graph section below exists only on disk — exactly the state left behind
    by a concurrent ``PUT /api/memory/graph``. A writer that merges into the
    cached snapshot erases it; one that reloads under the lock preserves it.
    """
    path = _seed_graph_section(tmp_hal0_home)
    driver = _all_drivers(tmp_path, monkeypatch)[route_id]

    await driver()

    assert _graph_survived(path), f"{route_id} clobbered a section it never read"


async def test_concurrent_writers_do_not_lose_updates(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fire every writer at once; each one's key must be present at the end.

    The lost update this reproduces needs no artificial delay: each route did
    its own load → modify → save, so interleaving at any await lost whichever
    section was written by the request that finished first.
    """
    _seed_graph_section(tmp_hal0_home)
    drivers = _all_drivers(tmp_path, monkeypatch)

    results = await asyncio.gather(*(d() for d in drivers.values()), return_exceptions=True)
    for route_id, result in zip(drivers, results, strict=True):
        assert not isinstance(result, BaseException), f"{route_id} failed: {result!r}"

    final = load_hal0_config()
    assert final.memory.graph.enabled is True  # graph section (disk-only) survived
    assert final.memory.graph.extraction_slot == "agent"
    assert final.telemetry.enabled is True  # settings PUT
    assert final.telemetry.channel == "preview"  # updater channel PUT
    assert final.security.require_auth is False  # auth PUT
    assert str(tmp_path / "store") in (final.models.roots or [])  # config models PUT
    assert final.models.store == str(tmp_path / "store")  # model-store POST


# ── cross-process serialization ───────────────────────────────────────────────


async def test_txn_waits_for_a_cross_process_advisory_lock(tmp_hal0_home: str) -> None:
    """The in-process asyncio lock cannot see the CLI/installer/updater.

    ``install.sh`` runs its post-activation hal0.toml migration BEFORE it
    restarts hal0-api, so a live daemon can be mid-settings-PUT while another
    process rewrites the same file. The txn therefore also holds the
    ``hal0.toml.lock`` advisory lock — asserted here by taking that lock from
    another thread (a stand-in for the other process) and requiring the txn to
    refuse rather than race.
    """
    target = _toml_path(tmp_hal0_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(Hal0Config(), target)

    held = threading.Event()
    release = threading.Event()

    def _hold_lock() -> None:
        with file_lock(target):
            held.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=_hold_lock, daemon=True)
    holder.start()
    try:
        assert held.wait(timeout=5)
        with pytest.raises(ConfigLockBusy):
            async with hal0_config_txn(path=target, timeout=0.2):
                pass  # pragma: no cover — the acquire must fail first
    finally:
        release.set()
        holder.join(timeout=5)

    # Lock free again → the same txn now succeeds.
    async with hal0_config_txn(path=target, timeout=5) as txn:
        assert isinstance(txn.config, Hal0Config)


async def test_txn_does_not_block_the_event_loop_while_waiting(tmp_hal0_home: str) -> None:
    """Waiting for the cross-process lock must be an await, not a stalled loop.

    A blocking ``flock`` on the loop thread freezes every request in the
    process, not just the config writer, for as long as the peer holds it.
    """
    target = _toml_path(tmp_hal0_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(Hal0Config(), target)

    held = threading.Event()
    release = threading.Event()

    def _hold_lock() -> None:
        with file_lock(target):
            held.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=_hold_lock, daemon=True)
    holder.start()
    ticks = 0
    try:
        assert held.wait(timeout=5)

        async def _tick() -> None:
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.01)
                ticks += 1

        waiter = asyncio.ensure_future(_await_busy_txn(target))
        await _tick()
        assert ticks == 5, "other coroutines must keep running while the txn waits"
        assert not waiter.done()
    finally:
        release.set()
        holder.join(timeout=5)
    await waiter


async def _await_busy_txn(target: Path) -> None:
    async with hal0_config_txn(path=target, timeout=10):
        pass


def test_updater_config_migration_takes_the_shared_file_lock(tmp_hal0_home: str) -> None:
    """The updater's schema migration is a hal0.toml RMW like any other.

    It runs either on an ``asyncio.to_thread`` inside the live API process or
    as a bare ``hal0`` process from ``install.sh``; both need the advisory lock
    the API writers hold, or a migration can erase a settings save (and vice
    versa).
    """
    from hal0.updater.updater import _maybe_run_config_migrations

    target = _toml_path(tmp_hal0_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(Hal0Config(), target)

    entered = threading.Event()
    finished = threading.Event()

    with file_lock(target):
        worker = threading.Thread(
            target=lambda: (entered.set(), _maybe_run_config_migrations(1), finished.set()),
            daemon=True,
        )
        worker.start()
        assert entered.wait(timeout=5)
        # While this thread holds the lock the migration must not complete.
        assert not finished.wait(timeout=0.3), "migration ran without the hal0.toml lock"

    assert finished.wait(timeout=10), "migration never completed after the lock was released"
    worker.join(timeout=5)


async def test_txn_body_timeout_is_not_relabelled_as_lock_contention(
    tmp_hal0_home: str,
) -> None:
    """A TimeoutError from the BODY must surface as itself.

    The memory/graph route awaits a bounded hindsight-api restart inside the
    critical section; reporting that as "another process is writing hal0.toml"
    would send an operator hunting the wrong bug.
    """
    target = _toml_path(tmp_hal0_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(Hal0Config(), target)

    with pytest.raises(TimeoutError) as caught:
        async with hal0_config_txn(path=target, timeout=5):
            raise TimeoutError("propagation timed out")
    assert not isinstance(caught.value, ConfigLockBusy)
    assert "propagation timed out" in str(caught.value)


async def test_nested_txn_in_one_task_does_not_deadlock(tmp_hal0_home: str) -> None:
    """A txn opened inside a txn by the same task must not hang.

    ``HAL0_TOML_LOCK`` is reached before the file lock's owner check, so
    without task tracking at the transaction level the inner acquire waits on
    a lock its own caller holds — forever (#1721 review).
    """
    target = _toml_path(tmp_hal0_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(Hal0Config(), target)

    async def _nested() -> str:
        async with hal0_config_txn(path=target, timeout=5) as outer:
            outer.config.telemetry.enabled = True
            outer.save()
            async with hal0_config_txn(path=target, timeout=5) as inner:
                # The inner txn sees the outer's committed write.
                assert inner.config.telemetry.enabled is True
                return "ok"

    assert await asyncio.wait_for(_nested(), timeout=10) == "ok"


async def test_nested_txn_shares_the_outer_working_state(tmp_hal0_home: str) -> None:
    """A nested txn must see the outer's UNSAVED edits and share its handle.

    #1721 review round 2: yielding a freshly loaded model to the nested caller
    forks the working state — the helper never sees the outer's in-flight
    changes, and the outer's later ``save()`` writes its own stale object back
    over whatever the helper committed. That is a lost update inside a single
    request, the exact bug class this accessor exists to remove.
    """
    target = _toml_path(tmp_hal0_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(Hal0Config(), target)

    async def _helper() -> None:
        async with hal0_config_txn(path=target, timeout=5) as inner:
            # The outer's unsaved edit is visible here.
            assert inner.config.telemetry.enabled is True
            inner.config.telemetry.channel = "preview"

    async with hal0_config_txn(path=target, timeout=5) as outer:
        outer.config.telemetry.enabled = True  # NOT saved yet
        await _helper()
        # The helper's edit landed on the same object, not a forked copy.
        assert outer.config.telemetry.channel == "preview"
        outer.save()

    final = load_hal0_config(target)
    assert final.telemetry.enabled is True
    assert final.telemetry.channel == "preview"


def test_installer_shell_writer_replaces_hal0_toml_atomically(tmp_path: Path) -> None:
    """Run install.sh's embedded writer for real and prove the swap is atomic.

    The advisory lock binds cooperating writers only — every API read path calls
    ``load_hal0_config()`` unlocked — so a truncate-then-write is observable by
    a live daemon as an empty file (silently all-defaults) or as half-written
    TOML (#1721 review round 2).
    """
    install_sh = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
    text = install_sh.read_text(encoding="utf-8")
    match = re.search(
        r"python3 - \"\$\{HAL0_TOML\}\" \"\$\{MODELS_DIR\}\" <<'PYEOF'\n(.*?)\nPYEOF",
        text,
        re.S,
    )
    assert match, "could not locate install.sh's [models].store writer"
    writer = match.group(1)
    assert "os.replace" in writer and "fsync" in writer, "the installer write must be atomic"

    toml_path = tmp_path / "hal0.toml"
    toml_path.write_text(
        '[telemetry]\nenabled = true\n\n[models]\nstore = "/old"\nroots = ["/a"]\n',
        encoding="utf-8",
    )
    script = tmp_path / "writer.py"
    script.write_text(writer, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(script), str(toml_path), "/new/models"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr

    parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert parsed["models"]["store"] == "/new/models"
    assert parsed["models"]["pull_root"] == "/new/models"
    assert parsed["models"]["roots"] == ["/a"]  # untouched
    assert parsed["telemetry"]["enabled"] is True  # other sections survive
    # No tempfile left behind, and the lock file is a plain sibling.
    assert not list(tmp_path.glob(".hal0.toml.*.tmp"))


def test_installer_shell_writer_seeds_a_config_without_a_models_table(tmp_path: Path) -> None:
    """The old ``printf >>`` append branch folded into the locked writer.

    That branch is the one a table-less (or absent) hal0.toml took, and it was
    the least protected of the two: a bare shell append with no lock at all.
    """
    install_sh = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
    match = re.search(
        r"python3 - \"\$\{HAL0_TOML\}\" \"\$\{MODELS_DIR\}\" <<'PYEOF'\n(.*?)\nPYEOF",
        install_sh.read_text(encoding="utf-8"),
        re.S,
    )
    assert match
    script = tmp_path / "writer.py"
    script.write_text(match.group(1), encoding="utf-8")

    for label, seed in (("table-less", "[telemetry]\nenabled = true\n"), ("absent", None)):
        toml_path = tmp_path / f"hal0-{label}.toml"
        if seed is not None:
            toml_path.write_text(seed, encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(script), str(toml_path), "/new/models"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"{label}: {proc.stderr}"
        parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        assert parsed["models"]["store"] == "/new/models", label
        if seed is not None:
            assert parsed["telemetry"]["enabled"] is True, label


async def test_txn_is_reentrant_within_one_task(tmp_hal0_home: str) -> None:
    """A nested txn in the same task passes through instead of self-deadlocking.

    ``asyncio.Lock`` is not recursive, so without an owner check a writer that
    (directly or via a helper) opened a second txn would hang forever rather
    than fail — the worst possible failure mode for a config write path.
    """
    target = _toml_path(tmp_hal0_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(Hal0Config(), target)

    async def _nested() -> str:
        # The inner acquire is the one that used to hang.
        async with hal0_config_txn(path=target, timeout=5), _reenter_file_lock(target):
            return "ok"

    assert await asyncio.wait_for(_nested(), timeout=10) == "ok"


def _reenter_file_lock(target: Path) -> Any:
    from hal0.config.locking import async_file_lock

    return async_file_lock(target, timeout=2)


# ── the ratchet ───────────────────────────────────────────────────────────────


def test_no_hal0_toml_writer_hand_rolls_its_own_read_modify_write() -> None:
    """#1721 happened because the lock lived at the call sites, not the writer.

    Every module that persists a whole hal0.toml must go through
    ``hal0_config_txn`` (event loop) or ``hal0_config_file_lock`` (sync/threaded)
    — a new route that calls ``save_hal0_config`` directly reintroduces exactly
    the lost update this issue is about. Allowlisted below are the loader
    itself (which defines both) and nothing else.
    """
    src = Path(__file__).resolve().parents[2] / "src" / "hal0"
    offenders: list[str] = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "save_hal0_config(" not in text:
            continue
        rel = py.relative_to(src).as_posix()
        if rel == "config/loader.py":
            continue
        if "hal0_config_txn" in text or "hal0_config_file_lock" in text:
            continue
        offenders.append(rel)

    assert not offenders, (
        "these modules write hal0.toml without the serialized RMW path "
        f"(hal0_config_txn / hal0_config_file_lock): {offenders}"
    )


def test_raw_hal0_toml_writers_are_covered_too() -> None:
    """The ratchet must catch RAW writers, not just ``save_hal0_config`` ones.

    #1721 review: the updater's stamp pass and ``hal0 config migrate`` go
    straight to ``write_toml_atomic`` against ``paths.hal0_toml()``, so scanning
    for the typed writer alone let two cooperating processes escape the
    advisory-lock contract entirely.
    """
    src = Path(__file__).resolve().parents[2] / "src" / "hal0"
    offenders: list[str] = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "write_toml_atomic(" not in text:
            continue  # a mention in prose is not a writer
        if "hal0_toml()" not in text and "_hal0_toml_path()" not in text:
            continue  # writes some other TOML (slots, profiles, registry)
        rel = py.relative_to(src).as_posix()
        if rel == "config/loader.py":
            continue
        if "hal0_config_txn" in text or "hal0_config_file_lock" in text:
            continue
        offenders.append(rel)

    assert not offenders, (
        f"these modules write hal0.toml raw, outside the advisory lock: {offenders}"
    )


def test_installer_shell_writer_takes_the_advisory_lock() -> None:
    """``install.sh`` patches [models].store while hal0-api may still be live.

    It restarts the service only further down the script, so this write races a
    settings PUT on a real upgrade. It cannot import hal0 (the venv may not
    exist yet), so it takes the same lock file with plain fcntl.
    """
    install_sh = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
    text = install_sh.read_text(encoding="utf-8")
    start = text.index('HAL0_TOML="${ETC_DIR}/hal0.toml"')
    block = text[start : start + 3000]
    assert "hal0.toml" in block
    assert ".lock" in block and "flock" in block, (
        "install.sh's [models].store patch must hold hal0.toml.lock"
    )
    assert "O_NOFOLLOW" in block, "the root-run lock open must refuse symlinks"


def test_lock_file_open_refuses_a_symlink(tmp_path: Path) -> None:
    """A symlinked lock file must be refused, not followed.

    ``/etc/hal0`` is service-writable under the ownership flip while
    ``install.sh`` / ``hal0 update`` take this lock as root, so following a
    symlink would let the service account aim the next root-run upgrade at any
    root-writable file (#1721 review).
    """
    target = tmp_path / "hal0.toml"
    target.write_text("", encoding="utf-8")
    victim = tmp_path / "victim"
    victim.write_text("precious", encoding="utf-8")
    (tmp_path / "hal0.toml.lock").symlink_to(victim)

    with pytest.raises(OSError), file_lock(target):
        pass  # pragma: no cover — the open must fail first
    assert victim.read_text(encoding="utf-8") == "precious"


def test_new_lock_file_is_group_writable_regardless_of_umask(tmp_path: Path) -> None:
    """A privileged writer must not leave a lock the service account can't open.

    ``sudo hal0 config migrate`` / the root-run installer can create
    ``hal0.toml.lock`` first, under ``umask 022``. Relying on ``os.open``'s
    creation mode would land 0644 and the daemon — which opens the lock O_RDWR
    — would then fail EVERY config write with PermissionError until a
    ``doctor perms --fix``. Same hazard that bit ``slots.lock`` on halo150
    (#1721 review round 3), so the mode is set explicitly at creation.
    """
    target = tmp_path / "hal0.toml"
    target.write_text("", encoding="utf-8")
    lock_path = tmp_path / "hal0.toml.lock"

    old_umask = os.umask(0o022)  # exactly what install.sh sets
    try:
        with file_lock(target):
            pass
    finally:
        os.umask(old_umask)

    assert lock_path.exists()
    mode = stat.S_IMODE(lock_path.stat().st_mode)
    assert mode == 0o664, f"lock created 0o{mode:o}; the service account needs group write"


def test_installer_lock_open_sets_mode_explicitly() -> None:
    """install.sh's inline lock needs the same creation-mode fix.

    It runs as root under ``umask 022`` by construction, so it is the most
    likely creator of the lock file on a fresh box.
    """
    install_sh = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
    match = re.search(
        r"python3 - \"\$\{HAL0_TOML\}\" \"\$\{MODELS_DIR\}\" <<'PYEOF'\n(.*?)\nPYEOF",
        install_sh.read_text(encoding="utf-8"),
        re.S,
    )
    assert match
    writer = match.group(1)
    assert "fchmod" in writer, "the installer must set the lock mode explicitly, not via umask"
    assert "fchown" in writer, "a root-created lock must adopt the guarded file's owner"


async def test_permanent_flock_failure_is_not_polled(tmp_hal0_home: str) -> None:
    """A filesystem without advisory locks must fail fast, not burn the timeout.

    ``ENOSYS``/``EOPNOTSUPP`` fail identically on every attempt, so retrying
    them spends the whole timeout and then reports a misleading
    ``config.lock_busy`` — or spins forever when ``timeout=None``
    (#1721 review round 3).
    """
    target = _toml_path(tmp_hal0_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(Hal0Config(), target)

    import fcntl as fcntl_mod

    from hal0.config import locking as locking_mod

    calls = 0

    def _boom(fd: int, op: int) -> None:
        nonlocal calls
        calls += 1
        raise OSError(errno.ENOSYS, "function not implemented")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(locking_mod.fcntl, "flock", _boom)
        with pytest.raises(OSError) as caught:
            async with hal0_config_txn(path=target, timeout=30):
                pass  # pragma: no cover — the acquire must fail first

    assert caught.value.errno == errno.ENOSYS
    assert not isinstance(caught.value, ConfigLockBusy)
    assert calls == 1, f"a permanent error was retried {calls} times"
    assert fcntl_mod.flock is not _boom  # monkeypatch cleaned up


async def test_async_lock_does_not_fake_reentrancy_for_a_sibling_task(
    tmp_hal0_home: str,
) -> None:
    """A sibling task's blocking ``file_lock`` must not sail through.

    The sync lock's re-entrancy is thread-local, and every task shares the loop
    thread — so marking the thread as "already holding" while an async writer
    runs would let an unrelated task write with no lock at all (#1721 review).
    """
    target = _toml_path(tmp_hal0_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(Hal0Config(), target)

    from hal0.config.locking import async_file_lock

    sibling_got_lock = threading.Event()

    async with async_file_lock(target, timeout=5):
        # Same loop thread, different task → must contend, not pass through.
        worker = threading.Thread(
            target=lambda: (file_lock(target).__enter__(), sibling_got_lock.set()),
            daemon=True,
        )
        worker.start()
        assert not sibling_got_lock.wait(timeout=0.3), (
            "a second acquirer took the lock while the async holder had it"
        )
    assert sibling_got_lock.wait(timeout=5)
