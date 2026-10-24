"""Live-truth resolver for OpenWebUI's dynamic env blocks and wired chips.

Reads the actually-bound capability state — an embed-capable slot, a
ComfyUI (img) slot, a search provider — and turns it into the overrides
:func:`hal0.openwebui.env_writer.write_openwebui_env` merges on top of its
defaults. A block with nothing bound comes back as ``None`` per key, which the
writer reads as "no claim": it clears only keys hal0 wrote itself, never an
operator's hand-set value (#2256).

:func:`openwebui_wiring_status` is the SAME classifier the Services route
exposes for the dashboard's wired chips (one classifier on the server — the UI
never re-derives "is documents/images wired").

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
from collections.abc import Mapping
from typing import Any

from hal0.capabilities.config import CapabilityConfig, load_capabilities_config
from hal0.openwebui.env_writer import dynamic_env_overrides

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
#:
#: There is deliberately no ``model`` row. OWUI writes its configured model id
#: into that node, and the id (``sd-1.5-pruned-emaonly``, ``ckpt/<path>``) is
#: never a checkpoint filename; the baked graph already carries the checkpoint.
_OWUI_NODE_TYPES: tuple[tuple[str, str, str], ...] = (
    ("prompt", "positive_prompt", "text"),
    ("width", "width", "width"),
    ("height", "height", "height"),
    ("steps", "steps", "steps"),
    ("seed", "seed", "seed"),
    ("n", "batch_size", "batch_size"),
)


def _selection(cfg: CapabilityConfig, slot: str, child: str) -> Any:
    return cfg.selections.get(slot, {}).get(child)


def _agent_model_ids(
    upstreams: Any | None, model_cache: Mapping[str, list[str]] | None
) -> list[str]:
    """Model ids Hermes answers as: every ``agent:hermes`` upstream's live
    ``/v1/models`` ids (``model_cache``) when known, else its ``owned_models``.
    Fail-soft: a bad registry yields ``[]``, never an exception."""
    if upstreams is None:
        return []
    try:
        ids: set[str] = set()
        for up in upstreams.list():
            if getattr(up, "managed_by", None) != "agent:hermes":
                continue
            cached = model_cache.get(up.name) if model_cache is not None else None
            ids.update(cached or up.owned_models)
        return sorted(ids)
    except Exception:
        return []


def openwebui_wiring_status(
    *,
    upstreams: Any | None = None,
    model_cache: Mapping[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Live truth behind both the dashboard's wired chips and the dynamic
    env blocks.

    Chat and voice are unconditionally wired (env_writer's static defaults
    point them at hal0's own ``/v1`` regardless of capability state — see
    ``_DEFAULT_OPENWEBUI_ENV``). Documents/images/web-search are gated on a
    real bound backend: an *enabled* capability selection with a non-empty
    model, never merely a slot file existing on disk.

    ``agents`` lists the model ids Hermes profiles answer as; it needs the
    live upstream registry, so callers outside the API process (the env
    writer) omit it and get ``[]``.
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
        "agents": _agent_model_ids(upstreams, model_cache),
    }


def _owui_baked_workflow(model_id: str) -> tuple[str, str, str] | None:
    """Build the static ``COMFYUI_WORKFLOW`` / ``COMFYUI_WORKFLOW_NODES``
    pair OWUI needs, plus the ``IMAGE_SIZE`` default (the model's native
    ``WxH``), from the SAME resolution and translator hal0's own
    ``/v1/images/generations`` route uses
    (:func:`hal0.comfyui.resolve.resolve_image_model`,
    :mod:`hal0.providers.comfyui_workflows`) — so the baked default never
    drifts from what the img slot actually runs. Never bakes ODS's
    SDXL-Lightning graph; it renders the workflow template of the operator's
    real bound model instead, at that model's native size.

    Returns ``None`` when the model does not resolve (unknown, not installed,
    or not API-renderable) or its workflow can't be built — the caller must
    then treat images as not wired rather than claim a broken default.
    """
    from hal0.comfyui.catalog import parse_pointer
    from hal0.comfyui.resolve import resolve_image_model
    from hal0.errors import Hal0Error
    from hal0.providers.comfyui_workflows import build_workflow, template_params

    try:
        resolved = resolve_image_model(model_id, selection_model=None)
        graph, _debug_meta = build_workflow(
            body={"prompt": _OWUI_PLACEHOLDER_PROMPT, "size": resolved.defaults["size"]},
            template=resolved.template,
            ckpt_override=resolved.ckpt_override,
            request_tag=_OWUI_REQUEST_TAG,
        )
        params = template_params(resolved.template)
    except Hal0Error:
        # Covers the resolution errors and WorkflowTemplateError, which share the base.
        return None

    nodes: list[dict[str, Any]] = []
    for owui_type, param_key, field in _OWUI_NODE_TYPES:
        pointer = params.get(param_key)
        if not isinstance(pointer, str):
            continue
        try:
            node_id, _ = parse_pointer(pointer)
        except ValueError:
            continue
        nodes.append({"type": owui_type, "key": field, "node_ids": [node_id]})

    return (
        json.dumps(graph, separators=(",", ":")),
        json.dumps(nodes, separators=(",", ":")),
        resolved.defaults["size"],
    )


def resolve_dynamic_env_overrides() -> dict[str, str | None]:
    """Live capability state → the overrides dict
    :func:`~hal0.openwebui.env_writer.write_openwebui_env` merges in.
    """
    status = openwebui_wiring_status()
    image_workflow_json: str | None = None
    image_nodes_json: str | None = None
    image_size: str | None = None
    image_model = status["image_model"]
    if status["images"] and image_model:
        built = _owui_baked_workflow(image_model)
        if built is None:
            # Model resolution / template build failed — never claim a
            # capability we can't actually back with a working workflow.
            image_model = None
        else:
            image_workflow_json, image_nodes_json, image_size = built

    return dynamic_env_overrides(
        embed_model_id=status["embed_model"],
        image_model_id=image_model,
        image_workflow_json=image_workflow_json,
        image_workflow_nodes_json=image_nodes_json,
        image_size=image_size,
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

    ``None`` is "hal0 has no claim on the web-search keys", not "delete them":
    :func:`~hal0.openwebui.env_writer.write_openwebui_env` removes a dynamic
    key on ``None`` only if hal0 itself wrote it earlier. While no provider
    exists hal0 has written none, so a hand-set self-hosted SearXNG
    (``WEB_SEARCH_ENGINE``, ``SEARXNG_QUERY_URL``, …) is left exactly as the
    operator wrote it and a render that changes nothing else changes no
    bytes — and so triggers no companion restart (#2256).
    """
    return None


__all__ = [
    "openwebui_wiring_status",
    "resolve_dynamic_env_overrides",
]
