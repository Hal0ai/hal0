"""hal0.db — the shared SQLite substrate (seam S8).

One embedded database (``/var/lib/hal0/hal0.db``, see
:func:`hal0.config.paths.db_path`) is the foundation every later
storage lane builds on: the model registry (ML-1, this lane), then
PortAuthority's port claims, the metrics tables, and runtime-state.
This package is deliberately generic — nothing here is registry-
specific.

Key exports:
    connect  — open one PRAGMA-configured connection (WAL, foreign_keys=ON).
    tx       — ``BEGIN IMMEDIATE`` write-transaction context manager.
    db_path  — resolve the default database file path.
    migrate  — forward-only ``schema_migrations`` runner.

Port target: none (new substrate). See PLAN.md §7.5/§8 and
hal0-specs/spec-ml1-sqlite.final.md.
"""

from __future__ import annotations

from hal0.db.connection import connect, db_path, tx
from hal0.db.migrate import migrate

__all__ = ["connect", "db_path", "migrate", "tx"]
