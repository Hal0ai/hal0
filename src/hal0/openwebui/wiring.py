"""Live-truth resolver for OpenWebUI's dynamic env blocks and wired chips.

Reads the actually-bound capability state — an embed-capable slot, the img
selection, a search provider — and turns it into the overrides
:func:`hal0.openwebui.env_writer.write_openwebui_env` merges on top of its
defaults. A block with nothing bound comes back as ``None`` per key, which the
writer reads as "no claim": it clears only keys hal0 wrote itself, never an
operator's hand-set value (#2256).

:func:`openwebui_wiring_status` is the SAME classifier the Services route
exposes for the dashboard's wired chips (one classifier on the server — the UI
never re-derives "is documents/images wired").

Deliberately a separate module from :mod:`hal0.openwebui.env_writer`: this
one imports ``hal0.capabilities`` and ``hal0.comfyui.resolve`` freely, which is
fine because its only callers already run inside the full app
(:func:`hal0.components.openwebui_arm.converge_openwebui`, the services
route) — never the installer's cold ``python -m hal0.openwebui.env_writer``
(see that module's docstring for why it avoids this import weight).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hal0.capabilities.config import CapabilityConfig, load_capabilities_config
from hal0.openwebui.env_writer import dynamic_env_overrides


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
    model, never merely a slot file existing on disk. Images go one step
    further: the model must also resolve to something the API can render
    (installed, renderable — :func:`_renderable_image_model`), because that is
    the condition under which the image env block is written at all. The chip
    and the env block read the same result, so the dashboard never claims an
    image button Open WebUI does not have; ``image_model`` is the model the
    block is written for, ``None`` when it is not.

    ``agents`` lists the model ids Hermes profiles answer as; it needs the
    live upstream registry, so callers outside the API process (the env
    writer) omit it and get ``[]``.
    """
    cfg = load_capabilities_config()
    embed_sel = _selection(cfg, "embed", "embed")
    img_sel = _selection(cfg, "img", "img")
    embed_model = embed_sel.model.strip() if embed_sel is not None and embed_sel.enabled else ""
    selected_image = img_sel.model.strip() if img_sel is not None and img_sel.enabled else ""
    image_model = _renderable_image_model(selected_image) if selected_image else None
    return {
        "chat": True,
        "voice": True,
        "documents": bool(embed_model),
        "images": image_model is not None,
        "web_search": False,
        "embed_model": embed_model or None,
        "image_model": image_model,
        "agents": _agent_model_ids(upstreams, model_cache),
    }


def _renderable_image_model(model_id: str) -> str | None:
    """The id hal0's ``/v1/images/generations`` answers *model_id* under, or ``None``.

    Resolves through the SAME :func:`hal0.comfyui.resolve.resolve_image_model` the
    route uses, so Open WebUI is only pointed at a model that renders: a catalog id
    or a user checkpoint's ``ckpt/<path>``. ``None`` when the model is unknown, not
    installed or not API-renderable, when the models tree cannot be read, or when
    the id holds a line break or NUL, which ``openwebui.env`` cannot carry.
    """
    from hal0.comfyui.resolve import resolve_image_model
    from hal0.errors import Hal0Error

    try:
        resolved = resolve_image_model(model_id, selection_model=None)
    except (Hal0Error, OSError):
        # A Hal0Error covers the resolution errors; an OSError is a models tree the
        # hal0 user cannot read. Either way images are unwired and the RAG and
        # web-search blocks are unaffected.
        return None
    if any(c in resolved.id for c in "\n\r\0"):
        return None
    return resolved.id


def resolve_dynamic_env_overrides() -> dict[str, str | None]:
    """Live capability state → the overrides dict
    :func:`~hal0.openwebui.env_writer.write_openwebui_env` merges in.
    """
    status = openwebui_wiring_status()
    return dynamic_env_overrides(
        embed_model_id=status["embed_model"],
        image_model_id=status["image_model"],
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
