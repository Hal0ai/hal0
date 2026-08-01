"""``RunnerImageStore`` — SQLite-backed runner-image catalogue.

Follows the same shape as :class:`hal0.registry.sqlite_store.SqliteModelRegistry`
(the primary/current registry backend — see that module's docstring): one
fresh connection per call via :func:`hal0.db.connection.connect`, schema
migration via :func:`hal0.db.migrate.migrate` run once per instance,
mutators wrapped in :func:`hal0.db.connection.tx` (``BEGIN IMMEDIATE`` for
cross-process write serialization), and a best-effort ``on_change`` hook
invoked after every successful mutation. There is no TOML-backed sibling
here — the runner-image catalogue is net-new, SQLite-first.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hal0.config import paths
from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate
from hal0.registry.runner_image import RunnerImage

log = logging.getLogger(__name__)

_DB_FILENAME = "hal0.db"

#: Row columns that map 1:1 onto RunnerImage fields (everything except
#: ``build``/``extra``, which are JSON-encoded columns).
_SCALAR_FIELDS = (
    "id",
    "image",
    "tag",
    "digest",
    "size_bytes",
    "manifest_key",
    "ownership",
    "publish",
    "notes",
    "local_path",
    "downloaded_at",
    "discovered_at",
    "updated_at",
)


def _row_to_runner_image(row: sqlite3.Row) -> RunnerImage:
    build_raw = row["build_json"]
    extra_raw = row["extra"]
    return RunnerImage(
        **{k: row[k] for k in _SCALAR_FIELDS},
        build=json.loads(build_raw) if build_raw else None,
        extra=json.loads(extra_raw) if extra_raw else {},
    )


def _runner_image_to_row(image: RunnerImage, *, discovered_at: str | None = None) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    row: dict[str, Any] = {k: getattr(image, k) for k in _SCALAR_FIELDS if k != "discovered_at"}
    row["discovered_at"] = discovered_at or image.discovered_at or now
    row["updated_at"] = now
    row["build_json"] = json.dumps(image.build) if image.build is not None else None
    row["extra"] = json.dumps(image.extra) if image.extra else None
    return row


class RunnerImageStore:
    """SQLite-backed store for :class:`RunnerImage` rows."""

    #: Best-effort post-mutation hook, identical contract to
    #: ``SqliteModelRegistry.on_change``.
    on_change: Callable[[], None] | None = None

    def __init__(self, *, db_path: str | Path | None = None) -> None:
        self._db_path_override: Path | None = Path(db_path) if db_path is not None else None
        self._migrated = False

    @property
    def db_path(self) -> Path:
        if self._db_path_override is not None:
            return self._db_path_override
        return paths.db_path()

    def _connect(self):
        return connect(self.db_path)

    def _ensure_migrated(self, conn: sqlite3.Connection) -> None:
        if self._migrated:
            return
        migrate(conn)
        self._migrated = True

    def _notify_change(self) -> None:
        cb = self.on_change
        if cb is None:
            return
        try:
            cb()
        except Exception:
            log.warning("runner_images.on_change_failed", exc_info=True)

    # ── reads ────────────────────────────────────────────────────────────

    def list(self) -> list[RunnerImage]:
        """Return every catalogued runner image, sorted by id."""
        with self._connect() as conn:
            self._ensure_migrated(conn)
            rows = conn.execute("SELECT * FROM runner_image ORDER BY id").fetchall()
            return [_row_to_runner_image(r) for r in rows]

    def get(self, image_id: str) -> RunnerImage | None:
        """Return one catalogued image by id, or None if absent."""
        with self._connect() as conn:
            self._ensure_migrated(conn)
            row = conn.execute("SELECT * FROM runner_image WHERE id = ?", (image_id,)).fetchone()
            return _row_to_runner_image(row) if row is not None else None

    def list_downloaded(self) -> list[RunnerImage]:
        """Return only images with a local pull landed (``local_path`` set).

        This is the shape the sibling ``fix/slot-edit-drawer-cleanup``
        branch's Runner Image dropdown is expected to consume.
        """
        with self._connect() as conn:
            self._ensure_migrated(conn)
            rows = conn.execute(
                "SELECT * FROM runner_image WHERE local_path IS NOT NULL ORDER BY id"
            ).fetchall()
            return [_row_to_runner_image(r) for r in rows]

    # ── writes ───────────────────────────────────────────────────────────

    def upsert(self, image: RunnerImage) -> RunnerImage:
        """Insert or update a catalogue row by id.

        Used by the sync job (discovery merge) as well as by the download
        job (to stamp ``local_path``/``downloaded_at`` on completion).
        Preserves the original ``discovered_at`` and any local-download
        state already on disk unless the caller explicitly overwrites it
        on the passed-in ``image``.
        """
        with self._connect() as conn:
            self._ensure_migrated(conn)
            with tx(conn):
                existing = conn.execute(
                    "SELECT discovered_at FROM runner_image WHERE id = ?", (image.id,)
                ).fetchone()
                row = _runner_image_to_row(
                    image,
                    discovered_at=existing["discovered_at"] if existing is not None else None,
                )
                columns = ", ".join(row)
                placeholders = ", ".join(f":{k}" for k in row)
                updates = ", ".join(f"{k} = :{k}" for k in row if k != "id")
                conn.execute(
                    f"INSERT INTO runner_image ({columns}) VALUES ({placeholders}) "
                    f"ON CONFLICT(id) DO UPDATE SET {updates}",
                    row,
                )
        self._notify_change()
        result = self.get(image.id)
        assert result is not None  # just wrote it
        return result

    def set_local_state(
        self, image_id: str, *, local_path: str | None, downloaded_at: str | None = None
    ) -> RunnerImage | None:
        """Stamp (or clear) local-download state on an existing row.

        Returns None if ``image_id`` isn't catalogued (the download job
        guards against this before calling, but the store stays defensive).
        """
        with self._connect() as conn:
            self._ensure_migrated(conn)
            with tx(conn):
                cur = conn.execute(
                    "UPDATE runner_image SET local_path = ?, downloaded_at = ?, "
                    "updated_at = ? WHERE id = ?",
                    (
                        local_path,
                        downloaded_at if local_path else None,
                        datetime.now(UTC).isoformat(),
                        image_id,
                    ),
                )
                if cur.rowcount == 0:
                    return None
        self._notify_change()
        return self.get(image_id)

    def prune_absent(self, keep_ids: set[str]) -> int:
        """Delete rows whose id is not in ``keep_ids`` and has no local pull.

        Called by the sync job after a *successful* images.json fetch so
        removed/renamed manifest entries (e.g. the repo-path ids used
        before images.json carried per-entry ``id`` short names) don't
        linger as stale duplicates. Rows with ``local_path`` set are kept —
        a downloaded image stays visible even if delisted upstream.
        Returns the number of rows deleted.
        """
        with self._connect() as conn:
            self._ensure_migrated(conn)
            with tx(conn):
                if keep_ids:
                    placeholders = ", ".join("?" for _ in keep_ids)
                    cur = conn.execute(
                        f"DELETE FROM runner_image WHERE local_path IS NULL "
                        f"AND id NOT IN ({placeholders})",
                        tuple(keep_ids),
                    )
                else:
                    cur = conn.execute("DELETE FROM runner_image WHERE local_path IS NULL")
                removed = int(cur.rowcount)
        if removed:
            self._notify_change()
        return removed

    def reload(self) -> None:
        """No-op — kept for interface symmetry with SqliteModelRegistry."""
        return None


__all__ = ["RunnerImageStore"]
