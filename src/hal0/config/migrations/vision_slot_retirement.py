"""One-shot boot sweep: retire the pre-1.0 ``vision`` scaffold slot.

The dedicated ``vision`` slot lane is gone — vision is a MODEL property
(mmproj sidecar, the registry's ``capabilities`` list and ``defaults.vision``
tri-state, surfaced per-slot via ``LoadedSlot.modalities``), served by any
llm slot whose bound model carries it. The capability orchestrator no longer
maps a ``vision.vision`` child to a slot, and ``capabilities.toml`` drops a
stray ``[selections.vision]`` table on load.

This sweep removes the slot older installs carry — but ONLY the untouched
scaffold: a ``vision`` slot with no model bound. A vision slot the operator
pointed at a model is left alone (it keeps working as a plain llm slot under
that name; the name is no longer reserved either way).

Retirement goes through ``SlotManager.delete`` so the WHOLE lifecycle is
cleaned: on an id-keyed box a prior ``fold_identity()`` pass gave the
scaffold a persistent identity row, a port claim, and state under
``slot_data_dir`` — unlinking just the name-keyed TOML would leak all three.

Idempotent and best-effort: after the first sweep there is nothing left to
find, and a failure must not block startup.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)

_RETIRED_SLOT = "vision"


async def retire_vision_scaffold(slot_manager: Any) -> bool:
    """Delete an untouched ``vision`` scaffold slot. Returns True if removed."""
    from hal0.slots.state import SlotNotFound

    try:
        cfg = await slot_manager.get_config(_RETIRED_SLOT)
    except SlotNotFound:
        return False
    model_default = str(((cfg.get("model") or {}).get("default")) or "").strip()
    if model_default:
        log.info(
            "migrate.vision_slot_kept",
            slot=_RETIRED_SLOT,
            reason="model bound — operator-owned, retiring only the scaffold",
            model=model_default,
        )
        return False
    # force=True: the scaffold can carry a pin on some boxes; the retirement
    # decision is release-level, not per-box. delete() releases the port
    # claim, drops the identity row, and removes both keyed artefact forms.
    await slot_manager.delete(_RETIRED_SLOT, force=True)
    log.info("migrate.vision_slot_retired", slot=_RETIRED_SLOT)
    return True


__all__ = ["retire_vision_scaffold"]
