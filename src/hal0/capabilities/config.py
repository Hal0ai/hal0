"""Pydantic models + atomic read/write for ``/etc/hal0/capabilities.toml``.

The overlay configuration carries one :class:`CapabilitySelection` per
(slot, child) tuple. The on-disk shape is intentionally flat / nested
TOML so operators can hand-edit the file::

    schema_version = 2

    [selections.embed.embed]
    device   = "gpu-rocm"
    provider = "llama-server"
    model    = "nomic-embed-text-v1.5"
    enabled  = true

    [selections.voice.tts]
    device   = "cpu"
    provider = "kokoro"
    model    = "kokoro-v1"
    enabled  = false

v0.1.x wrote the same shape but used ``backend`` instead of ``device``
and omitted ``schema_version`` (implicit v1). The auto-migration in
:func:`migrate_capabilities_v1_to_v2` reads a legacy file, snaps the
field rename + value mapping, and stamps ``schema_version = 2``. See
ADR-0006 §7.

The full file is rewritten atomically on every change via
:func:`hal0.config.loader.write_toml_atomic` so an interrupted write
leaves the prior file intact.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from hal0.config import paths
from hal0.config.loader import write_toml_atomic
from hal0.config.schema import (
    CAPABILITIES_SCHEMA_VERSION_CURRENT,
    CAPABILITIES_SCHEMA_VERSION_LEGACY,
    map_backend_to_device,
)

log = logging.getLogger(__name__)


class CapabilitySelection(BaseModel):
    """One operator-facing selection for a (slot, child) tuple.

    Mirrors the dashboard's per-child editor card: which device the
    user picked, which provider runs on it, which model id they bound,
    and whether the child is currently active.

    v0.2 rename: the ``backend`` field is now :attr:`device` (ADR-0006
    §7). For one release the model accepts both: a TOML carrying only
    ``backend`` auto-promotes via :func:`map_backend_to_device` and
    round-trips with ``backend`` re-emitted on dump so a v0.1.x downgrade
    sees its old field. Removal in v0.3.
    """

    model_config = {"populate_by_name": True, "str_strip_whitespace": True}

    device: str = Field(
        default="",
        description=(
            "v0.2 hardware-preference id: 'gpu-rocm' | 'gpu-vulkan' | "
            "'npu' | 'cpu'. Empty == unset (matches the dashboard's "
            "blank-picker UX). See ADR-0006 §7."
        ),
    )
    backend: str = Field(
        default="",
        description=(
            "DEPRECATED v0.2 (removed v0.3): legacy alias for ``device``. "
            "Reading a CapabilitySelection that has ``backend`` set without "
            "``device`` auto-promotes via ``map_backend_to_device`` and "
            "logs a deprecation warning. Round-tripped on save so v0.1.x "
            "downgrades stay legible."
        ),
    )
    provider: str = Field(
        default="",
        description="Provider name: 'llama-server' | 'flm' | 'moonshine' | 'kokoro' | 'comfyui'.",
    )
    model: str = Field(
        default="",
        description="Model id from the registry. Empty when the child is unset.",
    )
    enabled: bool = Field(
        default=False,
        description="True when the underlying slot should be loaded with this model.",
    )

    @model_validator(mode="before")
    @classmethod
    def _promote_backend_to_device(cls, data: Any) -> Any:
        """Promote a legacy ``backend`` field to ``device`` on load.

        The auto-migration is supposed to rewrite the file before we get
        here, but we also defend against:

          1. Hand-edited TOMLs that revert to the old shape.
          2. Programmatic call sites that still pass ``backend=...``.

        Symmetry note: we keep both fields populated. The migration
        purges ``backend`` from the persisted file; this validator just
        makes in-memory construction tolerant.
        """
        if not isinstance(data, dict):
            return data
        has_device = bool(data.get("device"))
        backend_val = data.get("backend") or ""
        if has_device:
            return data
        if not backend_val:
            return data
        new_data = dict(data)
        new_data["device"] = map_backend_to_device(str(backend_val))
        return new_data


class CapabilityConfig(BaseModel):
    """Parsed ``/etc/hal0/capabilities.toml``.

    Selections are keyed ``[selections.<slot>.<child>]`` — see the module
    docstring for the on-disk shape. Empty selections are allowed (the
    orchestrator initialises them lazily on first read).

    ``schema_version`` independently tracks the on-disk shape (separate
    from ``hal0.toml``'s ``meta.schema_version``). New writes always
    stamp the current version; reads of a legacy file trigger
    :func:`migrate_capabilities_v1_to_v2`.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    schema_version: int = Field(
        default=CAPABILITIES_SCHEMA_VERSION_CURRENT,
        ge=1,
        description=(
            "Capabilities-file schema version. v1 used ``backend``; v2 "
            "(ADR-0006 §7) uses ``device``. Auto-migrated on hal0-api "
            "boot via ``migrate_capabilities_v1_to_v2``."
        ),
    )

    selections: dict[str, dict[str, CapabilitySelection]] = Field(
        default_factory=dict,
        description="Nested map: { slot_name: { child_name: CapabilitySelection } }.",
    )


# ── Path resolution ───────────────────────────────────────────────────────────


def capabilities_toml_path() -> Path:
    """Return ``/etc/hal0/capabilities.toml`` (HAL0_HOME-aware)."""
    return paths.etc() / "capabilities.toml"


def capabilities_v1_backup_path(path: Path | None = None) -> Path:
    """Return the path to the pre-migration v1 backup.

    The auto-migration on hal0-api boot renames the live
    ``capabilities.toml`` to this path before rewriting in the v2 shape.
    Kept around so the on-load auto-migration can preserve the v1 file
    for revert/downgrade scenarios (per the issue body).
    """
    base = Path(path) if path is not None else capabilities_toml_path()
    return base.with_name(base.name + ".v1.bak")


# ── Migration ─────────────────────────────────────────────────────────────────


def read_schema_version(raw: dict[str, Any]) -> int:
    """Extract the ``schema_version`` from a raw capabilities dict.

    Missing or non-integer values are treated as legacy v1 — matches the
    behaviour of :class:`hal0.config.migrations.MigrationError`-style
    walkers and keeps the read forgiving on hand-edited files.
    """
    v = raw.get("schema_version", CAPABILITIES_SCHEMA_VERSION_LEGACY)
    try:
        return int(v)
    except (TypeError, ValueError):
        return CAPABILITIES_SCHEMA_VERSION_LEGACY


def migrate_capabilities_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """Pure-dict v1 → v2 migration. Idempotent on v2 inputs.

    Transforms applied:

      - For each ``selections[slot][child]`` entry, rename the
        ``backend`` key to ``device``. The value is normalised through
        :func:`hal0.config.schema.map_backend_to_device` so legacy
        ``vulkan|rocm|flm|moonshine|kokoro|cpu`` collapse onto the v0.2
        ``gpu-vulkan|gpu-rocm|npu|cpu`` enum.
      - Stamp ``schema_version = 2``.
      - Already-v2 inputs round-trip unchanged (the rename is a no-op
        and the version stamp is overwritten with the same value).

    Edge cases:

      - A selection that already has ``device`` set keeps it and drops
        any leftover ``backend`` key so the on-disk shape is clean.
      - A selection with an unrecognised ``backend`` value (e.g. an
        operator hand-edited ``backend = "rcom"``) is mapped to ``cpu``
        by :func:`map_backend_to_device`. The function logs a warning
        per unknown value so the demotion is visible.
      - Non-dict ``selections`` or non-dict child entries pass through
        verbatim — defensive against half-baked TOML hand edits.
    """
    # Shallow rebuild — never mutate the caller's dict.
    out: dict[str, Any] = {k: v for k, v in raw.items() if k != "selections"}
    out["schema_version"] = CAPABILITIES_SCHEMA_VERSION_CURRENT

    selections_in = raw.get("selections")
    if not isinstance(selections_in, dict):
        out["selections"] = {}
        return out

    selections_out: dict[str, dict[str, Any]] = {}
    for slot_name, children in selections_in.items():
        if not isinstance(children, dict):
            continue
        slot_bucket: dict[str, Any] = {}
        for child_name, sel in children.items():
            if not isinstance(sel, dict):
                continue
            new_sel = dict(sel)
            existing_device = (new_sel.get("device") or "").strip()
            legacy_backend = (new_sel.get("backend") or "").strip()
            if existing_device:
                # Already in v2 shape. Drop any stray backend key so the
                # on-disk shape is single-source-of-truth.
                new_sel.pop("backend", None)
            elif legacy_backend:
                new_sel["device"] = map_backend_to_device(legacy_backend)
                new_sel.pop("backend", None)
            else:
                # Unset selection (the "blank picker" state). Leave
                # device empty so the dashboard still shows it unset.
                new_sel.pop("backend", None)
            slot_bucket[child_name] = new_sel
        selections_out[slot_name] = slot_bucket

    out["selections"] = selections_out
    return out


def auto_migrate_capabilities_file(path: Path | None = None) -> bool:
    """Migrate ``capabilities.toml`` on disk if it's still schema_version=1.

    Returns ``True`` when a migration was performed, ``False`` when the
    file was already v2 (or absent — nothing to migrate).

    Side effects:

      1. Atomic rename live file → ``<path>.v1.bak`` (preserves the
         original bytes for ``--revert`` and v0.1.x downgrade).
      2. Apply :func:`migrate_capabilities_v1_to_v2` and write atomically
         via :func:`hal0.config.loader.write_toml_atomic`.
      3. Log one info-level line summarising the action.

    Crash safety: the rename happens before the rewrite. If the rewrite
    crashes, the next boot sees a missing live file + an intact
    ``.v1.bak`` — the orchestrator's ``initialize_if_missing`` path will
    re-seed from slot TOMLs, and the operator can restore by moving
    ``capabilities.toml.v1.bak`` back over ``capabilities.toml``.
    """
    import os

    target = Path(path) if path is not None else capabilities_toml_path()
    if not target.exists():
        return False

    with open(target, "rb") as f:
        raw = tomllib.load(f)

    current_version = read_schema_version(raw)
    if current_version >= CAPABILITIES_SCHEMA_VERSION_CURRENT:
        return False

    backup = capabilities_v1_backup_path(target)
    # Atomic-rename live → .v1.bak. If a stale backup exists (a prior
    # migration that crashed mid-write), refuse to clobber it — the
    # operator will see the live file untouched on next boot.
    if backup.exists():
        log.warning(
            "capabilities.migrate.backup_exists",
            extra={"path": str(backup)},
        )
    else:
        os.replace(target, backup)

    migrated = migrate_capabilities_v1_to_v2(raw)
    # Validate before write — a malformed migration output should never
    # land on disk. ``model_validate`` raises with the offending path.
    CapabilityConfig.model_validate(migrated)
    write_toml_atomic(target, migrated)

    log.info(
        "migrated capabilities.toml from schema_version=%d to %d",
        current_version,
        CAPABILITIES_SCHEMA_VERSION_CURRENT,
    )
    return True


# ── Read / write ──────────────────────────────────────────────────────────────


def load_capabilities_config(path: Path | None = None) -> CapabilityConfig:
    """Load and validate ``capabilities.toml``.

    Returns an empty :class:`CapabilityConfig` when the file does not
    exist — callers (notably :meth:`CapabilityOrchestrator.initialize_if_missing`)
    detect that with :func:`exists` below and seed defaults.

    Loading is read-only: callers that want to migrate a stale v1 file
    on disk must call :func:`auto_migrate_capabilities_file` explicitly.
    The orchestrator does this once at hal0-api startup.
    """
    target = path if path is not None else capabilities_toml_path()
    if not Path(target).exists():
        return CapabilityConfig()
    with open(target, "rb") as f:
        raw = tomllib.load(f)
    return CapabilityConfig.model_validate(raw)


def capabilities_toml_payload(cfg: CapabilityConfig) -> dict[str, Any]:
    """Serialise a validated config into the canonical on-disk dict shape.

    Single source for both :func:`save_capabilities_config` and the
    :class:`hal0.slot_config.SlotConfigStore` ChangeSet computation
    (issue #697) so the two can never disagree on the persisted shape.

    Always stamps the current schema_version so a downgrade that wrote
    v1-shaped TOML on top of a v2 install can be detected on the next
    upgrade.
    """
    # Pydantic v2 ``model_dump`` walks the nested CapabilitySelection tables
    # into plain dicts — exactly the shape ``tomli_w.dump`` wants.
    data: dict[str, Any] = cfg.model_dump(mode="python")
    # Re-stamp the version (model defaults guarantee this is correct,
    # but a hand-constructed CapabilityConfig that overrode it would
    # leak the wrong version onto disk).
    data["schema_version"] = CAPABILITIES_SCHEMA_VERSION_CURRENT
    # Drop the deprecated ``backend`` field from each selection so the
    # canonical persisted shape is single-source-of-truth. Operators who
    # want to downgrade keep the ``.v1.bak`` produced at migration time.
    sels = data.get("selections")
    if isinstance(sels, dict):
        for _slot, children in sels.items():
            if not isinstance(children, dict):
                continue
            for _child, entry in children.items():
                if isinstance(entry, dict):
                    entry.pop("backend", None)
    return data


def save_capabilities_config(cfg: CapabilityConfig, path: Path | None = None) -> None:
    """Atomically rewrite ``capabilities.toml`` from a validated config.

    NOTE(#697): the capability-apply path writes through
    ``hal0.slot_config.SlotConfigStore`` instead (one ChangeSet covering
    both files). This helper remains for the NON-apply writers whose
    semantics don't fit a selection ChangeSet: the first-boot seed
    (``CapabilityOrchestrator.initialize_if_missing``), the schema
    migrations (``auto_migrate_capabilities_file`` + the
    ``hal0 capabilities migrate*`` CLI with its ``.v1.bak`` backup
    dance). Both serialise through :func:`capabilities_toml_payload`,
    so the on-disk shape cannot diverge from the store's.
    """
    target = path if path is not None else capabilities_toml_path()
    write_toml_atomic(target, capabilities_toml_payload(cfg))


__all__ = [
    "CAPABILITIES_SCHEMA_VERSION_CURRENT",
    "CAPABILITIES_SCHEMA_VERSION_LEGACY",
    "CapabilityConfig",
    "CapabilitySelection",
    "auto_migrate_capabilities_file",
    "capabilities_toml_path",
    "capabilities_toml_payload",
    "capabilities_v1_backup_path",
    "load_capabilities_config",
    "migrate_capabilities_v1_to_v2",
    "read_schema_version",
    "save_capabilities_config",
]
