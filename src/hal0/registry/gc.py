"""Real GC for the model store — orphan blob prune + guarded delete (ML-3).

Historically "delete model" (``api/routes/models.py::delete_model``) was
registry-row-only: the bytes on disk were never touched, and no GC ever
existed to reclaim them. This module adds the two pieces plan §7.1e /
§23.3 asked for:

* :func:`collect_orphans` / :func:`prune_orphans` — refcount-driven
  ``store_blob`` cleanup (a hardlinked-dedup blob whose last referencing
  ``model_file`` row is gone).
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
from dataclasses import dataclass, field
from pathlib import Path

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


__all__ = [
    "GCReport",
    "collect_orphans",
    "delete_model_files",
    "prune_orphans",
]
