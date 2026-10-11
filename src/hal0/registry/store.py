"""Model registry public surface: ``ModelRegistry``, its typed errors, and
the field-level merge every ``update()`` applies.

The registry is SQLite-backed (:class:`hal0.registry.sqlite_store.SqliteModelRegistry`,
in the shared ``hal0.db`` file). This module owns the names callers import —
``ModelRegistry``, ``RegistryError``, ``ModelNotFound``, ``ModelAlreadyExists``
and :func:`merge_update` — so call sites never depend on the storage module.

The TOML-backed store that preceded SQLite is gone. A ``registry.toml`` left
on an upgraded box is converted once and renamed by
:func:`hal0.registry.toml_conversion.convert_registry_toml`; nothing reads
that file afterwards.

The typed errors subclass ``Hal0Error`` so the API middleware reshapes them
into the structured error envelope automatically.
"""

from __future__ import annotations

from typing import Any

from hal0.errors import Hal0Error
from hal0.registry.model import Model

# #1413: the nested `Model` tables `merge_update` merges ONE level deep instead
# of replacing wholesale. Deliberately NOT `metadata` — that one is a freeform
# bag with no schema, so "absent = keep" would leave callers no way to drop a
# key; it stays replace-only.
_MERGED_SUBTABLES = frozenset({"defaults", "capability_flags"})


# ── Typed errors ──────────────────────────────────────────────────────────────


class RegistryError(Hal0Error):
    """Base error for registry operations."""

    code = "model.registry_error"
    status = 500


class ModelNotFound(RegistryError):
    """The requested model id is not in the registry."""

    code = "model.not_found"
    status = 404


class ModelAlreadyExists(RegistryError):
    """add() called but the model id is already present."""

    code = "model.already_exists"
    status = 409


def merge_update(existing: Model, model_id: str, updates: dict[str, Any]) -> Model:
    """Field-level merge shared by every ``update()`` implementation.

    ``updates`` overwrites matching top-level fields on ``existing``; the
    ``id`` key in ``updates`` is always ignored (``model_id`` wins) — the
    id is immutable through ``update()`` by design (use remove + add to
    rename). Used by
    :meth:`hal0.registry.sqlite_store.SqliteModelRegistry.update`.

    The two NESTED tables in :data:`_MERGED_SUBTABLES` — ``defaults`` and
    ``capability_flags`` — merge ONE level deep (#1413). The merge used to
    be flat everywhere, which made a body that so much as named
    ``defaults`` replace the whole ``ModelDefaults`` table: every sibling
    key the client didn't resend (``context_size``, ``chat_template``,
    ``mtp``/``jinja``/``enable_thinking``/``vision`` …) silently reset to
    ``None`` behind an HTTP 200, contradicting the route's own documented
    "body accepts any subset" contract. The sub-key semantics are the
    tri-state ones the fields already carry:

    * absent sub-key    → keep the stored value
    * ``null`` sub-key  → clear that ONE value (the DELETE verb)
    * ``{"defaults": null}`` → drop the whole table (unchanged)

    Anything that isn't a dict (or a table not in the set — ``metadata``
    stays freeform and replace-only) keeps the flat overwrite.

    Raises:
        RegistryError: If the merged fields fail ``Model`` validation.
    """
    existing_dict = existing.model_dump(mode="python")
    merged = dict(existing_dict)
    for key, value in updates.items():
        if key == "id":
            continue
        if key in _MERGED_SUBTABLES and isinstance(value, dict):
            stored = existing_dict.get(key)
            merged[key] = {**(stored if isinstance(stored, dict) else {}), **value}
            continue
        merged[key] = value
    merged["id"] = model_id
    try:
        return Model.model_validate(merged)
    except Exception as exc:
        raise RegistryError(
            f"update for {model_id!r} produced an invalid Model: {exc}",
            details={"model_id": model_id, "reason": str(exc)},
        ) from exc


# Imported at the BOTTOM of the module, not the top: `sqlite_store` imports
# the typed errors and `merge_update` FROM this module, so importing it any
# earlier would be a circular import. By deferring to here, everything
# `sqlite_store` needs already exists as a module attribute by the time it
# runs.
from hal0.registry.sqlite_store import SqliteModelRegistry  # noqa: E402

#: Public entry point. Every call site constructs `ModelRegistry()` /
#: `ModelRegistry(registry_dir=...)` and only touches the §0.2 interface
#: (list/get/has/add/remove/update/route_for/reload/on_change).
ModelRegistry = SqliteModelRegistry

__all__ = [
    "ModelAlreadyExists",
    "ModelNotFound",
    "ModelRegistry",
    "RegistryError",
    "SqliteModelRegistry",
    "merge_update",
]
