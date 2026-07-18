"""Real GC for the model store — orphan blob prune + guarded delete (ML-3).

Historically "delete model" (``api/routes/models.py::delete_model``) was
registry-row-only: the bytes on disk were never touched, and no GC ever
existed to reclaim them. This module adds the two pieces plan §7.1e /
§23.3 asked for:

* :func:`collect_orphans` / :func:`prune_orphans` — refcount-driven
  ``store_blob`` cleanup (a hardlinked-dedup blob whose last referencing
  ``model_file`` row is gone).
* :func:`reconcile_store_tree` — filesystem-driven cleanup (REWORK.md §B:
  "GC reconciles db rows AND filesystem state"). Walks the store root and
  reaps *bare bytes* — a file physically on disk under the store root that
  NO ``store_blob`` row AND NO ``model_file`` row tracks (a crashed pull, an
  interrupted write, a manual copy). This is the fs-walk half the
  refcount-row-driven prune above cannot see.
* :func:`delete_model_files` — the guarded, opt-in "actually remove the
  bytes" path a caller (route handler) invokes explicitly; it is never
  implicit in a registry row delete. When a delete drops a shared blob's
  refcount but leaves it referenced (refcount > 0), it re-points the blob's
  canonical ``blob_path`` to a surviving referent so ``blob_path`` never
  dangles at a just-unlinked file (which would defeat hardlink-dedup for a
  later same-sha pull).

Every unlink in this module goes through
:func:`hal0.config.store.assert_under_store` FIRST (fail-fast severity —
this is a destructive write path, never a running-slot resolve), so a
corrupted ``blob_path``/``dest`` can never walk this code outside the
configured store root.
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from hal0.config import store
from hal0.config.store import assert_under_store
from hal0.db import repository
from hal0.db.connection import connect, tx

log = logging.getLogger(__name__)


@dataclass
class GCReport:
    """Summary of one GC pass."""

    orphans_found: int = 0
    orphans_deleted: int = 0
    bytes_reclaimed: int = 0
    errors: list[str] = field(default_factory=list)


def collect_orphans(conn) -> list[str]:
    """Return ``blob_path`` values for every ``store_blob`` row with
    ``refcount <= 0`` — candidates for :func:`prune_orphans`."""
    rows = conn.execute("SELECT blob_path FROM store_blob WHERE refcount <= 0").fetchall()
    return [row["blob_path"] for row in rows]


def prune_orphans(conn=None, *, dry_run: bool = True) -> GCReport:
    """Delete (or, if ``dry_run``, just report) every orphaned blob.

    ``dry_run=True`` is the safe default — a caller doing an interactive
    "GC" action must opt into ``dry_run=False`` explicitly. The startup
    sweep (plan §23.3 / api lifespan) calls this with ``dry_run=False`` to
    reap blobs whose refcount fell to 0 via a crash mid-delete, but that is
    the ONE place that does — every other caller should default safe.

    Accepts an existing connection (tests, callers already inside a
    transaction) or opens/owns its own when ``conn`` is ``None``.
    """
    if conn is None:
        with connect() as owned:
            return prune_orphans(owned, dry_run=dry_run)

    report = GCReport()
    rows = conn.execute(
        "SELECT sha256, blob_path, size_bytes FROM store_blob WHERE refcount <= 0"
    ).fetchall()
    report.orphans_found = len(rows)
    for row in rows:
        sha256, blob_path, size_bytes = row["sha256"], row["blob_path"], row["size_bytes"]
        if dry_run:
            continue
        try:
            resolved = assert_under_store(blob_path, severity="fail")
            resolved.unlink(missing_ok=True)
        except Exception as exc:
            report.errors.append(f"{sha256}: {exc}")
            log.warning(
                "gc.orphan_unlink_failed sha256=%s blob_path=%s error=%s", sha256, blob_path, exc
            )
            continue
        with tx(conn):
            conn.execute("DELETE FROM store_blob WHERE sha256 = ?", (sha256,))
        report.orphans_deleted += 1
        report.bytes_reclaimed += size_bytes or 0
    return report


def delete_model_files(conn, model_id: str) -> int:
    """Decrement/GC every ``model_file`` row's blob ref, then unlink its
    ``dest`` hardlink, for ``model_id``. Returns the count of files removed.

    Caller contract (``api/routes/models.py::delete_model``): this is
    invoked ONLY when ``delete_files=True`` is explicitly requested — the
    default stays registry-row-only (bytes untouched), matching the
    historic "safe by default" behaviour. When a blob's refcount hits 0
    here its bytes are unlinked immediately (not deferred to the next
    sweep) since we already hold the row lock for this model's files.

    ``model_file`` rows themselves are NOT deleted here — the caller's
    subsequent ``registry.remove(model_id)`` cascades them via
    ``ON DELETE CASCADE`` (ML-1's ``model_file(... ON DELETE CASCADE)``).
    """
    removed = 0
    files = repository.list_model_files(conn, model_id)
    for f in files:
        dest = f.get("dest")
        sha256 = f.get("sha256")
        if sha256:
            new_count = repository.drop_blob_ref(conn, sha256)
            if new_count == 0:
                blob = repository.get_blob(conn, sha256)
                if blob is not None:
                    with contextlib.suppress(Exception):
                        resolved = assert_under_store(blob["blob_path"], severity="fail")
                        resolved.unlink(missing_ok=True)
            elif new_count > 0:
                # The blob is still referenced by another model, but the dest
                # we are about to unlink below may BE the blob's canonical
                # referent (blob_path). If so, re-point blob_path at a
                # surviving referent's live hardlink so it never dangles at a
                # deleted path — a dangling blob_path breaks hardlink-dedup for
                # a later same-sha pull (_maybe_hardlink_from_blob probes
                # blob_path.is_file()). #8.
                _repoint_shared_blob(conn, sha256, dest, exclude_model_id=model_id)
        if dest:
            try:
                resolved_dest = assert_under_store(dest, severity="fail")
                # Only unlink when this dest isn't the blob's own canonical
                # path (already removed above) — a hardlinked dest and its
                # blob_path are different inodes with the SAME sha though,
                # so unlink here is always correct/needed for a hardlinked
                # file; for the canonical (blob==dest) case the unlink
                # above already removed it and this is a harmless no-op
                # (missing_ok=True).
                resolved_dest.unlink(missing_ok=True)
            except Exception as exc:
                log.warning(
                    "gc.model_file_unlink_failed model_id=%s dest=%s error=%s", model_id, dest, exc
                )
                continue
        removed += 1
    return removed


def _same_path(a: str | None, b: str | None) -> bool:
    """Best-effort equality of two on-disk paths (resolve, fall back to raw)."""
    if not a or not b:
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a) == str(b)


def _repoint_shared_blob(
    conn, sha256: str, deleted_dest: str | None, *, exclude_model_id: str
) -> None:
    """Re-point a still-referenced blob's canonical ``blob_path`` off a
    just-to-be-deleted dest onto a surviving referent's live hardlink (#8).

    A no-op unless the blob's current ``blob_path`` IS ``deleted_dest`` (the
    only case that would leave it dangling). Picks the first surviving
    ``model_file`` referent — excluding the model being deleted — whose
    ``dest`` is still a real file on disk. If none is (all referents already
    gone from disk, an unexpected state), ``blob_path`` is left unchanged and
    the ordinary refcount→0 sweep still reclaims the row later.
    """
    blob = repository.get_blob(conn, sha256)
    if blob is None:
        return
    if not _same_path(blob["blob_path"], deleted_dest):
        return  # canonical referent is elsewhere — nothing dangles
    for ref in repository.blob_referents(conn, sha256, exclude_model_id=exclude_model_id):
        ref_dest = ref.get("dest")
        if ref_dest and _same_path(ref_dest, deleted_dest):
            # A different model whose row happens to record the SAME dest
            # string — not a distinct surviving hardlink; skip it.
            continue
        if ref_dest and Path(ref_dest).is_file():
            repository.set_blob_path(conn, sha256, ref_dest)
            log.info(
                "gc.blob_path_repointed sha256=%s from=%s to=%s",
                sha256,
                deleted_dest,
                ref_dest,
            )
            return


# ── filesystem reconcile (bare-bytes fs-walk — REWORK.md §B) ────────────────

#: The pull engine stages in-flight downloads under ``<store_root>/.tmp``
#: (``hal0.registry.pull._tmp_dir``); the reconcile walk never descends into
#: it (nor any other dot-directory) so a growing ``*.part`` is never reaped.
_STAGING_DIRNAME = ".tmp"


def _norm_path(p: str | None) -> str | None:
    """Normalise a stored path for set-membership comparison against the walk."""
    if not p:
        return None
    try:
        return str(Path(p).resolve())
    except OSError:
        return str(Path(p))


def _sqlite_files(conn) -> set[str]:
    """The active SQLite database's own on-disk files (main db + ``-wal`` /
    ``-shm`` sidecars). Added to the tracked set so a store root that happens
    to contain the hal0 DB (e.g. a test's ``HAL0_MODEL_STORE`` pointed at the
    same dir, or a misconfigured deployment) can never have its DB reaped as
    "bare bytes". In-memory DBs report an empty filename and are skipped."""
    files: set[str] = set()
    with contextlib.suppress(Exception):
        for row in conn.execute("PRAGMA database_list").fetchall():
            db_file = row["file"]
            if not db_file:
                continue
            for suffix in ("", "-wal", "-shm", "-journal"):
                norm = _norm_path(db_file + suffix)
                if norm:
                    files.add(norm)
    return files


def _tracked_store_paths(conn) -> set[str]:
    """Every on-disk path the DB knows about: ``store_blob.blob_path`` (LFS
    dedup blobs) together with ``model_file.dest`` (installed files, incl.
    non-LFS tokenizer/config rows that carry NO blob), plus the SQLite DB's own files.
    A file under the store root in none of these is *bare bytes* — untracked,
    reap-eligible."""
    tracked: set[str] = _sqlite_files(conn)
    for row in conn.execute("SELECT blob_path FROM store_blob").fetchall():
        norm = _norm_path(row["blob_path"])
        if norm:
            tracked.add(norm)
    for row in conn.execute("SELECT dest FROM model_file WHERE dest IS NOT NULL").fetchall():
        norm = _norm_path(row["dest"])
        if norm:
            tracked.add(norm)
    return tracked


def reconcile_store_tree(conn=None, *, dry_run: bool = True, max_files: int = 100_000) -> GCReport:
    """Reap *bare bytes* — files under the store root tracked by NEITHER a
    ``store_blob`` row NOR a ``model_file`` row (REWORK.md §B fs-walk half).

    A crashed pull, an interrupted write, or a manual copy can leave a real
    file physically under the store root with no DB row referencing it. The
    refcount-driven :func:`prune_orphans` cannot see those (it is row-driven,
    and there is no row); this walk reconciles the filesystem against the DB.

    Safety contract:
      * ``dry_run=True`` (the default) only *counts* — a caller must opt into
        ``dry_run=False`` to actually unlink, exactly like :func:`prune_orphans`.
      * Live-referenced bytes are never touched: any path recorded as a
        ``store_blob.blob_path`` or a ``model_file.dest`` is tracked and skipped.
      * In-flight partials are never touched: the ``.tmp`` staging dir (and
        every other dot-directory) is pruned from the walk, and dot-files
        (``.part``/``.part.json``, hardlink-/pointer-tmp) are skipped.
      * Symlinks (the ``by-id`` pointer tree) are never followed or reaped.
      * Every unlink still passes through :func:`assert_under_store` (fail-fast).
      * The walk is bounded by ``max_files``; hitting the cap logs + records an
        error and stops rather than scanning an unbounded tree.

    Accepts an existing connection or opens its own when ``conn`` is ``None``.
    """
    if conn is None:
        with connect() as owned:
            return reconcile_store_tree(owned, dry_run=dry_run, max_files=max_files)

    report = GCReport()
    root = store.store_root().resolve()
    if not root.is_dir():
        return report

    tracked = _tracked_store_paths(conn)
    by_id = store.by_id_dir().resolve()
    scanned = 0

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        # Prune dot-dirs (``.tmp`` staging + any hidden dir) and the by-id
        # pointer dir so the walk never visits in-flight partials or symlinks.
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and (here / d).resolve() != by_id
        ]
        for name in filenames:
            if name.startswith("."):
                continue  # .part / .part.json / .*-tmp-<pid> — transient staging
            scanned += 1
            if scanned > max_files:
                msg = f"reconcile walk truncated at max_files={max_files} under {root}"
                report.errors.append(msg)
                log.warning("gc.reconcile_walk_truncated root=%s max_files=%d", root, max_files)
                return report
            p = here / name
            if p.is_symlink():
                continue
            if _norm_path(str(p)) in tracked:
                continue
            report.orphans_found += 1
            if dry_run:
                continue
            try:
                resolved = assert_under_store(p, severity="fail")
                size = resolved.stat().st_size
                resolved.unlink(missing_ok=True)
            except Exception as exc:
                report.errors.append(f"{p}: {exc}")
                log.warning("gc.reconcile_unlink_failed path=%s error=%s", p, exc)
                continue
            report.orphans_deleted += 1
            report.bytes_reclaimed += size
            log.info("gc.reconcile_reaped path=%s size=%d", p, size)
    return report


__all__ = [
    "GCReport",
    "collect_orphans",
    "delete_model_files",
    "prune_orphans",
    "reconcile_store_tree",
]
