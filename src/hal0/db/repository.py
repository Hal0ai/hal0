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

    §7.1 columns with no ``Model`` field yet (``revision``,
    ``preferred_runner``, ``mtp``, ``jinja``) always write NULL in this
    pilot — they exist purely as a ready-made landing spot for the ML-4/
    ML-5 lanes that add those fields to ``Model`` itself. ``architecture``
    IS populated here — §7.1d is the lane that lands it (see
    ``Model.architecture``). ``capability_flags``/``modalities_override``
    have no reserved column yet, so they fold into the ``extra`` JSON blob
    under the reserved keys above instead of a schema migration.
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
        "preferred_runner": None,
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
        capability_flags=capability_flags,
        modalities_override=modalities_override,
        defaults=defaults,
        metadata=metadata,
    )
