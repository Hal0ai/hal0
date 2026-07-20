"""Shared picker/apply profile-fit inference (device → runtime profile name).

Single source for the ``(capability, device) → profile name`` rule that the
capability *picker* (``catalog._profile_for_fit``) and the *apply-time*
reconciler (``CapabilityOrchestrator._profile_for_fit``) both need so a
selection resolves to the SAME runtime profile in both surfaces.

This is the conservative, *plain-base* mapping: it uses the canonical
device-class-representative table (:data:`hal0.config.schema.DEVICE_DEFAULT_PROFILES`)
and deliberately never prefers the MTP ``rocm-dnse`` image — a picker/reconcile
must not silently force a slot onto MTP. The install-flavoured, MTP-preferring
counterpart is :func:`hal0.install.profile_derive.derive_profile`.
"""

from __future__ import annotations

from hal0.capabilities.catalog import tts_profile_for_device
from hal0.config.schema import DEVICE_DEFAULT_PROFILES


def profile_name_for_fit(capability: str, device: str) -> str | None:
    """Infer the runtime profile name implied by a picker/apply selection.

    Keeps inference conservative: use profiles where the device/capability
    already identifies a runtime family, and avoid treating generic CPU as
    kokoro except for TTS. Returns ``None`` when nothing can be inferred (the
    selection schema does not yet carry an explicit profile).

    Order matters — the ``tts`` branch must precede the generic ``gpu`` branch
    so a TTS slot never receives the rocm/vulkan llama profile (wrong runtime
    family — the slot would never start the TTS image).
    """
    if capability == "tts":
        # Engine switch within the tts slot — GPU resolves Qwen3, CPU Kokoro.
        return tts_profile_for_device(device)
    if device == "npu":
        return DEVICE_DEFAULT_PROFILES.get("npu")
    if capability == "embed" and device in {"gpu-rocm", "gpu-vulkan"}:
        # Dedicated GPU embed lane (llama-server --embedding), backend-coherent:
        # gpu-rocm→embed, gpu-vulkan→vulkan-embed. Both non-MTP, so this honours
        # the resolver's "never force MTP" contract. The base chat profile never
        # emits --embedding, so embed must not fall through to it.
        return "embedding"
    if capability == "rerank" and device in {"gpu-rocm", "gpu-vulkan"}:
        # Dedicated GPU rerank lane (llama-server --reranking → /v1/rerank),
        # backend-coherent: gpu-rocm→rerank, gpu-vulkan→vulkan-rerank.
        return "reranking"
    if device in {"gpu-rocm", "gpu-vulkan"}:
        return DEVICE_DEFAULT_PROFILES.get(device)
    if capability == "image":
        return "comfyui"
    return None
