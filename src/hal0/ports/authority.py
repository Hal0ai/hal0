"""PortAuthority — the single writer of port ownership (rework §11.2).

Where :mod:`hal0.ports` (the harvester) *observes* who is using which port
by recomputing from live truth on every question, ``PortAuthority`` is the
one path that *issues* claims: it allocates from the configured pool,
reserves by stable slot ``id``, rejects a duplicate live claim, checks
active listeners before granting, reconciles orphaned claims on startup,
and releases on slot deletion. No route, installer, or manager path
hand-assigns a slot port any more — every allocation flows through here.

Substrate: the ``port_claim`` table (``db/migrations/004_slots_ports.sql``).
At most one *live* row (``released_at IS NULL``) per port is enforced by the
``uq_port_claim_live`` partial unique index; released rows stay as an audit
trail, so a port can be re-granted after release. Two concurrent grabs of
the same free port race on that index — the loser catches ``IntegrityError``
and retries the scan.

Synchronous, house pattern (see :class:`hal0.slots.identity.SlotIdentityStore`):
one connection per call via :func:`hal0.db.connection.connect`, each write
under :func:`hal0.db.connection.tx`. Writes accept an optional ``conn`` so a
slot-create can allocate the port in the same ``BEGIN IMMEDIATE`` as the
slot-row insert.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate


class PortAuthorityError(Exception):
    """Base class for port-authority errors."""


class PortPoolExhausted(PortAuthorityError):
    """No free port remains in the configured pool."""


@dataclass(frozen=True, slots=True)
class AuthorityClaim:
    """One ``port_claim`` row."""

    id: int
    port: int
    slot_id: int | None
    owner_kind: str
    owner_label: str
    coresident_group: str | None
    acquired_at: float
    released_at: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "slot_id": self.slot_id,
            "owner_kind": self.owner_kind,
            "owner_label": self.owner_label,
            "coresident_group": self.coresident_group,
            "acquired_at": self.acquired_at,
            "released_at": self.released_at,
        }


def _row_to_claim(row: sqlite3.Row) -> AuthorityClaim:
    return AuthorityClaim(
        id=int(row["id"]),
        port=int(row["port"]),
        slot_id=int(row["slot_id"]) if row["slot_id"] is not None else None,
        owner_kind=row["owner_kind"],
        owner_label=row["owner_label"],
        coresident_group=row["coresident_group"],
        acquired_at=float(row["acquired_at"]),
        released_at=float(row["released_at"]) if row["released_at"] is not None else None,
    )


class PortAuthority:
    """Single source of port ownership. All slot allocation routes here."""

    def __init__(
        self,
        *,
        pool: tuple[int, int],
        reserved: dict[int, str] | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self._pool = (int(pool[0]), int(pool[1]))
        self._reserved = dict(reserved or {})
        self._db_path = Path(db_path) if db_path is not None else None
        with connect(self._db_path) as conn:
            migrate(conn)

    @property
    def pool(self) -> tuple[int, int]:
        return self._pool

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
        if conn is not None:
            yield conn
        else:
            with connect(self._db_path) as c, tx(c):
                yield c

    # ── queries ──────────────────────────────────────────────────────────

    def claims(
        self, *, live_only: bool = True, conn: sqlite3.Connection | None = None
    ) -> list[AuthorityClaim]:
        sql = "SELECT * FROM port_claim"
        if live_only:
            sql += " WHERE released_at IS NULL"
        sql += " ORDER BY port, id"
        with self._read(conn) as c:
            rows = c.execute(sql).fetchall()
        return [_row_to_claim(r) for r in rows]

    def _live_ports(self, conn: sqlite3.Connection | None = None) -> set[int]:
        with self._read(conn) as c:
            rows = c.execute("SELECT port FROM port_claim WHERE released_at IS NULL").fetchall()
        return {int(r["port"]) for r in rows}

    def _listener_ports(self) -> set[int]:
        """Ports in LISTEN state inside the pool (best-effort psutil scan)."""
        from hal0.ports import _listener_claims

        return {c.port for c in _listener_claims(*self._pool)}

    def is_free(
        self, port: int, *, include_listeners: bool = True, conn: sqlite3.Connection | None = None
    ) -> bool:
        used = self._live_ports(conn)
        if include_listeners:
            used |= self._listener_ports()
        return port not in used

    def next_free(
        self,
        *,
        preferred: int | None = None,
        include_listeners: bool = True,
        conn: sqlite3.Connection | None = None,
    ) -> int | None:
        """Lowest free port in the pool, honouring ``preferred`` when free."""
        start, end = self._pool
        used = self._live_ports(conn)
        if include_listeners:
            used |= self._listener_ports()
        if preferred is not None and start <= preferred <= end and preferred not in used:
            return preferred
        for port in range(start, end + 1):
            if port not in used:
                return port
        return None

    def held_by(
        self,
        slot_id: int,
        *,
        coresident_group: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> int | None:
        """The live port a slot owns, or the coresident group's shared port
        when the slot is a shadow sharing its anchor's claim."""
        with self._read(conn) as c:
            row = c.execute(
                "SELECT port FROM port_claim WHERE slot_id = ? AND released_at IS NULL "
                "ORDER BY id LIMIT 1",
                (slot_id,),
            ).fetchone()
            if row is not None:
                return int(row["port"])
            if coresident_group:
                grp = c.execute(
                    "SELECT port FROM port_claim WHERE coresident_group = ? "
                    "AND released_at IS NULL ORDER BY id LIMIT 1",
                    (coresident_group,),
                ).fetchone()
                if grp is not None:
                    return int(grp["port"])
        return None

    def is_held_by_other(
        self, port: int, *, slot_id: int, conn: sqlite3.Connection | None = None
    ) -> bool:
        """True when ``port`` has a live claim owned by a *different* slot."""
        with self._read(conn) as c:
            row = c.execute(
                "SELECT slot_id FROM port_claim WHERE port = ? AND released_at IS NULL",
                (port,),
            ).fetchone()
        return row is not None and row["slot_id"] is not None and int(row["slot_id"]) != slot_id

    def conflicts(self, *, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        """Ports whose live claim collides with an external listener owned by
        a different process (the harvester's group-fold semantics do not
        apply here — a live claim is already a single owner per port)."""
        listeners = self._listener_ports()
        out: list[dict[str, Any]] = []
        for claim in self.claims(live_only=True, conn=conn):
            # A listener on the claimed port is the slot's own server socket
            # (expected). A conflict is a *claimed but pool-listened* port
            # with no owning slot row (orphaned reservation) — surfaced for
            # the operator. Same-slot listeners are not a conflict.
            if claim.slot_id is None and claim.owner_kind == "slot" and claim.port in listeners:
                out.append({"port": claim.port, "owner_label": claim.owner_label})
        return out

    # ── writes ───────────────────────────────────────────────────────────

    def reserve(self, port: int, *, label: str, conn: sqlite3.Connection | None = None) -> None:
        """Reserve a port for a non-slot owner (the API's own port, etc.).

        Idempotent: reserving an already-live-reserved port under the same
        label is a no-op; a different live owner raises ``IntegrityError``
        via ``uq_port_claim_live``.
        """
        with self._write(conn) as c:
            existing = c.execute(
                "SELECT id, owner_label FROM port_claim WHERE port = ? AND released_at IS NULL",
                (port,),
            ).fetchone()
            if existing is not None:
                if existing["owner_label"] == label:
                    return
                raise sqlite3.IntegrityError(
                    f"port {port} already live-claimed by {existing['owner_label']!r}"
                )
            c.execute(
                "INSERT INTO port_claim (port, slot_id, owner_kind, owner_label) "
                "VALUES (?, NULL, 'reserved', ?)",
                (port, label),
            )

    def acquire(
        self,
        slot_id: int,
        *,
        preferred: int | None = None,
        coresident_group: str | None = None,
        owner_label: str | None = None,
        include_listeners: bool = True,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Allocate a port and bind it to ``slot_id``; return the granted port.

        Coresident carve-out (§2.4): when the slot belongs to a
        ``coresident_group`` that already holds a live port, the slot shares
        that port — no second ``port_claim`` row is written (the anchor owns
        the one row; shadows route through the group's port).

        Otherwise the lowest free port is granted (``preferred`` first when
        free). Concurrency: a racing acquire of the same free port trips
        ``uq_port_claim_live`` and this retries the scan. The retry loop
        assumes it owns the transaction (``conn is None``); when a caller
        threads its own ``conn`` a single attempt is made.
        """
        label = owner_label or f"slot:{slot_id}"
        if coresident_group:
            with self._read(conn) as c:
                peer = c.execute(
                    "SELECT port FROM port_claim WHERE coresident_group = ? "
                    "AND released_at IS NULL ORDER BY id LIMIT 1",
                    (coresident_group,),
                ).fetchone()
            if peer is not None:
                return int(peer["port"])

        start, end = self._pool
        attempts = (end - start + 1) + 1
        pref = preferred
        for _ in range(attempts):
            port = self.next_free(preferred=pref, include_listeners=include_listeners, conn=conn)
            if port is None:
                raise PortPoolExhausted(f"no free port in pool {self._pool} for slot {slot_id}")
            try:
                with self._write(conn) as c:
                    c.execute(
                        "INSERT INTO port_claim "
                        "(port, slot_id, owner_kind, owner_label, coresident_group) "
                        "VALUES (?, ?, 'slot', ?, ?)",
                        (port, slot_id, label, coresident_group),
                    )
                return port
            except sqlite3.IntegrityError:
                if conn is not None:
                    # Caller owns the tx; we can't cleanly retry inside a
                    # poisoned transaction — surface the race to the caller.
                    raise
                pref = None  # someone took the preferred/scanned port; rescan
                continue
        raise PortPoolExhausted(f"no free port in pool {self._pool} for slot {slot_id}")

    def reallocate(
        self,
        slot_id: int,
        *,
        preferred: int | None = None,
        coresident_group: str | None = None,
        owner_label: str | None = None,
        include_listeners: bool = True,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Release the slot's current claim and grant a new one (a port move)."""
        self.release(slot_id, conn=conn)
        return self.acquire(
            slot_id,
            preferred=preferred,
            coresident_group=coresident_group,
            owner_label=owner_label,
            include_listeners=include_listeners,
            conn=conn,
        )

    def release(self, slot_id: int, *, conn: sqlite3.Connection | None = None) -> int:
        """Free every live claim a slot holds (stamps ``released_at``).

        Returns the number of claims released. No row is deleted — the audit
        trail survives and the port becomes grantable again.
        """
        with self._write(conn) as c:
            cur = c.execute(
                "UPDATE port_claim SET released_at = strftime('%s','now') "
                "WHERE slot_id = ? AND released_at IS NULL",
                (slot_id,),
            )
            return cur.rowcount

    def release_port(self, port: int, *, conn: sqlite3.Connection | None = None) -> int:
        """Free the live claim on a specific port (for reserved rows)."""
        with self._write(conn) as c:
            cur = c.execute(
                "UPDATE port_claim SET released_at = strftime('%s','now') "
                "WHERE port = ? AND released_at IS NULL",
                (port,),
            )
            return cur.rowcount

    # ── startup reconcile ────────────────────────────────────────────────

    def reconcile(self, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        """Startup pass: release claims orphaned by an out-of-band deletion.

        A slot deleted directly in SQLite has its ``port_claim.slot_id`` set
        NULL by the ``ON DELETE SET NULL`` FK; a matching ``release`` should
        have stamped ``released_at`` in the same transaction, but a crash
        between the two leaves a live slot-claim with no owner. Reclaim those
        so the port frees. Returns a summary for logging.
        """
        with self._write(conn) as c:
            cur = c.execute(
                "UPDATE port_claim SET released_at = strftime('%s','now') "
                "WHERE released_at IS NULL AND owner_kind = 'slot' AND slot_id IS NULL"
            )
            orphans_released = cur.rowcount
        return {"orphans_released": orphans_released}

    def reconcile_listeners(
        self, *, conn: sqlite3.Connection | None = None
    ) -> list[dict[str, Any]]:
        """Report pool ports in LISTEN state that no live claim covers.

        Observational — listeners are transient truth and are not written
        into the authority table. An orphan listener (something bound a pool
        port without a claim) surfaces so it shows up in ``/api/ports``.
        """
        claimed = self._live_ports(conn)
        out: list[dict[str, Any]] = []
        for port in sorted(self._listener_ports()):
            if port not in claimed:
                out.append({"port": port, "owner_kind": "listener"})
        return out
