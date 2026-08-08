"""One-shot boot sweep: fold behaviour tags into typed fields, then strip them.

The curated "type" tag chips (``mtp``/``moe``/``tool-calling``/``reasoning``/
``coder``/``vision``) are retired from the model editors: behaviour is owned
by typed fields and nothing routes on ``Model.tags``. Registry rows stamped by
older releases still carry the tags, so this sweep folds the two that ever
drove behaviour into their typed homes and strips all six:

* ``mtp`` → ``defaults.mtp = True`` (absent-only — an explicit operator
  False stays False; ``model_is_mtp_eligible`` prefers the typed field).
* ``vision`` → folded into the ``capabilities`` list ONLY when the row
  carries an mmproj sidecar — a vision capability with no projector is a
  lie the modality derivation would immediately contradict; a projector-
  less ``vision`` tag is dropped (the tag never made the model multimodal).
* ``moe`` → stripped only when ``Model.architecture`` is set (the typed
  MoE signal); with no architecture the tag is the row's only MoE marker,
  so it stays until a scan/detect fills the typed field.
* ``coder`` → KEPT — the bench roster selector matches it as a descriptive
  label (tags are inert for routing either way).
* ``tool-calling``/``reasoning`` → dropped; ``capability_flags.
  tool_calling`` and ``defaults.enable_thinking`` own those behaviours.

Curated CATALOGUE entries (registry/curated.py) keep their descriptive tags —
this sweep touches only registry rows. Idempotent and best-effort: a store
hiccup on one row must not block startup or the other rows.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: Tags stripped unconditionally once folded, lowercase. ``moe`` and
#: ``vision`` strip conditionally (see module docstring); ``coder`` is kept
#: as a descriptive label the bench roster selector matches.
RETIRED_TYPE_TAGS: frozenset[str] = frozenset({"mtp", "tool-calling", "reasoning"})

#: Conditionally-stripped tags — see the per-tag rules in the sweep body.
CONDITIONAL_TYPE_TAGS: frozenset[str] = frozenset({"moe", "vision"})


def retire_model_type_tags(registry: Any) -> list[str]:
    """Fold + strip retired type tags on every registry row. Returns changed ids."""
    from hal0.registry.model import ModelDefaults

    changed: list[str] = []
    try:
        rows = registry.list()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("registry.tag_retirement_list_failed", error=str(exc))
        return changed
    for row in rows:
        try:
            tags = list(getattr(row, "tags", None) or [])
            lowered = {str(t).lower() for t in tags}
            hit = lowered & (RETIRED_TYPE_TAGS | CONDITIONAL_TYPE_TAGS)
            if not hit:
                continue
            strip = set(RETIRED_TYPE_TAGS)
            # ``moe`` strips only when the typed MoE signal exists.
            if "moe" in hit and str(getattr(row, "architecture", "") or "").strip():
                strip.add("moe")
            # ``vision`` folds into capabilities only alongside an mmproj
            # sidecar; either way the tag itself strips (it drove nothing).
            has_mmproj = bool(str(getattr(row, "mmproj", "") or "").strip())
            if "vision" in hit:
                strip.add("vision")
            new_tags = [t for t in tags if str(t).lower() not in strip]
            if new_tags == tags and not (hit & strip):
                continue  # nothing strippable this pass (e.g. lone un-typed moe)
            updates: dict[str, Any] = {"tags": new_tags}
            defaults = getattr(row, "defaults", None)
            if "mtp" in hit and (defaults is None or defaults.mtp is None):
                updates["defaults"] = (
                    defaults.model_copy(update={"mtp": True})
                    if defaults is not None
                    else ModelDefaults(mtp=True)
                )
            if "vision" in hit and has_mmproj:
                caps = list(getattr(row, "capabilities", None) or [])
                if "vision" not in {str(c).lower() for c in caps}:
                    updates["capabilities"] = [*caps, "vision"]
            registry.update(row.id, updates)
            changed.append(row.id)
            log.info("registry.type_tags_retired", model=row.id, folded=sorted(hit & strip))
        except Exception as exc:  # pragma: no cover — best-effort per row
            log.warning(
                "registry.tag_retirement_row_failed",
                model=getattr(row, "id", "?"),
                error=str(exc),
            )
    return changed


__all__ = ["CONDITIONAL_TYPE_TAGS", "RETIRED_TYPE_TAGS", "retire_model_type_tags"]
