"""The one owner of every ComfyUI data path (ADR-0037).

ComfyUI's working data (models, workflows, custom nodes, the
``extra_model_paths.yaml`` that points ComfyUI at the models) lives under one
root. The container spec, the API routes and the pull engine all derive their
paths from the functions below; none of them keeps its own copy of the layout.

The root follows the configured model store, so moving the store moves
ComfyUI with it. ``HAL0_COMFYUI_DATA_ROOT`` overrides the root as a whole. No
subdirectory has an override of its own, so the models, workflows and data
directories cannot disagree.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from hal0.config import paths as config_paths

_DATA_ROOT_ENV = "HAL0_COMFYUI_DATA_ROOT"

# ComfyUI model-type key -> directory name under models_root(). The first eight
# are the catalog's component kinds; the rest are directories ComfyUI scans
# that operators drop files into. extra_model_paths.yaml is rendered from this.
SUBDIRS: Mapping[str, str] = MappingProxyType(
    {
        "checkpoints": "checkpoints",
        "diffusion_models": "diffusion_models",
        "text_encoders": "text_encoders",
        "vae": "vae",
        "loras": "loras",
        "upscale_models": "upscale_models",
        "clip_vision": "clip_vision",
        "latent_upscale_models": "latent_upscale_models",
        "unet": "unet",
        "clip": "clip",
        "controlnet": "controlnet",
        "embeddings": "embeddings",
        "style_models": "style_models",
    }
)


def data_root() -> Path:
    """The ComfyUI data root: ``HAL0_COMFYUI_DATA_ROOT``, else ``<model store>/comfyui``."""
    override = os.environ.get(_DATA_ROOT_ENV, "").strip()
    if override:
        return Path(override)
    # Read through the module so the store resolver stays the single patch point.
    return Path(config_paths.model_store_root()) / "comfyui"


def models_root() -> Path:
    """Directory holding every ComfyUI model subdirectory."""
    return data_root() / "models"


def workflows_dir() -> Path:
    """Directory of launchable workflow JSON files."""
    return data_root() / "workflows"


def user_dir() -> Path:
    """ComfyUI's ``user`` directory: its saved workflows, settings and user data."""
    return data_root() / "user"


def custom_nodes_dir() -> Path:
    """Directory of ComfyUI custom nodes."""
    return data_root() / "custom_nodes"


def extra_model_paths_file() -> Path:
    """The ``extra_model_paths.yaml`` that maps ComfyUI's model types onto :data:`SUBDIRS`."""
    return data_root() / "extra_model_paths.yaml"


def is_under_models_root(path: Path) -> bool:
    """True when ``path`` resolves to ``models_root()`` or something inside it.

    Both sides are resolved first, so a symlink that points into the tree counts
    and ``..`` segments cannot escape it. The comparison is by path components,
    never by string prefix: ``models-backup`` is not under ``models``.
    """
    return path.resolve().is_relative_to(models_root().resolve())


def is_under_data_root(path: Path) -> bool:
    """True when ``path`` resolves to ``data_root()`` or something inside it.

    The whole ComfyUI tree (models, workflows, user data, custom nodes and
    ``extra_model_paths.yaml``) is owned by the image catalog and the ComfyUI
    container, never by the model registry or its store GC. Both sides are
    resolved first, like :func:`is_under_models_root`, and the comparison is by
    path components. A path that cannot be resolved (a symlink loop, an
    unreadable parent) counts as outside, so a caller walking a tree never
    aborts on it.
    """
    try:
        return path.resolve().is_relative_to(data_root().resolve())
    except (OSError, RuntimeError):
        return False
