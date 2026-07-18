"""SQLite connection + transaction helpers — the hal0 ``db/`` foundation.

One connection per request/task; WAL journaling makes that safe without a
global singleton connection (plan §8.1). There is no module-level pool or
cached connection here on purpose — every call site opens its own via
:func:`connect`, exactly like the house pattern in
``hal0.activity.AuditStore._connect``, generalised with two additions:
``foreign_keys=ON`` and a real forward-only migration runner
(:mod:`hal0.db.migrate`) instead of ad-hoc ``CREATE TABLE IF NOT EXISTS``.

Load-bearing gotcha: ``PRAGMA foreign_keys`` is a *per-connection* SQLite
setting, not a database-wide one. It must be reissued on every
:func:`connect` call — the ``model_file``/``model_backend`` ``ON DELETE
CASCADE`` in ``db/migrations/001_registry.sql`` silently no-ops otherwise.

Backups: one file replaces N concurrently-written JSON/TOML files, which
fixes the class of PBS/FUSE backup hangs a flat file tree produces. Take an
atomic, consistent snapshot without blocking writers with either::

    conn.execute("VACUUM INTO ?", (str(backup_path),))

or, using the stdlib online backup API from a second connection::

    with connect() as src, sqlite3.connect(backup_path) as dst:
        src.backup(dst)
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hal0.config import paths

#: Retry budget for the one-time WAL-mode switch (see `_set_wal_mode`).
_WAL_SWITCH_ATTEMPTS = 20
_WAL_SWITCH_RETRY_DELAY_S = 0.05


def db_path() -> Path:
    """Return the default hal0 SQLite database path.

    Thin wrapper over :func:`hal0.config.paths.db_path` so callers only
    need to import this module. HAL0_HOME-aware (tests get automatic
    isolation the same way :func:`hal0.config.paths.registry_dir` does).
    """
    return paths.db_path()


def _set_wal_mode(conn: sqlite3.Connection) -> None:
    """Switch to WAL journal mode, retrying through transient contention.

    The *first* switch away from the default rollback-journal mode needs
    a brief exclusive lock; under many connections racing to open the
    same brand-new database file at once (e.g. a burst of concurrent
    ``SqliteModelRegistry.add()`` calls, each opening its own connection),
    that lock request can raise ``sqlite3.OperationalError: database is
    locked`` immediately rather than waiting out ``busy_timeout`` — this
    pragma is not routed through SQLite's normal busy-handler retry loop
    on every platform/version. Once WAL is active, re-issuing this pragma
    on a later connection is a cheap no-op read, so the retry loop only
    ever matters on true first-open contention, not on the steady state.
    """
    for attempt in range(_WAL_SWITCH_ATTEMPTS):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            if attempt == _WAL_SWITCH_ATTEMPTS - 1:
                raise
            time.sleep(_WAL_SWITCH_RETRY_DELAY_S)


@contextmanager
def connect(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Open one PRAGMA-configured connection to the hal0 database.

    ``isolation_level=None`` puts the connection in autocommit mode — this
    module owns transaction boundaries explicitly via :func:`tx` rather
    than relying on sqlite3's implicit "open a transaction before the
    first DML statement" behaviour.

    Pragmas applied on *every* call (see module docstring — ``foreign_keys``
    does not persist across connections):

    * ``journal_mode=WAL`` — concurrent readers don't block a writer.
    * ``foreign_keys=ON`` — required for ``ON DELETE CASCADE`` children.
    * ``busy_timeout=5000`` — wait instead of raising ``database is locked``
      under brief write contention.
    * ``synchronous=NORMAL`` — safe under WAL (durable on commit at the OS
      level; only a hard power loss between WAL checkpoints is at risk,
      same trade-off the ``activity`` store already makes).
    """
    target = Path(path) if path is not None else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # busy_timeout FIRST: switching journal_mode to WAL itself needs a
    # transient exclusive lock, so under heavy concurrent first-open
    # contention (many threads/connections racing to switch a fresh
    # database into WAL mode at once) it can hit "database is locked" if
    # the timeout isn't already active for that very statement.
    conn.execute("PRAGMA busy_timeout=5000")
    _set_wal_mode(conn)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Wrap a write in an explicit ``BEGIN IMMEDIATE`` … ``COMMIT``/``ROLLBACK``.

    ``BEGIN IMMEDIATE`` acquires SQLite's write lock immediately (rather
    than at the first write statement), giving the same cross-process
    read-modify-write serialization the registry's old ``fcntl.flock``
    sidecar gave — plus native cross-thread safety, which the flock alone
    did not provide. This is why the SQLite-backed registry
    (``hal0.registry.sqlite_store.SqliteModelRegistry``) needs no
    ``threading.RLock``, no sidecar lockfile, and no mtime cache: SQLite's
    own locking replaces all three.

    On any exception the transaction is rolled back and the exception
    re-raised untouched.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
