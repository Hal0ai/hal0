"""One-shot boot sweep: fold behaviour tags into typed fields, then strip them.

The curated "type" tag chips (``mtp``/``moe``/``tool-calling``/``reasoning``/
``coder``/``vision``) are retired from the model editors: behaviour is owned
by typed fields and nothing routes on ``Model.tags``. Registry rows stamped by
older releases still carry the tags, so this sweep folds the two that ever
drove behaviour into their typed homes and strips all six:

* ``mtp`` → ``defaults.mtp = True`` (absent-only — an explicit operator
  False stays False; ``model_is_mtp_eligible`` prefers the typed field).
* ``vision`` → ensure ``"vision"`` is in the ``capabilities`` list (the
  fact-derived surface model pickers and ``LoadedSlot.modalities`` read).
* ``moe``/``tool-calling``/``reasoning``/``coder`` → dropped; their typed
  owners (``Model.architecture``, ``capability_flags.tool_calling``,
  ``defaults.enable_thinking``, the capabilities list) are populated by
  detection/curation, not by tags.

Curated CATALOGUE entries (registry/curated.py) keep their descriptive tags —
this sweep touches only registry rows. Idempotent and best-effort: a store
hiccup on one row must not block startup or the other rows.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: The retired behaviour tags, lowercase.
RETIRED_TYPE_TAGS: frozenset[str] = frozenset(
    {"mtp", "moe", "tool-calling", "reasoning", "coder", "vision"}
)


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
            hit = lowered & RETIRED_TYPE_TAGS
            if not hit:
                continue
            updates: dict[str, Any] = {
                "tags": [t for t in tags if str(t).lower() not in RETIRED_TYPE_TAGS]
            }
            defaults = getattr(row, "defaults", None)
            if "mtp" in hit and (defaults is None or defaults.mtp is None):
                updates["defaults"] = (
                    defaults.model_copy(update={"mtp": True})
                    if defaults is not None
                    else ModelDefaults(mtp=True)
                )
            if "vision" in hit:
                caps = list(getattr(row, "capabilities", None) or [])
                if "vision" not in {str(c).lower() for c in caps}:
                    updates["capabilities"] = [*caps, "vision"]
            registry.update(row.id, updates)
            changed.append(row.id)
            log.info("registry.type_tags_retired", model=row.id, folded=sorted(hit))
        except Exception as exc:  # pragma: no cover — best-effort per row
            log.warning(
                "registry.tag_retirement_row_failed",
                model=getattr(row, "id", "?"),
                error=str(exc),
            )
    return changed


__all__ = ["RETIRED_TYPE_TAGS", "retire_model_type_tags"]
