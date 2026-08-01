"""hal0.config.migrations — versioned config migration transforms.

# TIER3: config schema versioning + migration framework.

Each migration is a callable in a module named ``vN.py`` where N is the
schema version the migration produces.  A migration takes the raw
``dict`` representation of ``hal0.toml`` at version ``N-1`` and returns
the equivalent at version ``N``.  The first migration (``v1``) is the
identity transform — the v1 schema is the initial published shape.

The runner walks ``current_version → target_version`` and applies each
migration in order, then updates ``[meta] schema_version``.  Migrations
are pure dict transforms so they can be golden-tested without touching
the filesystem.

See PLAN.md §5 Tier 3 ("Config evolution / migration tooling").

Public API::

    from hal0.config.migrations import (
        Migration, run_migrations, MIGRATIONS, latest_version,
    )

    new_data, new_version = run_migrations(old_data, target_version=2)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hal0.errors import Hal0Error

# A migration is a function: (dict) -> dict.  It receives the raw config
# at version N-1 and returns the equivalent at version N.  It must not
# mutate the input dict.
Migration = Callable[[dict[str, Any]], dict[str, Any]]


class MigrationError(Hal0Error):
    """Raised when a migration chain cannot be applied."""

    code = "config.migration_error"
    status = 500


# Registry of migrations, keyed by the target version they produce.
# Populated below by import side-effects.  Insertion order matters: the
# runner walks keys in numeric order.
MIGRATIONS: dict[int, Migration] = {}


def register(target_version: int) -> Callable[[Migration], Migration]:
    """Decorator that registers a migration as producing ``target_version``."""

    def _wrap(fn: Migration) -> Migration:
        if target_version in MIGRATIONS:
            raise MigrationError(
                f"migration for v{target_version} is already registered",
                details={"target_version": target_version},
            )
        MIGRATIONS[target_version] = fn
        return fn

    return _wrap


def latest_version() -> int:
    """Return the highest registered target version (>=1)."""
    if not MIGRATIONS:
        return 1
    return max(MIGRATIONS.keys())


def run_migrations(
    data: dict[str, Any],
    *,
    target_version: int | None = None,
) -> tuple[dict[str, Any], int]:
    """Walk migrations from ``data``'s current version to ``target_version``.

    Reads the source version from ``data["meta"]["schema_version"]``,
    defaulting to 1 if absent.  Applies each registered migration whose
    target version is in ``(source, target]`` in ascending order.

    Args:
        data: Raw ``hal0.toml`` dict (pre-validation).
        target_version: Stop after producing this version.  Defaults to
            ``latest_version()``.

    Returns:
        ``(new_data, new_version)`` — the migrated dict and the final
        schema version stamped on it.

    Raises:
        MigrationError: If a step in the chain is missing or fails.
    """
    if target_version is None:
        target_version = latest_version()

    source_version = _read_schema_version(data)

    if target_version < source_version:
        # NOTE: downgrade migrations are explicitly unsupported in v1.
        # If you need them, write per-version inverses; this is hard in
        # the general case (lossy transforms) and PLAN.md only requires
        # forward migration.
        raise MigrationError(
            f"cannot downgrade config from v{source_version} to v{target_version}",
            details={"source": source_version, "target": target_version},
        )

    if target_version == source_version:
        # No-op; still stamp the version explicitly so callers can rely
        # on `meta.schema_version` being present after run_migrations().
        out = _deep_copy_dict(data)
        out.setdefault("meta", {})["schema_version"] = target_version
        return out, target_version

    current = _deep_copy_dict(data)
    for step in range(source_version + 1, target_version + 1):
        if step not in MIGRATIONS:
            raise MigrationError(
                f"missing migration to v{step} (have: {sorted(MIGRATIONS)})",
                details={"missing_step": step, "have": sorted(MIGRATIONS)},
            )
        try:
            current = MIGRATIONS[step](current)
        except Exception as exc:
            raise MigrationError(
                f"migration to v{step} failed: {exc}",
                details={"step": step, "reason": str(exc)},
            ) from exc
        current.setdefault("meta", {})["schema_version"] = step

    return current, target_version


def _read_schema_version(data: dict[str, Any]) -> int:
    """Read ``meta.schema_version`` from a raw config dict, defaulting to 1."""
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return 1
    v = meta.get("schema_version", 1)
    try:
        return int(v)
    except (TypeError, ValueError):
        return 1


def _deep_copy_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Cheap deepcopy for the TOML-shaped dicts we deal with.

    TOML decodes to str / int / float / bool / list / dict / datetime, so
    a recursive copy with shallow-copy of leaves is correct.
    """
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[k] = _deep_copy_dict(v)
        elif isinstance(v, list):
            out[k] = list(v)
        else:
            out[k] = v
    return out


# ── Register migrations ───────────────────────────────────────────────────────
# Import side-effects: each migration module decorates its transform with
# @register(N), wiring it into MIGRATIONS.

from hal0.config.migrations import v1, v2  # noqa: E402, F401  (registration)

__all__ = [
    "MIGRATIONS",
    "Migration",
    "MigrationError",
    "latest_version",
    "register",
    "run_migrations",
]
