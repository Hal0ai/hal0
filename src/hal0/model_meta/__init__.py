"""model_meta — the one home for model classification + device→backend resolution.

Issue #695. Before this module the same logic was copy-pasted across
five sites (``routes/models.py``, ``routes/slots.py``,
``capabilities/orchestrator.py``, ``omni_router/filter.py``,
``slots/manager.py``); a classification-rule change meant hunting every
copy. They all import from here now.

The module is **stateless** — no classes, no construction, just
importable functions. ``classify`` and ``device_to_backend`` are pure;
``is_resolvable`` takes the registry explicitly as an argument so the
module stays importable everywhere without threading a handle through.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


# ── classification ───────────────────────────────────────────────────────────

# Capability → coarse modality bucket the dashboard's Models view (and the
# endpoints widget W7) counts by. ``chat`` covers text/vision LLMs; the
# rest map a model's primary non-chat function so embed/rerank/voice/image
# models are counted instead of being lumped under "chat" or omitted.
_CAPABILITY_TO_TYPE: dict[str, str] = {
    "chat": "chat",
    "vision": "chat",
    "embed": "embed",
    "rerank": "rerank",
    "asr": "stt",
    "stt": "stt",
    "tts": "tts",
    "image": "img",
    "img": "img",
}

# Precedence when a model advertises several capabilities: a dedicated
# embedder that also lists "chat" should still classify as embed. chat is
# the lowest-priority fallback so genuinely non-chat models surface.
_TYPE_PRIORITY: tuple[str, ...] = ("rerank", "embed", "stt", "tts", "img", "chat")


def classify(model_id: str = "", capabilities: Any = None) -> str:
    """Return the primary modality bucket for a model.

    Reads the model's ``capabilities`` list (chat/embed/rerank/asr/tts/
    vision/image). Falls back to filename heuristics on the id so
    upstream-only rows (which carry no capabilities) still classify.
    Defaults to ``"chat"`` when nothing else matches.
    """
    found: set[str] = set()
    if isinstance(capabilities, (list, tuple)):
        for cap in capabilities:
            t = _CAPABILITY_TO_TYPE.get(str(cap).strip().lower())
            if t:
                found.add(t)
    if not found and model_id:
        mid = model_id.lower()
        if "rerank" in mid:
            found.add("rerank")
        elif "embed" in mid or "bge" in mid or "nomic" in mid:
            found.add("embed")
        elif "whisper" in mid or "moonshine" in mid or "-stt" in mid or "asr" in mid:
            found.add("stt")
        elif "tts" in mid or "kokoro" in mid or "vibevoice" in mid or "-voice" in mid:
            found.add("tts")
        elif "flux" in mid or "sdxl" in mid or "stable-diffusion" in mid or "-img" in mid:
            found.add("img")
    for t in _TYPE_PRIORITY:
        if t in found:
            return t
    return "chat"


# ── device → Lemonade recipe/backend mapping ─────────────────────────────────
#
# Plan §4.1 + ADR-0008 §6 locked the four-way mapping. ``gpu-*`` slots
# load through llama.cpp with an explicit backend flag; ``cpu`` is the
# same recipe with CPU-only inference; ``npu`` uses Lemonade's FLM
# recipe and does not take a llamacpp_backend (FLM is its own backend).
#
# Returned tuple shape: ``(recipe, llamacpp_backend)``. ``recipe=None``
# means "let Lemonade pick its default" (currently the llama.cpp recipe
# for gpu/cpu).  Either value being ``None`` causes
# :meth:`LemonadeClient.load` to omit the key from the request body —
# Lemonade then falls through to its internal sentinel logic.


def device_to_backend(device: str | None) -> tuple[str | None, str | None]:
    """Map hal0's ``device`` enum onto Lemonade's recipe+backend pair.

    Args:
        device: One of ``gpu-rocm`` | ``gpu-vulkan`` | ``cpu`` | ``npu``.
                Empty / unknown values fall back to ``(None, None)`` so
                Lemonade picks its own defaults — same semantics as
                omitting the keys from the load body.

    Returns:
        ``(recipe, llamacpp_backend)``. Either may be ``None`` to mean
        "don't send this key in the /v1/load body". The two are
        mutually exclusive in practice — NPU uses ``recipe="flm"`` with
        no llamacpp_backend; everything else uses ``recipe=None`` with
        a concrete llamacpp_backend.
    """
    if not device:
        return (None, None)
    d = device.strip().lower()
    if d == "gpu-rocm":
        return (None, "rocm")
    if d == "gpu-vulkan":
        return (None, "vulkan")
    if d == "cpu":
        return (None, "cpu")
    if d == "npu":
        # FLM recipe; ``llamacpp_backend`` is meaningless here. Lemonade
        # routes the load to its fastflowlm_server backend.
        return ("flm", None)
    log.warning(
        "lemonade.provider.unknown_device",
        extra={"device": device},
    )
    return (None, None)


# ── device-namespace normalisation (ex-orchestrator helpers) ─────────────────


def canonical_device(value: str) -> str:
    """Normalise a backend/device string to the canonical ``device`` enum.

    After ADR-0006 §7 both the slot TOML and the capabilities catalog
    speak the same enum (``gpu-rocm | gpu-vulkan | cpu | npu``), so this
    is a near-identity. It still tolerates a legacy ``backend``-style
    input (``vulkan|rocm|flm|moonshine|kokoro``) for forward
    compatibility with hand-edited slot TOMLs by routing through
    :func:`hal0.config.schema.map_backend_to_device`.

    Empty input means "no opinion" and returns ``""``.
    """
    from hal0.config.schema import _VALID_DEVICES, map_backend_to_device

    if not value:
        return ""
    if value in _VALID_DEVICES:
        return value
    return map_backend_to_device(value)


# NOTE(#695): this is deliberately NOT expressed through
# ``device_to_backend`` — the two sites disagreed on unknown input.
# ``device_to_backend`` maps unknown devices to ``(None, None)`` (let
# Lemonade pick), while the orchestrator's ``_slot_backend_for_catalog_id``
# passed unknown tokens through UNCHANGED so hand-edited values stay
# legible on downgrade. Both behaviours are preserved as-is.
_DEVICE_TO_LEGACY_BACKEND: dict[str, str] = {
    "gpu-vulkan": "vulkan",
    "gpu-rocm": "rocm",
    "npu": "flm",
    "cpu": "cpu",
}


def device_to_legacy_backend(device: str) -> str:
    """DEPRECATED namespace — translate a catalog ``device`` id to the legacy
    ``backend`` token.

    Still used by code paths that write the deprecated SlotConfig.backend
    field (kept until the ``backend`` field is excised for downgrade
    legibility). Unknown values pass through unchanged.
    """
    return _DEVICE_TO_LEGACY_BACKEND.get(device, device)


# ── resolvability ────────────────────────────────────────────────────────────


def is_resolvable(model_id: str, registry: Any) -> bool:
    """True if ``model_id`` can actually be loaded onto a slot.

    The slot-apply guard used to require ``registry.has(model_id)``, but FLM
    models are lemond-owned and are never in hal0's registry (see the
    2026-06-07 shape audits) — yet they load fine via npu.toml's config path.
    So gate on *provider-resolvability*: registry-resident OR an installed FLM
    model. (Extensible later to a general ``lemond_serves(id)`` probe.)

    ``registry`` is passed explicitly (anything with a ``has(model_id)``
    method, or ``None``) so this module never grows registry state.
    """
    if registry is not None and registry.has(model_id):
        return True
    from hal0.providers.flm import is_installed_flm_id

    return is_installed_flm_id(model_id)


# ── label extraction ─────────────────────────────────────────────────────────


def labels_of(cfg: dict[str, Any]) -> set[str]:
    """Pull the ``model.labels`` list out of a slot config dict.

    Single source for both :func:`SlotManager.route_for_request` and the
    omni-router tool filter (``omni_router/filter.py``) so the filter's
    decision always matches what ``route_for_request`` will pick — they
    used to be two hand-synced copies.
    """
    model = cfg.get("model") or {}
    if isinstance(model, dict):
        raw = model.get("labels", ())
        if isinstance(raw, (list, tuple)):
            return {str(x) for x in raw}
    return set()


__all__ = [
    "canonical_device",
    "classify",
    "device_to_backend",
    "device_to_legacy_backend",
    "is_resolvable",
    "labels_of",
]
