"""Bridge client store: ``bridge.db`` holds client key hashes (ADR-0043).

Machine state, so SQLite rather than TOML. The file is a separate 0600 database
from ``hal0.db`` because it holds credential hashes and, later, the text of
delegated runs. Only the sha256 of each client key is stored; the plaintext is
returned once by :meth:`BridgeStore.add_client` / :meth:`BridgeStore.rotate_client`
and cannot be recovered.

All methods are synchronous and open their own connection per call, the house
rule in :mod:`hal0.db.connection`; async callers wrap them in
``asyncio.to_thread``.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hal0.bridge import keys
from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

#: Owner read/write only: the file carries key hashes and run text.
_DB_MODE = 0o600

_DEFAULT_SCOPES: tuple[str, ...] = ("agents", "memory")

_CLIENT_COLUMNS = "name, created_at, created_by, revoked_at, last_seen_at, note, scopes"


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
            migrate(conn, migrations_dir=_MIGRATIONS_DIR)
        os.chmod(self.path, _DB_MODE)

    def add_client(
        self,
        name: str,
        created_by: str,
        note: str = "",
        scopes: Iterable[str] = _DEFAULT_SCOPES,
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
        embedded name is not the name on the row its hash matches. A hit bumps
        ``last_seen_at`` and returns the row as it is after the bump.
        """
        name = keys.name_of(key)
        if name is None:
            return None
        digest = keys.hash_key(key)
        with connect(self.path) as conn:
            # Read first so keys that match nothing never take the write lock.
            hit = conn.execute(
                "SELECT 1 FROM clients WHERE key_hash = ? AND name = ? AND revoked_at IS NULL",
                (digest, name),
            ).fetchone()
            if hit is None:
                return None
            with tx(conn):
                # Match again under the write lock: a revoke or rotate may have
                # landed since the read, and it must not be answered with a row.
                updated = conn.execute(
                    "UPDATE clients SET last_seen_at = ? "
                    "WHERE key_hash = ? AND name = ? AND revoked_at IS NULL",
                    (time.time(), digest, name),
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
