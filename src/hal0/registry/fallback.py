"""Model-fallback heuristics — moved out of ``slots/manager.py`` (ML-2/ML-3,
the P3-slots-deferred extraction).

P3-slots decomposed ``slots/manager.py`` into a core state machine plus
several collaborator modules (``config_write``, ``routing``, ``npu``,
``drift``, ``profile_adopt``, ``reaper``, ``watchdog``) but deliberately left
FOUR pieces behind for this lane: :func:`resolve_servable_model`,
:func:`fallback_local_model`, the diffusion/non-text guard
(:func:`looks_diffusion_or_nontext` + its token tables), and the id-token
overlap ranking helpers. They belong here — the registry/discovery layer —
not the slot-lifecycle state machine: the question they answer ("is there a
locally-servable model that can stand in for this slot's configured-but-
missing default") is a REGISTRY query, not slot orchestration.

``SlotManager`` keeps thin delegator methods with the SAME names/signatures
(``_resolve_servable_model``, ``_fallback_local_model``) so every existing
call site and test is unaffected — see ``slots/manager.py``'s module
docstring "New in ML-3" note.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hal0.config.schema import SlotConfig
    from hal0.registry.model import Model

log = logging.getLogger(__name__)

#: Slot ``type`` → the model capability a fallback search should match when
#: the slot's configured ``model.default`` is not locally servable. Inverse
#: of ``capabilities.catalog._CAPABILITY_TO_SLOT_TYPE`` — duplicated here
#: (not imported) to stay clear of the capabilities import cycle that
#: ``slots.*``/``registry.*`` deliberately avoid.
SLOT_TYPE_TO_CAPABILITY: dict[str, str] = {
    "llm": "chat",
    "embedding": "embed",
    "reranking": "rerank",
    "transcription": "asr",
    "tts": "tts",
    "image": "image",
}

# ── Diffusion / non-text fallback guard (#940 hardening) ──────────────────
#
# discover._guess_capability defaults any unrecognised gguf to "chat", so
# video/image/diffusion ggufs land in the chat candidate pool. Combined with
# the fallback's old "largest-first" pick this grabbed the biggest wrong model
# — live, the chat utility slot fell back to ltx-2-19b-dev-fp8, a 25GB VIDEO
# diffusion model, which llama-server then failed to load. The fallback must
# never select an image/video/diffusion/non-text model for a text slot, so we
# screen candidates by several independent signals before they qualify.

#: Substrings in a model id / name / path that mark a diffusion / image /
#: video artifact (matched on a normalised lower-case, separator-collapsed
#: form so ``sd-``/``sd_``/``SDXL`` all hit). Word-ish tokens are matched on
#: token boundaries; the rest are plain substring contains.
_DIFFUSION_NAME_TOKENS: frozenset[str] = frozenset(
    {"sdxl", "sd", "flux", "ltx", "wan", "comfyui", "turbo", "refiner", "upscaler", "esrgan", "vae"}
)
_DIFFUSION_NAME_SUBSTRINGS: tuple[str, ...] = ("diffus", "lora", "unet", "controlnet")
#: Non-text model file suffixes the llama-server / FLM text providers cannot
#: serve — a gguf default-guessed as chat is fine, these are not. Kept to the
#: diffusion/checkpoint formats; ``.bin`` is deliberately excluded (ggml ASR
#: weights use it and are legitimately text-adjacent).
_NONTEXT_MODEL_SUFFIXES: frozenset[str] = frozenset({".safetensors", ".ckpt", ".pth", ".onnx"})
#: Capabilities that are inherently non-text. A candidate advertising any of
#: these can never serve a chat/embed/rerank/asr/tts slot.
_NONTEXT_CAPABILITIES: frozenset[str] = frozenset({"image", "video"})


def looks_diffusion_or_nontext(model: Any) -> bool:
    """True when *model* looks like a diffusion / image / video / non-text artifact.

    Robust to mislabelled capabilities: a video gguf that
    :func:`hal0.registry.discover._guess_capability` defaulted to ``chat`` is
    still caught by its id/name/path tokens and (for non-gguf checkpoints)
    its file suffix. Conservative — only fires on strong signals so a
    legitimately-named text model (e.g. ``wandb``-tagged) is not excluded by
    an accidental substring.
    """
    caps = {str(c).lower() for c in (getattr(model, "capabilities", None) or [])}
    if caps & _NONTEXT_CAPABILITIES:
        return True
    tags = {str(t).lower() for t in (getattr(model, "tags", None) or [])}
    if tags & _NONTEXT_CAPABILITIES or "diffusion" in tags:
        return True
    path = str(getattr(model, "path", "") or "")
    if path:
        suffix = Path(path).suffix.lower()
        if suffix in _NONTEXT_MODEL_SUFFIXES:
            return True
    haystacks = (
        str(getattr(model, "id", "") or ""),
        str(getattr(model, "name", "") or ""),
        path,
    )
    for raw in haystacks:
        if not raw:
            continue
        tokens = set(id_tokens(raw))
        if tokens & _DIFFUSION_NAME_TOKENS:
            return True
        collapsed = raw.lower()
        if any(sub in collapsed for sub in _DIFFUSION_NAME_SUBSTRINGS):
            return True
    return False


def id_tokens(value: str) -> list[str]:
    """Split a model id / name / path into lower-case alphanumeric tokens.

    ``gemma-4-12b-it_UD-Q4_K_XL`` → ``['gemma', '4', '12b', 'it', 'ud', ...]``.
    Any run of non-alphanumeric characters is a separator, so ``sd-``, ``sd_``,
    ``sd.`` and ``SDXL`` all tokenise predictably.
    """
    return [tok for tok in re.split(r"[^a-z0-9]+", value.lower()) if tok]


def leading_token_overlap(a: list[str], b: list[str]) -> int:
    """Count of shared leading tokens between two token lists.

    Used to rank fallback candidates by name similarity to the configured id:
    ``gemma-4-12b-it`` vs ``gemma-4-12b-it-ud-q4-k-xl`` shares 4 leading
    tokens, beating an unrelated (0-overlap) but larger chat model.
    """
    if not a or not b:
        return 0
    shared = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        shared += 1
    return shared


def default_model_cache_check(model_id: str) -> bool:
    """Default predicate: registered + path-on-disk → cached.

    Imports the registry lazily so test fixtures that haven't wired
    ``HAL0_HOME`` still load the module. Missing registry / model → not
    cached → caller flips through PULLING (where the pull hook either
    materialises the file or raises).

    Raises:
        RegistryUnavailableError: When the registry lookup fails for any
            reason other than "model not registered" (unreadable registry
            dir, permission error, …) — a missing registry is NOT an
            outage, it's an ordinary empty-registry / ``ModelNotFound``.
    """
    try:
        from hal0.registry.store import ModelNotFound, ModelRegistry
    except ImportError:
        return True
    try:
        model = ModelRegistry().get(model_id)
    except ModelNotFound:
        return False
    except Exception as exc:
        from hal0.slots.manager import RegistryUnavailableError

        raise RegistryUnavailableError(
            f"registry lookup failed for {model_id!r}: {exc}",
            details={"model_id": model_id, "error": str(exc)},
        ) from exc
    path = getattr(model, "path", "") or ""
    if not path:
        return False
    try:
        return Path(path).exists()
    except OSError:
        return False


def fallback_local_model(capability: str, configured_id: str = "") -> Model | None:
    """Pick a locally-registered model (file on disk) to stand in for a
    non-servable slot default.

    Candidates must carry ``capability`` AND a real on-disk file. They are
    additionally filtered through :func:`looks_diffusion_or_nontext` so an
    image/video/diffusion artifact (which
    :func:`hal0.registry.discover._guess_capability` may have mislabelled
    ``chat``) can never be served into a text slot — the live
    ``ltx-2-19b-dev-fp8`` incident, where a 25GB video model was the
    largest "chat" candidate and llama-server then failed to load it.

    Selection order (a text slot wants a look-alike of its configured id,
    not merely the biggest model on the box):
      1. **Name similarity** — the candidate sharing the most leading
         hyphen tokens with ``configured_id`` (e.g. ``gemma-4-12b-it`` →
         ``gemma-4-12b-it-ud-q4-k-xl``). Ties broken by larger size, then id.
      2. **Size** — only when nothing shares a leading token: the largest
         on-disk model, tie-broken by id (the historic behaviour).

    ``None`` when no candidate matches.
    """
    try:
        from hal0.registry.store import ModelRegistry
    except ImportError:
        return None
    try:
        models = ModelRegistry().list()
    except Exception:
        return None
    candidates = []
    for m in models:
        caps = getattr(m, "capabilities", None) or []
        if capability not in caps:
            continue
        # Never serve a diffusion/image/video artifact into a text slot,
        # even if it leaked into the chat candidate pool via a default
        # capability guess (see looks_diffusion_or_nontext).
        if looks_diffusion_or_nontext(m):
            continue
        path = getattr(m, "path", "") or ""
        if not path:
            continue
        try:
            if not Path(path).exists():
                continue
        except OSError:
            continue
        candidates.append(m)
    if not candidates:
        return None
    config_tokens = id_tokens(configured_id)

    def _shared_leading(m: Any) -> int:
        return leading_token_overlap(config_tokens, id_tokens(getattr(m, "id", "")))

    best_overlap = max(_shared_leading(m) for m in candidates)
    if best_overlap > 0:
        # Prefer the closest name match; size + id only tie-break peers
        # that share the same number of leading tokens.
        candidates.sort(
            key=lambda m: (
                -_shared_leading(m),
                -(getattr(m, "size_bytes", 0) or 0),
                getattr(m, "id", ""),
            )
        )
    else:
        # Nothing resembles the configured id — fall back to size.
        candidates.sort(key=lambda m: (-(getattr(m, "size_bytes", 0) or 0), m.id))
    return candidates[0]


def resolve_servable_model(model_id: str, cfg: SlotConfig | dict[str, Any]) -> str:
    """Resolve a slot's configured model id to one that can actually serve.

    A seed/default may pin an id that never landed locally under that exact
    id — e.g. a catalog id (``gemma-4-12b-it``, ``upstream=hal0``, no file)
    while the operator's scanned gguf registered under the normalised stem
    (``gemma-4-12b-it-ud-q4-k-xl``). Pinned to the ghost, the slot would
    crash-loop on a ``--model`` path that doesn't exist. When that happens
    we fall back to a locally-registered model matching the slot's
    capability and log it loudly so the operator can fix the config.

    Returns ``model_id`` unchanged when:
      * the slot is FLM/NPU (``device=npu``) — those are served by tag, not
        a local gguf file, so registry-path checks don't apply;
      * the configured model is already registered with a file on disk;
      * the configured id is a known curated model (still to be pulled —
        don't pre-empt a legitimate download with a fallback);
      * no local model matches the slot's capability.
    """
    from hal0.slots._cfg_helpers import _cfg_to_dict

    d = _cfg_to_dict(cfg)
    device = d.get("device") or d.get("slot", {}).get("device")
    if device == "npu":
        return model_id
    if default_model_cache_check(model_id):
        return model_id
    try:
        from hal0.registry.curated import get_curated

        if get_curated(model_id) is not None:
            return model_id  # pullable as configured — let load() pull it
    except ImportError:
        pass
    slot_type = (d.get("type") or d.get("slot", {}).get("type") or "").lower()
    capability = SLOT_TYPE_TO_CAPABILITY.get(slot_type)
    if not capability:
        return model_id
    fallback = fallback_local_model(capability, configured_id=model_id)
    if fallback is None or fallback.id == model_id:
        return model_id
    log.warning(
        "slot.model_default_fallback",
        extra={
            "configured": model_id,
            "fallback": fallback.id,
            "capability": capability,
        },
    )
    return fallback.id


__all__ = [
    "SLOT_TYPE_TO_CAPABILITY",
    "default_model_cache_check",
    "fallback_local_model",
    "id_tokens",
    "leading_token_overlap",
    "looks_diffusion_or_nontext",
    "resolve_servable_model",
]
