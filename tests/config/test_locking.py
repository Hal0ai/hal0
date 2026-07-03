"""Unit tests for the shared advisory ``file_lock`` helper (SC-10).

Mirrors the intent of the existing ``hal0.mcp.installed._registry_lock``:
an exclusive cross-process advisory lock on a sibling ``<target>.lock`` that
serializes a read-modify-write. The lock is also re-entrant within a single
process/thread so a locked outer writer (e.g. ``initialize_if_missing``) can
call a locked inner writer (``auto_migrate_capabilities_file``) without a
self-deadlock.
"""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path
from typing import Any

from hal0.config.locking import file_lock


def _hold_then_release(target: str, barrier: Any, hold_s: float, events: Any) -> None:
    """Acquire the lock, record acquire/release timestamps around a hold."""
    barrier.wait()
    with file_lock(target):
        events.put(("holder_acquire", time.monotonic()))
        time.sleep(hold_s)
        events.put(("holder_release", time.monotonic()))


def _contender(target: str, barrier: Any, head_start_s: float, events: Any) -> None:
    """Wait for the holder to grab the lock first, then block on acquire."""
    barrier.wait()
    time.sleep(head_start_s)  # let the holder win the lock first
    with file_lock(target):
        events.put(("contender_acquire", time.monotonic()))


def test_second_process_blocks_until_first_releases(tmp_path: Path) -> None:
    target = tmp_path / "thing.toml"

    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)
    events: Any = ctx.Queue()

    holder = ctx.Process(target=_hold_then_release, args=(str(target), barrier, 0.5, events))
    contender = ctx.Process(target=_contender, args=(str(target), barrier, 0.1, events))
    holder.start()
    contender.start()
    holder.join(timeout=15)
    contender.join(timeout=15)
    assert holder.exitcode == 0 and contender.exitcode == 0

    timeline: dict[str, float] = {}
    while not events.empty():
        name, ts = events.get()
        timeline[name] = ts

    assert set(timeline) == {"holder_acquire", "holder_release", "contender_acquire"}
    # The contender must not enter the critical section until the holder left.
    assert timeline["contender_acquire"] >= timeline["holder_release"], (
        "contender acquired the lock before the holder released it"
    )


def test_creates_sibling_lock_file(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "cfg.toml"
    with file_lock(target):
        pass
    assert (tmp_path / "sub" / "cfg.toml.lock").exists()


def test_reentrant_within_same_process(tmp_path: Path) -> None:
    """A nested acquire on the same path in the same thread must not deadlock."""
    target = tmp_path / "cfg.toml"
    with file_lock(target), file_lock(target):
        entered = True
    assert entered
