"""The image rows of the capability picker (ADR-0037).

The ``img`` capability's model picker lists what ``/v1/images/generations`` can render: the
API-renderable models of the image catalog, installed or not, and the operator's own SD and
SDXL checkpoints (:mod:`hal0.comfyui.inventory`). It is not built from the LLM model
registry, and its rows never go through the LLM model-fit check.

A row has the keys :func:`hal0.capabilities.catalog._entry_to_row` emits, plus the three the
image picker shows: ``display_name``, ``license`` and ``recommended``. There is one row per
model and backend id; which backends the host can serve is the caller's decision
(:func:`hal0.capabilities.catalog._comfyui_backends`).
"""

from __future__ import annotations

from typing import Any

from hal0.comfyui import inventory, paths
from hal0.comfyui.catalog import CATALOG, install_state, recommended_model_id
from hal0.config.loader import load_hardware_info
from hal0.install.agent_model import _pool_gb

_PROVIDER = "comfyui"
_CAPABILITY = "image"


def recommended_id() -> str:
    """The catalog's starter pick for this box's memory pool.

    Read per call: it is one small JSON file. A pool that cannot be read, or that reads as
    zero because the box was never probed, is passed on as unknown (``None``).
    """
    try:
        pool_gb: float | None = _pool_gb(load_hardware_info())
    except Exception:  # an unreadable hardware.json must not take the picker down
        pool_gb = None
    return recommended_model_id(pool_gb if pool_gb else None)


def _models() -> list[dict[str, Any]]:
    """The models the picker lists, before they are fanned out over backends.

    The API-renderable catalog models in catalog order, then the user checkpoints in scan
    order under the ``api_id`` the inventory gave them. The id is never rebuilt from a file
    name: ``a.safetensors`` and ``sub/a.safetensors`` are different models.
    """
    root = paths.models_root()
    recommended = recommended_id()
    models: list[dict[str, Any]] = [
        {
            "id": m.id,
            "size_gb": m.approx_gb,
            "downloaded": install_state(m, root).state == "complete",
            "pullable": True,
            "display_name": m.label,
            "license": m.license,
            "recommended": m.id == recommended,
        }
        for m in CATALOG.values()
        if m.api_renderable
    ]
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
        for f in inventory.list_files()
        if "api_id" in f
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


__all__ = ["image_picker_rows", "recommended_id"]
