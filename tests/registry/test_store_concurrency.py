"""Cross-process write serialization for ModelRegistry (MR-5 regression).

These tests reproduce the "lost update" defect: two writers each do a
read-modify-write of ``registry.toml`` (read current models → add a row →
atomic ``os.replace``). The per-instance ``threading.RLock`` only serializes
threads *inside one ModelRegistry instance*; it does nothing across separate
instances or separate processes. Without a shared file lock, the second
writer's ``os.replace`` clobbers the first writer's freshly-committed row.

The fix adds a POSIX advisory ``fcntl.flock`` on a stable sidecar lockfile
(``registry.toml.lock``) around each mutator's critical section, so the two
read-modify-write sections cannot interleave.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import threading
import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.registry.model import Model

# ML-1: `ModelRegistry` now names the SQLite-backed store; this file
# regression-tests the TOML store's cross-process sidecar flock
# specifically, so it exercises `TomlModelRegistry` under the local name
# `ModelRegistry`. SQLite's own `BEGIN IMMEDIATE` write-lock equivalent is
# covered separately in tests/registry/test_sqlite_store.py.
from hal0.registry.store import TomlModelRegistry as ModelRegistry


def _model(model_id: str, path: str = "/models/x.gguf") -> Model:
    return Model(id=model_id, path=path)


def _read_ids_from_disk(registry_dir: Path) -> set[str]:
    """Re-read registry.toml straight off disk (no cache) and return ids."""
    toml_path = registry_dir / "registry.toml"
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    return set(data.get("models", {}).keys())


def _slow_atomic_write_wrapper(reg: ModelRegistry, delay: float) -> None:
    """Patch reg._atomic_write to sleep AFTER read, before os.replace.

    Widening the read→write window guarantees the two critical sections
    interleave, making the lost-update deterministic when unserialized.
    """
    import time

    original = reg._atomic_write

    def slow(models: dict[str, Model]) -> None:
        time.sleep(delay)
        original(models)

    reg._atomic_write = slow  # type: ignore[method-assign]


# ── two-instance (models two processes within one pytest process) ────────────


def test_two_instances_no_lost_update(tmp_path: Path) -> None:
    """Two ModelRegistry instances on the same dir must not drop rows.

    Each instance opens its own fd, so ``fcntl.flock(LOCK_EX)`` is mutually
    exclusive per open file description — this models two processes even
    within a single pytest process.

    Pre-fix: fails — RLock is per-instance so nothing serializes the two
    read-modify-write sections; the later ``os.replace`` drops the other row.
    Post-fix: passes — the sidecar flock serializes the critical sections and
    each re-reads the committed base under the lock.
    """
    registry_dir = tmp_path / "registry"
    reg_a = ModelRegistry(registry_dir=registry_dir)
    reg_b = ModelRegistry(registry_dir=registry_dir)

    # Seed a base row so both writers must preserve it.
    reg_a.add(_model("base"))

    # Widen the read→write window on BOTH instances so the interleave is
    # deterministic without the fix.
    _slow_atomic_write_wrapper(reg_a, 0.05)
    _slow_atomic_write_wrapper(reg_b, 0.05)

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def add_on(reg: ModelRegistry, mid: str) -> None:
        try:
            barrier.wait()
            reg.add(_model(mid))
        except BaseException as exc:
            errors.append(exc)

    t_a = threading.Thread(target=add_on, args=(reg_a, "a"))
    t_b = threading.Thread(target=add_on, args=(reg_b, "b"))
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    assert not errors, f"writer raised: {errors!r}"

    ids = _read_ids_from_disk(registry_dir)
    assert ids == {"base", "a", "b"}, f"lost update: on-disk ids = {ids!r}"


# ── true cross-process via multiprocessing ───────────────────────────────────


def _child_add(hal0_home: str, model_id: str, barrier: Any, delay: float) -> None:
    """Child process: construct a ModelRegistry on HAL0_HOME and add a row."""
    import time

    os.environ["HAL0_HOME"] = hal0_home
    # Re-import inside the child so paths resolve against this env.
    from hal0.registry.model import Model as _Model
    from hal0.registry.store import TomlModelRegistry as _Registry

    reg = _Registry()

    original = reg._atomic_write

    def slow(models: dict) -> None:
        time.sleep(delay)
        original(models)

    reg._atomic_write = slow  # type: ignore[method-assign]

    barrier.wait()
    reg.add(_Model(id=model_id, path="/models/x.gguf"))


def test_multiprocessing_no_lost_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two child processes adding distinct rows: both must survive.

    This proves true cross-process safety — separate interpreters, separate
    fds, coordinated only by the on-disk sidecar flock.
    """
    hal0_home = tmp_path / "home"
    hal0_home.mkdir(parents=True, exist_ok=True)

    # Seed a base row using a parent-side registry rooted at the same HAL0_HOME.
    # monkeypatch.setenv auto-restores HAL0_HOME on teardown so this never
    # leaks into later tests (test_models_config default-path assertions).
    monkeypatch.setenv("HAL0_HOME", str(hal0_home))
    from hal0.config import paths

    seed = ModelRegistry()
    seed.add(_model("base"))
    registry_dir = paths.registry_dir()

    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2)
    procs = [
        ctx.Process(target=_child_add, args=(str(hal0_home), "a", barrier, 0.05)),
        ctx.Process(target=_child_add, args=(str(hal0_home), "b", barrier, 0.05)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0, f"child exited with {p.exitcode}"

    ids = _read_ids_from_disk(registry_dir)
    assert ids == {"base", "a", "b"}, f"lost update: on-disk ids = {ids!r}"


# ── CLI import (`_atomic_copy`) must not clobber a concurrent store add ───────


def test_registry_import_does_not_drop_concurrent_add(tmp_path: Path) -> None:
    """`registry import --force` (_atomic_copy) serializes with a store add.

    Timeline (both threads released by one barrier):

    * ``add`` enters ``ModelRegistry.add`` immediately, acquires the lock,
      reads ``{base}``, then its patched ``_atomic_write`` sleeps 0.1s before
      ``os.replace`` — a wide read→write window.
    * ``import`` sleeps 0.02s (so ``add`` is mid-critical-section), then runs
      ``_atomic_copy`` of a backup containing only ``imported``.

    Pre-fix: nothing serializes them. At t≈0.02 the import ``os.replace``-s the
    file to ``{imported}``; at t≈0.1 the add's ``os.replace`` — built from its
    stale ``{base}`` read — lands last and silently clobbers the import,
    resurrecting ``base`` and producing ``{base, a}``. The import "succeeded"
    but its result vanished.

    Post-fix: ``add`` holds the sidecar flock for its whole critical section,
    so ``_atomic_copy`` blocks until t≈0.1, then replaces the file cleanly —
    on-disk state is exactly ``{imported}``, the import's complete result.
    """
    import time

    from hal0.cli import registry_commands

    registry_dir = tmp_path / "registry"
    reg = ModelRegistry(registry_dir=registry_dir)
    reg.add(_model("base"))
    dest = reg.registry_file

    # A backup file containing only "imported" (no "base", no "a").
    backup = tmp_path / "backup.toml"
    backup.write_text('[models.imported]\npath = "/models/imported.gguf"\n')

    _slow_atomic_write_wrapper(reg, 0.1)

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def do_add() -> None:
        try:
            barrier.wait()
            reg.add(_model("a"))
        except BaseException as exc:
            errors.append(exc)

    def do_import() -> None:
        try:
            barrier.wait()
            time.sleep(0.02)  # let the add enter its critical section first
            registry_commands._atomic_copy(backup, dest)
        except BaseException as exc:
            errors.append(exc)

    t_add = threading.Thread(target=do_add)
    t_import = threading.Thread(target=do_import)
    t_add.start()
    t_import.start()
    t_add.join()
    t_import.join()

    assert not errors, f"worker raised: {errors!r}"

    ids = _read_ids_from_disk(registry_dir)
    assert ids == {"imported"}, f"import clobbered by stale store write: {ids!r}"
