"""Cross-process advisory file locking for config read-modify-write (SC-10).

Several config files — most notably ``/etc/hal0/capabilities.toml`` and its
paired ``slots/*.toml`` — are read-modify-write surfaces touched by more than
one cooperating hal0 process (the API and the ``hal0`` CLI). Two concurrent
writers otherwise interleave ``read -> modify -> write`` and clobber each
other's change (the classic lost update).

:func:`file_lock` generalises the ``hal0.mcp.installed._registry_lock`` pattern
into one shared helper: it holds an exclusive advisory ``fcntl.flock`` on a
sibling ``<target>.lock`` for the duration of the ``with`` body. Every writer
of a given file must lock the SAME path — ``flock`` is advisory and per-inode,
so serialization only holds when all cooperating writers point at one lock
file.

Two properties the callers rely on:

  - **Cross-process exclusion.** ``flock(LOCK_EX)`` blocks a second process
    until the first releases. Advisory locks are honoured only by processes
    that also call :func:`file_lock`, which is exactly the API + CLI surface
    the finding names.
  - **Same-thread re-entrancy.** ``flock`` on a fresh descriptor is NOT
    recursive: two open descriptions of the same inode contend even within one
    process, so a naive nested acquire would self-deadlock. A locked outer
    writer (``initialize_if_missing``) legitimately calls a locked inner writer
    (``auto_migrate_capabilities_file``); to keep that safe we track a
    per-thread recursion depth and only take/release the OS lock on the
    outermost acquire. Different threads still serialize — each takes its own
    descriptor and the second ``flock`` blocks.

Graceful degradation: on a platform without ``fcntl`` (e.g. Windows) the lock
becomes a best-effort no-op so the config paths still function, matching the
advisory nature of the primitive.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import stat
import threading
import time
import weakref
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

# Per-thread recursion depth keyed by lock-file path. Only the outermost
# acquire in a thread touches the OS lock; nested acquires are cheap no-ops.
_reentry = threading.local()


def _depths() -> dict[str, int]:
    depths: dict[str, int] | None = getattr(_reentry, "depths", None)
    if depths is None:
        depths = {}
        _reentry.depths = depths
    return depths


#: Mode for a freshly created lock file. Owner+group writable, matching the
#: ``/etc/hal0 *.lock`` and ``/var/lib/hal0 *.lock`` perms rows: root and the
#: service account both have to be able to take these locks.
LOCK_FILE_MODE = 0o664


def _adopt_target_ownership(fd: int, target: Path) -> None:
    """Give a just-created lock file the guarded file's owner, when running as root.

    A root-run writer that creates the lock first (``sudo hal0 config migrate``,
    the installer, ``hal0 update``) would otherwise leave it ``root:root`` and
    ``umask``-filtered — install.sh sets ``umask 022``, so ``0644`` — and the
    ``hal0`` daemon, which opens the lock ``O_RDWR``, then fails EVERY config
    write with ``PermissionError`` until someone runs ``doctor perms --fix``.
    The same hazard already bit ``slots.lock`` on halo150 (see the ``*.lock``
    rows in ``hal0.install.perms``); a standalone privileged CLI run has no
    later repair step at all, so the fix belongs at creation time.

    The lock inherits the guarded file's uid/gid (falling back to its directory)
    rather than a hard-coded account, so it tracks whichever side of the
    service-user ownership flip the box is on. Best-effort: a non-root process
    cannot chown, and it does not need to — it created the file as itself.
    """
    os.fchmod(fd, LOCK_FILE_MODE)  # explicit: os.open's mode is umask-filtered
    if os.geteuid() != 0:
        return
    source = target if target.exists() else target.parent
    try:
        st = source.stat()
    except OSError:
        return
    if st.st_uid == 0 and st.st_gid == 0:
        return  # unflipped, root-owned box — root:root is already correct
    with contextlib.suppress(OSError):
        os.fchown(fd, st.st_uid, st.st_gid)


def _open_lock_file(lock_path: Path, target: Path):  # type: ignore[no-untyped-def]
    """Open ``lock_path`` for locking, refusing symlinks and non-regular files.

    These locks straddle a privilege boundary: ``/etc/hal0`` is writable by the
    ``hal0`` service account under the ownership flip, while ``install.sh`` and
    ``hal0 update`` acquire the same lock as **root**. A plain
    ``open(path, "w")`` there follows a symlink and truncates whatever it points
    at, so a compromised service account could aim ``hal0.toml.lock`` at any
    root-writable file and have the next upgrade clobber it. ``O_NOFOLLOW``
    plus an ``S_ISREG`` check on the descriptor (not the path — no TOCTOU
    window) closes that. No ``O_TRUNC``: the lock file's contents are never
    read or written, only its inode is locked.

    Creation is a separate ``O_EXCL`` attempt so the mode/ownership fix-up in
    :func:`_adopt_target_ownership` runs on the creating process only, and never
    re-chmods a lock another process is already holding.
    """
    created = False
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, LOCK_FILE_MODE)
        created = True
    except FileExistsError:
        fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"lock path is not a regular file: {lock_path}")
        if created:
            _adopt_target_ownership(fd, target)
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "r+b")


def lock_path_for(target: Path | str) -> Path:
    """Return the sibling ``.lock`` path :func:`file_lock` locks for ``target``."""
    return Path(f"{os.fspath(target)}.lock")


@contextlib.contextmanager
def file_lock(target: Path | str) -> Iterator[None]:
    """Hold an exclusive advisory lock serializing an RMW on ``target``.

    Locks the sibling ``<target>.lock`` (created on demand). Re-entrant within
    the same thread; serializing across threads and processes. See the module
    docstring for the full contract.
    """
    lock_path = lock_path_for(target)
    key = str(lock_path)
    depths = _depths()

    if depths.get(key, 0) > 0:
        # Re-entrant acquire in this thread — the OS lock is already held.
        depths[key] += 1
        try:
            yield
        finally:
            depths[key] -= 1
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if fcntl is None:  # pragma: no cover - non-POSIX platforms
        # No advisory locking available: degrade to a no-op guard so the
        # config paths still work (single-process installs are unaffected).
        depths[key] = 1
        try:
            yield
        finally:
            depths.pop(key, None)
        return

    with _open_lock_file(lock_path, Path(target)) as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        depths[key] = 1
        try:
            yield
        finally:
            depths.pop(key, None)
            with contextlib.suppress(OSError):
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# ── async variant ─────────────────────────────────────────────────────────────
#
# ``file_lock`` blocks the calling thread. On the API's event loop that is a
# whole-process stall: while an ``install.sh`` migration or a CLI writer holds
# the lock, EVERY request would freeze, not just the config writer. The async
# variant therefore polls ``LOCK_EX | LOCK_NB`` and awaits between attempts, so
# only the waiting coroutine is parked.
#
# Re-entrancy is tracked by TASK, not by thread. The sync path's thread-local
# depth map is deliberately NOT touched here: every task on the loop shares one
# thread, so marking depth > 0 while an async holder runs would make a sibling
# task's sync ``file_lock`` believe it already held the lock and write with no
# lock at all — a silent lost update, worse than the contention it avoids.
# Coroutines serialize on a per-loop, per-path ``asyncio.Lock`` and the holding
# task is recorded so its own nested acquire passes through instead of hanging
# on the non-recursive lock.
#
# Corollary: never call the blocking :func:`file_lock` from the event-loop
# thread for a target an async writer also locks — it would block the loop, and
# if a txn on that same loop holds it, forever. Off-loop writers must run in a
# worker thread (``asyncio.to_thread``) or use this async variant.

#: ``flock(LOCK_NB)`` errnos that mean "held by someone else, try again".
#: Anything else (ENOSYS/EOPNOTSUPP on a filesystem without advisory locks, EBADF)
#: is permanent and must surface immediately rather than be polled.
_CONTENTION_ERRNOS = frozenset({errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK, errno.EINTR})

_async_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


class PerLoopLock:
    """An ``asyncio.Lock`` that survives being declared at module scope.

    A bare module-level ``asyncio.Lock()`` binds to the FIRST event loop that
    awaits it and raises ``RuntimeError: bound to a different event loop`` on
    every later loop. In the daemon that never shows (one loop for the process
    lifetime), but it makes a module-scope lock unusable from any second loop —
    a test suite, a CLI that spins its own ``asyncio.run``, or a future worker
    loop — and the failure mode is a raised writer, not a queued one.

    Keying one real lock per running loop keeps the single-loop semantics
    identical while making the object safe to hold as a module constant.
    Supports the ``asyncio.Lock`` surface its callers use: ``async with``,
    ``acquire``, ``release``, ``locked``.
    """

    __slots__ = ("_locks",)

    def __init__(self) -> None:
        self._locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
            weakref.WeakKeyDictionary()
        )

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
        return lock

    async def acquire(self) -> bool:
        return await self._lock().acquire()

    def release(self) -> None:
        self._lock().release()

    def locked(self) -> bool:
        return self._lock().locked()

    async def __aenter__(self) -> None:
        await self.acquire()

    async def __aexit__(self, *exc: object) -> None:
        self.release()


#: Task currently holding each key's async lock, per loop. Needed for
#: re-entrancy: the thread-local depth map cannot tell "this task already holds
#: it" (safe to pass through) from "a sibling task holds it" (must wait), and
#: an ``asyncio.Lock`` is not recursive — a nested acquire by the holder would
#: hang forever rather than time out.
_async_owners: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, asyncio.Task[Any] | None]
] = weakref.WeakKeyDictionary()


def _async_lock_for(key: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    per_loop = _async_locks.get(loop)
    if per_loop is None:
        per_loop = {}
        _async_locks[loop] = per_loop
    lock = per_loop.get(key)
    if lock is None:
        lock = asyncio.Lock()
        per_loop[key] = lock
    return lock


def _async_owner_map() -> dict[str, asyncio.Task[Any] | None]:
    loop = asyncio.get_running_loop()
    owners = _async_owners.get(loop)
    if owners is None:
        owners = {}
        _async_owners[loop] = owners
    return owners


@contextlib.asynccontextmanager
async def async_file_lock(
    target: Path | str,
    *,
    timeout: float | None = 60.0,
    poll_interval: float = 0.02,
) -> AsyncIterator[None]:
    """:func:`file_lock` for the event loop — never blocks the loop thread.

    Same exclusion contract as :func:`file_lock` (same ``<target>.lock`` file,
    so the two serialize against each other across processes and threads), but
    the wait is an ``await`` rather than a blocking ``flock``.

    Args:
        target: File whose read-modify-write is being serialized.
        timeout: Seconds to wait for the OS lock before raising
            ``TimeoutError``. ``None`` waits forever. A timeout is preferred
            over an unbounded wait: a stuck peer should fail one request, not
            wedge the writer surface permanently.
        poll_interval: Delay between non-blocking acquire attempts.

    Raises:
        TimeoutError: The lock was still held after ``timeout`` seconds.
    """
    lock_path = lock_path_for(target)
    key = str(lock_path)
    owners = _async_owner_map()
    current = asyncio.current_task()

    if current is not None and owners.get(key) is current:
        # Re-entrant acquire by the task that already holds it. Passing through
        # is the only non-hanging option: ``asyncio.Lock`` is not recursive, so
        # waiting here would block on ourselves until the process ends.
        yield
        return

    async with _async_lock_for(key):
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is None:  # pragma: no cover - non-POSIX platforms
            owners[key] = current
            try:
                yield
            finally:
                owners.pop(key, None)
            return

        with _open_lock_file(lock_path, Path(target)) as fh:
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    # Only genuine contention is worth retrying. A filesystem
                    # with no advisory-lock support (ENOSYS / EOPNOTSUPP) fails
                    # identically on every attempt, so polling it would burn the
                    # whole timeout and then report a misleading "another
                    # process is writing" — or spin forever when timeout=None.
                    if exc.errno not in _CONTENTION_ERRNOS:
                        raise
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"timed out after {timeout}s waiting for advisory lock {lock_path}"
                        ) from None
                    await asyncio.sleep(poll_interval)
            owners[key] = current
            try:
                yield
            finally:
                owners.pop(key, None)
                with contextlib.suppress(OSError):
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


__all__ = ["PerLoopLock", "async_file_lock", "file_lock", "lock_path_for"]
