"""Select the first renderable image model that lands as the ``img`` capability's model.

A fresh install has no image selection, so nothing wires ``/v1/images`` or Open WebUI to
the model the operator just installed until they open the dashboard and pick it. The first
install that can serve the API makes that pick once; after that the selection is the
operator's and is never replaced. Shared by the install route and the setup path
(:mod:`hal0.install.image_models`), which is why it lives here and not beside either.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from hal0.capabilities import catalog as capability_catalog
from hal0.comfyui.catalog import ImageModel
from hal0.errors import Hal0Error

if TYPE_CHECKING:
    from hal0.capabilities.orchestrator import CapabilityOrchestrator

log = logging.getLogger(__name__)

# The device the shipped ``img.toml`` seeds, and the one the capability apply accepts for the
# ComfyUI row. The pinned ComfyUI image is ROCm-only, so this is a contract and not a
# preference (#2067). ``comfyui_backends()[0]`` is the picker's generic GPU row
# (``gpu-vulkan``); applying that would rewrite the seeded slot to a Vulkan backend this
# runner has no lane for.
_IMG_DEVICE = "gpu-rocm"


class AutoSelectStatus(StrEnum):
    """What :func:`auto_select_first_image_model` did."""

    selected = "selected"
    kept = "kept"  # an enabled selection exists; it is the operator's and was left alone
    skipped = "skipped"  # nothing to do, or nothing possible here
    failed = "failed"  # the apply was refused


@dataclass(frozen=True)
class AutoSelectOutcome:
    """The result of an auto-select. ``detail`` is the slot's state after the apply for
    ``selected``, the existing model for ``kept``, and the reason or the refusal message for
    ``skipped`` and ``failed``."""

    status: AutoSelectStatus
    detail: str = ""


async def enabled_img_model(orchestrator: Any) -> str | None:
    """The model the ``img`` capability has selected, or ``None`` when none is enabled.

    Read from the orchestrator's state (``selections.img.img``), the same truth the
    dashboard shows. Raises whatever ``get_state()`` raises; callers decide what an
    unreadable selection means for them.
    """
    state = await orchestrator.get_state()
    selection = ((state.get("selections") or {}).get("img") or {}).get("img") or {}
    model = selection.get("model")
    if not selection.get("enabled") or not isinstance(model, str):
        return None
    return model.strip() or None


async def auto_select_first_image_model(
    model: ImageModel, *, orchestrator: CapabilityOrchestrator | None
) -> AutoSelectOutcome:
    """Select ``model`` for the ``img`` capability when nothing is selected yet.

    A no-op unless all of these hold: the API can render ``model`` (a video model never
    becomes the image pick), there is an orchestrator, the ``img`` capability has no
    enabled selection with a model, and the host has a ComfyUI lane
    (:func:`~hal0.capabilities.catalog.comfyui_backends` is non-empty). The apply sends
    ``device="gpu-rocm"``, the seeded slot's own, and not the lane gate's answer.

    Never raises for a refused apply (:class:`~hal0.errors.Hal0Error`, logged): the caller
    is an install that already succeeded. A selection that cannot be read is treated as
    "may exist" and left alone. The returned outcome says which of these happened.
    """
    if not model.api_renderable:
        return AutoSelectOutcome(AutoSelectStatus.skipped, "the API cannot render this model")
    if orchestrator is None:
        return AutoSelectOutcome(AutoSelectStatus.skipped, "no capability orchestrator")
    if not capability_catalog.comfyui_backends():
        return AutoSelectOutcome(
            AutoSelectStatus.skipped, capability_catalog.COMFYUI_NO_ENGINE_REASON
        )
    try:
        existing = await enabled_img_model(orchestrator)
    except Exception:
        log.warning(
            "comfyui.auto_select_state_unreadable", extra={"model_id": model.id}, exc_info=True
        )
        return AutoSelectOutcome(
            AutoSelectStatus.skipped, "could not read the current image selection"
        )
    if existing:
        return AutoSelectOutcome(AutoSelectStatus.kept, existing)
    try:
        applied = await orchestrator.apply(
            "img",
            "img",
            {"enabled": True, "model": model.id, "device": _IMG_DEVICE, "provider": "comfyui"},
        )
    except Hal0Error as exc:
        log.warning(
            "comfyui.auto_select_failed", extra={"model_id": model.id, "error": exc.message}
        )
        return AutoSelectOutcome(AutoSelectStatus.failed, exc.message)
    log.info("comfyui.auto_selected", extra={"model_id": model.id})
    state = applied.get("status") if isinstance(applied, dict) else None
    return AutoSelectOutcome(AutoSelectStatus.selected, state if isinstance(state, str) else "")
