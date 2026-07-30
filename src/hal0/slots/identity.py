"""Slot identity store — the id ⇄ name bridge (rework §11.1).

Every slot gets a stable opaque ``id`` (SQLite ``AUTOINCREMENT``: monotonic,
never reused). The human-readable ``name`` becomes a mutable display label.
Units, ports, state files and routes address a slot by its ``id``, so a
rename is a pure ``UPDATE slot SET name=? WHERE id=?`` — zero reference
churn, no artefact rewrite, no port re-allocation.

This is a thin, synchronous SQLite wrapper around the ``slot`` and
``slot_link`` tables (``db/migrations/004_slots_ports.sql``), following the
same house pattern as :class:`hal0.registry.sqlite_store.SqliteModelRegistry`:
one connection opened per call via :func:`hal0.db.connection.connect` (WAL-
safe, no in-process lock), each write wrapped in
:func:`hal0.db.connection.tx`'s ``BEGIN IMMEDIATE``. There is no cached
connection and no sidecar lockfile — SQLite's own locking is the whole story.

Writes accept an optional ``conn`` so a caller (e.g. slot creation, which
must allocate a port in the *same* transaction as the slot insert — see
:class:`hal0.ports.authority.PortAuthority`) can thread one open
``BEGIN IMMEDIATE`` through both stores.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate


class SlotIdentityError(Exception):
    """Base class for slot-identity errors."""


class SlotNotFound(SlotIdentityError):
    """Raised when a slot id (or name) resolves to no row."""


class SlotAlreadyExists(SlotIdentityError):
    """Raised when creating/renaming to a name another slot already holds."""


@dataclass(frozen=True, slots=True)
class SlotRow:
    """One ``slot`` table row — the identity lookup result.

    Carries no port: the port lives in ``port_claim`` (the authority owns
    it, not the identity row). The API snapshot (:class:`hal0.slots.manager.Slot`)
    is the enriched shape that folds port + live state in at the route
    boundary; ``SlotRow`` is only ever the identity.
    """

    id: int
    name: str
    slot_type: str
    device: str
    runtime: str
    coresident_group: str | None
    is_seed: bool
    created_at: float
    updated_at: float


def _row_to_slot(row: sqlite3.Row) -> SlotRow:
    return SlotRow(
        id=int(row["id"]),
        name=row["name"],
        slot_type=row["slot_type"],
        device=row["device"] or "",
        runtime=row["runtime"] or "container",
        coresident_group=row["coresident_group"],
        is_seed=bool(row["is_seed"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


class SlotIdentityStore:
    """Resolve slot id ⇄ name; thin CRUD over the ``slot`` table."""

    def __init__(self, *, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else None
        # Apply pending migrations once on construction (idempotent no-op
        # once 004 has landed), mirroring SqliteModelRegistry's boot.
        with connect(self._db_path) as conn:
            migrate(conn)

    # ── connection plumbing ──────────────────────────────────────────────

    @contextmanager
    def _read(self, conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
        if conn is not None:
            yield conn
        else:
            with connect(self._db_path) as c:
                yield c

    @contextmanager
    def _write(self, conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
        # When the caller passes an open connection it also owns the
        # transaction (it is threading create+acquire through one
        # BEGIN IMMEDIATE); otherwise we open + own our own.
        if conn is not None:
            yield conn
        else:
            with connect(self._db_path) as c, tx(c):
                yield c

    # ── id-keyed surface ─────────────────────────────────────────────────

    def get(self, slot_id: int, *, conn: sqlite3.Connection | None = None) -> SlotRow:
        with self._read(conn) as c:
            row = c.execute("SELECT * FROM slot WHERE id = ?", (slot_id,)).fetchone()
        if row is None:
            raise SlotNotFound(f"no slot with id={slot_id}")
        return _row_to_slot(row)

    def create(
        self,
        *,
        name: str,
        slot_type: str,
        device: str = "",
        runtime: str = "container",
        coresident_group: str | None = None,
        is_seed: bool = False,
        conn: sqlite3.Connection | None = None,
    ) -> SlotRow:
        """Insert a new slot row and return it (id assigned by SQLite).

        ``slot.enabled`` is left to its schema default (``1``) — GH #1383
        dropped the enabled/disabled surface (model-presence is the
        activation signal since #1369), but the column itself stays
        (additive schema, no destructive migration) so no INSERT column
        list touches it.
        """
        try:
            with self._write(conn) as c:
                cur = c.execute(
                    "INSERT INTO slot "
                    "(name, slot_type, device, runtime, coresident_group, is_seed) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        name,
                        slot_type,
                        device,
                        runtime,
                        coresident_group,
                        1 if is_seed else 0,
                    ),
                )
                new_id = int(cur.lastrowid)
                return self.get(new_id, conn=c)
        except sqlite3.IntegrityError as exc:
            raise SlotAlreadyExists(f"slot name {name!r} already exists") from exc

    def rename(
        self, slot_id: int, new_name: str, *, conn: sqlite3.Connection | None = None
    ) -> None:
        """Change a slot's display label. References key off id, so this is
        the *entire* rename — no unit/port/state churn."""
        try:
            with self._write(conn) as c:
                cur = c.execute(
                    "UPDATE slot SET name = ?, updated_at = strftime('%s','now') WHERE id = ?",
                    (new_name, slot_id),
                )
                if cur.rowcount == 0:
                    raise SlotNotFound(f"no slot with id={slot_id}")
        except sqlite3.IntegrityError as exc:
            raise SlotAlreadyExists(f"slot name {new_name!r} already exists") from exc

    def set_coresident_group(
        self,
        slot_id: int,
        group: str | None,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        with self._write(conn) as c:
            cur = c.execute(
                "UPDATE slot SET coresident_group = ?, updated_at = strftime('%s','now') "
                "WHERE id = ?",
                (group, slot_id),
            )
            if cur.rowcount == 0:
                raise SlotNotFound(f"no slot with id={slot_id}")

    def delete(self, slot_id: int, *, conn: sqlite3.Connection | None = None) -> None:
        """Drop the slot row. ``slot_link`` children cascade; ``port_claim``
        rows keep their audit trail (``slot_id`` set NULL by the FK)."""
        with self._write(conn) as c:
            c.execute("DELETE FROM slot WHERE id = ?", (slot_id,))

    def list_by_type(
        self, slot_type: str, *, conn: sqlite3.Connection | None = None
    ) -> list[SlotRow]:
        with self._read(conn) as c:
            rows = c.execute(
                "SELECT * FROM slot WHERE slot_type = ? ORDER BY id", (slot_type,)
            ).fetchall()
        return [_row_to_slot(r) for r in rows]

    def list_all(self, *, conn: sqlite3.Connection | None = None) -> list[SlotRow]:
        with self._read(conn) as c:
            rows = c.execute("SELECT * FROM slot ORDER BY id").fetchall()
        return [_row_to_slot(r) for r in rows]

    # ── name-keyed bridge ────────────────────────────────────────────────

    def get_by_name(self, name: str, *, conn: sqlite3.Connection | None = None) -> SlotRow | None:
        with self._read(conn) as c:
            row = c.execute("SELECT * FROM slot WHERE name = ?", (name,)).fetchone()
        return _row_to_slot(row) if row is not None else None

    def resolve_id(self, name: str, *, conn: sqlite3.Connection | None = None) -> int:
        """``name → id`` or raise :class:`SlotNotFound`."""
        row = self.get_by_name(name, conn=conn)
        if row is None:
            raise SlotNotFound(f"no slot named {name!r}")
        return row.id

    def list_seed_ids(self, *, conn: sqlite3.Connection | None = None) -> list[int]:
        with self._read(conn) as c:
            rows = c.execute("SELECT id FROM slot WHERE is_seed = 1 ORDER BY id").fetchall()
        return [int(r["id"]) for r in rows]

    # ── cross-table links ────────────────────────────────────────────────

    def link(
        self,
        parent_id: int,
        child_id: int,
        kind: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """Record a parent→child edge (idempotent on ``(parent, child, kind)``)."""
        with self._write(conn) as c:
            c.execute(
                "INSERT OR IGNORE INTO slot_link (parent_id, child_id, kind) VALUES (?, ?, ?)",
                (parent_id, child_id, kind),
            )

    def unlink(
        self,
        parent_id: int,
        child_id: int,
        kind: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        with self._write(conn) as c:
            c.execute(
                "DELETE FROM slot_link WHERE parent_id = ? AND child_id = ? AND kind = ?",
                (parent_id, child_id, kind),
            )

    def children_of(
        self, parent_id: int, kind: str, *, conn: sqlite3.Connection | None = None
    ) -> list[int]:
        with self._read(conn) as c:
            rows = c.execute(
                "SELECT child_id FROM slot_link WHERE parent_id = ? AND kind = ? ORDER BY child_id",
                (parent_id, kind),
            ).fetchall()
        return [int(r["child_id"]) for r in rows]

    def parents_of(
        self, child_id: int, kind: str, *, conn: sqlite3.Connection | None = None
    ) -> list[int]:
        with self._read(conn) as c:
            rows = c.execute(
                "SELECT parent_id FROM slot_link WHERE child_id = ? AND kind = ? ORDER BY parent_id",
                (child_id, kind),
            ).fetchall()
        return [int(r["parent_id"]) for r in rows]
