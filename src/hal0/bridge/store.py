"""Bridge store: ``bridge.db`` holds client key hashes and the run ledger (ADR-0043).

Machine state, so SQLite rather than TOML. The file is a separate 0600 database
from ``hal0.db`` because it holds credential hashes and the text of delegated
runs. Only the sha256 of each client key is stored; the plaintext is returned
once by :meth:`BridgeStore.add_client` / :meth:`BridgeStore.rotate_client` and
cannot be recovered.

The run ledger (decision 9) is the ``runs`` table plus the newest
:data:`EVENT_CAP` event rows of each run in ``run_events``. A run is the bridge's
record of work it delegated to Hermes; the ledger is what survives a client
disconnect or a hal0-api restart so the run can still be polled and replayed.

All methods are synchronous and open their own connection per call, the house
rule in :mod:`hal0.db.connection`; async callers wrap them in
``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from hal0.bridge import keys
from hal0.bridge.errors import BridgeError
from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate

#: The ``bridge.db`` schema, applied by :func:`hal0.db.migrate.migrate`.
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

#: Owner read/write only: the file carries key hashes and run text.
_DB_MODE = 0o600

#: ``last_seen_at`` is stamped at most this often per client (seconds), so
#: verifying a key on every request is a read, not a write.
LAST_SEEN_RESOLUTION_S = 60.0

_CLIENT_COLUMNS = "name, created_at, created_by, revoked_at, last_seen_at, note, scopes"

#: Event rows kept per run: ``append_event`` deletes every row below the
#: ``EVENT_CAP``-th newest. A count of rows, not a window of sequence numbers:
#: Hermes gives every ``message.delta`` its own ``seq`` while the watcher stores
#: many as one ``message.text`` row, so a window of 500 sequence numbers kept the
#: text of only the last ~500 deltas. A run's whole output stays in
#: ``runs.output`` and is read in windows (:mod:`hal0.bridge.output`).
EVENT_CAP = 500

#: Run statuses that mean nothing more will happen to a run. ``unknown`` is
#: Hermes saying it no longer knows the run, so the ledger will not hear more.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "cancelled", "interrupted", "unknown"}
)

_TERMINAL_SQL = ", ".join(f"'{status}'" for status in sorted(TERMINAL_STATUSES))


class BridgeStoreError(Exception):
    """A store operation was refused; ``code`` is the stable ``bridge.*`` error code.

    Codes: ``bridge.client_exists``, ``bridge.client_not_found``,
    ``bridge.client_revoked``, ``bridge.invalid_name``, ``bridge.invalid_scope``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ClientRow:
    """One bridge client. Carries no key material, not even the hash."""

    name: str
    created_at: float
    created_by: str
    revoked_at: float | None
    last_seen_at: float | None
    note: str
    scopes: frozenset[str]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dict, with ``scopes`` as a sorted list."""
        return {
            "name": self.name,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "revoked_at": self.revoked_at,
            "last_seen_at": self.last_seen_at,
            "note": self.note,
            "scopes": sorted(self.scopes),
        }


def _client_from_row(row: sqlite3.Row) -> ClientRow:
    return ClientRow(
        name=row["name"],
        created_at=row["created_at"],
        created_by=row["created_by"],
        revoked_at=row["revoked_at"],
        last_seen_at=row["last_seen_at"],
        note=row["note"],
        scopes=frozenset(s for s in row["scopes"].split(",") if s),
    )


@dataclass(frozen=True)
class RunRow:
    """One delegated run, mirroring the ``runs`` columns.

    ``usage_json`` and ``pending_request_json`` hold JSON text exactly as stored;
    :meth:`as_dict` decodes them. ``last_seq`` is the highest event sequence the
    ledger has written for the run.
    """

    run_id: str
    client: str
    agent: str
    profile: str
    session_key: str | None
    status: str
    created_at: float
    updated_at: float
    input_preview: str
    output: str | None
    error: str | None
    usage_json: str | None
    admission_id: str | None
    execution_generation: int | None
    pending_request_json: str | None
    last_seq: int
    #: The key hal0 sent Hermes for the start (``hal0-bridge:<client>:<key>``), or None.
    idempotency_key: str | None = None
    #: sha256 of the start's request (``BridgeService``), to tell a replay from a conflict.
    request_sha256: str | None = None
    #: When hal0 decided to stop the run while the stop has not reached Hermes yet.
    pending_stop: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dict: ``usage`` and ``pending`` are decoded, the raw columns dropped.

        The replay columns (``idempotency_key``, ``request_sha256``) are hal0's own
        bookkeeping and are left out too.
        """
        body = {f.name: getattr(self, f.name) for f in fields(self)}
        body.pop("idempotency_key")
        body.pop("request_sha256")
        usage = body.pop("usage_json")
        pending = body.pop("pending_request_json")
        body["usage"] = None if usage is None else json.loads(usage)
        body["pending"] = None if pending is None else json.loads(pending)
        return body


_RUN_COLUMNS = tuple(f.name for f in fields(RunRow))
_RUN_SELECT = ", ".join(_RUN_COLUMNS)


def _run_from_row(row: sqlite3.Row) -> RunRow:
    return RunRow(**{name: row[name] for name in _RUN_COLUMNS})


def _select_run(conn: sqlite3.Connection, run_id: str) -> RunRow | None:
    row = conn.execute(f"SELECT {_RUN_SELECT} FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return None if row is None else _run_from_row(row)


def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "seq": row["seq"],
        "ts": row["ts"],
        "event": row["event"],
        "payload": json.loads(row["payload_json"]),
    }


def _run_not_found(run_id: str) -> BridgeError:
    return BridgeError("bridge.run_not_found", f"no run {run_id!r}")


def _select_client(conn: sqlite3.Connection, name: str) -> ClientRow | None:
    row = conn.execute(f"SELECT {_CLIENT_COLUMNS} FROM clients WHERE name = ?", (name,)).fetchone()
    return None if row is None else _client_from_row(row)


def _require_client(conn: sqlite3.Connection, name: str) -> ClientRow:
    row = _select_client(conn, name)
    if row is None:
        raise BridgeStoreError("bridge.client_not_found", f"no bridge client named {name!r}")
    return row


def _check_name(name: str) -> None:
    if not keys.valid_name(name):
        raise BridgeStoreError(
            "bridge.invalid_name",
            f"invalid bridge client name {name!r}: use 1-32 characters from a-z, 0-9, '_' "
            "and '-', starting with a letter or digit",
        )
    if keys.is_reserved_name(name):
        raise BridgeStoreError(
            "bridge.invalid_name",
            f"invalid bridge client name {name!r}: reserved name",
        )


def _scopes_or_error(values: Iterable[str]) -> frozenset[str]:
    try:
        return keys.normalize_scopes(values)
    except ValueError as exc:
        raise BridgeStoreError("bridge.invalid_scope", str(exc)) from exc


def _scopes_column(scopes: frozenset[str]) -> str:
    return ",".join(sorted(scopes))


class BridgeStore:
    """The bridge client store, backed by one SQLite file at ``path``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def open(self) -> None:
        """Create the database if needed, apply migrations, and make it owner-only.

        Idempotent: a second call applies no migration and changes no row. The
        file is created empty at 0600 *before* SQLite first opens it, so there
        is no window at the process umask; SQLite gives the ``-wal`` and
        ``-shm`` siblings the main file's mode. A pre-existing file with wider
        bits is narrowed, never widened.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(self.path, os.O_RDWR | os.O_CREAT, _DB_MODE))
        with connect(self.path) as conn:
            migrate(conn, migrations_dir=MIGRATIONS_DIR)
        os.chmod(self.path, _DB_MODE)

    def add_client(
        self,
        name: str,
        created_by: str,
        note: str = "",
        scopes: Iterable[str] = keys.DEFAULT_SCOPES,
    ) -> tuple[ClientRow, str]:
        """Add a client; return ``(row, plaintext key)``.

        The plaintext is shown once and never stored. A name that already has a
        row, revoked or not, is refused with ``bridge.client_exists``: a revoked
        client keeps its name so past runs stay attributable.
        """
        _check_name(name)
        granted = _scopes_or_error(scopes)
        plain, digest = keys.mint(name)
        with connect(self.path) as conn, tx(conn):
            if _select_client(conn, name) is not None:
                raise BridgeStoreError(
                    "bridge.client_exists", f"a bridge client named {name!r} already exists"
                )
            conn.execute(
                "INSERT INTO clients (name, key_hash, created_at, created_by, note, scopes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, digest, time.time(), created_by, note, _scopes_column(granted)),
            )
            return _require_client(conn, name), plain

    def verify_key(self, key: str) -> ClientRow | None:
        """Return the live client that ``key`` belongs to, or None.

        None for a malformed key, an unknown or revoked key, and a key whose
        embedded name is not the name on the row its hash matches. The lookup
        is one indexed read on the sha256 of the key; matching a hash replaces
        a constant-time compare, since the digest of a guessed key tells a
        timing observer nothing about the stored one.

        A hit stamps ``last_seen_at``, but at most once every
        :data:`LAST_SEEN_RESOLUTION_S`: a fresher stamp is returned as read,
        so the per-request check stays a read and takes no write lock. A
        stale one is bumped and the row returned as it is after the bump.
        """
        name = keys.name_of(key)
        if name is None:
            return None
        digest = keys.hash_key(key)
        with connect(self.path) as conn:
            row = conn.execute(
                f"SELECT {_CLIENT_COLUMNS} FROM clients "
                "WHERE key_hash = ? AND name = ? AND revoked_at IS NULL",
                (digest, name),
            ).fetchone()
            if row is None:
                return None
            client = _client_from_row(row)
            now = time.time()
            seen = client.last_seen_at
            if seen is not None and 0 <= now - seen < LAST_SEEN_RESOLUTION_S:
                return client
            with tx(conn):
                # Match again under the write lock: a revoke or rotate may have
                # landed since the read, and it must not be answered with a row.
                updated = conn.execute(
                    "UPDATE clients SET last_seen_at = ? "
                    "WHERE key_hash = ? AND name = ? AND revoked_at IS NULL",
                    (now, digest, name),
                )
                if updated.rowcount == 0:
                    return None
                return _select_client(conn, name)

    def list_clients(self) -> list[ClientRow]:
        """Return every client, revoked ones included, ordered by name."""
        with connect(self.path) as conn:
            rows = conn.execute(f"SELECT {_CLIENT_COLUMNS} FROM clients ORDER BY name").fetchall()
        return [_client_from_row(r) for r in rows]

    def get_client(self, name: str) -> ClientRow | None:
        """Return the client named ``name``, or None."""
        with connect(self.path) as conn:
            return _select_client(conn, name)

    def revoke_client(self, name: str) -> ClientRow:
        """Revoke ``name``'s key. Revoking an already revoked client changes nothing."""
        with connect(self.path) as conn, tx(conn):
            _require_client(conn, name)
            conn.execute(
                "UPDATE clients SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL",
                (time.time(), name),
            )
            return _require_client(conn, name)

    def rotate_client(self, name: str) -> tuple[ClientRow, str]:
        """Replace ``name``'s key; return ``(row, new plaintext key)``.

        The old key stops verifying at once. Scopes, note and the created and
        last-seen fields are kept. A revoked client cannot be rotated.
        """
        with connect(self.path) as conn, tx(conn):
            current = _require_client(conn, name)
            if current.revoked_at is not None:
                raise BridgeStoreError(
                    "bridge.client_revoked",
                    f"bridge client {name!r} is revoked; add a new client instead",
                )
            # Every stored name passed valid_name when it was added, so mint cannot raise.
            plain, digest = keys.mint(name)
            conn.execute("UPDATE clients SET key_hash = ? WHERE name = ?", (digest, name))
            return _require_client(conn, name), plain

    def set_scopes(self, name: str, scopes: Iterable[str]) -> ClientRow:
        """Replace ``name``'s scopes with ``scopes`` (``agents`` is always kept)."""
        granted = _scopes_or_error(scopes)
        with connect(self.path) as conn, tx(conn):
            _require_client(conn, name)
            conn.execute(
                "UPDATE clients SET scopes = ? WHERE name = ?", (_scopes_column(granted), name)
            )
            return _require_client(conn, name)

    # -- run ledger (decision 9) -------------------------------------------------------

    def insert_run(self, row: RunRow) -> None:
        """Record a new run. A ``run_id`` that already exists raises ``sqlite3.IntegrityError``."""
        placeholders = ", ".join("?" for _ in _RUN_COLUMNS)
        with connect(self.path) as conn, tx(conn):
            conn.execute(
                f"INSERT INTO runs ({_RUN_SELECT}) VALUES ({placeholders})",
                tuple(getattr(row, name) for name in _RUN_COLUMNS),
            )

    def get_run(self, run_id: str) -> RunRow | None:
        """Return the run, or None."""
        with connect(self.path) as conn:
            return _select_run(conn, run_id)

    def run_for_idempotency_key(
        self, client: str, profile: str, idempotency_key: str
    ) -> RunRow | None:
        """The newest run ``client`` started on ``profile`` with ``idempotency_key``, or None."""
        with connect(self.path) as conn:
            row = conn.execute(
                f"SELECT {_RUN_SELECT} FROM runs "
                "WHERE client = ? AND profile = ? AND idempotency_key = ? "
                "ORDER BY created_at DESC, run_id DESC LIMIT 1",
                (client, profile, idempotency_key),
            ).fetchone()
        return None if row is None else _run_from_row(row)

    def list_runs(self, *, client: str | None, status: str | None, limit: int) -> list[RunRow]:
        """Return up to ``limit`` runs, newest first.

        ``client`` scopes the list to one bridge client (None lists every client's
        runs, the operator view); ``status`` keeps only runs in exactly that status.
        """
        where: list[str] = []
        args: list[Any] = []
        if client is not None:
            where.append("client = ?")
            args.append(client)
        if status is not None:
            where.append("status = ?")
            args.append(status)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with connect(self.path) as conn:
            rows = conn.execute(
                f"SELECT {_RUN_SELECT} FROM runs{clause} "
                "ORDER BY created_at DESC, run_id DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
        return [_run_from_row(r) for r in rows]

    def update_run(self, run_id: str, /, **fields_: Any) -> RunRow:
        """Set columns on a run and return it as it is after the update.

        ``fields_`` are ``runs`` column names (JSON columns take JSON text).
        ``updated_at`` is set to now unless given. ``run_id`` cannot change and an
        unknown column raises ``ValueError``; an unknown run raises
        ``bridge.run_not_found``.
        """
        with connect(self.path) as conn, tx(conn):
            return self._update_run(conn, run_id, fields_)

    @staticmethod
    def _update_run(conn: sqlite3.Connection, run_id: str, fields_: dict[str, Any]) -> RunRow:
        """:meth:`update_run` inside the caller's transaction."""
        if "run_id" in fields_:
            raise ValueError("update_run cannot change run_id")
        unknown = sorted(set(fields_) - set(_RUN_COLUMNS))
        if unknown:
            raise ValueError(f"unknown runs column(s): {', '.join(unknown)}")
        fields_.setdefault("updated_at", time.time())
        assignments = ", ".join(f"{name} = ?" for name in fields_)
        updated = conn.execute(
            f"UPDATE runs SET {assignments} WHERE run_id = ?", (*fields_.values(), run_id)
        )
        if updated.rowcount == 0:
            raise _run_not_found(run_id)
        row = _select_run(conn, run_id)
        assert row is not None  # the UPDATE just matched it, inside this transaction
        return row

    def record(
        self,
        run_id: str,
        event: tuple[int, float, str, Any] | None,
        decide: Callable[[RunRow], dict[str, Any]],
        *,
        skip_terminal: bool,
    ) -> tuple[RunRow, RunRow, bool] | None:
        """Append ``event`` (``(seq, ts, name, payload)``, optional) and update the run, atomically.

        One transaction reads the run, writes the event, and applies the column
        changes ``decide(run)`` picks, so a crash cannot keep the event without
        the row change (a pending request lost while its ``approval.request``
        event survives) or the other way round, and a reader of a terminal
        status finds every event the run recorded (ADR-0043 decision 9).

        Returns ``(before, after, changed)``, or None, with nothing written, when
        the run is gone or, with ``skip_terminal``, already terminal.
        """
        with connect(self.path) as conn, tx(conn):
            before = _select_run(conn, run_id)
            if before is None or (skip_terminal and before.status in TERMINAL_STATUSES):
                return None
            if event is not None:
                seq, ts, name, payload = event
                self._append_event(conn, run_id, seq, ts, name, payload)
            changes = decide(before)
            if changes:
                after = self._update_run(conn, run_id, changes)
            elif event is not None:
                after = _select_run(conn, run_id) or before  # last_seq moved
            else:
                after = before
            return before, after, bool(changes) or event is not None

    def append_event(self, run_id: str, seq: int, ts: float, event: str, payload: Any) -> None:
        """Write one event, raise ``last_seq``, and keep only the newest :data:`EVENT_CAP` rows.

        A repeated ``seq`` replaces the earlier row (a reconnect can replay a frame).
        An unknown run raises ``bridge.run_not_found``.
        """
        with connect(self.path) as conn, tx(conn):
            self._append_event(conn, run_id, seq, ts, event, payload)

    @staticmethod
    def _append_event(
        conn: sqlite3.Connection, run_id: str, seq: int, ts: float, event: str, payload: Any
    ) -> None:
        """:meth:`append_event` inside the caller's transaction."""
        bumped = conn.execute(
            "UPDATE runs SET last_seq = MAX(last_seq, ?) WHERE run_id = ?", (seq, run_id)
        )
        if bumped.rowcount == 0:
            raise _run_not_found(run_id)
        conn.execute(
            "INSERT OR REPLACE INTO run_events (run_id, seq, ts, event, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, ts, event, json.dumps(payload)),
        )
        # Below the EVENT_CAP-th newest row; NULL (nothing deleted) while there are fewer.
        conn.execute(
            "DELETE FROM run_events WHERE run_id = ? AND seq < ("
            "SELECT seq FROM run_events WHERE run_id = ? ORDER BY seq DESC LIMIT 1 OFFSET ?)",
            (run_id, run_id, EVENT_CAP - 1),
        )

    def events(self, run_id: str, *, since_seq: int, limit: int) -> list[dict[str, Any]]:
        """Return up to ``limit`` events with ``seq`` above ``since_seq``, oldest first.

        Each is ``{"seq", "ts", "event", "payload"}`` with ``payload`` decoded.
        """
        with connect(self.path) as conn:
            rows = conn.execute(
                "SELECT seq, ts, event, payload_json FROM run_events "
                "WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, since_seq, limit),
            ).fetchall()
        return [_event_from_row(r) for r in rows]

    def last_events(self, run_id: str, *, limit: int) -> list[dict[str, Any]]:
        """Return the newest ``limit`` events of a run, oldest first (a wait's tail)."""
        with connect(self.path) as conn:
            rows = conn.execute(
                "SELECT seq, ts, event, payload_json FROM ("
                "SELECT seq, ts, event, payload_json FROM run_events "
                "WHERE run_id = ? ORDER BY seq DESC LIMIT ?) ORDER BY seq",
                (run_id, limit),
            ).fetchall()
        return [_event_from_row(r) for r in rows]

    def live_runs(self) -> list[RunRow]:
        """Return every run that is not terminal, oldest first (what the watcher resumes)."""
        with connect(self.path) as conn:
            rows = conn.execute(
                f"SELECT {_RUN_SELECT} FROM runs WHERE status NOT IN ({_TERMINAL_SQL}) "
                "ORDER BY created_at, run_id"
            ).fetchall()
        return [_run_from_row(r) for r in rows]

    def count_live_runs(self, client: str) -> int:
        """Count ``client``'s runs that are not terminal (the concurrency cap checks this)."""
        with connect(self.path) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM runs WHERE client = ? AND status NOT IN ({_TERMINAL_SQL})",
                (client,),
            ).fetchone()
        return int(row[0])

    def prune_runs(self, older_than: float) -> int:
        """Delete runs created before ``older_than`` (epoch seconds) with their events.

        Retention counts from creation and ignores status: a run still marked live
        after the retention window is an orphan the watcher lost. Returns the number
        of runs deleted.
        """
        with connect(self.path) as conn, tx(conn):
            deleted = conn.execute("DELETE FROM runs WHERE created_at < ?", (older_than,))
            return deleted.rowcount
