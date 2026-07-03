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

import contextlib
import os
import threading
from collections.abc import Iterator
from pathlib import Path

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


__all__ = ["file_lock", "lock_path_for"]
