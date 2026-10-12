"""Profile fit predicates for slots (ADR-0029).

Does a profile suit this slot (its slot type and, for a runner-carrying
profile, its device class)? Which profile does a slot type need (the mode
flag of ``embedding`` / ``reranking``, the engine of a TTS/STT/image slot)?
Does a runner fit a slot's device? A model no longer picks its slot's
profile: a swap keeps the slot's profile.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

log = logging.getLogger(__name__)


def profile_fits_slot(profile_name: str, cfg_dict: dict[str, Any]) -> bool:
    """True when ``profile_name`` is safe to adopt for this slot.

    The profile must exist in the catalog AND match the slot's type and, for
    a runner-carrying profile, the slot's device class. We never flip a slot's
    hardware to satisfy a profile — a wrong-device profile is rejected so the caller
    keeps the slot's current/device-default profile.
    """
    from hal0.errors import NotFound
    from hal0.profiles import ProfileCatalog

    try:
        resolved = ProfileCatalog().resolve(profile_name)
    except NotFound:
        return False
    return resolved_profile_fits_slot(resolved, cfg_dict)


def resolved_profile_fits_slot(resolved: Any, cfg_dict: Mapping[str, Any]) -> bool:
    """The fit half of :func:`profile_fits_slot`, for an already-resolved profile.

    Pure: no catalog lookup, so a dry run that already holds the definition
    (see :func:`hal0.profiles.resolve_loaded_profile`) can ask the same
    question without the adoption write ``ProfileCatalog.resolve`` may do.
    """
    slot_type = cfg_dict.get("type")
    if slot_type and slot_type not in resolved.supported_slot_types:
        return False
    device = str(cfg_dict.get("device") or "")
    if device and resolved.runner:
        slot_class = (
            "gpu"
            if device.startswith("gpu")
            else device
            if device in ("npu", "cpu", "img")
            else "cpu"
        )
        from hal0.runners import get_runner

        try:
            runner = get_runner(resolved.runner)
        except Exception:
            # Unknown runner key (hand-edited profiles.toml, or a runner since
            # removed from the registry) — never adopt it.
            return False
        if runner.device_class != slot_class:
            return False
    return True


#: Slot type → the capability string the shared ``(capability, device) →
#: profile`` rule speaks (:func:`hal0.capabilities.profile_fit.profile_name_for_fit`).
#: ``llm`` is deliberately absent: a chat slot has no mode flag at stake, so its
#: tune stays the operator's (or the model's stamped) choice rather than a
#: device default silently written into every new slot.
_SLOT_TYPE_TO_CAPABILITY: dict[str, str] = {
    "embedding": "embed",
    "reranking": "rerank",
    "transcription": "stt",
    "tts": "tts",
    "image": "image",
}

#: The CLI's historical ``--provider`` default (``hal0 slot create`` always sends
#: it, even for a capability type that has nothing to do with llama-server).
#: Treated as "unspecified" by the inference below so the CLI and the dashboard
#: modal — which sends no provider at all — infer the SAME profile.
_UNSPECIFIED_PROVIDER = "llama-server"


def _runtime_family_fits_device(profile_name: str, cfg_dict: dict[str, Any]) -> bool:
    """False when the profile's RUNTIME FAMILY has no runner for this device.

    :func:`profile_fits_slot` compares the PROFILE's own ``device_class`` /
    ``backend``, and the 1.0 seeds are deliberately device-agnostic logical
    tunes with both fields ``None`` — so it waves ``qwen3-tts`` through for a
    ``gpu-vulkan`` slot even though ``RUNNER_IMAGES["qwen3tts"]`` is ROCm-only
    (``Qwen3TTSProvider`` unconditionally wires /dev/kfd + MIOpen). The runner
    registry is the authority on which backend a runtime family can actually
    run on, so an inferred profile is vetoed when the slot's backend conflicts
    with its family's runner (#1830 follow-up).

    Only the BACKEND is compared. The runner registry keys ComfyUI under the
    pseudo device class ``img`` while its slot's device is ``gpu-rocm``, so a
    device_class comparison would veto correct answers; hardware-class
    coherence is already :func:`profile_fits_slot`'s job for any profile that
    declares one.
    """
    from hal0.errors import NotFound
    from hal0.model_meta import device_to_backend
    from hal0.profiles import ProfileCatalog
    from hal0.runners import get_runner, runner_matches

    device = str(cfg_dict.get("device") or "").strip().lower()
    if not device:
        return True  # no device pinned yet — nothing to conflict with
    try:
        family = str(ProfileCatalog().resolve(profile_name).runtime_family or "")
        runner = get_runner(family)
    except NotFound:
        # ``llama-server`` is a multi-runner family (one runner per backend)
        # and so is not a runner key: nothing single-image to veto on.
        return True
    return runner_matches(runner, device_class=None, backend=device_to_backend(device)[1])


def type_implied_profile(cfg_dict: dict[str, Any]) -> str | None:
    """The runtime profile a slot's TYPE requires, or ``None`` (#1830).

    Creation-time counterpart of :func:`profile_fits_slot`. For every slot type
    except ``llm`` the profile is a CORRECTNESS fact, not a tuning choice: it
    carries the llama-server mode flag (``--embedding`` / ``--reranking``) or
    selects the runtime engine (kokoro / qwen3-tts, moonshine, comfyui, FLM). A
    capability slot created without one launches as a plain chat server,
    reaches ``state=ready`` and 501s the one endpoint it exists to serve.

    The ``(capability, device) → profile`` mapping is NOT re-derived here — it
    is the single shared rule the picker and capability-apply already use
    (:func:`hal0.capabilities.profile_fit.profile_name_for_fit`), reached
    through the slot-type→capability translation above.

    The answer is vetoed TWICE, so a candidate that cannot actually run on this
    slot is dropped rather than persisted:

    * :func:`profile_fits_slot` — the catalog must have it and its
      device_class/backend/slot-type must match. E.g. ``transcription`` on a
      GPU device, where hal0 ships no STT engine.
    * :func:`_runtime_family_fits_device` — the profile's runtime family must
      have a runner image for this slot's backend. E.g. ``tts`` on
      ``gpu-vulkan``: the shared rule answers ``qwen3-tts`` for every GPU, but
      the qwen3tts runner is ROCm-only, so a Vulkan box keeps the profile-less
      Kokoro fallback ``providers/container._spec_provider_for`` applies
      instead of being pinned to a container it cannot start.

    A catalog that cannot be read at all yields ``None``: slot creation must
    never fail because inference could not run.
    """
    capability = _SLOT_TYPE_TO_CAPABILITY.get(str(cfg_dict.get("type") or "").strip().lower())
    if not capability:
        return None
    device = str(cfg_dict.get("device") or "").strip().lower()
    provider = str(cfg_dict.get("provider") or "").strip().lower()
    if provider == _UNSPECIFIED_PROVIDER:
        provider = ""
    try:
        from hal0.capabilities.profile_fit import profile_name_for_fit

        candidate = profile_name_for_fit(capability, device, provider)
        if not candidate:
            return None
        fits = profile_fits_slot(candidate, cfg_dict) and _runtime_family_fits_device(
            candidate, cfg_dict
        )
    except Exception:  # unreadable/broken catalog — never block the create
        log.warning(
            "slot.profile_inference_skipped",
            extra={"type": cfg_dict.get("type"), "device": device},
        )
        return None
    return candidate if fits else None


# RETIRED (spec-hw-slot-ownership §2/§3): the model-driven preferred-runner
# adoption (``preferred_runner_for`` + ``apply_preferred_runner``) is gone — the
# runner is the slot's own ``SlotConfig.binary`` field, set by the operator, and
# the image resolves from ``slot.image_pin or RUNNER_IMAGES[slot.binary]`` (or
# the HW-gated default) at launch. Only the generic device/backend fit predicate
# below survives (reused by the §4 fit-check).


def runner_fits_slot(runner_key: str, cfg_dict: dict[str, Any]) -> bool:
    """True when ``RUNNER_IMAGES[runner_key]`` is safe to adopt for this slot.

    Mirrors :func:`profile_fits_slot`'s device/backend coherence derivation
    but delegates the actual match to :func:`hal0.runners.runner_matches`.
    Retained as a shared "does this runner fit this slot's device/backend"
    predicate (spec-hw-slot-ownership §4 fit-check reuse). An unknown key, or
    one whose device_class/backend doesn't match the slot, returns False.
    """
    from hal0.errors import NotFound
    from hal0.runners import get_runner, runner_matches

    try:
        runner = get_runner(runner_key)
    except NotFound:
        return False
    device = str(cfg_dict.get("device") or "")
    if not device:
        return True  # no device pinned yet — nothing to conflict with
    slot_class = (
        "gpu" if device.startswith("gpu") else device if device in ("npu", "cpu", "img") else "cpu"
    )
    from hal0.model_meta import device_to_backend

    slot_backend = device_to_backend(device)[1]
    return runner_matches(runner, device_class=slot_class, backend=slot_backend)


__all__ = [
    "profile_fits_slot",
    "runner_fits_slot",
    "type_implied_profile",
]
