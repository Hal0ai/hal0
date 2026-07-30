"""One-shot migration: sweep ``enabled`` off slot TOMLs (#1369).

``SlotConfig.enabled`` is gone; a non-empty ``[model].default`` IS the
activation signal (see :mod:`hal0.slots.activation`). Because ``SlotConfig``
is ``extra="allow"``, a leftover ``enabled`` key does not break anything — it
just round-trips as debris. One shape, though, is NOT harmless:

    enabled = false
    [model]
    default = "qwen3-4b"

Under the old rules that slot was off. Under the new rules the bound model
makes it **on** — so an upgrade would silently activate a slot the operator
had deliberately switched off. This migration resolves that by clearing the
model, which is exactly how "off" is expressed now.

Every other shape only needs the key dropped:

  - ``enabled = true`` → the model already said "on".
  - ``enabled = false`` with no model → both signals already said "off"
    (this is every shipped seed except ``brain``).

The NPU trio shadows (``device=npu`` with ``type`` transcription/embedding)
are the one carve-out: their ``[model].default`` is a structural placeholder
naming FLM's bundled whisper / embed-gemma, not an operator pick, and their
real dispatch gate is the ANCHOR's ``[npu]`` table
(:data:`hal0.slots.activation.NPU_MODALITY_KEY`). Clearing it would strand the
record with nothing to re-derive the id from, so shadows only lose the key.

Contract:

* **Pure core.** :func:`migrate_slot_toml` is a filesystem-free transform
  returning ``None`` when there is nothing to do, so the interesting rules are
  table-testable. :func:`migrate_slot_dir` is the thin walk around it.
* **Idempotent.** ``"enabled" in raw`` IS the check — after one pass the key is
  gone, so a re-run reports nothing and rewrites no bytes.
* **Best-effort per file.** An unparseable TOML is logged and skipped rather
  than aborting the sweep; this runs during API boot
  (``_boot_slot_reconcile``), where one corrupt file must not block startup.
  ``hal0 slot migrate-enabled-removal`` is the same sweep on demand.
* **One-way.** Rolling back to pre-#1369 code sees the missing key as
  ``enabled = True`` (the old default), so slots cleared by this migration
  come back model-less rather than re-enabled. Restore from a config backup if
  you need the old state.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

from hal0.slot_config import write_slot_toml

log = logging.getLogger("hal0.config.migrations.slot_enabled_removal")

__all__ = ["migrate_slot_dir", "migrate_slot_toml"]

#: Slot types that are NPU trio shadows when ``device == "npu"`` — records for
#: the anchor's one FLM process, whose model id is a structural placeholder.
#: Mirrors ``hal0.slots.npu.trio._TRIO_SHADOW_SPEC``'s types; ``llm`` is
#: deliberately absent (that is the anchor, a real operator pick).
_TRIO_SHADOW_TYPES: frozenset[str] = frozenset({"transcription", "embedding"})


def _is_trio_shadow(raw: dict[str, Any]) -> bool:
    return raw.get("device") == "npu" and raw.get("type") in _TRIO_SHADOW_TYPES


def migrate_slot_toml(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Return the migrated slot dict, or ``None`` when nothing needs writing.

    Never mutates ``raw`` — the caller may be holding it as a before-snapshot.
    """
    if "enabled" not in raw:
        return None

    out = {k: v for k, v in raw.items() if k != "enabled"}
    # Only an explicit False needs the model cleared; a truthy (or junk
    # non-False) value already meant "on", which the bound model now says.
    if raw.get("enabled") is False and not _is_trio_shadow(raw):
        model = raw.get("model")
        # Preserve sibling [model] keys (context_size, labels, …) — this is a
        # deactivation, not a reset of the slot's tuning.
        out["model"] = {**model, "default": ""} if isinstance(model, dict) else {"default": ""}
    return out


def migrate_slot_dir(slots_dir: Path | str) -> list[str]:
    """Sweep every ``*.toml`` under ``slots_dir``; return the slots rewritten.

    Files that don't carry ``enabled`` are left byte-identical (no rewrite), so
    a second run is a genuine no-op rather than a reformat.
    """
    base = Path(slots_dir)
    if not base.is_dir():
        return []
    migrated: list[str] = []
    for path in sorted(base.glob("*.toml")):
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning(
                "slot.enabled_removal_unreadable",
                extra={"path": str(path), "error": str(exc)},
            )
            continue
        after = migrate_slot_toml(raw)
        if after is None:
            continue
        try:
            write_slot_toml(path, after)
        except Exception as exc:
            log.warning(
                "slot.enabled_removal_write_failed",
                extra={"path": str(path), "error": str(exc)},
            )
            continue
        migrated.append(path.stem)
        log.info(
            "slot.enabled_removal_migrated",
            extra={
                "slot": path.stem,
                "model_cleared": raw.get("enabled") is False and not _is_trio_shadow(raw),
            },
        )
    return migrated
