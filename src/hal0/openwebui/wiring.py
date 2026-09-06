"""Live-truth resolver for OpenWebUI's dynamic env blocks and wired chips.

Reads the actually-bound capability state — an embed-capable slot, a
ComfyUI (img) slot, a search provider — and turns it into the overrides
:func:`hal0.openwebui.env_writer.write_openwebui_env` merges on top of its
defaults. :func:`openwebui_wiring_status` is the SAME classifier the
Services route exposes for the dashboard's wired chips (one classifier on
the server — the UI never re-derives "is documents/images wired").

Deliberately a separate module from :mod:`hal0.openwebui.env_writer`: this
one imports ``hal0.capabilities``, ``hal0.registry`` and
``hal0.providers.comfyui_workflows`` freely, which is fine because its only
callers already run inside the full app
(:func:`hal0.components.openwebui_arm.converge_openwebui`, the services
route) — never the installer's cold ``python -m hal0.openwebui.env_writer``
(see that module's docstring for why it avoids this import weight).
"""

from __future__ import annotations

import json
from typing import Any

from hal0.capabilities.config import CapabilityConfig, load_capabilities_config
from hal0.openwebui.env_writer import dynamic_env_overrides
from hal0.registry.curated import get_curated

#: filename_prefix / placeholder prompt baked into the static workflow OWUI
#: ships with — real requests always overwrite both (see
#: hal0.providers.comfyui_workflows.build_workflow's node-pointer patching,
#: which is exactly what COMFYUI_WORKFLOW_NODES tells OWUI to do at request
#: time).
_OWUI_REQUEST_TAG = "openwebui"
_OWUI_PLACEHOLDER_PROMPT = "a placeholder prompt"

#: OWUI's own COMFYUI_WORKFLOW_NODES vocabulary (type, substitution key) →
#: the translator template's _meta.params pointer key that names the same
#: node. Generic across every shipped template (sdxl_turbo_simple,
#: sd15_simple, …) because it walks the template's own pointer map rather
#: than hardcoding node ids — a new template needs no changes here.
_OWUI_NODE_TYPES: tuple[tuple[str, str, str], ...] = (
    ("prompt", "positive_prompt", "text"),
    ("model", "ckpt_name", "ckpt_name"),
    ("width", "width", "width"),
    ("height", "height", "height"),
    ("steps", "steps", "steps"),
    ("seed", "seed", "seed"),
    ("n", "batch_size", "batch_size"),
)


def _selection(cfg: CapabilityConfig, slot: str, child: str) -> Any:
    return cfg.selections.get(slot, {}).get(child)


def openwebui_wiring_status() -> dict[str, Any]:
    """Live truth behind both the dashboard's wired chips and the dynamic
    env blocks.

    Chat and voice are unconditionally wired (env_writer's static defaults
    point them at hal0's own ``/v1`` regardless of capability state — see
    ``_DEFAULT_OPENWEBUI_ENV``). Documents/images/web-search are gated on a
    real bound backend: an *enabled* capability selection with a non-empty
    model, never merely a slot file existing on disk.
    """
    cfg = load_capabilities_config()
    embed_sel = _selection(cfg, "embed", "embed")
    img_sel = _selection(cfg, "img", "img")
    embed_model = embed_sel.model.strip() if embed_sel is not None and embed_sel.enabled else ""
    image_model = img_sel.model.strip() if img_sel is not None and img_sel.enabled else ""
    return {
        "chat": True,
        "voice": True,
        "documents": bool(embed_model),
        "images": bool(image_model),
        "web_search": False,
        "embed_model": embed_model or None,
        "image_model": image_model or None,
    }


def _owui_baked_workflow(model_id: str) -> tuple[str, str] | None:
    """Build the static ``COMFYUI_WORKFLOW`` / ``COMFYUI_WORKFLOW_NODES``
    pair OWUI needs, from the SAME translator hal0's own
    ``/v1/images/generations`` route uses (:mod:`hal0.providers.comfyui_workflows`)
    — so the baked default never drifts from what the img slot actually
    runs. Never bakes ODS's SDXL-Lightning graph; hal0 doesn't ship that
    checkpoint by default, and this always reflects the operator's real
    bound model instead.

    Returns ``None`` when the workflow can't be built (unknown model with
    no resolvable template/checkpoint) — the caller must then treat images
    as not wired rather than claim a broken default.
    """
    from hal0.providers.comfyui_workflows import (
        WorkflowTemplateError,
        build_workflow,
        template_params_for_model_class,
    )

    curated = get_curated(model_id)
    ckpt_filename = curated.hf_file if curated is not None else model_id
    model_class = curated.model_class if curated is not None else None
    try:
        graph, _debug_meta = build_workflow(
            body={"prompt": _OWUI_PLACEHOLDER_PROMPT},
            model_class=model_class,
            ckpt_filename=ckpt_filename,
            request_tag=_OWUI_REQUEST_TAG,
        )
    except WorkflowTemplateError:
        return None

    params = template_params_for_model_class(model_class)
    nodes: list[dict[str, Any]] = []
    for owui_type, param_key, field in _OWUI_NODE_TYPES:
        pointer = params.get(param_key)
        if not isinstance(pointer, str) or not pointer.startswith("node:"):
            continue
        node_id = pointer[len("node:") :].split(".", 1)[0]
        nodes.append({"type": owui_type, "key": field, "node_ids": [node_id]})

    return (
        json.dumps(graph, separators=(",", ":")),
        json.dumps(nodes, separators=(",", ":")),
    )


def resolve_dynamic_env_overrides() -> dict[str, str | None]:
    """Live capability state → the overrides dict
    :func:`~hal0.openwebui.env_writer.write_openwebui_env` merges in.
    """
    status = openwebui_wiring_status()
    image_workflow_json: str | None = None
    image_nodes_json: str | None = None
    image_model = status["image_model"]
    if status["images"] and image_model:
        built = _owui_baked_workflow(image_model)
        if built is None:
            # Curated lookup / template resolution failed — never claim a
            # capability we can't actually back with a working workflow.
            image_model = None
        else:
            image_workflow_json, image_nodes_json = built

    return dynamic_env_overrides(
        embed_model_id=status["embed_model"],
        image_model_id=image_model,
        image_workflow_json=image_workflow_json,
        image_workflow_nodes_json=image_nodes_json,
        search_provider=_search_provider_lookup(),
    )


def _search_provider_lookup() -> dict[str, str] | None:
    """Seam for a future search-provider extension (e.g. SearXNG).

    No search service ships with hal0 today. An extension would register
    itself here — a registry lookup this function grows into, e.g. reading
    an installed-extensions manifest — rather than a hardcoded URL landing
    in this function. Returns ``None`` until one exists, so
    ``ENABLE_WEB_SEARCH`` is never rendered against a service that isn't
    actually there (ODS's own Apple footgun — a VRAM fallback that
    "assumes zero current usage" and can over-report what fits,
    ``ods/extensions/services/dashboard-api/routers/features.py:27-32`` in
    the ODS reference tree).
    """
    return None


__all__ = [
    "openwebui_wiring_status",
    "resolve_dynamic_env_overrides",
]
