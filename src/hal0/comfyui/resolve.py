"""Which workflow an image request's ``model`` renders through (ADR-0037, spec D9).

``/v1/images/generations`` and the Open WebUI baked workflow both turn a ``model`` string
into a template stem, an optional checkpoint override and the model's own defaults. This is
the one place that decides it, in this order:

* An empty ``model`` means the img capability's selected model.
* A catalog id renders through its entry's template, provided the entry is API-renderable
  and every weight file is on disk.
* ``ckpt/<path>`` names a checkpoint the operator dropped into ``checkpoints/`` whose
  family sniffed as SDXL or SD 1.5 (:mod:`hal0.comfyui.inventory`); ``<path>`` is the file's
  path under ``checkpoints/`` without the ``.safetensors`` suffix (``ckpt/sub/a`` for
  ``sub/a.safetensors``). It renders through the generic template of that family with the
  file as the checkpoint override.
* Anything else is unknown.

The checkpoint override comes from the scan of the models tree, never from the request: the
requested id is compared to the scanned ids and never joined into a path, so a client can
pick a model by name but cannot name a path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hal0.comfyui import inventory, paths
from hal0.comfyui.catalog import (
    CATALOG,
    USER_CKPT_TEMPLATES,
    get_model,
    install_route,
    install_state,
    load_template,
    missing_gib,
)
from hal0.errors import Hal0Error, UnprocessableEntity

_USER_CKPT_PREFIX = "ckpt/"
_EDITOR_HINT = (
    "open it in the ComfyUI editor, which the img slot serves on port 8188 "
    "and which needs a ROCm lane on this host"
)

# Native size of a user checkpoint, by family. SDXL is trained at 1024, SD 1.5 at 512.
_USER_CKPT_SIZES = {"sdxl": "1024x1024", "sd15": "512x512"}


class ImageModelUnknown(Hal0Error):
    """``model`` is neither a catalog id nor an installed user checkpoint."""

    code = "image.model_unknown"
    status = 404


class ImageModelNotInstalled(Hal0Error):
    """A catalog model that can render, with weights still to download."""

    code = "image.model_not_installed"
    status = 409


class ImageModelNotRenderable(Hal0Error):
    """A catalog model the API cannot render (video, edit or upscale); the editor can."""

    code = "image.model_not_renderable"
    status = 422


class ImageModelRequired(Hal0Error):
    """No ``model`` in the request and no model selected for the img capability."""

    code = "image.model_required"
    status = 422


class ImageWontFit(UnprocessableEntity):
    """A render the memory budget cannot hold; refused before any GPU slot unloads.

    The message is the fit verdict's own sentence. ``details`` carries the ``need``, the two
    free figures it was judged against, the ``model`` and the effective ``size``.
    """

    code = "image.wont_fit"
    status = 422


@dataclass(frozen=True)
class ResolvedImageModel:
    """What a request renders through.

    ``id`` is the name the request is answered under: the catalog id, or ``ckpt/<path>``.
    ``template`` is a workflow stem under ``hal0.providers.workflows`` and ``ckpt_override``
    the checkpoint file (relative to ``checkpoints/``) a user-checkpoint template loads;
    it is ``None`` for a catalog model, whose template names its own weights. ``defaults``
    is the template's ``_meta.defaults`` plus ``size``, the model's native ``WxH``.
    ``size_bytes`` is the checkpoint file's size, from the scan that found it; ``None`` for a
    catalog model, whose sizes the catalog holds.
    """

    id: str
    template: str
    ckpt_override: str | None
    defaults: dict[str, Any]
    size_bytes: int | None = None


def _template_defaults(stem: str) -> dict[str, Any]:
    """A copy of the template's ``_meta.defaults``."""
    return dict((load_template(stem).get("_meta") or {}).get("defaults") or {})


def renderable_ids() -> list[str]:
    """Every model id a request can name right now.

    The installed API-renderable catalog models in catalog order, then the user
    checkpoints' ``ckpt/<path>`` ids in scan order.
    """
    root = paths.models_root()
    ids = [
        m.id
        for m in CATALOG.values()
        if m.api_renderable and install_state(m, root).state == "complete"
    ]
    ids.extend(f["api_id"] for f in inventory.list_files() if "api_id" in f)
    return ids


def is_image_selection_id(model_id: str) -> bool:
    """True when ``model_id`` is one an ``img`` selection can name, installed or not.

    An API-renderable catalog id or a ``ckpt/<path>`` id. These never are model-registry
    rows, so a slot ``load`` or ``swap`` by id cannot find them; the image path picks the
    model through the capability selection instead.
    """
    model = get_model(model_id)
    if model is not None:
        return model.api_renderable
    return model_id.startswith(_USER_CKPT_PREFIX)


def resolve_image_model(model_id: str | None, *, selection_model: str | None) -> ResolvedImageModel:
    """Resolve ``model_id`` (the request's ``model``) to what renders it.

    ``selection_model`` is the img capability's selected model, used when ``model_id`` is
    empty. Raises :class:`ImageModelRequired` when both are, :class:`ImageModelNotRenderable`
    for a catalog model the API cannot render, :class:`ImageModelNotInstalled` for one whose
    weights are not all on disk, and :class:`ImageModelUnknown` for anything else.
    """
    requested = (model_id or "").strip() or (selection_model or "").strip()
    if not requested:
        raise ImageModelRequired(
            "no image model: set `model` in the request or select an image model "
            "for the img capability"
        )

    model = get_model(requested)
    if model is not None:
        if not model.api_renderable:
            raise ImageModelNotRenderable(
                f"image model {model.id!r} ({model.task}) cannot be rendered through the API; "
                f"{_EDITOR_HINT}",
                details={"model": model.id, "task": model.task, "hint": _EDITOR_HINT},
            )
        state = install_state(model)
        if state.state != "complete":
            install = install_route(model.id)
            still_to_download = missing_gib(state)  # GiB, like the models route's rows
            raise ImageModelNotInstalled(
                f"image model {model.id!r} is not installed ({still_to_download} GB still to "
                f"download); install it with {install}",
                details={"model": model.id, "install": install, "missing_gb": still_to_download},
            )
        return ResolvedImageModel(
            id=model.id,
            template=model.template,
            ckpt_override=None,
            defaults={**_template_defaults(model.template), "size": model.sizes[0]},
        )

    if requested.startswith(_USER_CKPT_PREFIX):
        for entry in inventory.list_files():
            if entry.get("api_id") == requested:
                template = USER_CKPT_TEMPLATES[entry["arch"]]
                return ResolvedImageModel(
                    id=requested,
                    template=template,
                    ckpt_override=entry["relpath"],
                    defaults={
                        **_template_defaults(template),
                        "size": _USER_CKPT_SIZES[entry["arch"]],
                    },
                    size_bytes=entry["size_bytes"],
                )

    available = renderable_ids()
    listing = (
        f"installed image models: {', '.join(available)}"
        if available
        else "no image models are installed"
    )
    raise ImageModelUnknown(
        f"unknown image model {requested!r}; {listing}",
        details={"model": requested, "available": available},
    )


__all__ = [
    "ImageModelNotInstalled",
    "ImageModelNotRenderable",
    "ImageModelRequired",
    "ImageModelUnknown",
    "ImageWontFit",
    "ResolvedImageModel",
    "renderable_ids",
    "resolve_image_model",
]
