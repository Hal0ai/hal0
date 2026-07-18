"""R2 golden-path integration tests — model store + registry, end-to-end at
the db + filesystem level (plan REWORK.md §golden-path L602 / §B storage rules).

These stitch the ML-1 registry, ML-2 file-set planner, and ML-3 CAS/refcount/GC
store seams together the way a real deployment does — a pull lands bytes AND
writes registry + ``model_file`` + ``store_blob`` rows AND flips the
``by-id`` pointer — but WITHOUT a live box: the HuggingFace download layer is
stubbed at the same ``httpx.MockTransport`` seam ``tests/registry/test_pull.py``
already uses, so nothing here touches the network, podman, or systemd.

Scope note (what is deliberately NOT here — needs a live box, tracked not
dropped): a real cross-filesystem hardlink degrade-to-copy (needs two real
mounts), a real NFS mount's relabel omission (``is_nfs_path`` reads the host
``/proc/mounts`` — the pure-logic branch is exercised in
``tests/config/test_store.py``), and SELinux ``chcon`` behaviour. The
filesystem-vs-db *bare-bytes* reconcile (a regular file on disk under the
store root with NO ``store_blob`` AND no ``model_file`` row) IS implemented
now — ``hal0.registry.gc.reconcile_store_tree`` — and
``TestGcReconcilesDbVsFilesystem`` exercises BOTH halves of the GC: the
refcount-row-driven prune and the fs-walk reconcile.

Why these live alongside the existing unit tests rather than replacing them:
``test_fileset.py`` proves ``plan_fileset`` groups shards; ``test_gc.py``
proves ``prune_orphans`` respects refcount; ``test_store.py`` (config) proves
the resolver precedence. NONE of them drive ``run_pull(..., fileset=...)`` —
the generalised N-file install loop that is ML-2→ML-3's actual runtime path —
end to end. That is the gap these close.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import httpx
import pytest

from hal0.config import store
from hal0.db import repository
from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate
from hal0.registry import gc
from hal0.registry.fileset import RawTreeEntry, plan_fileset
from hal0.registry.pull import make_job, run_pull
from hal0.registry.sqlite_store import SqliteModelRegistry

# ── helpers ──────────────────────────────────────────────────────────────────


def _body(rel: str, size: int = 512) -> bytes:
    """Deterministic per-file bytes so sha256 is reproducible across runs.

    Distinct ``rel`` → distinct bytes → distinct sha256 (each becomes its own
    ``store_blob``); the same ``rel`` and size → identical bytes → identical
    sha256 (the content-addressed dedup path). A leading ``GGUF`` magic keeps
    the payload shaped like the real thing without mattering to the test.
    """
    seed = hashlib.sha256(rel.encode()).digest()
    filler = (seed * (size // len(seed) + 1))[: max(0, size - 4)]
    return b"GGUF" + filler


def _entry(rel: str, body: bytes) -> RawTreeEntry:
    """Build an HF-tree row whose advertised LFS oid IS ``body``'s sha256, so
    the pull engine's integrity check passes against the bytes we serve."""
    digest = hashlib.sha256(body).hexdigest()
    return RawTreeEntry(path=rel, size=len(body), lfs_oid=digest, lfs_size=len(body))


def _tree(files: dict[str, bytes]) -> list[RawTreeEntry]:
    return [_entry(rel, body) for rel, body in files.items()]


def _mock_client(files: dict[str, bytes]) -> httpx.AsyncClient:
    """A transport that serves ``files`` keyed by the HF ``resolve`` URL tail.

    URL shape is ``https://huggingface.co/<repo>/resolve/<rev>/<rel>`` (see
    ``hf_download_url``); we match on the longest ``rel`` that is a suffix of
    the request path so subdir-nested variants (``variant-a/model.gguf``)
    resolve correctly.
    """

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        match = None
        for rel in files:
            if (path.endswith("/" + rel) or path.endswith(rel)) and (
                match is None or len(rel) > len(match)
            ):
                match = rel
        if match is None:
            return httpx.Response(404, content=b"")
        body = files[match]
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _pull_fileset(
    model_id: str,
    repo: str,
    revision: str,
    files: dict[str, bytes],
    *,
    registry: SqliteModelRegistry,
    requested_variant: str | None = None,
) -> Path:
    """Drive a full file-set pull through the PUBLIC ``run_pull`` entry point
    (which delegates to ``_run_pull_fileset``) with a stubbed HF transport.

    Returns the resolved entry ``dest`` on success. Raises through if the pull
    fails so a test never green-lights a silent failure.
    """
    plan = plan_fileset(
        _tree(files), repo=repo, revision=revision, requested_variant=requested_variant
    )
    job = make_job(model_id)
    client = _mock_client(files)
    try:
        await run_pull(job, registry=registry, client=client, fileset=plan)
    finally:
        await client.aclose()
    assert job.state == "completed", f"pull {model_id!r} did not complete: {job.error}"
    return Path(job.path)


def _model_files(registry: SqliteModelRegistry, model_id: str) -> list[dict]:
    with connect(registry.db_path) as conn:
        return repository.list_model_files(conn, model_id)


def _blob(registry: SqliteModelRegistry, sha256: str) -> dict | None:
    with connect(registry.db_path) as conn:
        row = repository.get_blob(conn, sha256)
        return dict(row) if row is not None else None


@pytest.fixture
def registry(tmp_hal0_home: str) -> SqliteModelRegistry:
    """A SQLite registry rooted under the isolated ``HAL0_HOME`` tree, so its
    ``db_path`` == ``connect()``'s default (the pull engine's blob-bookkeeping
    opens ``connect()`` with no arg) and ``store.store_root()`` defaults to the
    same tree's ``models_dir``. One consistent home, no override drift.

    The schema is migrated up front because the pull engine's blob
    bookkeeping (``_maybe_hardlink_from_blob`` / ``_register_blob_after_install``)
    opens a RAW ``connect()`` and touches ``store_blob`` directly — in a live
    deployment the api-startup lifespan has already migrated the DB before any
    pull runs; here we stand in for that one-time bootstrap."""
    reg = SqliteModelRegistry()
    with connect(reg.db_path) as conn:
        migrate(conn)
    return reg


# ── 1. pull → registry row + fileset persisted ───────────────────────────────


class TestPullPersistsRegistryAndFileset:
    @pytest.mark.asyncio
    async def test_single_file_pull_writes_row_blob_and_pointer(
        self, registry: SqliteModelRegistry
    ) -> None:
        body = _body("qwen3-4b.gguf", 1024)
        digest = hashlib.sha256(body).hexdigest()
        dest = await _pull_fileset(
            "qwen3-4b",
            "Qwen/Qwen3-4B-GGUF",
            "rev-sha-1",
            {"qwen3-4b.gguf": body},
            registry=registry,
        )

        # Bytes persisted under the store root, HF-cache-shaped layout.
        assert dest.exists()
        assert dest.read_bytes() == body
        store.assert_under_store(dest, severity="fail")  # never escaped the tree

        # Registry row.
        entry = registry.get("qwen3-4b")
        assert entry.path == str(dest)
        assert entry.size_bytes == len(body)
        assert entry.hf_repo == "Qwen/Qwen3-4B-GGUF"

        # store_blob row (content-addressed, refcount 1).
        blob = _blob(registry, digest)
        assert blob is not None
        assert blob["refcount"] == 1
        assert Path(blob["blob_path"]) == dest

        # model_file row.
        rows = _model_files(registry, "qwen3-4b")
        assert [r["rel"] for r in rows] == ["qwen3-4b.gguf"]
        assert rows[0]["role"] == "model"
        assert rows[0]["sha256"] == digest

        # by-id pointer resolves to the entry file (the stable indirection a
        # slot launches through — a revision bump never edits a slot TOML).
        assert store.resolve_entry_pointer("qwen3-4b") == dest.resolve()

        # revision pinned on the row.
        with connect(registry.db_path) as conn:
            row = conn.execute("SELECT revision FROM model WHERE id = ?", ("qwen3-4b",)).fetchone()
        assert row["revision"] == "rev-sha-1"


# ── 2. multi-shard + mmproj file-set: roles/shards recorded, none dropped ─────


class TestMultiShardMmprojFileset:
    @pytest.mark.asyncio
    async def test_all_shards_and_mmproj_recorded_with_roles(
        self, registry: SqliteModelRegistry
    ) -> None:
        files = {
            "model-00001-of-00003.gguf": _body("s1", 300),
            "model-00002-of-00003.gguf": _body("s2", 300),
            "model-00003-of-00003.gguf": _body("s3", 300),
            "mmproj-F16.gguf": _body("mm", 128),
        }
        entry_dest = await _pull_fileset(
            "vision-model", "org/vision-gguf", "rev-sha-2", files, registry=registry
        )

        rows = _model_files(registry, "vision-model")
        by_rel = {r["rel"]: r for r in rows}

        # Every shard persisted, ordered, role=shard, contiguous shard_index.
        for i in range(1, 4):
            rel = f"model-{i:05d}-of-00003.gguf"
            assert rel in by_rel, f"shard {rel} missing from model_file rows"
            assert by_rel[rel]["role"] == "shard"
            assert by_rel[rel]["shard_index"] == i

        # mmproj recorded with its own role (NOT dropped, NOT mis-rolled).
        assert "mmproj-F16.gguf" in by_rel
        assert by_rel["mmproj-F16.gguf"]["role"] == "mmproj"
        assert by_rel["mmproj-F16.gguf"]["shard_index"] is None

        # Entry point is shard-1; every shard file exists on disk (discovery /
        # install NEVER unlinks a shard — the historic drop-on-sight bug).
        assert entry_dest.name == "model-00001-of-00003.gguf"
        for rel in files:
            f = entry_dest.parent / Path(rel).name
            assert f.exists(), f"{rel} was not persisted / was deleted"

        # Registry row carries the mmproj sidecar + aggregate size.
        entry = registry.get("vision-model")
        assert entry.mmproj is not None
        assert Path(entry.mmproj).name == "mmproj-F16.gguf"
        assert entry.size_bytes == sum(len(b) for b in files.values())


# ── 3. revision update → atomic by-id pointer flip, old bytes retained ────────


class TestRevisionUpdatePointerFlip:
    @pytest.mark.asyncio
    async def test_repull_new_revision_flips_pointer_and_keeps_old_bytes(
        self, registry: SqliteModelRegistry
    ) -> None:
        repo = "org/model-gguf"
        old_body = _body("v1", 512)
        old_dest = await _pull_fileset(
            "roller", repo, "rev-old", {"model.gguf": old_body}, registry=registry
        )
        assert store.resolve_entry_pointer("roller") == old_dest.resolve()

        # Re-pull the SAME id at a NEW revision with NEW bytes.
        new_body = _body("v2", 640)
        new_dest = await _pull_fileset(
            "roller", repo, "rev-new", {"model.gguf": new_body}, registry=registry
        )

        # New revision lands in its own snapshot dir (distinct on-disk path).
        assert new_dest != old_dest
        assert new_dest.read_bytes() == new_body

        # by-id pointer atomically flips to the new revision's entry file.
        assert store.resolve_entry_pointer("roller") == new_dest.resolve()

        # Old revision bytes are RETAINED (nothing unlinks them implicitly —
        # a live slot may still be mmap'd against them; GC reclaims later).
        assert old_dest.exists()
        assert old_dest.read_bytes() == old_body

        # Registry row now points at the new revision + path.
        entry = registry.get("roller")
        assert entry.path == str(new_dest)
        with connect(registry.db_path) as conn:
            row = conn.execute("SELECT revision FROM model WHERE id = ?", ("roller",)).fetchone()
        assert row["revision"] == "rev-new"


# ── 4. content-addressed + refcounted: one blob shared across two models ──────


class TestContentAddressedDedup:
    @pytest.mark.asyncio
    async def test_identical_blob_across_two_models_is_one_inode_refcount_two(
        self, registry: SqliteModelRegistry
    ) -> None:
        # Byte-identical file (same rel + size → same bytes → same sha256).
        shared = _body("shared.gguf", 777)
        digest = hashlib.sha256(shared).hexdigest()

        dest_a = await _pull_fileset(
            "model-a", "org/a-gguf", "rev-a", {"shared.gguf": shared}, registry=registry
        )
        dest_b = await _pull_fileset(
            "model-b", "org/b-gguf", "rev-b", {"shared.gguf": shared}, registry=registry
        )

        # Two distinct destination paths…
        assert dest_a != dest_b
        # …but ONE physical inode (hardlink dedup) — the whole point of CAS.
        assert os.stat(dest_a).st_ino == os.stat(dest_b).st_ino

        # Single store_blob row, refcount 2 (one blob, two referents).
        blob = _blob(registry, digest)
        assert blob is not None
        assert blob["refcount"] == 2

        # Each model's file row references the shared sha256.
        assert _model_files(registry, "model-a")[0]["sha256"] == digest
        assert _model_files(registry, "model-b")[0]["sha256"] == digest


# ── 5. delete → refcount-safe byte cleanup ───────────────────────────────────


class TestRefcountSafeDelete:
    @pytest.mark.asyncio
    async def test_delete_one_of_two_keeps_bytes_delete_last_removes_bytes(
        self, registry: SqliteModelRegistry
    ) -> None:
        shared = _body("shared.gguf", 640)
        digest = hashlib.sha256(shared).hexdigest()
        dest_a = await _pull_fileset(
            "keep-a", "org/a-gguf", "rev-a", {"shared.gguf": shared}, registry=registry
        )
        dest_b = await _pull_fileset(
            "keep-b", "org/b-gguf", "rev-b", {"shared.gguf": shared}, registry=registry
        )
        assert _blob(registry, digest)["refcount"] == 2

        # model-a was pulled first, so it is the CANONICAL blob (its dest ==
        # store_blob.blob_path). model-b hardlinked off it.
        assert Path(_blob(registry, digest)["blob_path"]) == dest_a

        # Delete the FIRST model's files: refcount drops to 1, bytes RETAINED
        # (the second model still references the shared blob).
        with connect(registry.db_path) as conn:
            removed = gc.delete_model_files(conn, "keep-a")
        assert removed == 1
        assert _blob(registry, digest)["refcount"] == 1
        # keep-a's own hardlink is gone; keep-b's dest (and the bytes) remain —
        # the byte-safety contract: a shared blob's bytes survive a partial
        # delete.
        assert not dest_a.exists()
        assert dest_b.exists()
        assert dest_b.read_bytes() == shared

        # ── #8 FIX: canonical blob_path re-pointed to a surviving referent ──
        # delete_model_files unlinked keep-a's dest — which WAS the blob's
        # canonical blob_path — but the blob is still referenced (refcount 1),
        # so it re-points blob_path at keep-b's live hardlink instead of
        # leaving it dangling at the deleted dest_a. blob_path now always
        # points at a live file while refcount > 0.
        assert Path(_blob(registry, digest)["blob_path"]) == dest_b  # re-pointed
        assert Path(_blob(registry, digest)["blob_path"]).exists()

        # ── #8 FIX: a THIRD same-sha pull now hits hardlink-dedup ──────────
        # With blob_path live again, _maybe_hardlink_from_blob's
        # blob_path.is_file() probe succeeds, so keep-c hardlinks off the
        # existing blob (refcount++ , SAME inode) instead of re-downloading.
        dest_c = await _pull_fileset(
            "keep-c", "org/c-gguf", "rev-c", {"shared.gguf": shared}, registry=registry
        )
        assert _blob(registry, digest)["refcount"] == 2
        # Same physical inode as the surviving referent — proof of a hardlink,
        # not a fresh download (a re-download would allocate a new inode).
        assert os.stat(dest_c).st_ino == os.stat(dest_b).st_ino
        assert dest_c.read_bytes() == shared

        # Delete keep-c: refcount back to 1. blob_path is dest_b (still live,
        # keep-b holds it), NOT dest_c, so no re-point is needed here.
        with connect(registry.db_path) as conn:
            gc.delete_model_files(conn, "keep-c")
        assert _blob(registry, digest)["refcount"] == 1
        assert not dest_c.exists()
        assert dest_b.exists()  # keep-b still references the shared bytes
        assert Path(_blob(registry, digest)["blob_path"]) == dest_b

        # Delete the LAST referent: refcount hits 0 and the bytes are unlinked
        # immediately (the model's OWN dest hardlink — now also the canonical
        # blob_path — is unlinked). Last-delete reclaims the bytes.
        with connect(registry.db_path) as conn:
            gc.delete_model_files(conn, "keep-b")
        assert _blob(registry, digest)["refcount"] == 0
        assert not dest_b.exists()  # bytes reclaimed on last delete

        # The refcount=0 store_blob ROW persists for a subsequent prune sweep
        # to reap. Its blob_path is the (now-unlinked) dest_b, so the sweep
        # tolerates the absence + drops the row.
        with connect(registry.db_path) as conn:
            stale_path = _blob(registry, digest)["blob_path"]
            assert gc.collect_orphans(conn) == [stale_path]
            report = gc.prune_orphans(conn, dry_run=False)
        assert report.orphans_found == 1
        assert _blob(registry, digest) is None


# ── 6. GC reconciles db rows vs filesystem (refcount-driven) ──────────────────


class TestGcReconcilesDbVsFilesystem:
    """GC reconciles db rows AND filesystem state (REWORK.md §B) — BOTH halves:

    * refcount-row-driven: ``prune_orphans`` reconciles every ``store_blob``
      row with ``refcount <= 0`` against the filesystem — unlinking its bytes,
      tolerating already-missing bytes, never touching a live (refcount > 0)
      blob (:meth:`test_orphan_pruned_live_retained_missing_bytes_tolerated`).
    * filesystem-walk-driven: ``reconcile_store_tree`` walks the store root and
      reaps *bare bytes* — a file with NO ``store_blob`` AND no ``model_file``
      row (crashed pull / manual copy) — while retaining live-referenced files
      and skipping in-flight ``.tmp`` partials
      (:meth:`test_reconcile_reaps_bare_bytes_retains_live_skips_partial`).
    """

    @pytest.mark.asyncio
    async def test_orphan_pruned_live_retained_missing_bytes_tolerated(
        self, registry: SqliteModelRegistry
    ) -> None:
        live_body = _body("live.gguf", 300)
        live_sha = hashlib.sha256(live_body).hexdigest()
        live_dest = await _pull_fileset(
            "live", "org/live-gguf", "rev-l", {"live.gguf": live_body}, registry=registry
        )

        # Fabricate two more store_blob rows directly (the two reconcile edge
        # states a crash-mid-delete leaves behind):
        #   orphan  — refcount 0, bytes present on disk under the store root.
        #   missing — refcount 0, bytes already gone (unlinked out from under
        #             the row); GC must tolerate this, not error.
        orphan_dest = live_dest.parent / "orphan.gguf"
        orphan_dest.write_bytes(b"orphan-bytes")
        with connect(registry.db_path) as conn, tx(conn):
            repository.insert_blob(
                conn,
                sha256="orphan-sha",
                size_bytes=orphan_dest.stat().st_size,
                blob_path=str(orphan_dest),
                refcount=0,
            )
            repository.insert_blob(
                conn,
                sha256="missing-sha",
                size_bytes=999,
                blob_path=str(live_dest.parent / "never-existed.gguf"),
                refcount=0,
            )

        with connect(registry.db_path) as conn:
            report = gc.prune_orphans(conn, dry_run=False)

        # Both refcount-0 rows are found; both rows are reaped (orphan's bytes
        # unlinked, missing's absent bytes tolerated via missing_ok) with no
        # errors.
        assert report.orphans_found == 2
        assert report.orphans_deleted == 2
        assert report.errors == []
        assert not orphan_dest.exists()  # orphan bytes reclaimed
        assert _blob(registry, "orphan-sha") is None
        assert _blob(registry, "missing-sha") is None

        # The live (refcount 1) blob + its bytes are UNTOUCHED.
        assert live_dest.exists()
        assert _blob(registry, live_sha)["refcount"] == 1

    @pytest.mark.asyncio
    async def test_reconcile_reaps_bare_bytes_retains_live_skips_partial(
        self, registry: SqliteModelRegistry
    ) -> None:
        # A real installed file (store_blob + model_file rows) — live bytes.
        live_body = _body("live2.gguf", 300)
        live_dest = await _pull_fileset(
            "live2", "org/live2-gguf", "rev-l2", {"live2.gguf": live_body}, registry=registry
        )

        # Bare bytes: a regular file physically under the store root that NO
        # store_blob row and NO model_file row tracks (a crashed pull, an
        # interrupted write, a manual copy). Same snapshot dir as the live file.
        bare = live_dest.parent / "bare-orphan.gguf"
        bare.write_bytes(b"bare-bytes-with-no-db-row")

        # An in-flight partial under the pull staging dir (.tmp) — the fs-walk
        # must skip it entirely so a concurrent download is never corrupted.
        tmp_dir = store.store_root() / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        partial = tmp_dir / "downloading--model.gguf.part"
        partial.write_bytes(b"half-a-download")

        # Dry-run: finds the bare file, unlinks nothing.
        with connect(registry.db_path) as conn:
            dry = gc.reconcile_store_tree(conn, dry_run=True)
        assert dry.orphans_found == 1
        assert dry.orphans_deleted == 0
        assert bare.exists()

        # Real pass: only the bare file is reaped.
        with connect(registry.db_path) as conn:
            report = gc.reconcile_store_tree(conn, dry_run=False)
        assert report.orphans_found == 1
        assert report.orphans_deleted == 1
        assert report.errors == []
        assert report.bytes_reclaimed == len(b"bare-bytes-with-no-db-row")
        assert not bare.exists()  # bare bytes reaped

        # Live-referenced file retained (its store_blob + model_file rows track
        # it); the in-flight .tmp partial never touched.
        assert live_dest.exists()
        assert live_dest.read_bytes() == live_body
        assert partial.exists()
        assert partial.read_bytes() == b"half-a-download"


# ── 7. read/write store-path precedence is identical ─────────────────────────


class TestReadWritePrecedenceParity:
    """One resolver decides location. Complements the unit precedence tests in
    ``tests/config/test_store.py`` with a *pull-driven* end-to-end proof: the
    path the writer (pull install) chose is the exact path the reader
    (``store_root`` / the by-id pointer) resolves back to."""

    @pytest.mark.asyncio
    async def test_written_dest_is_under_the_read_resolver_root(
        self, registry: SqliteModelRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Point the ONE resolver at an explicit override; both the write path
        # (file_dest) and the read path (store_root) must honour it identically.
        override = Path(os.environ["HAL0_HOME"]) / "explicit-store"
        override.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HAL0_MODEL_STORE", str(override))

        body = _body("m.gguf", 400)
        dest = await _pull_fileset(
            "precedent", "org/p-gguf", "rev-p", {"m.gguf": body}, registry=registry
        )

        # Writer landed under the override root…
        assert override.resolve() in dest.resolve().parents
        # …and the READ resolver agrees byte-for-byte (read precedence ==
        # write precedence — the dual-resolver trap plan §7.1e defect #1 fix).
        from hal0.config import paths

        assert store.store_root().resolve() == override.resolve()
        assert Path(paths.model_store_root()).resolve() == store.store_root().resolve()
        # The by-id pointer (the read-side indirection) resolves back into it.
        assert store.resolve_entry_pointer("precedent") == dest.resolve()


# ── 8. NFS store omits the SELinux relabel flag (pure logic) ─────────────────


class TestNfsRelabelOmission:
    """Store omits the SELinux relabel suffix on an NFS mount (``chcon``
    ENOTSUP there — plan §23.3d). Pure logic: ``is_nfs_path`` is stubbed, no
    real NFS mount. A local mount keeps ``z``; an NFS mount omits it entirely
    (NOT ``z``→``Z`` — both relabel)."""

    def test_local_mount_keeps_relabel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(store, "is_nfs_path", lambda _p: False)
        m = store.mount_for("/var/lib/hal0/models", read_only=True)
        assert m.selinux == "z"

    def test_nfs_mount_omits_relabel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(store, "is_nfs_path", lambda _p: True)
        m = store.mount_for("/mnt/ai-models", read_only=True)
        assert m.selinux == ""
        assert m.selinux != "Z"  # not a swapped relabel — omitted outright
