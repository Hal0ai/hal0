"""hal0.registry.gc — refcounted blob orphan prune + guarded delete (ML-3).

Covers: refcount inc/dec (via db.repository, exercised end-to-end here),
orphan collection/pruning (dry-run vs real), and delete_model_files'
assert_under_store-before-unlink guard.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hal0.db import repository
from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate
from hal0.registry import gc


def _seed_model(conn, model_id: str, path: str) -> None:
    migrate(conn)
    with tx(conn):
        conn.execute(
            "INSERT INTO model (id, path, name) VALUES (?, ?, ?)",
            (model_id, path, model_id),
        )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "hal0.db"


class TestCollectAndPruneOrphans:
    def test_collect_orphans_finds_zero_refcount_blobs(self, db_path: Path, tmp_path: Path) -> None:
        blob_file = tmp_path / "blob-a"
        blob_file.write_bytes(b"x")
        with connect(db_path) as conn:
            migrate(conn)
            with tx(conn):
                repository.insert_blob(
                    conn, sha256="sha-a", size_bytes=1, blob_path=str(blob_file), refcount=0
                )
            orphans = gc.collect_orphans(conn)
        assert orphans == [str(blob_file)]

    def test_referenced_blob_is_not_an_orphan(self, db_path: Path, tmp_path: Path) -> None:
        blob_file = tmp_path / "blob-b"
        blob_file.write_bytes(b"x")
        with connect(db_path) as conn:
            migrate(conn)
            with tx(conn):
                repository.insert_blob(
                    conn, sha256="sha-b", size_bytes=1, blob_path=str(blob_file), refcount=1
                )
            assert gc.collect_orphans(conn) == []

    def test_prune_orphans_dry_run_does_not_delete(
        self, db_path: Path, tmp_path: Path, monkeypatch
    ) -> None:
        blob_file = tmp_path / "blob-c"
        blob_file.write_bytes(b"x")
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        with connect(db_path) as conn:
            migrate(conn)
            with tx(conn):
                repository.insert_blob(
                    conn, sha256="sha-c", size_bytes=1, blob_path=str(blob_file), refcount=0
                )
            report = gc.prune_orphans(conn, dry_run=True)
        assert report.orphans_found == 1
        assert report.orphans_deleted == 0
        assert blob_file.exists()

    def test_prune_orphans_real_deletes_blob_and_row(
        self, db_path: Path, tmp_path: Path, monkeypatch
    ) -> None:
        blob_file = tmp_path / "blob-d"
        blob_file.write_bytes(b"x" * 10)
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        with connect(db_path) as conn:
            migrate(conn)
            with tx(conn):
                repository.insert_blob(
                    conn, sha256="sha-d", size_bytes=10, blob_path=str(blob_file), refcount=0
                )
            report = gc.prune_orphans(conn, dry_run=False)
            assert report.orphans_deleted == 1
            assert report.bytes_reclaimed == 10
            assert not blob_file.exists()
            assert repository.get_blob(conn, "sha-d") is None

    def test_prune_orphans_refcount_guard_never_deletes_referenced(
        self, db_path: Path, tmp_path: Path, monkeypatch
    ) -> None:
        """The GC contract's core safety property: refcount>0 blobs are
        never candidates for deletion, dry_run or not."""
        blob_file = tmp_path / "blob-e"
        blob_file.write_bytes(b"x")
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        with connect(db_path) as conn:
            migrate(conn)
            with tx(conn):
                repository.insert_blob(
                    conn, sha256="sha-e", size_bytes=1, blob_path=str(blob_file), refcount=3
                )
            report = gc.prune_orphans(conn, dry_run=False)
        assert report.orphans_found == 0
        assert blob_file.exists()

    def test_prune_orphans_escape_guard_before_unlink(
        self, db_path: Path, tmp_path: Path, monkeypatch
    ) -> None:
        """A blob_path outside the configured store root must never be
        unlinked — assert_under_store fires first and the row is skipped
        (not silently deleted), surfaced via report.errors."""
        outside = tmp_path / "outside" / "blob-f"
        outside.parent.mkdir()
        outside.write_bytes(b"x")
        store_root = tmp_path / "store"
        store_root.mkdir()
        monkeypatch.setenv("HAL0_MODEL_STORE", str(store_root))
        with connect(db_path) as conn:
            migrate(conn)
            with tx(conn):
                repository.insert_blob(
                    conn, sha256="sha-f", size_bytes=1, blob_path=str(outside), refcount=0
                )
            report = gc.prune_orphans(conn, dry_run=False)
        assert report.orphans_deleted == 0
        assert report.errors
        assert outside.exists()  # never unlinked


class TestDeleteModelFiles:
    def test_delete_unlinks_dest_and_drops_blob_at_zero_refcount(
        self, db_path: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        dest = tmp_path / "models--org--repo" / "snapshots" / "rev" / "model.gguf"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"x" * 5)
        with connect(db_path) as conn:
            _seed_model(conn, "my-model", str(dest))
            with tx(conn):
                repository.insert_blob(
                    conn, sha256="sha-g", size_bytes=5, blob_path=str(dest), refcount=1
                )
                repository.insert_model_file(
                    conn,
                    model_id="my-model",
                    rel="model.gguf",
                    dest=str(dest),
                    sha256="sha-g",
                    role="model",
                )
            removed = gc.delete_model_files(conn, "my-model")
            assert removed == 1
            # The blob's bytes are unlinked immediately (we already hold the
            # row lock for this model's files); the store_blob ROW itself
            # persists at refcount=0 for a subsequent prune_orphans sweep
            # to reap (this is the orphan it will collect).
            row = repository.get_blob(conn, "sha-g")
            assert row is not None
            assert row["refcount"] == 0
        assert not dest.exists()

    def test_delete_decrements_shared_blob_refcount_without_deleting_it(
        self, db_path: Path, tmp_path: Path, monkeypatch
    ) -> None:
        """Two models sharing a hardlinked blob: deleting one must only
        decrement the refcount, never remove bytes still referenced by the
        other model's file row."""
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        blob_path = tmp_path / "models--org--repo" / "snapshots" / "rev" / "shared.gguf"
        blob_path.parent.mkdir(parents=True)
        blob_path.write_bytes(b"x" * 5)
        dest_b = tmp_path / "models--org--repo2" / "snapshots" / "rev" / "shared.gguf"
        dest_b.parent.mkdir(parents=True)
        dest_b.write_bytes(b"x" * 5)  # hardlink stand-in for the test
        with connect(db_path) as conn:
            _seed_model(conn, "model-a", str(blob_path))
            with tx(conn):
                conn.execute(
                    "INSERT INTO model (id, path, name) VALUES (?, ?, ?)",
                    ("model-b", str(dest_b), "model-b"),
                )
                repository.insert_blob(
                    conn, sha256="sha-shared", size_bytes=5, blob_path=str(blob_path), refcount=2
                )
                repository.insert_model_file(
                    conn,
                    model_id="model-a",
                    rel="shared.gguf",
                    dest=str(blob_path),
                    sha256="sha-shared",
                    role="model",
                )
                repository.insert_model_file(
                    conn,
                    model_id="model-b",
                    rel="shared.gguf",
                    dest=str(dest_b),
                    sha256="sha-shared",
                    role="model",
                )
            gc.delete_model_files(conn, "model-a")
            blob_row = repository.get_blob(conn, "sha-shared")
            assert blob_row is not None
            assert blob_row["refcount"] == 1
        # model-b's dest is untouched; the blob still has one referent.
        assert dest_b.exists()

    def test_delete_repoints_canonical_blob_path_to_surviving_referent(
        self, db_path: Path, tmp_path: Path, monkeypatch
    ) -> None:
        """#8: deleting the model that owns a shared blob's CANONICAL
        blob_path must re-point blob_path at a surviving referent's live
        hardlink — never leave it dangling at the just-unlinked dest."""
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        # model-a is canonical (blob_path == its dest); model-b hardlinks it.
        dest_a = tmp_path / "models--org--a" / "snapshots" / "rev" / "shared.gguf"
        dest_a.parent.mkdir(parents=True)
        dest_a.write_bytes(b"x" * 5)
        dest_b = tmp_path / "models--org--b" / "snapshots" / "rev" / "shared.gguf"
        dest_b.parent.mkdir(parents=True)
        os.link(dest_a, dest_b)  # real hardlink: same inode, live via either path
        with connect(db_path) as conn:
            _seed_model(conn, "model-a", str(dest_a))
            with tx(conn):
                conn.execute(
                    "INSERT INTO model (id, path, name) VALUES (?, ?, ?)",
                    ("model-b", str(dest_b), "model-b"),
                )
                repository.insert_blob(
                    conn, sha256="sha-shared", size_bytes=5, blob_path=str(dest_a), refcount=2
                )
                repository.insert_model_file(
                    conn,
                    model_id="model-a",
                    rel="shared.gguf",
                    dest=str(dest_a),
                    sha256="sha-shared",
                    role="model",
                )
                repository.insert_model_file(
                    conn,
                    model_id="model-b",
                    rel="shared.gguf",
                    dest=str(dest_b),
                    sha256="sha-shared",
                    role="model",
                )
            gc.delete_model_files(conn, "model-a")
            blob = repository.get_blob(conn, "sha-shared")
            assert blob["refcount"] == 1
            # blob_path re-pointed off the deleted dest_a onto the live dest_b.
            assert Path(blob["blob_path"]) == dest_b
        assert not dest_a.exists()  # canonical hardlink unlinked
        assert dest_b.exists()  # surviving referent (and the bytes) live on


class TestReconcileStoreTree:
    """#9: fs-walk reconcile — reap bare bytes (on disk, tracked by NO
    store_blob AND no model_file row) while retaining every tracked file and
    skipping in-flight ``.tmp`` partials."""

    def test_reaps_bare_bytes_retains_tracked_skips_partial(
        self, db_path: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        snap = tmp_path / "models--org--repo" / "snapshots" / "rev"
        snap.mkdir(parents=True)
        # (1) blob-tracked live file (store_blob.blob_path, refcount 1).
        blob_file = snap / "model.gguf"
        blob_file.write_bytes(b"x" * 8)
        # (2) model_file-only live file (non-LFS tokenizer: dest row, NO blob).
        tok = snap / "tokenizer.json"
        tok.write_bytes(b"{}")
        # (3) bare bytes: on disk, tracked by neither table.
        bare = snap / "leftover.gguf"
        bare.write_bytes(b"orphaned")
        # (4) in-flight partial under .tmp — must be skipped.
        tmp_stage = tmp_path / ".tmp"
        tmp_stage.mkdir()
        partial = tmp_stage / "job--model.gguf.part"
        partial.write_bytes(b"partial")

        with connect(db_path) as conn:
            _seed_model(conn, "m", str(blob_file))
            with tx(conn):
                repository.insert_blob(
                    conn, sha256="sha-live", size_bytes=8, blob_path=str(blob_file), refcount=1
                )
                repository.insert_model_file(
                    conn,
                    model_id="m",
                    rel="model.gguf",
                    dest=str(blob_file),
                    sha256="sha-live",
                    role="model",
                )
                repository.insert_model_file(
                    conn,
                    model_id="m",
                    rel="tokenizer.json",
                    dest=str(tok),
                    sha256=None,
                    role="tokenizer",
                )
            report = gc.reconcile_store_tree(conn, dry_run=False)

        assert report.orphans_found == 1
        assert report.orphans_deleted == 1
        assert report.bytes_reclaimed == len(b"orphaned")
        assert report.errors == []
        assert not bare.exists()  # only the bare file reaped
        assert blob_file.exists()  # store_blob-tracked retained
        assert tok.exists()  # model_file-only (non-LFS) retained
        assert partial.exists()  # in-flight .tmp partial skipped

    def test_dry_run_reports_without_unlinking(
        self, db_path: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        snap = tmp_path / "models--org--repo" / "snapshots" / "rev"
        snap.mkdir(parents=True)
        bare = snap / "leftover.gguf"
        bare.write_bytes(b"orphaned")
        with connect(db_path) as conn:
            migrate(conn)
            report = gc.reconcile_store_tree(conn, dry_run=True)
        assert report.orphans_found == 1
        assert report.orphans_deleted == 0
        assert bare.exists()

    def test_max_files_bounds_the_walk(self, db_path: Path, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
        snap = tmp_path / "models--org--repo" / "snapshots" / "rev"
        snap.mkdir(parents=True)
        for i in range(5):
            (snap / f"f{i}.bin").write_bytes(b"z")
        with connect(db_path) as conn:
            migrate(conn)
            report = gc.reconcile_store_tree(conn, dry_run=True, max_files=2)
        assert report.errors  # truncation recorded, walk stopped at the cap
