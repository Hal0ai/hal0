"""``model`` row ⇄ :class:`hal0.registry.model.Model` mapping — the pydantic seam.

Keeps :class:`hal0.registry.sqlite_store.SqliteModelRegistry` thin: all the
JSON-encoding, ``ModelDefaults`` fold-down/reconstruction, and
``metadata``/``extra`` splitting lives here. Validation stays exactly where
it already was — pydantic's :class:`~hal0.registry.model.Model` — this
module only ever returns/accepts that existing model, never a bespoke row
type (plan §8.1: "a thin repository layer returns/accepts the existing
pydantic models, so validation stays where it is").

Field-mapping rules (must hold for a lossless TOML round-trip, see
``db/migrations/001_registry.sql``):

1. ``metadata["context_length"]`` gets its own ``model.context_length``
   column; every other metadata key (including the reserved
   ``upstream_url``, read by :meth:`SqliteModelRegistry.route_for`) rides
   verbatim inside the catch-all ``extra`` JSON blob.
2. ``capabilities``/``tags`` are JSON-array text columns for this pilot —
   small enough not to need child tables yet.
3. ``backends`` lives in the separate ``model_backend`` child table, not on
   this row at all — see :meth:`row_to_model`'s ``backends`` parameter.
4. ``defaults`` is ``None`` when every ``ModelDefaults`` column is NULL;
   otherwise it is reconstructed from the columns. Mirrors
   ``hal0.registry.store._model_to_toml``'s "collapse to no key when
   nothing is set".
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from hal0.registry.model import Model, ModelCapabilities, ModelDefaults

_CONTEXT_LENGTH_KEY = "context_length"

#: §7.1d fields with no dedicated column yet (``capability_flags``,
#: ``modalities_override``) ride inside the catch-all ``extra`` JSON blob
#: under these reserved, underscore-prefixed keys so they round-trip
#: losslessly without a schema migration (only ``mtp``/``jinja`` have
#: reserved *columns* today — ``tool_calling`` does not, since a new
#: column needs a migration file and this pilot only ships ``001``).
#: Split out of ``metadata`` on read so they never leak into
#: ``Model.metadata``.
_CAPABILITY_FLAGS_EXTRA_KEY = "_capability_flags"
_MODALITIES_OVERRIDE_EXTRA_KEY = "_modalities_override"

#: Columns of the `model` table, in the order INSERT/UPDATE statements bind
#: them. Kept as a tuple (not re-derived from a dict each call) so
#: SqliteModelRegistry can build parameterized SQL generically.
MODEL_COLUMNS: tuple[str, ...] = (
    "id",
    "source_repo",
    "revision",
    "path",
    "preferred_runner",
    "mmproj",
    "architecture",
    "context_length",
    "mtp",
    "jinja",
    "name",
    "size_bytes",
    "quant",
    "license",
    "hf_filename",
    "profile",
    "extra_args",
    "n_gpu_layers",
    "chat_template",
    "context_size",
    "rope_freq_base",
    "capabilities",
    "tags",
    "sha256",
    "pulled_at",
    "created_at",
    "updated_at",
    "extra",
)

#: ModelDefaults columns folded onto the `model` row. `defaults` reconstructs
#: iff at least one of these is non-NULL.
_DEFAULTS_COLUMNS: tuple[str, ...] = (
    "profile",
    "extra_args",
    "n_gpu_layers",
    "chat_template",
    "context_size",
    "rope_freq_base",
)


def now_iso() -> str:
    """ISO-8601 UTC timestamp — matches the ``activity``/``bench`` convention."""
    return datetime.now(UTC).isoformat()


def model_to_row(
    model: Model,
    *,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Serialise a ``Model`` into a flat dict keyed by the `model` table columns.

    ``created_at`` should be passed through unchanged on an UPDATE (only
    ``updated_at`` advances); on an INSERT both default to "now".

    §7.1 columns with no ``Model`` field yet (``revision``, ``mtp``,
    ``jinja``) always write NULL in this pilot — they exist purely as a
    ready-made landing spot for the ML-5 lane that adds those fields to
    ``Model`` itself. ``architecture`` and ``preferred_runner`` ARE
    populated here — §7.1d lands ``architecture``, ML-4 lands
    ``preferred_runner`` (see ``Model.preferred_runner``).
    ``capability_flags``/``modalities_override`` have no reserved column
    yet, so they fold into the ``extra`` JSON blob under the reserved keys
    above instead of a schema migration.
    """
    defaults = model.defaults or ModelDefaults()

    # context_length + the §7.1d extras get their own handling; everything
    # else (including the reserved upstream_url) rides inside `extra`
    # verbatim.
    metadata = dict(model.metadata or {})
    context_length = metadata.pop(_CONTEXT_LENGTH_KEY, None)
    capability_flags = model.capability_flags.model_dump(exclude_none=True)
    if capability_flags:
        metadata[_CAPABILITY_FLAGS_EXTRA_KEY] = capability_flags
    if model.modalities_override is not None:
        metadata[_MODALITIES_OVERRIDE_EXTRA_KEY] = [m.value for m in model.modalities_override]
    extra_json = json.dumps(metadata, separators=(",", ":")) if metadata else None

    ts = updated_at or now_iso()
    return {
        "id": model.id,
        "source_repo": model.hf_repo or None,
        "revision": None,
        "path": model.path,
        "preferred_runner": model.preferred_runner,
        "mmproj": model.mmproj,
        "architecture": model.architecture,
        "context_length": context_length,
        "mtp": None,
        "jinja": None,
        "name": model.name,
        "size_bytes": model.size_bytes,
        "quant": model.quant,
        "license": model.license,
        "hf_filename": model.hf_filename,
        "profile": defaults.profile,
        "extra_args": defaults.extra_args,
        "n_gpu_layers": defaults.n_gpu_layers,
        "chat_template": defaults.chat_template,
        "context_size": defaults.context_size,
        "rope_freq_base": defaults.rope_freq_base,
        "capabilities": json.dumps(list(model.capabilities), separators=(",", ":")),
        "tags": json.dumps(list(model.tags), separators=(",", ":")),
        "sha256": None,
        "pulled_at": None,
        "created_at": created_at or ts,
        "updated_at": ts,
        "extra": extra_json,
    }


# ── model_file / store_blob (ML-2/ML-3 — the file-SET + refcount tables) ────
#
# `model_file` ships EMPTY from ML-1's `001_registry.sql`; ML-2's
# `registry.fileset` module is its first writer, via `insert_model_file`
# below. `INSERT OR IGNORE` (not REPLACE) is deliberate idempotency: a
# re-run of the same fileset plan against an already-populated model must
# not clobber `dest`/`sha256` written by a prior successful install.


def insert_model_file(
    conn: sqlite3.Connection,
    *,
    model_id: str,
    rel: str,
    dest: str | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    lfs: bool | None = None,
    role: str | None = None,
    shard_index: int | None = None,
) -> None:
    """Insert one ``model_file`` row (idempotent — first-writer semantics)."""
    conn.execute(
        "INSERT OR IGNORE INTO model_file "
        "(model_id, rel, dest, size_bytes, sha256, lfs, role, shard_index) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            model_id,
            rel,
            dest,
            size_bytes,
            sha256,
            (1 if lfs else 0) if lfs is not None else None,
            role,
            shard_index,
        ),
    )


def upsert_model_file(
    conn: sqlite3.Connection,
    *,
    model_id: str,
    rel: str,
    dest: str | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    lfs: bool | None = None,
    role: str | None = None,
    shard_index: int | None = None,
) -> None:
    """Insert-or-replace one ``model_file`` row.

    Unlike :func:`insert_model_file` (idempotent no-op on an existing
    ``(model_id, rel)``), this is for the update-in-place path — a re-pull
    of a model that changed dest/sha256 (new revision) needs the row to
    actually advance.
    """
    conn.execute(
        "INSERT INTO model_file (model_id, rel, dest, size_bytes, sha256, lfs, role, shard_index) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(model_id, rel) DO UPDATE SET "
        "dest=excluded.dest, size_bytes=excluded.size_bytes, sha256=excluded.sha256, "
        "lfs=excluded.lfs, role=excluded.role, shard_index=excluded.shard_index",
        (
            model_id,
            rel,
            dest,
            size_bytes,
            sha256,
            (1 if lfs else 0) if lfs is not None else None,
            role,
            shard_index,
        ),
    )


def list_model_files(conn: sqlite3.Connection, model_id: str) -> list[dict[str, Any]]:
    """Return every ``model_file`` row for ``model_id``, shard-ordered.

    Entry point (shard_index=1 or the lone non-shard file) sorts first;
    non-shard rows (mmproj/tokenizer/config, ``shard_index`` NULL) sort
    after by ``rel`` for a stable, deterministic order.
    """
    rows = conn.execute(
        "SELECT * FROM model_file WHERE model_id = ? "
        "ORDER BY (shard_index IS NULL), shard_index, rel",
        (model_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_blob(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    """Return the ``store_blob`` row for ``sha256``, or ``None``."""
    return conn.execute("SELECT * FROM store_blob WHERE sha256 = ?", (sha256,)).fetchone()


def insert_blob(
    conn: sqlite3.Connection,
    *,
    sha256: str,
    size_bytes: int,
    blob_path: str,
    refcount: int = 1,
) -> None:
    """Register a freshly-installed file as the canonical blob for its sha256."""
    conn.execute(
        "INSERT INTO store_blob (sha256, size_bytes, blob_path, refcount, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (sha256, size_bytes, str(blob_path), refcount, now_iso()),
    )


def bump_blob_ref(conn: sqlite3.Connection, sha256: str, *, by: int = 1) -> None:
    """Increment ``store_blob.refcount`` — a new hardlink now shares this blob."""
    conn.execute(
        "UPDATE store_blob SET refcount = refcount + ? WHERE sha256 = ?",
        (by, sha256),
    )


def drop_blob_ref(conn: sqlite3.Connection, sha256: str, *, by: int = 1) -> int:
    """Decrement ``store_blob.refcount``; returns the new count, or -1 if absent.

    Floors at 0 (never goes negative even if called more times than the
    blob was ever referenced — a defensive clamp, not a correctness
    assumption). The caller (:mod:`hal0.registry.gc`) treats
    ``refcount <= 0`` as an orphan eligible for pruning.
    """
    row = get_blob(conn, sha256)
    if row is None:
        return -1
    new_count = max(0, row["refcount"] - by)
    conn.execute("UPDATE store_blob SET refcount = ? WHERE sha256 = ?", (new_count, sha256))
    return new_count


def row_to_model(row: sqlite3.Row, *, backends: list[str] | None = None) -> Model:
    """Reconstruct a ``Model`` from one `model` table row.

    ``backends`` comes from a separate ``model_backend`` query — the
    caller (:class:`~hal0.registry.sqlite_store.SqliteModelRegistry`) joins
    it in, since backends live in a child table, not a row column.
    """
    metadata: dict[str, Any] = json.loads(row["extra"]) if row["extra"] else {}
    if row["context_length"] is not None:
        metadata[_CONTEXT_LENGTH_KEY] = row["context_length"]

    raw_capability_flags = metadata.pop(_CAPABILITY_FLAGS_EXTRA_KEY, None)
    capability_flags = (
        ModelCapabilities(**raw_capability_flags)
        if isinstance(raw_capability_flags, dict)
        else ModelCapabilities()
    )
    raw_modalities_override = metadata.pop(_MODALITIES_OVERRIDE_EXTRA_KEY, None)
    modalities_override = (
        list(raw_modalities_override) if isinstance(raw_modalities_override, list) else None
    )

    defaults_kwargs = {col: row[col] for col in _DEFAULTS_COLUMNS}
    # NOTE: `n_gpu_layers`/`context_size` can legitimately be 0, which is
    # falsy — must check `is not None`, not truthiness, or a real all-
    # zero-but-set ModelDefaults would collapse to None on read.
    has_defaults = any(v is not None for v in defaults_kwargs.values())
    defaults = ModelDefaults(**defaults_kwargs) if has_defaults else None

    return Model(
        id=row["id"],
        name=row["name"] or "",
        path=row["path"],
        size_bytes=row["size_bytes"] or 0,
        quant=row["quant"],
        license=row["license"] or "unknown",
        capabilities=json.loads(row["capabilities"]) if row["capabilities"] else [],
        hf_repo=row["source_repo"] or "",
        hf_filename=row["hf_filename"] or "",
        tags=json.loads(row["tags"]) if row["tags"] else [],
        backends=list(backends) if backends else [],
        mmproj=row["mmproj"],
        architecture=row["architecture"],
        preferred_runner=row["preferred_runner"],
        capability_flags=capability_flags,
        modalities_override=modalities_override,
        defaults=defaults,
        metadata=metadata,
    )
