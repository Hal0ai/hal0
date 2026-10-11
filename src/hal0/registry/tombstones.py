"""Model-path tombstones — files on disk the discovery scan must ignore.

When an operator deletes a model but its files stay on disk (they unticked
"Also delete files from disk", or a safety guard refused to remove a file),
the next scan would find the file again and re-register it: the model
"reappears". A tombstone records the file's resolved path so
:func:`hal0.registry.discover.find_candidates` skips it.

A tombstone on a directory masks everything below it (an FLM model directory
left in place, for example). An explicit registration of a path — ``hal0
model add``, ``POST /api/models/add-from-path``, scan commit, or a pull —
clears every tombstone at or below that path, because the operator has asked
for it back.

The store is a small JSON list (``paths.model_tombstones_path()``) rather than
a registry table: it is a handful of strings consulted only by the scanner,
and it must survive a registry row's deletion by design. Writes are atomic
(tmpfile + fsync + replace), and every read-modify-write holds a thread lock
plus an ``flock`` on a sidecar ``ignored-paths.json.lock`` so concurrent
deletes cannot drop each other's entries. Every function is best-effort: a read failure
yields an empty set, a write failure logs and is swallowed, so a broken
tombstone file can never block a delete, a scan or an add. See ADR-0026.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from hal0.config import paths

log = logging.getLogger(__name__)

#: Serialises read-modify-write within this process. Deletes run their file
#: cleanup in ``asyncio.to_thread`` workers, so two can overlap.
_LOCK = threading.Lock()


@contextmanager
def _locked() -> Iterator[None]:
    """Hold the in-process lock plus an ``flock`` on ``<file>.lock``.

    The sidecar lock covers a second process (a root-run CLI next to the
    API). If the lock file cannot be opened, the in-process lock still
    applies and the failure is logged, keeping every operation best-effort.
    """
    with _LOCK:
        lock_path = paths.model_tombstones_path().with_suffix(".json.lock")
        fd: int | None = None
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o664)
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as exc:
            log.warning("tombstones.lock_failed path=%s err=%s", lock_path, exc)
            if fd is not None:
                with suppress(OSError):
                    os.close(fd)
                fd = None
        try:
            yield
        finally:
            if fd is not None:
                with suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
                with suppress(OSError):
                    os.close(fd)


def _norm(p: str | Path) -> str:
    """Resolved absolute form of ``p`` (falls back to the expanded path)."""
    path = Path(p).expanduser()
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def load() -> set[str]:
    """Return the set of tombstoned resolved paths (empty on any error)."""
    target = paths.model_tombstones_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except (OSError, ValueError) as exc:
        log.warning("tombstones.read_failed path=%s err=%s", target, exc)
        return set()
    entries = raw.get("paths") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return set()
    return {e for e in entries if isinstance(e, str) and e}


class TombstoneWriteError(OSError):
    """The tombstone file could not be written; the entries are NOT persisted."""


def _write(entries: set[str]) -> bool:
    """Atomically replace the tombstone file. Returns whether it was written."""
    target = paths.model_tombstones_path()
    payload = json.dumps({"version": 1, "paths": sorted(entries)}, indent=2) + "\n"
    tmp: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_str = tempfile.mkstemp(prefix=".ignored-paths-", suffix=".tmp", dir=target.parent)
        tmp = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
                # mkstemp makes 0600; keep the file group-readable for the
                # hal0-user API when a root-run CLI path writes it (same
                # reason as hal0.stacks.state.write_stack_state_atomic).
                os.fchmod(f.fileno(), 0o664)
        except BaseException:
            with suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp, target)
        tmp = None
        return True
    except OSError as exc:
        log.warning("tombstones.write_failed path=%s err=%s", target, exc)
        return False
    finally:
        if tmp is not None:
            with suppress(OSError):
                tmp.unlink(missing_ok=True)


def add(path_list: Iterable[str | Path]) -> list[str]:
    """Tombstone every path in ``path_list``; return the normalised entries added.

    Raises :class:`TombstoneWriteError` when the file cannot be written (full
    or read-only filesystem), so a caller never reports a path as hidden from
    scans when it is not.
    """
    new = {_norm(p) for p in path_list if str(p)}
    if not new:
        return []
    with _locked():
        current = load()
        added = sorted(new - current)
        if added and not _write(current | new):
            raise TombstoneWriteError(f"could not write {paths.model_tombstones_path()}")
    return added


def clear(path: str | Path) -> list[str]:
    """Drop every tombstone at or below ``path``; return the entries removed.

    Both the resolved and the literal (expanded) form are matched, so a
    symlinked path the operator re-adds clears the entry its target got.
    Never raises — an explicit add or pull must not fail on this.
    """
    try:
        forms = {Path(_norm(path)), Path(path).expanduser()}
        with _locked():
            current = load()
            if not current:
                return []
            removed = sorted(
                e for e in current if any(Path(e) == f or Path(e).is_relative_to(f) for f in forms)
            )
            if removed:
                _write(current - set(removed))
        return removed
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("tombstones.clear_failed path=%s err=%s", path, exc)
        return []


def is_tombstoned(path: str | Path, entries: set[str]) -> bool:
    """True when ``path`` or any of its parents is in ``entries``.

    ``path`` is compared as given — callers pass the resolved path the scanner
    already computed, so this does no filesystem I/O.
    """
    if not entries:
        return False
    p = Path(path)
    return str(p) in entries or any(str(parent) in entries for parent in p.parents)


__all__ = ["TombstoneWriteError", "add", "clear", "is_tombstoned", "load"]
