"""Auto-derive a slot's device + profile from the hardware probe (design D4).

Maps a capability to a ``DeviceLiteral`` via the probe, then to a
``SEED_PROFILES`` name. The chat lane prefers the MTP profile on ROCm;
embed/aux take the plain GPU profile. NPU lanes are selected only when the
NPU is present AND the operator opted in.

The (device, profile) pairs this produces are backend-coherent per #807:
``gpu-rocm``→``rocm``/``rocm-mtp`` (backend rocm), ``gpu-vulkan``→``vulkan``,
``npu``→``flm``, ``cpu``→``tts``/``vulkan``.
"""

from __future__ import annotations

from hal0.config.schema import HardwareInfo

#: NPU-trio capabilities (NPU chat agent + npu stt/embed passengers). Only
#: provisioned when the NPU is present and the operator opted in.
NPU_TRIO_CAPS = frozenset({"agent", "stt-npu", "embed-npu"})


def derive_device(capability: str, hw: HardwareInfo, *, npu_opt_in: bool) -> str | None:
    """Return a ``DeviceLiteral`` for the capability, or None to skip it.

    None means "do not provision this slot on this box" — e.g. an NPU-trio
    member when the NPU is absent or the operator didn't opt in.
    """
    if capability in NPU_TRIO_CAPS:
        return "npu" if (hw.npu.present and npu_opt_in) else None
    if capability == "tts":
        # kokoro runs on CPU (the `tts` seed profile, backend-None → coherent).
        return "cpu"
    if capability == "stt":
        # No CPU llama profile exists for Whisper in the seed set, so STT is
        # only provisioned on the NPU (opt-in). Otherwise skip it cleanly —
        # the §8 "needs upstream routing" case — rather than create an
        # incoherent cpu/gpu-profile slot that #807 would reject.
        return "npu" if (hw.npu.present and npu_opt_in) else None
    # chat / coder / embed → GPU lane. platform=="strix-halo" is the canonical
    # FP4 signal; compute_capable means a ROCm/CUDA runtime was detected.
    if hw.platform == "strix-halo" or any(g.compute_capable for g in hw.gpus):
        return "gpu-rocm"
    if any(g.vulkan_capable for g in hw.gpus):
        return "gpu-vulkan"
    return "cpu"


def derive_profile(capability: str, device: str) -> str:
    """Return a ``SEED_PROFILES`` name for a (capability, device) pair."""
    if device == "npu":
        return "flm"
    if device == "gpu-rocm":
        # Dense chat/coder benefit from MTP; embed + others take plain rocm.
        return "rocm-mtp" if capability in ("chat", "coder") else "rocm"
    if device == "gpu-vulkan":
        return "vulkan"
    if device == "cpu":
        return "tts" if capability == "tts" else "vulkan"
    return "vulkan"
