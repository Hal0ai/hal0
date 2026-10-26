"""The image rows of the capability picker (ADR-0037).

The ``img`` capability's model picker lists what ``/v1/images/generations`` can render: the
API-renderable models of the image catalog, installed or not, and the operator's own SD and
SDXL checkpoints (:mod:`hal0.comfyui.inventory`). It is not built from the LLM model
registry, and its rows never go through the LLM model-fit check.

A row has the keys :func:`hal0.capabilities.catalog._entry_to_row` emits, plus the three the
image picker shows: ``display_name``, ``license`` and ``recommended``. There is one row per
model and backend id; which backends the host can serve is the caller's decision
(:func:`hal0.capabilities.catalog.comfyui_backends`).
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from hal0.comfyui import inventory, paths
from hal0.comfyui.catalog import (
    CATALOG,
    ImageModel,
    install_state,
    recommended_model_id,
    weights_gib,
)
from hal0.comfyui.fit import assess, need_for
from hal0.config.loader import load_hardware_info
from hal0.config.schema import HardwareInfo
from hal0.hardware.memory_budget import MemoryBudget
from hal0.install.agent_model import pool_gb

log = logging.getLogger(__name__)

_PROVIDER = "comfyui"
_CAPABILITY = "image"

# The starter picks, largest first: the models :func:`~hal0.comfyui.catalog.recommended_model_id`
# can return. A live budget walks down this list from the RAM rule's pick.
_STARTER_LADDER = ("qwen-image-lightning", "sdxl-lightning", "sd-1.5-pruned-emaonly")


def _image_pool(hw: HardwareInfo) -> tuple[float, Literal["ram", "gpu"]] | None:
    """The image pool in GB and what limited it, or ``None`` when the pool reads zero.

    The one place the pool is worked out, so :func:`image_pool_gb` and the recommendation
    reason cannot disagree. The pool is ``pool_gb(hw)`` unless the smallest positive value of
    ``hw.ram_mb`` and ``hw.cgroup_max_mb`` is smaller, and that RAM limit is then what
    limited it (``"ram"``). Otherwise the pool itself is the figure: the box's unified
    GPU memory (``"gpu"``), or its system RAM when it has no unified memory (``"ram"``,
    because that is what the pool is made of).
    """
    pool = pool_gb(hw)
    if not pool:
        return None
    limits = [mb for mb in (hw.ram_mb, hw.cgroup_max_mb) if mb]
    if limits and min(limits) / 1024 < pool:
        return min(limits) / 1024, "ram"
    return pool, "gpu" if hw.unified_memory_mb else "ram"


def image_pool_gb(hw: HardwareInfo) -> float | None:
    """The memory, in GB, an image model can be loaded into on this box.

    ``min(pool_gb(hw), limit)``, where ``limit`` is the smallest positive value of
    ``hw.ram_mb`` and ``hw.cgroup_max_mb``. On a unified-memory host an LXC guest reads the
    host's whole pool as ``unified_memory_mb`` while ``ram_mb`` (MemTotal through lxcfs) and
    ``cgroup_max_mb`` carry the guest's limit. ComfyUI reads the weights into process memory,
    which the guest's memory cgroup is charged for, so the limit bounds what loads
    (#3057). The agent-model offer keeps :func:`pool_gb` as it is: GTT allocations are not
    charged to the guest's cgroup.

    A limit of zero or ``None`` is not known, not a zero-byte guest. ``None`` is returned
    when the pool reads zero: a box that was never probed, not a tiny one.
    """
    picked = _image_pool(hw)
    return picked[0] if picked else None


def _fitting(rule_pick: str, budget: MemoryBudget | None) -> str:
    """``rule_pick``, or the largest smaller starter whose native-size fit is not ``wont_fit``.

    The verdict is :func:`hal0.comfyui.fit.assess` against ``budget``, the same one the model
    card and ``/v1/images/generations`` use, so the pick can never be a model the box then
    refuses (#3076). An unknown budget (``assess`` returns ``None``, or the catalog facts
    cannot be read) keeps the RAM rule's pick.
    When even the smallest starter does not fit, the smallest is still the pick: it is the
    closest the box gets, and the card says why it is tight.
    """
    if budget is None or rule_pick not in _STARTER_LADDER:
        return rule_pick
    for model_id in _STARTER_LADDER[_STARTER_LADDER.index(rule_pick) :]:
        try:
            fit = assess(need_for(CATALOG[model_id], None), budget)
        except ValueError:  # malformed catalog facts are an unknown fit, as on the model rows
            return rule_pick
        if fit is None:
            return rule_pick
        if fit.verdict != "wont_fit":
            return model_id
    return _STARTER_LADDER[-1]


def recommended_for(hw: HardwareInfo, budget: MemoryBudget | None = None) -> str:
    """The catalog's starter pick for the memory ``hw``'s guest can use.

    The one owner of the pick: the picker, ``GET /api/comfyui/models``, the install-time
    offer and ``hal0 setup`` all call it. It is the RAM rule of
    :func:`hal0.comfyui.catalog.recommended_model_id` applied to :func:`image_pool_gb`, the
    smaller of the memory pool and the guest's RAM limit. A pool that reads as zero is a box
    that was never probed, so it is passed on as unknown (``None``), not as a tiny pool.

    With a live ``budget`` (the caller collected it, as for ``GET /api/comfyui/models``) the
    RAM rule's pick is then held to the fit check: a model that reads ``wont_fit`` at its
    native size is stepped down from (:func:`_fitting`). Without one (``hal0 setup`` runs
    before the service) the RAM rule stands.
    """
    return _fitting(recommended_model_id(image_pool_gb(hw)), budget)


def recommended_id(budget: MemoryBudget | None = None) -> str:
    """:func:`recommended_for` this box, read per call: it is one small JSON file.

    A hardware file that cannot be read is an unknown pool too.
    """
    try:
        return recommended_for(load_hardware_info(), budget)
    except Exception:  # an unreadable hardware.json must not take the picker down
        return _fitting(recommended_model_id(None), budget)


def recommendation_for(
    hw: HardwareInfo, budget: MemoryBudget | None = None
) -> dict[str, Any] | None:
    """The starter pick with the figure it was chosen from, for ``GET /api/comfyui/models``.

    ``{"id", "pool_gb", "limited_by"}``: ``id`` is :func:`recommended_for` ``hw``, ``pool_gb``
    is :func:`image_pool_gb` to one decimal, and ``limited_by`` says whether the RAM limit
    (``"ram"``) or the GPU pool (``"gpu"``) set it, so the dashboard can say why this model.
    ``None`` when the pool is unknown (a box that was never probed): there is no figure to
    cite, though :func:`recommended_for` still names a model.
    """
    picked = _image_pool(hw)
    if picked is None:
        return None
    pool, limited_by = picked
    return {
        "id": _fitting(recommended_model_id(pool), budget),
        "pool_gb": round(pool, 1),
        "limited_by": limited_by,
    }


def recommendation(budget: MemoryBudget | None = None) -> dict[str, Any] | None:
    """:func:`recommendation_for` this box, read per call; ``None`` for an unreadable hardware file."""
    try:
        return recommendation_for(load_hardware_info(), budget)
    except Exception:  # an unreadable hardware.json must not take the catalog down
        return None


def _models() -> list[dict[str, Any]]:
    """The models the picker lists, before they are fanned out over backends.

    The API-renderable catalog models in catalog order, then the user checkpoints in scan
    order under the ``api_id`` the inventory gave them. The id is never rebuilt from a file
    name: ``a.safetensors`` and ``sub/a.safetensors`` are different models. ``size_gb`` is
    GiB in both kinds of row.

    Never raises for a models tree the service user cannot read: ``GET /api/capabilities``
    builds every capability's picker in one synchronous pass, so a raise here would be a 500
    for the voice and embed selections too. A catalog model whose files cannot be checked
    reads as not downloaded (it stays installable, and the install route names the fix), the
    user-checkpoint rows are dropped when the scan fails, and one warning per call names the
    path the OS refused.
    """
    root = paths.models_root()
    recommended = recommended_id()
    refused: list[OSError] = []

    def downloaded(model: ImageModel) -> bool:
        try:
            return install_state(model, root).state == "complete"
        except OSError as exc:
            refused.append(exc)
            return False

    models: list[dict[str, Any]] = [
        {
            "id": m.id,
            "size_gb": weights_gib(m),
            "downloaded": downloaded(m),
            "pullable": True,
            "display_name": m.label,
            "license": m.license,
            "recommended": m.id == recommended,
        }
        for m in CATALOG.values()
        if m.api_renderable
    ]
    try:
        user_files = inventory.list_files()
    except OSError as exc:
        refused.append(exc)
        user_files = []
    models.extend(
        {
            "id": f["api_id"],
            "size_gb": round(f["size_bytes"] / 1024**3, 2),
            "downloaded": True,
            "pullable": False,
            "display_name": f["relpath"],
            "license": "",
            "recommended": False,
        }
        for f in user_files
        if "api_id" in f
    )
    if refused:
        first = refused[0]
        log.warning(
            "comfyui.picker_models_tree_unreadable path=%s error=%s",
            first.filename or root,
            first.strerror or first,
        )
    return models


def image_picker_rows(backend_ids: list[str]) -> list[dict[str, Any]]:
    """One flat picker row per listed image model and backend id (none without a backend)."""
    if not backend_ids:
        return []
    return [
        {
            "id": model["id"],
            "backend": backend_id,
            "provider": _PROVIDER,
            "size_gb": model["size_gb"],
            "capabilities": [_CAPABILITY],
            "downloaded": model["downloaded"],
            "pullable": model["pullable"],
            "display_name": model["display_name"],
            "license": model["license"],
            "recommended": model["recommended"],
        }
        for model in _models()
        for backend_id in backend_ids
    ]


__all__ = ["image_picker_rows", "recommendation", "recommended_id"]
