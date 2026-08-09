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
import os
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

    with open(lock_path, "w") as fh:
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
# Re-entrancy needs a second guard here that the sync path does not: the
# thread-local depth map is shared by every task on the loop thread, so a
# second TASK arriving while the first holds the lock would read depth > 0 and
# sail straight through with no lock at all. Coroutines are therefore
# serialized first by a per-loop, per-path ``asyncio.Lock`` — held for the same
# span as the OS lock — and only then does the depth map get set (which keeps a
# nested SYNC ``file_lock`` on the same target inside the body a no-op instead
# of a self-deadlock).

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
        depths = _depths()
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is None:  # pragma: no cover - non-POSIX platforms
            depths[key] = depths.get(key, 0) + 1
            owners[key] = current
            try:
                yield
            finally:
                owners.pop(key, None)
                depths[key] -= 1
                if depths[key] <= 0:
                    depths.pop(key, None)
            return

        with open(lock_path, "w") as fh:
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"timed out after {timeout}s waiting for advisory lock {lock_path}"
                        ) from None
                    await asyncio.sleep(poll_interval)
            depths[key] = depths.get(key, 0) + 1
            owners[key] = current
            try:
                yield
            finally:
                owners.pop(key, None)
                depths[key] -= 1
                if depths[key] <= 0:
                    depths.pop(key, None)
                with contextlib.suppress(OSError):
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


__all__ = ["PerLoopLock", "async_file_lock", "file_lock", "lock_path_for"]
