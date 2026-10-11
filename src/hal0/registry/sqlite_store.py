"""SqliteModelRegistry — the model catalog, stored in the shared ``hal0.db``.

Exposes the §0.2 interface every caller uses: ``list``/``get``/``has``/
``add``/``remove``/``update``/``route_for``/``reload``/``on_change``, plus
the three typed errors (``RegistryError``/``ModelNotFound``/
``ModelAlreadyExists``) defined in :mod:`hal0.registry.store`, which also
binds the public ``ModelRegistry`` name to this class.

Concurrency: ``BEGIN IMMEDIATE`` (see :func:`hal0.db.connection.tx`) gives
cross-process write serialization natively, and a fresh connection per call
keeps it thread-safe.

SQLite is the only store. Nothing reads or writes ``registry.toml`` here: a
file left over from the TOML era is backed up and renamed, never imported, by
:func:`hal0.registry.toml_retirement.retire_registry_toml`, which runs with
the post-activation migrations, so it is never read again.
Deleted models therefore stay deleted across restarts.

``registry_dir`` stays a constructor parameter because tests and setup code
pass it: it selects where this instance's SQLite file lives
(``registry_dir/hal0.db``) for test/dev isolation.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hal0.config import paths
from hal0.db import repository
from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate
from hal0.registry.model import Model
from hal0.registry.store import (
    ModelAlreadyExists,
    ModelNotFound,
    RegistryError,
    merge_update,
)

log = logging.getLogger(__name__)

_DB_FILENAME = "hal0.db"


class SqliteModelRegistry:
    """SQLite-backed model registry.

    Thread/process-safety: none of it is this class's job any more.
    :func:`hal0.db.connection.connect` opens a fresh connection per call
    (WAL-safe) and :func:`hal0.db.connection.tx` takes SQLite's own
    ``BEGIN IMMEDIATE`` write lock for the duration of each mutator —
    there is no in-process lock and no on-disk sidecar lockfile to reason
    about.
    """

    # Optional post-mutation callback: invoked after every successful
    # add/update/remove, best-effort (a raising hook is logged and
    # swallowed, never allowed to undo an already-committed write).
    on_change: Callable[[], None] | None = None

    def __init__(
        self,
        registry_dir: str | Path | None = None,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        """Initialise the registry.

        Args:
            registry_dir: When given, this instance's SQLite file lives at
                ``registry_dir/hal0.db`` (test/dev isolation).
            db_path: Explicit override for the SQLite file location,
                taking precedence over ``registry_dir``.
        """
        self._registry_dir_override: Path | None = (
            Path(registry_dir) if registry_dir is not None else None
        )
        self._db_path_override: Path | None = Path(db_path) if db_path is not None else None
        self._migrated = False

    # ── path resolution ───────────────────────────────────────────────────

    @property
    def registry_dir(self) -> Path:
        """Resolved registry directory (override or paths.registry_dir())."""
        if self._registry_dir_override is not None:
            return self._registry_dir_override
        return paths.registry_dir()

    @property
    def db_path(self) -> Path:
        """Resolved SQLite database path for this instance."""
        if self._db_path_override is not None:
            return self._db_path_override
        if self._registry_dir_override is not None:
            return self._registry_dir_override / _DB_FILENAME
        return paths.db_path()

    def _connect(self):
        return connect(self.db_path)

    def _ensure_migrated(self, conn: sqlite3.Connection) -> None:
        """Run the schema migrator, once per instance.

        ``migrate()`` is idempotent (it checks ``schema_migrations``), so the
        per-instance ``self._migrated`` guard is purely an optimisation.
        """
        if self._migrated:
            return
        migrate(conn)
        self._migrated = True

    # ── row/model mapping ────────────────────────────────────────────────

    def _row_to_model(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Model:
        # ORDER BY rowid (insertion order), not `backend` alphabetically —
        # Model.backends is an ordered list (e.g. a preference order like
        # ["vulkan", "rocm", "cpu"]) and losslessness means preserving
        # that order, not just the set of values.
        backend_rows = conn.execute(
            "SELECT backend FROM model_backend WHERE model_id = ? ORDER BY rowid",
            (row["id"],),
        ).fetchall()
        return repository.row_to_model(row, backends=[r["backend"] for r in backend_rows])

    # ── public reads ──────────────────────────────────────────────────────

    def list(self) -> list[Model]:
        """Return all registered models, sorted by id."""
        with self._connect() as conn:
            self._ensure_migrated(conn)
            rows = conn.execute("SELECT * FROM model ORDER BY id").fetchall()
            return [self._row_to_model(conn, row) for row in rows]

    def get(self, model_id: str) -> Model:
        """Return a single model by id.

        Raises:
            ModelNotFound: If the model is not in the registry.
        """
        with self._connect() as conn:
            self._ensure_migrated(conn)
            row = conn.execute("SELECT * FROM model WHERE id = ?", (model_id,)).fetchone()
            if row is None:
                raise ModelNotFound(
                    f"model {model_id!r} not in registry",
                    details={"model_id": model_id},
                )
            return self._row_to_model(conn, row)

    def has(self, model_id: str) -> bool:
        """Return True if ``model_id`` is registered."""
        with self._connect() as conn:
            self._ensure_migrated(conn)
            row = conn.execute("SELECT 1 FROM model WHERE id = ? LIMIT 1", (model_id,)).fetchone()
            return row is not None

    # ── public writes ─────────────────────────────────────────────────────

    def _notify_change(self) -> None:
        """Invoke the post-mutation hook, if any. Best-effort: a raising
        hook is logged and swallowed."""
        cb = self.on_change
        if cb is None:
            return
        try:
            cb()
        except Exception:
            log.warning("registry.on_change_failed", exc_info=True)

    def add(self, model: Model) -> None:
        """Add a new model to the registry.

        Raises:
            ModelAlreadyExists: If the model id is already present.
        """
        with self._connect() as conn:
            self._ensure_migrated(conn)
            with tx(conn):
                row = repository.model_to_row(model)
                columns = ", ".join(row)
                placeholders = ", ".join("?" for _ in row)
                try:
                    conn.execute(
                        f"INSERT INTO model ({columns}) VALUES ({placeholders})",
                        list(row.values()),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ModelAlreadyExists(
                        f"model {model.id!r} already in registry",
                        details={"model_id": model.id},
                    ) from exc
                conn.executemany(
                    "INSERT INTO model_backend (model_id, backend) VALUES (?, ?)",
                    [(model.id, backend) for backend in model.backends],
                )
        self._notify_change()

    def remove(self, model_id: str) -> bool:
        """Remove a model from the registry.

        Returns:
            ``True`` if the model was present and removed, ``False`` if absent.

        ``model_file``/``model_backend`` rows for this id are removed by
        ``ON DELETE CASCADE`` (``foreign_keys=ON`` is set on every
        connection — see :func:`hal0.db.connection.connect`).
        """
        with self._connect() as conn:
            self._ensure_migrated(conn)
            with tx(conn):
                cur = conn.execute("DELETE FROM model WHERE id = ?", (model_id,))
                removed = cur.rowcount > 0
        if removed:
            self._notify_change()
        return removed

    def update(self, model_id: str, updates: dict[str, Any]) -> Model:
        """Partially update a model entry.

        ``updates`` is a flat field-level merge — see
        :func:`hal0.registry.store.merge_update`. The ``id`` field is never changeable through
        update (use remove + add).

        Raises:
            ModelNotFound: If the model is not in the registry.
            RegistryError: If ``updates`` produces an invalid Model.
        """
        if not isinstance(updates, dict):
            raise RegistryError(
                "updates must be a dict",
                details={"got": type(updates).__name__},
            )
        with self._connect() as conn:
            self._ensure_migrated(conn)
            with tx(conn):
                row = conn.execute("SELECT * FROM model WHERE id = ?", (model_id,)).fetchone()
                if row is None:
                    raise ModelNotFound(
                        f"model {model_id!r} not in registry",
                        details={"model_id": model_id},
                    )
                existing = self._row_to_model(conn, row)
                new_model = merge_update(existing, model_id, updates)

                new_row = repository.model_to_row(new_model, created_at=row["created_at"])
                assignments = ", ".join(f"{col} = ?" for col in new_row if col != "id")
                params: list[Any] = [v for k, v in new_row.items() if k != "id"]
                params.append(model_id)
                conn.execute(f"UPDATE model SET {assignments} WHERE id = ?", params)

                conn.execute("DELETE FROM model_backend WHERE model_id = ?", (model_id,))
                conn.executemany(
                    "INSERT INTO model_backend (model_id, backend) VALUES (?, ?)",
                    [(model_id, backend) for backend in new_model.backends],
                )
        self._notify_change()
        return new_model

    def route_for(self, model_id: str) -> str | None:
        """Return the upstream URL for a model, or ``None`` if not assigned.

        Wire format: ``metadata = {"upstream_url": "http://127.0.0.1:8081"}``.
        """
        try:
            model = self.get(model_id)
        except ModelNotFound:
            return None
        url = model.metadata.get("upstream_url")
        if isinstance(url, str) and url.strip():
            return url
        return None

    def reload(self) -> None:
        """No-op: SQLite has no cache to invalidate.

        Part of the §0.2 interface; callers invoke it defensively.
        """
        return None


__all__ = ["SqliteModelRegistry"]
