"""Auto-derive a slot's device + profile from the hardware probe (design D4).

Maps a capability to a ``DeviceLiteral`` via the probe, then to a
``SEED_PROFILES`` name. The chat lane prefers the MTP profile on ROCm;
embed/aux take the plain GPU profile. NPU lanes are selected only when the
NPU is present AND the operator opted in.

The (device, profile) pairs this produces are backend-coherent per #807:
``gpu-rocm``→``rocm``/``rocm-dnse`` (backend rocm), ``gpu-vulkan``→``vulkan``,
``npu``→``flm``, ``cpu``→``tts``/``vulkan``.
"""

from __future__ import annotations

from hal0.config.schema import HardwareInfo

#: NPU-trio capabilities (NPU chat agent + npu stt/embed passengers). Kept as a
#: symbol because the trio code is left **dormant** (out of scope to remove,
#: design 2026-06-15) — but fresh-install provisioning no longer derives the
#: passengers. See :data:`NPU_ONLY_CHAT_CAPS` / :data:`NPU_FALLBACK_CHAT_CAPS`
#: for what actually lands on the NPU.
NPU_TRIO_CAPS = frozenset({"agent", "stt-npu", "embed-npu"})

#: Trio *passenger* capabilities (npu stt/embed shadows). These are no longer
#: auto-provisioned on fresh installs — they derive to None so the NPU box is
#: chat-only. The plain ``embed`` / ``stt`` capabilities are unaffected and
#: derive to the GPU/CPU lanes as usual.
NPU_TRIO_PASSENGER_CAPS = frozenset({"stt-npu", "embed-npu"})

#: NPU-only chat capability. ``agent`` lands on the NPU when claimed and is
#: skipped (None) otherwise — there is no GPU agent slot in the seed set.
NPU_ONLY_CHAT_CAPS = frozenset({"agent"})

#: Role-tracking chat capability. ``utility`` rides the NPU chat lane when the
#: NPU is claimed, but **falls back to the GPU/CPU lane** when the NPU is absent
#: or opted out, so the iGPU ``utility`` seed stays coherent (design
#: 2026-06-15).
NPU_FALLBACK_CHAT_CAPS = frozenset({"utility"})


def npu_takes_utility(hw: HardwareInfo, *, npu_opt_in: bool) -> bool:
    """True when the NPU claims the ``utility`` role on this box.

    When True, the firstrun bundle should NOT provision (or should disable) the
    iGPU ``utility`` slot — the chat-only NPU slot carries the role instead
    (design 2026-06-15). False on NPU-absent or opted-out boxes, where
    ``utility`` stays on the iGPU as before.
    """
    return bool(hw.npu.present and npu_opt_in)


def derive_device(capability: str, hw: HardwareInfo, *, npu_opt_in: bool) -> str | None:
    """Return a ``DeviceLiteral`` for the capability, or None to skip it.

    None means "do not provision this slot on this box" — e.g. the NPU chat
    lane when the NPU is absent / not opted in, or a (now-dormant) NPU-trio
    passenger which is never auto-provisioned (design 2026-06-15).
    """
    if capability in NPU_TRIO_PASSENGER_CAPS:
        # Trio passengers (stt-npu / embed-npu) are no longer auto-seeded on
        # fresh installs; the NPU box is chat-only. Plain embed/stt fall
        # through to the GPU/CPU lanes below.
        return None
    if capability in NPU_ONLY_CHAT_CAPS:
        # NPU agent lane: NPU when present and opted in, else skip (no GPU
        # agent slot exists in the seed set).
        return "npu" if (hw.npu.present and npu_opt_in) else None
    if capability in NPU_FALLBACK_CHAT_CAPS and npu_takes_utility(hw, npu_opt_in=npu_opt_in):
        # utility role on the NPU when claimed; otherwise fall through to the
        # GPU/CPU lane so the iGPU utility slot stays coherent on NPU-absent /
        # opted-out boxes (design 2026-06-15).
        return "npu"
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
        return "rocm-dnse" if capability in ("chat", "coder") else "rocm"
    if device == "gpu-vulkan":
        return "vulkan"
    if device == "cpu":
        return "tts" if capability == "tts" else "vulkan"
    return "vulkan"
