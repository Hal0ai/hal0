"""Auto-derive a slot's device + profile from the hardware probe (design D4).

Maps a capability to a ``DeviceLiteral`` via the probe, then to a
``SEED_PROFILES`` name. Chat/coder take the plain GPU base profile (MTP is
never auto-forced — slots opt into the ROCmFPX MTP profiles explicitly);
embed/rerank take their dedicated backend-coherent lanes. NPU lanes are
selected only when the NPU is present AND the operator opted in.

The (device, profile) pairs this produces are backend-coherent per #807:
``gpu-rocm``→``rocm`` (backend rocm), ``gpu-vulkan``→``vulkan``,
``npu``→``flm``, ``cpu``→``tts``/``cpu-llm``.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from hal0.config.schema import DEVICE_DEFAULT_PROFILES, HardwareInfo
from hal0.providers._gpu import default_image_serves_vulkan_lane, kfd_present

log = structlog.get_logger(__name__)

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


def npu_healthy(hw: HardwareInfo) -> bool:
    """True only when the NPU is present AND functionally healthy.

    "Healthy" means device-node detection (:attr:`NPUInfo.present`) *and* the
    functional ``flm validate`` pass recorded at install/setup time
    (:attr:`NPUInfo.validated` is ``True`` — the #1097 hardware.json fact,
    passthrough + render-group GID reachable). ``validated is False``
    (present-but-broken: libxrt-npu2 mismatch / passthrough gap) and
    ``validated is None`` (present but never validated) are both **unhealthy**:
    we never advertise / auto-enable a device we cannot confirm works.

    This is the single source of truth that folds NPU health into the one
    ``npu_opt_in`` boolean threaded through suggest + apply, so the picker never
    advertises an NPU slot that ``apply_setup`` would then skip.
    """
    return bool(hw.npu.present and hw.npu.validated is True)


def npu_takes_utility(hw: HardwareInfo, *, npu_opt_in: bool) -> bool:
    """True when the NPU claims the ``utility`` role on this box.

    When True, the firstrun bundle should NOT provision (or should disable) the
    iGPU ``utility`` slot — the chat-only NPU slot carries the role instead
    (design 2026-06-15). False on NPU-absent or opted-out boxes, where
    ``utility`` stays on the iGPU as before.
    """
    return bool(hw.npu.present and npu_opt_in)


@dataclass(frozen=True, slots=True)
class CpuFallback:
    """The CPU lane a slot fell back to, and why."""

    device: str
    reason: str


def apply_cpu_fallback(reason: str) -> CpuFallback:
    """The one function that turns a slot's lane to CPU, with a recorded reason.

    Single owner for every "this box doesn't have the GPU device class a
    slot needs, so it runs on CPU instead" decision (#1936, #1966): seed-time
    derivation (:func:`derive_device`, below) and the capability picker's
    host-backend fan-out (:mod:`hal0.capabilities.catalog`) both route
    through this instead of returning a bare ``"cpu"`` literal, so every
    fallback in hal0 logs the SAME structured reason a caller can hand to an
    operator (the slot state message, a dashboard device-picker hint, or a
    `hal0 doctor` finding) instead of a silent, unexplained "why did this
    land on CPU".

    Mirrors ODS's ``apply_cpu_gpu_fallback`` (``ods/installers/lib/
    detection.sh:235-257``) in spirit — warn loudly, then hand back the safe
    lane — but returns a value rather than mutating globals, since hal0's
    device derivation is a pure function over one capability at a time.
    """
    log.info("hal0.hardware.cpu_fallback_applied", reason=reason)
    return CpuFallback(device="cpu", reason=reason)


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
    # chat / coder / embed → GPU lane. #1888: the ROCm lane is the ONLY valid
    # GPU LLM lane on AMD, and it needs the /dev/kfd compute node — so
    # kfd_present() (device-node truth) is what decides this lane, never
    # ``compute_capable`` alone (#2216).
    #
    # ``compute_capable`` means only "rocm-smi --showproductname exited 0" —
    # a ROCm *userspace CLI* probe, not a GPU-can-run-ROCm probe. install.sh
    # never installs rocm-smi, so on hal0's own reference deploy shape (a
    # Proxmox LXC with /dev/kfd correctly forwarded) a fresh container has no
    # rocm-smi and ``compute_capable`` reads False on every GPU row even
    # though ROCm works perfectly — #2216, found building a privileged
    # Ubuntu 26.04 CT on a Strix Halo host with dev0/renderD128, dev2/kfd,
    # dev3/accel0 all forwarded: every slot seeded gpu-vulkan, and only
    # installing rocm-smi by hand (so ``compute_capable`` finally read True)
    # unlocked the ROCm lane it already had.
    #
    # ``hw.platform == "strix-halo"`` has the SAME gap from the other side:
    # :func:`hal0.hardware.probe._detect_platform` classifies containers
    # (LXC/WSL) before it ever reaches the bare-metal GPU/NPU classification
    # that produces ``"strix-halo"``, so the platform signal is unreachable
    # inside the container hal0 is actually deployed in.
    #
    # ``kfd_present()`` needs neither: /dev/kfd is AMD's own ROCm compute
    # node (see its docstring), so its mere presence AND openability by the
    # slot-runner identity already answers "can this box run ROCm" more
    # directly than either proxy.
    if any(g.compute_capable for g in hw.gpus) or kfd_present():
        return "gpu-rocm"
    # Vulkan-capable GPU, any vendor — AND a runner image that can serve the
    # lane. #1923 restricted this to non-AMD because the pinned runner's Vulkan
    # backend emitted invalid tokens on AMD, which sent a kfd-less AMD box to
    # CPU with a working GPU in it. #1948 fixed the image, so the lane comes
    # back — but only where it is real.
    #
    # The image half is not decoration (review B2). Deriving ``gpu-vulkan``
    # without it on an install whose runner is still the ade07ba lineage would
    # hand every seeded slot a device that the load-time gate then refuses by
    # name: a regression from "slow but working on CPU" to "no loadable LLM
    # slot at all". Asking the same question the gate asks means the derivation
    # and the gate can never disagree, whatever the pin happens to be —
    # :func:`~hal0.providers._gpu.default_image_serves_vulkan_lane` is the one
    # predicate all three ladders and the installer preflight share.
    #
    # Note the ORDER: this is reached only when the ROCm branch above declined,
    # so a box that can run ROCm still derives ROCm.
    if any(g.vulkan_capable for g in hw.gpus) and default_image_serves_vulkan_lane():
        return "gpu-vulkan"
    # No GPU lane this host can validly use for inference (#1936, #1966):
    # neither a ROCm compute node nor a runner-image-validated Vulkan GPU.
    return apply_cpu_fallback(
        f"no usable GPU lane for {capability!r} on this host (no /dev/kfd, and either "
        "no Vulkan-capable GPU or no runner image validated for the Vulkan lane)"
    ).device


def derive_profile(capability: str, device: str) -> str:
    """Return a ``SEED_PROFILES`` name for a (capability, device) pair.

    Reads the canonical device-class base profile from the single-source
    :data:`~hal0.config.schema.DEVICE_DEFAULT_PROFILES` table (gpu-rocm→rocm,
    gpu-vulkan→vulkan, cpu→cpu-llm, npu→flm) and layers the install path's
    capability specialisations on top:

    * ``embed`` → the ``embedding`` profile (llama-server ``--embedding``) on
      every llama-server device. The base chat profile never emits
      ``--embedding`` and would silently serve ``/v1/completions``, so embed
      always takes the dedicated encoder profile.
    * ``rerank`` → the ``reranking`` profile (llama-server ``--reranking`` →
      ``/v1/rerank``); MUST stay a separate instance from embed.
    * ``npu`` → ``flm`` for every capability, embed included: the NPU runs its
      own runtime family, not llama-server.
    * ``cpu`` + ``tts`` → the kokoro ``tts`` profile.

    Chat/coder take the plain base profile — MTP is never auto-forced (the legacy
    MTP ``rocm-dnse`` profile was removed 2026-07-05; slots opt into the ROCmFPX
    MTP profiles explicitly).

    The embed/rerank lanes are deliberately NOT device-gated (#1830). The old
    ``gpu-rocm``/``gpu-vulkan`` gate dated from the retired per-backend
    ``embed``/``vulkan-embed`` seeds; the 1.0 ``embedding``/``reranking`` seeds
    are device-agnostic logical tunes (no ``device_class``, no ``backend``), so
    a CPU-only box gets them too. While the gate stood, a CPU embed/rerank slot
    was seeded with a chat profile, launched without its mode flag, reported
    ``state=ready`` and 501'd its own endpoint.

    An unknown device falls back to ``cpu-llm`` (the CPU-coherent llama-server
    profile) — returning a GPU profile there caused #807 to reject the slot on
    GPU-less boxes (device=cpu + profile=vulkan incoherent, #834).
    """
    if device == "gpu-rocm" and capability in ("chat", "coder"):
        # Plain ROCm GPU LLM. (The legacy MTP rocm-dnse profile was removed
        # 2026-07-05; MTP dense now lives on the ROCmFPX profiles, which slots
        # opt into explicitly — derivation never silently forces MTP.)
        return "chat"
    if capability in ("embed", "rerank") and device != "npu":
        # Embeddings/rerankers get their dedicated profile (llama-server
        # --embedding / --reranking, -ub 8192) rather than the chat-tuned base —
        # the chat KV-quant/batch flags are meaningless for a pooled encoder,
        # and the base chat profile never emits the mode flag, so the slot would
        # silently serve /v1/completions instead of /v1/embeddings (or 501 on
        # /v1/rerank). Device-agnostic (#1830): the seeds carry no device_class.
        # NPU is excluded because it is a different runtime family (flm), which
        # the base table below supplies.
        return "embedding" if capability == "embed" else "reranking"
    if device == "cpu" and capability == "tts":
        # ``tts`` stays on the kokoro/CPU profile; everything else on CPU takes
        # the CPU-coherent llama-server profile from the base table below.
        return "kokoro"
    return DEVICE_DEFAULT_PROFILES.get(device, "cpu-chat")
