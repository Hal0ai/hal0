"""RUNNER_IMAGES — the runner registry (ADR-0006, ADR-0027, ADR-0028).

One entry per runner hal0 ships. A runner's key is its only identifier: the
same word names the registry entry, the ``runners`` row in ``manifest.json``
(:mod:`hal0.runners.pins`), the catalogue row in the runner-images repo's
``images.json`` and the published package ``ghcr.io/hal0ai/hal0-runner-<key>``
(``cuda`` pins the upstream ggml-org CUDA server image instead of a build).

This module decides "what image does runner X launch right now":
:func:`resolve_runner_image` applies the ``HAL0_RUNNER_IMAGE_<KEY>`` env
override, else the manifest pin. Providers still decide *which* runner key
applies (:func:`runner_for_backend` for the llama-server lanes, a fixed key for
the single-purpose runtimes).

``RUNNER_IMAGES`` is a CODE registry, not a database table — a model's
``preferred_runner`` column (``registry/model.py``) stores a TEXT key into it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from hal0.errors import NotFound
from hal0.runners.pins import env_override, pin_for

#: Kept as a plain local Literal (NOT imported from ``hal0.profiles``) so this
#: module has zero import-time coupling to the profiles subsystem —
#: ``hal0.profiles._runtime_family`` imports FROM here. Same vocabulary as
#: ``hal0.profiles.RuntimeFamily``.
RuntimeFamily = Literal["llama-server", "flm", "kokoro", "qwen3tts", "moonshine", "comfyui"]

#: Runtime tier vocabulary — see :attr:`Runner.tier`. Tuple order is display
#: order: the Runner Images page and the runtime pickers list tiers this way.
RunnerTier = Literal["default", "optional"]
RUNNER_TIERS: tuple[RunnerTier, ...] = ("default", "optional")


@dataclass(frozen=True, slots=True)
class RunnerSupports:
    """Capability gates a launch-flag resolver can key off (§7.1a / ML-5)."""

    mtp: bool = False
    jinja: bool = False
    mmproj: bool = False
    #: Speculative-decoding drafter kinds (ADR-0031) this runner's binary can
    #: use. Empty = none. A kind with a build floor in
    #: :data:`DRAFT_KIND_MIN_BUILD` is further trimmed by
    #: :func:`runner_draft_kinds` against the running image tag.
    draft_kinds: frozenset[str] = frozenset()


#: Minimum llama.cpp build number (``b<N>``) a drafter kind needs.
DRAFT_KIND_MIN_BUILD: dict[str, int] = {"lilicorr": 11669}

_LLAMA_BUILD_RE = re.compile(r"(?:^|[:/])b(\d+)(?![\w])")


def llama_build_from_tag(tag: str | None) -> int | None:
    """Parse the llama.cpp build number from an image tag or ``image:tag`` ref.

    ``b11716-r52bc433`` -> 11716; ``None`` when the tag carries no ``b<N>``.
    """
    if not tag:
        return None
    m = _LLAMA_BUILD_RE.search(tag)
    return int(m.group(1)) if m else None


@dataclass(frozen=True, slots=True)
class Runner:
    """One entry in the runner registry."""

    key: str
    runtime_family: RuntimeFamily
    supports: RunnerSupports
    device_class: str  # "gpu" | "cpu" | "npu" | "img"
    #: The runner's default lane ("rocm" | "vulkan" | "cuda" | None).
    backend: str | None = None
    #: Backends this image's runner binary can execute — the fit-check
    #: metadata for spec-hw-slot-ownership §4. A slot's ``(device, binary)``
    #: pair is compatible iff the device's backend is a member here; an
    #: incompatible pair WARNS at assignment (not at spawn). Metadata, not a
    #: selector: the concrete backend is chosen by the slot's typed
    #: ``device``. Empty ``()`` = backend-agnostic (no veto).
    supported_backends: tuple[str, ...] = ()
    #: Model-file format this runner consumes (``"gguf"`` for llama-server,
    #: else the single-purpose runtime's own format).
    format_arch: str | None = None
    #: Operator-facing display name. UI copy speaks "Runtime <title>", never
    #: the key. Naming rule (ADR-0027): a title says what the runtime is,
    #: never how its image was assembled.
    title: str = ""
    #: One plain-language line saying what the runtime runs on and when you
    #: would pick it.
    blurb: str = ""
    #: The ONE owner of "is this a default runtime" (ADR-0027). ``default``:
    #: in the set every box is expected to have; ``optional``: opt-in, picked
    #: deliberately for a specific model or device. ``/api/system-info``
    #: ``backends[].tier`` and the Runner Images page grouping both read it.
    tier: RunnerTier = "optional"

    @property
    def image(self) -> str:
        """The pinned image ref from ``manifest.json`` ``runners.<key>``."""
        return pin_for(self.key).ref


_LLAMA_DRAFT_KINDS = frozenset({"simple", "mtp", "dflash", "eagle3", "lilicorr"})

RUNNER_IMAGES: dict[str, Runner] = {
    "standard": Runner(
        key="standard",
        runtime_family="llama-server",
        supports=RunnerSupports(mtp=True, jinja=True, mmproj=True, draft_kinds=_LLAMA_DRAFT_KINDS),
        device_class="gpu",
        backend="rocm",
        # The one AMD GPU image serves both lanes: HIP for gpu-rocm, RADV for
        # gpu-vulkan. Vulkan-lane admission is still earned per digest
        # (manifest.json runners.standard.lanes_gated).
        supported_backends=("rocm", "vulkan"),
        format_arch="gguf",
        title="Standard",
        blurb=(
            "llama.cpp for AMD GPUs, on ROCm or Vulkan. The default on AMD; runs plain GGUF models."
        ),
        tier="default",
    ),
    "cpu": Runner(
        key="cpu",
        runtime_family="llama-server",
        supports=RunnerSupports(mtp=False, jinja=True, mmproj=True, draft_kinds=_LLAMA_DRAFT_KINDS),
        device_class="cpu",
        backend=None,
        supported_backends=("cpu",),
        format_arch="gguf",
        title="CPU",
        blurb=(
            "llama.cpp on the processor, no GPU needed. A slow fallback for"
            " boxes without a supported GPU."
        ),
        tier="default",
    ),
    "flm": Runner(
        key="flm",
        runtime_family="flm",
        supports=RunnerSupports(),
        device_class="npu",
        supported_backends=("npu",),
        format_arch="flm",
        title="NPU (FastFlowLM)",
        blurb="Runs supported models on the AMD XDNA 2 NPU, leaving the GPU free.",
        tier="default",
    ),
    "kokoro": Runner(
        key="kokoro",
        runtime_family="kokoro",
        supports=RunnerSupports(),
        device_class="cpu",
        supported_backends=("cpu",),
        format_arch="kokoro",
        title="Kokoro speech",
        blurb="Text-to-speech on the CPU. Works on any box, no GPU needed.",
        tier="default",
    ),
    "moonshine": Runner(
        key="moonshine",
        runtime_family="moonshine",
        supports=RunnerSupports(),
        device_class="cpu",
        supported_backends=("cpu",),
        format_arch="onnx",
        title="Moonshine transcription",
        blurb="Speech-to-text on the CPU. Works on any box, no GPU or NPU needed.",
        tier="default",
    ),
    "comfyui": Runner(
        key="comfyui",
        runtime_family="comfyui",
        supports=RunnerSupports(),
        device_class="img",
        supported_backends=("rocm",),
        format_arch="safetensors",
        title="ComfyUI",
        blurb="Image and video generation on the GPU (ROCm).",
        tier="default",
    ),
    "qwen3tts": Runner(
        key="qwen3tts",
        runtime_family="qwen3tts",
        supports=RunnerSupports(),
        device_class="gpu",
        backend="rocm",
        supported_backends=("rocm",),
        format_arch="qwen3tts",
        title="Qwen3 TTS",
        blurb=(
            "Higher-quality multilingual text-to-speech on the GPU (ROCm)."
            " Opt-in; a large download (about 10 GB)."
        ),
    ),
    "cuda": Runner(
        key="cuda",
        runtime_family="llama-server",
        supports=RunnerSupports(mtp=False, jinja=True, mmproj=True),
        device_class="gpu",
        backend="cuda",
        supported_backends=("cuda",),
        format_arch="gguf",
        title="NVIDIA CUDA",
        blurb="llama.cpp for NVIDIA GPUs. Pick it on a box with an NVIDIA card.",
    ),
}


def runner_draft_kinds(runner: Runner, image_tag: str | None) -> frozenset[str]:
    """Drafter kinds usable on ``runner`` running ``image_tag``.

    The static set minus kinds whose build floor the parsed build misses; an
    unparseable tag keeps the full set.
    """
    build = llama_build_from_tag(image_tag)
    if build is None:
        return runner.supports.draft_kinds
    return frozenset(
        k for k in runner.supports.draft_kinds if build >= DRAFT_KIND_MIN_BUILD.get(k, 0)
    )


def get_runner(key: str) -> Runner:
    """Look up a runner by key, or raise :class:`~hal0.errors.NotFound`."""
    runner = RUNNER_IMAGES.get(key)
    if runner is None:
        raise NotFound(
            f"runner {key!r} not found",
            code="runners.not_found",
            details={"runner": key, "available": sorted(RUNNER_IMAGES)},
        )
    return runner


def resolve_runner_image(runner: Runner) -> str:
    """Resolve ``runner`` to a pull-ready image ref.

    ``HAL0_RUNNER_IMAGE_<KEY>`` env override, else the ``manifest.json`` pin.
    The single resolver every provider shares.
    """
    return env_override(runner.key) or runner.image


def runner_for_backend(backend: str | None, device_class: str | None = None) -> Runner:
    """The llama-server runner for a ``(backend, device_class)`` lane.

    * ``backend == "cuda"`` → ``cuda``.
    * ``device_class == "cpu"`` or ``backend == "cpu"`` → ``cpu``.
    * anything else (``rocm``, ``vulkan``, an unspecified GPU) → ``standard``;
      the slot's ``device`` picks the lane.

    ``ui/src/dash/hw-cascade.js`` mirrors this; change both together.
    """
    be = (backend or "").lower()
    dc = (device_class or "").lower()
    if be == "cuda":
        return RUNNER_IMAGES["cuda"]
    if dc == "cpu" or be == "cpu":
        return RUNNER_IMAGES["cpu"]
    return RUNNER_IMAGES["standard"]


def cpu_lane_has_runner_image() -> bool:
    """Can the ``cpu`` runner launch anything on this build?

    True when ``HAL0_RUNNER_IMAGE_CPU`` names an image or the ``cpu`` pin is
    published. A CPU-only install with neither cannot serve a token, so the
    installer's Stage-1 gate refuses it (``installer/lib/preflight.sh``
    ``_hal0_cpu_lane_has_runner_image`` mirrors this from ``manifest.json``).
    """
    return bool(env_override("cpu")) or pin_for("cpu").published


def runner_matches(runner: Runner, *, device_class: str | None, backend: str | None) -> bool:
    """True when ``runner`` is a valid choice for a given device/backend lane.

    ``device_class`` is matched exactly when provided; ``backend`` is checked
    against ``supported_backends`` when the runner declares any, otherwise
    against ``backend`` when both sides declare one (npu/cpu/img runners are
    backend-agnostic). Used by the slot ``binary`` fit-check and
    :func:`hal0.slots.profile_adopt.runner_fits_slot`.
    """
    if device_class and runner.device_class != device_class:
        return False
    if not backend:
        return True
    if runner.supported_backends:
        return backend in runner.supported_backends
    return not (runner.backend and runner.backend != backend)


__all__ = [
    "RUNNER_IMAGES",
    "RUNNER_TIERS",
    "Runner",
    "RunnerSupports",
    "RunnerTier",
    "RuntimeFamily",
    "cpu_lane_has_runner_image",
    "get_runner",
    "resolve_runner_image",
    "runner_for_backend",
    "runner_matches",
]
