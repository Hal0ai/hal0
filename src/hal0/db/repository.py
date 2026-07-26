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

#: spec-hw-slot-ownership §1: ``ModelDefaults.enable_thinking`` / ``.vision``
#: are tri-state bools with no reserved column either (same "no schema
#: migration" fold as ``capability_flags``/``modalities_override`` above) —
#: only written when non-None, so a pre-existing row round-trips with both
#: reading back as None (no opinion), same as any other unset tri-state.
_ENABLE_THINKING_EXTRA_KEY = "_enable_thinking"
_VISION_EXTRA_KEY = "_vision"

#: Per-type default marker (:attr:`hal0.registry.model.Model.default`) also
#: rides the ``extra`` blob under a reserved key rather than getting its own
#: column — the single-holder invariant is enforced in Python (the
#: models_service chokepoint), so an indexed column would buy nothing. Only
#: written when True; a missing key reads back as False, so pre-existing rows
#: round-trip as non-default with no migration.
_DEFAULT_EXTRA_KEY = "_default"

#: Columns of the `model` table, in the order INSERT/UPDATE statements bind
#: them. Kept as a tuple (not re-derived from a dict each call) so
#: SqliteModelRegistry can build parameterized SQL generically.
MODEL_COLUMNS: tuple[str, ...] = (
    "id",
    "source_repo",
    "revision",
    "path",
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
#: iff at least one of these is non-NULL. ``mtp``/``jinja`` (§7.1a / ML-5)
#: are tri-state (NULL/0/1 -> None/False/True) — pydantic's lax bool
#: validator coerces the raw sqlite int back to bool on reconstruction.
# NOTE (spec-hw-slot-ownership §6): ``n_gpu_layers`` is NO LONGER a
# ModelDefaults field (NGL is slot-owned now). Its SQL column is KEPT (nulled by
# current code; the deploy-window fold reads it, and the physical DROP is
# deferred post-1.0 to avoid a same-release fold-vs-drop hazard) — it is neither
# written from the model nor folded back into ModelDefaults on read, hence its
# absence here.
_DEFAULTS_COLUMNS: tuple[str, ...] = (
    "profile",
    "extra_args",
    "chat_template",
    "context_size",
    "rope_freq_base",
    "mtp",
    "jinja",
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

    §7.1 ``revision`` has no ``Model`` field yet — always writes NULL, a
    ready-made landing spot for a future lane. ``architecture`` and
    ``mtp``/``jinja`` (folded from ``ModelDefaults``) ARE populated here —
    §7.1d lands ``architecture``, ML-5 lands ``mtp``/``jinja`` (see
    ``ModelDefaults.mtp``/``.jinja`` — tri-state, sqlite stores them as
    NULL/0/1 in the INTEGER columns; ``defaults.mtp``/``.jinja`` pass
    straight through since sqlite3 adapts a Python ``bool`` to 0/1
    natively, same as any other int).
    ``preferred_runner`` / ``n_gpu_layers`` are gone from the model (hardware is
    slot-owned now); their SQL columns are KEPT-but-unbound (nulled; physical
    DROP deferred post-1.0 — the deploy-window fold reads them first).
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
    # spec-hw-slot-ownership §1: enable_thinking/vision have no reserved
    # column (see _ENABLE_THINKING_EXTRA_KEY docstring above) — only stamped
    # when the tri-state is explicitly set.
    if defaults.enable_thinking is not None:
        metadata[_ENABLE_THINKING_EXTRA_KEY] = defaults.enable_thinking
    if defaults.vision is not None:
        metadata[_VISION_EXTRA_KEY] = defaults.vision
    # Per-type default marker — only stamped when set (see _DEFAULT_EXTRA_KEY).
    if model.default:
        metadata[_DEFAULT_EXTRA_KEY] = True
    extra_json = json.dumps(metadata, separators=(",", ":")) if metadata else None

    ts = updated_at or now_iso()
    return {
        "id": model.id,
        "source_repo": model.hf_repo or None,
        "revision": None,
        "path": model.path,
        # spec-hw-slot-ownership §6: the runner is slot-owned (SlotConfig.binary)
        # now — the model carries no preferred_runner, so this KEPT-but-unbound
        # SQL column is no longer written (stays NULL; drop deferred post-1.0).
        "mmproj": model.mmproj,
        "architecture": model.architecture,
        "context_length": context_length,
        "mtp": defaults.mtp,
        "jinja": defaults.jinja,
        "name": model.name,
        "size_bytes": model.size_bytes,
        "quant": model.quant,
        "license": model.license,
        "hf_filename": model.hf_filename,
        "profile": defaults.profile,
        "extra_args": defaults.extra_args,
        # NGL is slot-owned now (spec-hw-slot-ownership §6); this KEPT-but-unbound
        # SQL column is no longer written (stays NULL). The deploy-window fold
        # reads any prior value onto each referencing slot; drop deferred post-1.0.
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


def set_blob_path(conn: sqlite3.Connection, sha256: str, blob_path: str) -> None:
    """Re-point ``store_blob.blob_path`` at ``blob_path`` (the canonical
    on-disk referent for ``sha256``).

    The delete path uses this to keep ``blob_path`` pointing at a *live*
    hardlink after the original canonical referent is unlinked but the blob
    is still referenced (refcount > 0) — see
    :func:`hal0.registry.gc.delete_model_files`. Without it, ``blob_path``
    would dangle at the deleted path, breaking hardlink-dedup for a later
    same-sha pull (:func:`hal0.registry.pull._maybe_hardlink_from_blob`
    probes ``blob_path.is_file()``).
    """
    conn.execute("UPDATE store_blob SET blob_path = ? WHERE sha256 = ?", (str(blob_path), sha256))


def blob_referents(
    conn: sqlite3.Connection, sha256: str, *, exclude_model_id: str | None = None
) -> list[dict[str, Any]]:
    """Return every ``model_file`` row referencing ``sha256``.

    ``exclude_model_id`` drops the model being deleted so the caller can find
    a *surviving* referent to re-point a shared blob's canonical
    ``blob_path`` at (:func:`hal0.registry.gc.delete_model_files`). Rows are
    returned in a stable order (``model_id``, ``rel``) so re-point choice is
    deterministic across runs.
    """
    if exclude_model_id is not None:
        rows = conn.execute(
            "SELECT * FROM model_file WHERE sha256 = ? AND model_id != ? ORDER BY model_id, rel",
            (sha256, exclude_model_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM model_file WHERE sha256 = ? ORDER BY model_id, rel",
            (sha256,),
        ).fetchall()
    return [dict(row) for row in rows]


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
    default = bool(metadata.pop(_DEFAULT_EXTRA_KEY, False))

    raw_enable_thinking = metadata.pop(_ENABLE_THINKING_EXTRA_KEY, None)
    raw_vision = metadata.pop(_VISION_EXTRA_KEY, None)

    defaults_kwargs = {col: row[col] for col in _DEFAULTS_COLUMNS}
    defaults_kwargs["enable_thinking"] = (
        raw_enable_thinking if isinstance(raw_enable_thinking, bool) else None
    )
    defaults_kwargs["vision"] = raw_vision if isinstance(raw_vision, bool) else None
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
        # preferred_runner / n_gpu_layers are NOT read into Model — the fields are
        # gone (hardware is slot-owned). The columns are KEPT-but-unbound (nulled;
        # drop deferred post-1.0); the deploy-window fold reads them directly.
        capability_flags=capability_flags,
        modalities_override=modalities_override,
        defaults=defaults,
        default=default,
        metadata=metadata,
    )
