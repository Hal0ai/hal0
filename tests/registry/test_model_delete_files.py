"""Model delete removes files by default, behind guards (ADR-0026).

Drives :func:`hal0.services.models_service.cascade_delete_model` — the one
path both ``DELETE /api/models/{id}`` and ``hal0 model rm`` reach — against a
real SQLite registry and a real temp file tree.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from hal0.config.schema import ModelsConfig
from hal0.db import repository
from hal0.db.connection import connect, tx
from hal0.registry import tombstones
from hal0.registry.discover import scan_and_register
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.services import models_service as svc


@pytest.fixture
def root(tmp_hal0_home: str) -> Path:
    r = Path(tmp_hal0_home) / "models-root"
    r.mkdir()
    return r


@pytest.fixture
def registry(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(registry_dir=tmp_path / "registry")


def _write(p: Path, size: int = 64) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * size)
    return p


def _delete(
    registry: ModelRegistry, model_id: str, roots: list[Path], *, delete_files: bool = True
) -> dict[str, Any]:
    return asyncio.run(
        svc.cascade_delete_model(
            registry, None, model_id, [], delete_files=delete_files, roots=[str(r) for r in roots]
        )
    )


def _kept(out: dict[str, Any]) -> dict[str, str]:
    return {k["path"]: k["reason"] for k in out["files_kept"]}


def test_removes_path_shards_and_mmproj_and_prunes_empty_dirs(
    registry: ModelRegistry, root: Path
) -> None:
    d = root / "org" / "big-model"
    s1 = _write(d / "big-00001-of-00002.gguf", 100)
    s2 = _write(d / "big-00002-of-00002.gguf", 50)
    mm = _write(d / "mmproj-F16.gguf", 10)
    registry.add(
        Model(
            id="big",
            path=str(s1),
            mmproj=str(mm),
            metadata={"shards": [str(s1), str(s2)]},
        )
    )

    out = _delete(registry, "big", [root])

    assert out["deleted"] is True
    assert {f["path"] for f in out["files_removed"]} == {str(s1), str(s2), str(mm)}
    assert out["bytes_freed"] == 160
    assert out["files_kept"] == []
    assert out["tombstoned"] == []
    assert not registry.has("big")
    # Empty parents removed up to, never including, the root.
    assert not (root / "org").exists()
    assert root.is_dir()


def test_file_outside_roots_is_kept_and_tombstoned(
    registry: ModelRegistry, root: Path, tmp_path: Path
) -> None:
    outside = _write(tmp_path / "elsewhere" / "m.gguf")
    registry.add(Model(id="m", path=str(outside)))

    out = _delete(registry, "m", [root])

    assert out["deleted"] is True
    assert outside.exists()
    assert _kept(out) == {str(outside): "outside_roots"}
    assert out["tombstoned"] == [str(outside)]


def test_root_itself_is_never_removed(registry: ModelRegistry, root: Path) -> None:
    registry.add(Model(id="rooty", path=str(root)))

    out = _delete(registry, "rooty", [root])

    assert root.is_dir()
    assert _kept(out) == {str(root): "directory"}


def test_file_shared_with_another_model_is_kept_not_tombstoned(
    registry: ModelRegistry, root: Path
) -> None:
    d = root / "vision"
    a = _write(d / "a.gguf")
    b = _write(d / "b.gguf")
    mm = _write(d / "mmproj.gguf")
    registry.add(Model(id="a", path=str(a), mmproj=str(mm)))
    registry.add(Model(id="b", path=str(b), mmproj=str(mm)))

    out = _delete(registry, "a", [root])

    assert not a.exists()
    assert mm.exists(), "sidecar still used by model b"
    assert _kept(out) == {str(mm): "shared:b"}
    assert out["tombstoned"] == []
    assert b.exists()


def test_symlink_removes_link_and_resolved_blob(registry: ModelRegistry, root: Path) -> None:
    repo = root / "huggingface" / "hub" / "models--org--m"
    blob = _write(repo / "blobs" / ("a" * 64), 80)
    snap = repo / "snapshots" / "rev1" / "m.gguf"
    snap.parent.mkdir(parents=True)
    snap.symlink_to(os.path.relpath(blob, snap.parent))
    _write(repo / "refs" / "main", 4)
    registry.add(Model(id="hfm", path=str(snap)))

    out = _delete(registry, "hfm", [root])

    assert not os.path.lexists(snap)
    assert not blob.exists()
    assert out["bytes_freed"] == 80
    assert {f["path"] for f in out["files_removed"]} == {str(snap), str(blob)}
    # Emptied snapshot/blob dirs go; the repo dir stays (refs/ still there).
    assert not (repo / "snapshots").exists()
    assert not (repo / "blobs").exists()
    assert (repo / "refs" / "main").exists()


def test_symlink_target_shared_with_another_row_keeps_blob(
    registry: ModelRegistry, root: Path
) -> None:
    repo = root / "hub" / "models--org--m"
    blob = _write(repo / "blobs" / ("b" * 64), 80)
    snap = repo / "snapshots" / "rev1" / "m.gguf"
    snap.parent.mkdir(parents=True)
    snap.symlink_to(blob)
    registry.add(Model(id="via-link", path=str(snap)))
    registry.add(Model(id="via-blob", path=str(blob)))

    out = _delete(registry, "via-link", [root])

    assert not os.path.lexists(snap)
    assert blob.exists()
    assert _kept(out) == {str(blob): "shared:via-blob"}


def test_hardlinked_file_is_removed_with_space_warning(registry: ModelRegistry, root: Path) -> None:
    f = _write(root / "m.gguf", 100)
    other = root / "copy" / "m.gguf"
    other.parent.mkdir()
    os.link(f, other)
    registry.add(Model(id="hl", path=str(f)))

    out = _delete(registry, "hl", [root])

    assert not f.exists()
    assert other.exists()
    assert out["bytes_freed"] == 0
    assert out["files_removed"][0]["hardlinks"] == 2
    assert any("hardlink" in w for w in out["warnings"])


def test_flm_directory_is_never_removed(registry: ModelRegistry, root: Path) -> None:
    d = root / "flm" / "Qwen3-0.6B"
    _write(d / "weights.q4nx")
    registry.add(Model(id="qwen3:0.6b", path=str(d), metadata={"runtime": "flm"}))

    out = _delete(registry, "qwen3:0.6b", [root])

    assert out["deleted"] is True
    assert (d / "weights.q4nx").exists()
    assert _kept(out) == {str(d): "runtime_managed"}
    assert out["tombstoned"] == [str(d)]


def test_plain_directory_path_is_kept(registry: ModelRegistry, root: Path) -> None:
    d = root / "kokoro"
    _write(d / "model.onnx")
    registry.add(Model(id="kokoro", path=str(d)))

    out = _delete(registry, "kokoro", [root])

    assert (d / "model.onnx").exists()
    assert _kept(out) == {str(d): "directory"}


def test_opt_out_keeps_files_and_rescan_skips_them(registry: ModelRegistry, root: Path) -> None:
    f = _write(root / "keepme.gguf")
    registry.add(Model(id="keepme", path=str(f)))

    out = _delete(registry, "keepme", [root], delete_files=False)

    assert out["deleted"] is True
    assert f.exists()
    assert _kept(out) == {str(f): "kept_by_request"}
    assert out["tombstoned"] == [str(f)]

    result = scan_and_register(registry, ModelsConfig(roots=[str(root)]))
    assert result["added"] == []
    assert not registry.has("keepme")


def test_explicit_add_clears_tombstone(registry: ModelRegistry, root: Path) -> None:
    f = _write(root / "back.gguf")
    registry.add(Model(id="back", path=str(f)))
    _delete(registry, "back", [root], delete_files=False)
    assert tombstones.load() == {str(f)}

    asyncio.run(svc.add_from_path({"path": str(f)}, registry=registry, event_bus=None))

    assert tombstones.load() == set()


def test_scan_commit_clears_tombstone(registry: ModelRegistry, root: Path) -> None:
    f = _write(root / "committed.gguf")
    tombstones.add([f])

    result = asyncio.run(svc.commit_scan_rows([{"path": str(f)}], registry, None))

    assert result["added"]
    assert tombstones.load() == set()


def test_pulled_file_releases_store_blob_ref(registry: ModelRegistry, root: Path) -> None:
    """A removed pulled file drops its store_blob reference (refcount → 0
    deletes the row's bytes and leaves no dangling blob_path)."""
    f = _write(root / "pulled" / "p.gguf", 32)
    registry.add(Model(id="pulled", path=str(f)))
    sha = "c" * 64
    with connect(registry.db_path) as conn, tx(conn):
        repository.insert_blob(conn, sha256=sha, size_bytes=32, blob_path=str(f))
        repository.upsert_model_file(
            conn,
            model_id="pulled",
            rel="p.gguf",
            dest=str(f),
            size_bytes=32,
            sha256=sha,
            lfs=True,
            role="model",
        )

    out = _delete(registry, "pulled", [root])

    assert not f.exists()
    assert out["warnings"] == []
    with connect(registry.db_path) as conn:
        blob = repository.get_blob(conn, sha)
    assert blob is None or blob["refcount"] == 0


def test_unlink_failure_is_reported_not_raised(
    registry: ModelRegistry, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = _write(root / "stuck.gguf")
    registry.add(Model(id="stuck", path=str(f)))
    real_unlink = os.unlink

    def boom(path: Any, *, dir_fd: int | None = None) -> None:
        if str(path) == f.name:
            raise PermissionError(13, "Permission denied")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", boom)

    out = _delete(registry, "stuck", [root])

    assert out["deleted"] is True
    assert not registry.has("stuck")
    assert f.exists()
    reason = _kept(out)[str(f)]
    assert reason.startswith("unlink_failed:")
    assert out["tombstoned"] == [str(f)]


def test_hal0_state_files_are_never_removed_even_under_a_broad_root(
    registry: ModelRegistry, tmp_hal0_home: str
) -> None:
    """A scan root of the whole tree must not expose slot TOMLs or the DB."""
    from hal0.config import paths

    slot_toml = _write(paths.slots_config_dir() / "chat.toml")
    registry.add(Model(id="oops", path=str(slot_toml)))

    out = _delete(registry, "oops", [Path(tmp_hal0_home)])

    assert slot_toml.exists()
    assert _kept(out) == {str(slot_toml): "hal0_state"}


# ── PR #2543 review follow-ups ───────────────────────────────────────────────


def test_parent_pruning_stops_at_the_deepest_overlapping_root(
    registry: ModelRegistry, root: Path
) -> None:
    """With roots ``/x`` and ``/x/models-root``, emptied dirs are removed only
    up to ``models-root`` — never the more specific root itself."""
    f = _write(root / "sub" / "m.gguf")
    registry.add(Model(id="m", path=str(f)))

    out = _delete(registry, "m", [root.parent, root])

    assert {e["path"] for e in out["files_removed"]} == {str(f)}
    assert not (root / "sub").exists()
    assert root.is_dir()


def test_missing_file_at_delete_is_tombstoned_and_not_rediscovered(
    registry: ModelRegistry, root: Path
) -> None:
    """An unmounted share at delete time: the row goes, the path is tombstoned,
    and the file is not re-registered when the storage comes back."""
    f = root / "share" / "m.gguf"
    registry.add(Model(id="m", path=str(f)))

    out = _delete(registry, "m", [root])

    assert out["deleted"] is True
    assert _kept(out) == {str(f): "missing_at_delete"}
    assert out["tombstoned"] == [str(f)]
    _write(f)  # the share is back
    assert scan_and_register(registry, ModelsConfig(roots=[str(root)]))["added"] == []


def test_dangling_symlink_target_is_tombstoned(registry: ModelRegistry, root: Path) -> None:
    target = root / "blobs-gone" / ("d" * 64)
    link = root / "snap" / "m.gguf"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    registry.add(Model(id="m", path=str(link)))

    out = _delete(registry, "m", [root])

    assert not os.path.lexists(link)
    assert _kept(out) == {str(target): "missing_at_delete"}
    assert str(target) in out["tombstoned"]


def test_files_under_a_remaining_directory_model_are_kept(
    registry: ModelRegistry, root: Path
) -> None:
    d = root / "moonshine"
    inner = _write(d / "encoder.onnx")
    registry.add(Model(id="moonshine-dir", path=str(d)))
    registry.add(Model(id="encoder-file", path=str(inner)))

    out = _delete(registry, "encoder-file", [root])

    assert inner.exists()
    assert _kept(out) == {str(inner): "shared:moonshine-dir"}
    assert out["tombstoned"] == []


# ── PR #2543 review round 2 ──────────────────────────────────────────────────


def test_directory_swapped_for_symlink_after_checks_is_not_followed(
    registry: ModelRegistry, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOCTOU: another account swaps a checked directory for a symlink to an
    outside directory between the guards and the unlink. The fd-based unlink
    must refuse instead of deleting the same-named file outside the roots."""
    f = _write(root / "sub" / "m.gguf")
    outside = _write(tmp_path / "victim" / "m.gguf")
    registry.add(Model(id="m", path=str(f)))
    real_guard = svc._in_hal0_state
    swapped = {"done": False}

    def swap_then_check(p: Path, guard: Any) -> bool:
        if not swapped["done"]:
            (root / "sub").rename(root / "sub-moved")
            (root / "sub").symlink_to(outside.parent)
            swapped["done"] = True
        return real_guard(p, guard)

    monkeypatch.setattr(svc, "_in_hal0_state", swap_then_check)

    out = _delete(registry, "m", [root])

    assert outside.exists(), "file outside the roots must survive"
    assert _kept(out) == {str(f): "path_changed"}


def test_prune_empty_parents_does_not_follow_a_swapped_symlink(root: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim-dir"
    (victim / "empty").mkdir(parents=True)
    (root / "a").symlink_to(victim)

    svc._prune_empty_parents(root / "a" / "empty", root, set())

    assert (victim / "empty").is_dir()


def test_tombstone_write_failure_is_reported(
    registry: ModelRegistry, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = _write(root / "kept.gguf")
    registry.add(Model(id="kept", path=str(f)))
    monkeypatch.setattr(tombstones, "_write", lambda entries: False)

    out = _delete(registry, "kept", [root], delete_files=False)

    assert out["tombstoned"] == []
    assert any(w.startswith("tombstone_failed") for w in out["warnings"])


def test_keep_files_releases_blob_ref_without_touching_bytes(
    registry: ModelRegistry, root: Path
) -> None:
    f = _write(root / "pulled" / "p.gguf", 32)
    registry.add(Model(id="pulled", path=str(f)))
    sha = "f" * 64
    with connect(registry.db_path) as conn, tx(conn):
        repository.insert_blob(conn, sha256=sha, size_bytes=32, blob_path=str(f))
        repository.upsert_model_file(
            conn,
            model_id="pulled",
            rel="p.gguf",
            dest=str(f),
            size_bytes=32,
            sha256=sha,
            lfs=True,
            role="model",
        )

    _delete(registry, "pulled", [root], delete_files=False)

    assert f.exists(), "kept bytes stay on disk"
    with connect(registry.db_path) as conn:
        assert repository.get_blob(conn, sha) is None, "no stale positive refcount"


def test_delete_holds_the_scan_lock_during_row_removal_and_cleanup(
    registry: ModelRegistry, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.registry.discover import SCAN_DELETE_LOCK

    f = _write(root / "m.gguf")
    registry.add(Model(id="m", path=str(f)))
    seen: dict[str, bool] = {}
    real_remove = registry.remove
    real_cleanup = svc.delete_model_files_on_disk

    def remove(mid: str) -> bool:
        seen["remove"] = SCAN_DELETE_LOCK.locked()
        return real_remove(mid)

    def cleanup(*a: Any, **kw: Any) -> dict[str, Any]:
        seen["cleanup"] = SCAN_DELETE_LOCK.locked()
        return real_cleanup(*a, **kw)

    monkeypatch.setattr(registry, "remove", remove)
    monkeypatch.setattr(svc, "delete_model_files_on_disk", cleanup)

    _delete(registry, "m", [root])

    assert seen == {"remove": True, "cleanup": True}


def test_scan_waits_for_the_scan_delete_lock(registry: ModelRegistry, root: Path) -> None:
    import threading

    from hal0.registry.discover import SCAN_DELETE_LOCK

    _write(root / "new-q4.gguf")
    done = threading.Event()

    def scan() -> None:
        scan_and_register(registry, ModelsConfig(roots=[str(root)]))
        done.set()

    with SCAN_DELETE_LOCK:
        t = threading.Thread(target=scan)
        t.start()
        assert not done.wait(0.3), "scan must block while a delete holds the lock"
    t.join(10)
    assert done.is_set()


def test_delete_cancels_an_active_pull_and_waits(registry: ModelRegistry, root: Path) -> None:
    from hal0.registry.pull import make_job

    f = _write(root / "m.gguf")
    registry.add(Model(id="m", path=str(f)))
    job = make_job("m")
    job.state = "running"

    async def run() -> dict[str, Any]:
        async def pull_task() -> None:
            while not job.cancel_requested:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            job.state = "cancelled"
            job._signal()

        task = asyncio.create_task(pull_task())
        out = await svc.cascade_delete_model(
            registry, None, "m", [], roots=[str(root)], pull_jobs={"m": job}
        )
        await task
        return out

    out = asyncio.run(run())

    assert job.cancel_requested is True
    assert job.state == "cancelled"
    assert out["deleted"] is True
    assert not f.exists()


def test_delete_refuses_while_a_pull_will_not_stop(registry: ModelRegistry, root: Path) -> None:
    from hal0.errors import Conflict
    from hal0.registry.pull import make_job

    f = _write(root / "m.gguf")
    registry.add(Model(id="m", path=str(f)))
    job = make_job("m")
    job.state = "running"

    with pytest.raises(Conflict):
        asyncio.run(svc.cancel_active_pull({"m": job}, "m", timeout_s=0.2))

    assert registry.has("m")
    assert f.exists()


# ── PR #2543 review round 3 ──────────────────────────────────────────────────


def test_owner_check_failure_fails_closed(
    registry: ModelRegistry, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the remaining rows can't be read, no file is deleted: sharing
    can't be ruled out. The row delete still goes through."""
    f = _write(root / "m.gguf")
    registry.add(Model(id="m", path=str(f)))

    def boom() -> list[Model]:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(registry, "list", boom)

    out = _delete(registry, "m", [root])

    assert out["deleted"] is True
    assert f.exists()
    assert _kept(out) == {str(f): "owner_check_failed"}
    assert out["tombstoned"] == [str(f)]
    assert any(w.startswith("owner_check_failed") for w in out["warnings"])


def test_model_file_read_failure_also_fails_closed(
    registry: ModelRegistry, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = _write(root / "m.gguf")
    registry.add(Model(id="m", path=str(f)))
    real_rows = svc._model_file_rows

    def rows(reg: Any, model_id: str | None = None, *, strict: bool = False) -> list[Any]:
        if strict:
            raise RuntimeError("disk I/O error")
        return real_rows(reg, model_id)

    monkeypatch.setattr(svc, "_model_file_rows", rows)

    out = _delete(registry, "m", [root])

    assert f.exists()
    assert _kept(out) == {str(f): "owner_check_failed"}


def test_directory_reported_kept_is_not_rmdired(registry: ModelRegistry, root: Path) -> None:
    d = root / "onnx-model"
    mm = _write(d / "mmproj.gguf")
    registry.add(Model(id="onnx", path=str(d), mmproj=str(mm)))

    out = _delete(registry, "onnx", [root])

    assert not mm.exists()
    assert d.is_dir(), "a directory reported as kept must survive empty-parent pruning"
    assert _kept(out) == {str(d): "directory"}


def test_new_pull_is_refused_while_the_model_is_being_deleted(
    registry: ModelRegistry, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.errors import Conflict
    from hal0.registry import pull as pull_mod

    f = _write(root / "m.gguf")
    registry.add(Model(id="m", path=str(f)))
    seen: dict[str, bool] = {}

    async def probe(pull_jobs: Any, model_id: str, **kw: Any) -> None:
        try:
            pull_mod.ensure_not_deleting(model_id)
            seen["refused"] = False
        except Conflict as exc:
            seen["refused"] = exc.code == "model.deleting"

    monkeypatch.setattr(svc, "cancel_active_pull", probe)

    _delete(registry, "m", [root])

    assert seen == {"refused": True}
    pull_mod.ensure_not_deleting("m")  # the guard is released after the delete
