"""Static backend vocabulary surface.

Mounted under /api/meta:

    GET /api/meta/enums — every identity vocabulary the backend speaks, in
    one JSON document, sourced verbatim from the canonical taxonomy in
    :mod:`hal0.model_meta` (see its module docstring for the vocabulary
    table + unknown-value policy).

The UI reads this once at startup instead of hard-coding its own copies of
the device/backend/capability enums, so a taxonomy change lands in exactly
one place. The payload is pure code-level constants — it only changes with
a hal0 release — so it is served with a version-keyed ``ETag`` and a
``Cache-Control`` allowing revalidated caching. No auth (ADR-0012: the
read-only /api surface is open), no app state touched.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request, Response

from hal0 import __version__
from hal0.model_meta import (
    BACKEND_TO_DEVICE,
    CANONICAL_DEVICES,
    CAPABILITY_ALIASES,
    CURATED_MODEL_TAGS,
    DEVICE_CLASSES,
    DEVICE_TO_DEFAULT_PROFILE,
    LEGACY_BACKENDS,
    MODEL_BACKENDS,
    MODEL_CAPABILITIES,
    RUNTIME_FAMILIES,
    SELECTABLE_BACKENDS,
    SLOT_TYPES,
)

router = APIRouter()

# The payload only changes with a code release, so the running version is a
# sufficient cache validator. Weak ETag: byte-identity is irrelevant here.
_ENUMS_ETAG = f'W/"hal0-enums-{__version__}"'


@router.get("/enums")
async def get_enums(request: Request, response: Response) -> Any:
    """Canonical backend vocabularies (devices, backends, capabilities, …).

    Response contract (the dashboard codes against these exact field
    names):

    - ``devices``: ordered device cards — ``{id, label, device_class,
      default_profile, legacy_backend, recommended, description}``.
      ``gpu-rocm`` carries ``recommended: true`` (CONTEXT.md: best
      throughput on Strix Halo; ``gpu-vulkan`` is the slower fallback).
    - ``backends``: DEPRECATED v0.1 ``SlotConfig.backend`` tokens.
    - ``selectable_backends``: what POST /api/slots/{name}/backend accepts.
    - ``device_classes`` / ``slot_types`` / ``runtime_families``: the
      profile + dispatcher vocabularies.
    - ``model_capabilities`` + ``capability_aliases``: canonical registry
      capability spellings and the tolerated synonyms.
    - ``model_backends``: valid registry ``model.backends`` values.
    - ``curated_model_tags``: the curated ``model.tags`` vocabulary (type
      tags + provenance + catalogue descriptors) the dashboard's tag
      chips draw from. Freeform tags outside this list stay legal.
    - ``backend_to_device`` / ``device_default_profiles``: the two
      canonical translation maps.

    Static data — served with a version-keyed ETag so clients can cache
    across the session and revalidate for free (304).
    """
    cache_headers = {"ETag": _ENUMS_ETAG, "Cache-Control": "public, max-age=3600"}
    if request.headers.get("if-none-match") == _ENUMS_ETAG:
        return Response(status_code=304, headers=cache_headers)
    response.headers.update(cache_headers)
    return {
        "devices": [asdict(d) for d in CANONICAL_DEVICES],
        "backends": list(LEGACY_BACKENDS),
        "selectable_backends": list(SELECTABLE_BACKENDS),
        "device_classes": list(DEVICE_CLASSES),
        "slot_types": list(SLOT_TYPES),
        "model_capabilities": list(MODEL_CAPABILITIES),
        "capability_aliases": dict(CAPABILITY_ALIASES),
        "model_backends": list(MODEL_BACKENDS),
        "curated_model_tags": list(CURATED_MODEL_TAGS),
        "runtime_families": list(RUNTIME_FAMILIES),
        "backend_to_device": dict(BACKEND_TO_DEVICE),
        "device_default_profiles": dict(DEVICE_TO_DEFAULT_PROFILE),
    }
