"""One-shot boot sweep: retire the legacy ``vision`` scaffold slot.

The dedicated ``vision`` slot lane is gone — vision is a MODEL property
(mmproj sidecar, the registry's ``capabilities`` list and ``defaults.vision``
tri-state, surfaced per-slot via ``LoadedSlot.modalities``), served by any
llm slot whose bound model carries it. The capability orchestrator no longer
maps a ``vision.vision`` child to a slot, and ``capabilities.toml`` drops a
stray ``[selections.vision]`` table on load.

This sweep removes the on-disk scaffold slot older installs carry — but ONLY
the untouched scaffold: a ``vision`` slot with no model bound. A vision slot
the operator pointed at a model is left alone (it keeps working as a plain
llm slot under that name; the name is no longer reserved either way).

Idempotent and best-effort: after the first sweep there is nothing left to
find, and a config-dir problem must not block startup.
"""

from __future__ import annotations

import contextlib
import tomllib
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_RETIRED_SLOT = "vision"


def migrate_vision_slot(slots_dir: Path) -> bool:
    """Remove an untouched ``vision`` scaffold slot TOML. Returns True if removed.

    Only the name-keyed file is considered: an id-keyed slot went through the
    identity migration, which means it was live enough to matter — that is
    operator territory, not scaffold debris.
    """
    cfg_path = slots_dir / f"{_RETIRED_SLOT}.toml"
    if not cfg_path.is_file():
        return False
    try:
        with open(cfg_path, "rb") as f:
            cfg = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
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
    with contextlib.suppress(OSError):
        cfg_path.unlink()
        state = slots_dir / _RETIRED_SLOT / "state.json"
        with contextlib.suppress(OSError):
            state.unlink()
        log.info("migrate.vision_slot_retired", slot=_RETIRED_SLOT, path=str(cfg_path))
        return True
    return False


__all__ = ["migrate_vision_slot"]
