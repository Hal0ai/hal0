"""RUNNER_IMAGES — the runner-image registry (plan §7.1b / ML-4).

Before this module, container-image resolution was THREE inconsistent
chains (see the spec's PART 1.4 "current state map"):

  * llama-server (``providers.container._resolve_image_ref``): slot.image
    → profile.image → :func:`hal0.config.schema.resolve_default_image`
    (a hand-rolled HW gate).
  * FLM (``providers.flm.FLMProvider.image_ref``): env var or a literal
    default — ``slot.image`` and ``profile.image`` were never consulted.
  * kokoro / qwen3-tts: ``container_spec`` used ``profile.image`` directly;
    their own ``image_ref()`` methods were dead code.
  * comfyui was the ONE provider that also read the release manifest
    (``manifest_image_ref``) for a digest pin.

This module is the single place that decides "what image does runner X
resolve to right now" — :func:`resolve_runner_image` folds the env-var
override, the manifest digest pin, and the bare default into ONE
precedence order every runner honors identically. Providers still decide
*which* runner key applies (via :func:`runner_for_backend` for the
llama-server HW gate, or a fixed key for the single-purpose runtimes);
this module never guesses that part.

``RUNNER_IMAGES`` is a CODE registry, not a database table — the model
registry's ``preferred_runner`` column (see ``registry/model.py``) stores a
TEXT key into this dict, resolved at read/launch time. See plan §8.2: the
SQLite schema deliberately has no ``runner`` table.

Deliberate scope note for ``manifest_key``: ``manifest.json``'s
``toolbox_images`` table predates the ROCmFPX unification and still keys
the OLD per-backend toolboxes as ``"rocm"`` / ``"vulkan"`` (see
``hal0-toolbox-rocm:v1`` / ``hal0-toolbox-vulkan:v1`` in that file) — a
DIFFERENT image lineage from :data:`~hal0.config.schema.DEFAULT_ROCMFPX_IMAGE`.
Wiring ``rocmfpx`` to those manifest keys would silently
downgrade every fresh/updated install's GPU runner to the old toolbox the
day a manifest ships with real digests. So — unlike the spec's illustrative
pseudocode — the ``rocmfpx``/``cuda``/``cpu`` entries below
carry ``manifest_key=None`` (env override still applies; the manifest tier
is simply skipped, exactly matching the OLD :func:`resolve_default_image`
behaviour, which never consulted the manifest either). ``flm`` / ``kokoro``
/ ``qwen3tts`` / ``comfyui`` DO have real, correctly-corresponding manifest
keys and are wired accordingly — and ``promptforge`` joined them once the
#1891 ct150 gate passed (2026-08-30): its manifest entry was created fresh
for this image lineage, so the wrong-lineage trap above does not apply.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

from hal0.config.schema import (
    DEFAULT_PROMPTFORGE_IMAGE,
    DEFAULT_ROCMFPX_IMAGE,
    FALLBACK_CUDA_IMAGE,
    FALLBACK_VULKAN_IMAGE,
    STALE_ROCMFPX_IMAGE_REFS,
)
from hal0.errors import NotFound

log = logging.getLogger(__name__)

#: Dedup set for the alias-env-override warning in
#: :func:`resolve_runner_image` — config resolves live per request, so
#: without this a pre-collapse box logs the same warning continuously until the
#: operator switches off the old env var. Keyed ``(surface, key)``; tests
#: that assert on this warning must clear it in their setup — see
#: ``tests/runners/test_resolve_image.py``.
_warned: set[tuple[str, str]] = set()


def _warn_once(surface: str, key: str, message: str, *args: object) -> None:
    dedup_key = (surface, key)
    if dedup_key in _warned:
        return
    _warned.add(dedup_key)
    log.warning(message, *args)


#: Kept as a plain local Literal (NOT imported from ``hal0.profiles``) so
#: this module has zero import-time coupling to the profiles subsystem —
#: ``hal0.profiles._runtime_family`` imports FROM here (lazily, to look up
#: a runner's family), and a module-level import the other way would risk
#: a cycle the first time either side changes its import shape. The value
#: set is the same vocabulary as ``hal0.profiles.RuntimeFamily``.
RuntimeFamily = Literal["llama-server", "flm", "kokoro", "qwen3tts", "moonshine", "comfyui"]

#: Bundled default image tags for the single-purpose runtimes. Literal
#: constants live HERE (not duplicated in each provider module) — a
#: provider that needs its old ``_DEFAULT_*_IMAGE`` name for back-compat
#: imports it from :data:`RUNNER_IMAGES` instead of redefining the string.
_FLM_IMAGE = "ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44"
_KOKORO_IMAGE = "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1"
_MOONSHINE_IMAGE = "ghcr.io/hal0ai/hal0-toolbox-moonshine:v1"
_QWEN3TTS_IMAGE = "ghcr.io/hal0ai/hal0-toolbox-qwen3tts:v1"
_COMFYUI_IMAGE = "docker.io/kyuz0/amd-strix-halo-comfyui:latest"


@dataclass(frozen=True, slots=True)
class RunnerSupports:
    """Capability gates a launch-flag resolver can key off (§7.1a / ML-5).

    Not consumed by ML-4 — reserved for the flag-resolution lane so a
    single ``Runner`` lookup can answer both "what image" and "what
    capabilities does this runtime support" without a second registry.
    """

    mtp: bool = False
    jinja: bool = False
    mmproj: bool = False
    #: Specialty-distribution keys this image can execute accelerated
    #: (matched against Model.metadata["specialty"]; see
    #: hal0.registry.specialty.SPECIALTY_KINDS). Empty = plain runner.
    specialties: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Runner:
    """One entry in the runner-image registry."""

    key: str
    image: str
    runtime_family: RuntimeFamily
    supports: RunnerSupports
    device_class: str  # "gpu" | "cpu" | "npu" | "img"
    backend: str | None = None  # "rocm" | "vulkan" | "cuda" | None
    manifest_key: str | None = None  # key into manifest.json's toolbox_images
    #: Backends this image's runner binary can actually execute — the
    #: fit-check metadata for spec-hw-slot-ownership §4. A slot's
    #: ``(device, BINARY)`` pair is compatible iff the device's backend is a
    #: member here; an incompatible pair WARNS at assignment (not at spawn).
    #: This is metadata, NOT a selector: ``rocmfpx`` is the single GPU key
    #: and lists both ``("rocm", "vulkan")`` because one Vulkan-portable
    #: image serves both lanes — the prior ``vulkanfpx`` spelling is a
    #: permanent alias that folds to ``rocmfpx`` before lookup, not a
    #: second registry entry. The concrete backend is chosen by the slot's
    #: typed ``device``, never by which key/spelling was used. Empty ``()``
    #: = backend-agnostic (no veto).
    supported_backends: tuple[str, ...] = ()
    #: Model-file format / arch family this runner consumes (``"gguf"`` for the
    #: llama-server fork family, else the single-purpose runtime's own format).
    #: Carries the lxc105 finding — GGUF forks can reject newer GGUF arch
    #: versions — as a coarse first-class marker for the §4 fit-check to refine.
    format_arch: str | None = None


RUNNER_IMAGES: dict[str, Runner] = {
    "rocmfpx": Runner(
        "rocmfpx",
        DEFAULT_ROCMFPX_IMAGE,
        "llama-server",
        RunnerSupports(mtp=True, jinja=True, mmproj=True),
        "gpu",
        "rocm",
        None,
        supported_backends=("rocm", "vulkan"),
        format_arch="gguf",
    ),
    "promptforge": Runner(
        "promptforge",
        DEFAULT_PROMPTFORGE_IMAGE,
        "llama-server",
        RunnerSupports(mtp=True, jinja=True, mmproj=True, specialties=("promptforge",)),
        "gpu",
        "rocm",
        # Real key as of the #1891 ct150 gate PASS (2026-08-30, report at
        # /mnt/mintdev/artifacts/hal0-promptforge-gate-report-2026-08-30.md):
        # manifest.json's toolbox_images.promptforge now exists and carries
        # the exact digest the gate validated, so — unlike rocmfpx, whose
        # manifest key would resolve a DIFFERENT lineage (module docstring)
        # — this key points at the same image this Runner's bundled tag
        # names. promptforge remains an OPTIONAL runner either way (operator
        # decision on #1946): a slot gets this image only by selecting this
        # runner; nothing here touches the AMD default.
        "promptforge",
        supported_backends=("rocm",),  # HIP-only build: deliberately NOT
        # vulkan — the existing (device, BINARY) fit-check refuses a
        # gpu-vulkan pairing with no new code.
        format_arch="gguf",
    ),
    "cuda": Runner(
        "cuda",
        FALLBACK_CUDA_IMAGE,
        "llama-server",
        RunnerSupports(mtp=False, jinja=True, mmproj=True),
        "gpu",
        "cuda",
        None,
        supported_backends=("cuda",),
        format_arch="gguf",
    ),
    "cpu": Runner(
        "cpu",
        FALLBACK_VULKAN_IMAGE,
        "llama-server",
        RunnerSupports(mtp=False, jinja=True, mmproj=True),
        "cpu",
        None,
        None,
        supported_backends=("cpu",),
        format_arch="gguf",
    ),
    "flm": Runner(
        "flm",
        _FLM_IMAGE,
        "flm",
        RunnerSupports(),
        "npu",
        None,
        "flm",
        supported_backends=("npu",),
        format_arch="flm",
    ),
    "kokoro": Runner(
        "kokoro",
        _KOKORO_IMAGE,
        "kokoro",
        RunnerSupports(),
        "cpu",
        None,
        "kokoro",
        supported_backends=("cpu",),
        format_arch="kokoro",
    ),
    "moonshine": Runner(
        "moonshine",
        _MOONSHINE_IMAGE,
        "moonshine",
        RunnerSupports(),
        "cpu",
        None,
        "moonshine",
        supported_backends=("cpu",),
        format_arch="onnx",
    ),
    "qwen3tts": Runner(
        "qwen3tts",
        _QWEN3TTS_IMAGE,
        "qwen3tts",
        RunnerSupports(mtp=False, jinja=False, mmproj=False),
        "gpu",
        "rocm",
        "qwen3tts",
        supported_backends=("rocm",),
        format_arch="qwen3tts",
    ),
    "comfyui": Runner(
        "comfyui",
        _COMFYUI_IMAGE,
        "comfyui",
        RunnerSupports(mmproj=False),
        "img",
        None,
        "comfyui",
        supported_backends=("rocm",),
        format_arch="safetensors",
    ),
}

#: Back-compat alias — the old name lived on ``hal0.config.schema``; kept
#: importable from BOTH modules so existing ``from hal0.config.schema
#: import STALE_ROCMFPX_IMAGE_REFS`` call sites (tests, the updater's old
#: import) keep working unchanged.
STALE_RUNNER_IMAGE_REFS = STALE_ROCMFPX_IMAGE_REFS

#: Legacy runner keys → the canonical key that replaced them. PERMANENT —
#: persisted slot TOML (`binary = "vulkanfpx"`), model `preferred_runner`
#: rows, env overrides, and operator muscle memory all outlive any release;
#: the cost of keeping an entry is one dict line. `vulkanfpx` never named a
#: real binary: the runner is ROCmFPX, whose image happens to ship a
#: working Vulkan backend (see the collapse spec, 2026-08-30).
RUNNER_ALIASES: dict[str, str] = {"vulkanfpx": "rocmfpx"}


def canonical_runner_key(key: str) -> str:
    """Fold a superseded runner key to its canonical replacement (identity otherwise)."""
    return RUNNER_ALIASES.get(key, key)


def get_runner(key: str) -> Runner:
    """Look up a runner by key, or raise :class:`~hal0.errors.NotFound`."""
    key = canonical_runner_key(key)
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

    Precedence: ``HAL0_TOOLBOX_IMAGE_<KEY>`` env override (canonical name,
    then any alias env name, warned) → manifest pin → bundled default.
    This is THE single resolver every provider (llama-server HW-gated
    runners, FLM, kokoro, qwen3-tts, comfyui) now shares — see the module
    docstring.
    """
    env_key = f"HAL0_TOOLBOX_IMAGE_{runner.key.upper()}"
    env_val = os.environ.get(env_key, "").strip()
    if env_val:
        return env_val
    for alias_key, canon in RUNNER_ALIASES.items():
        if canon != runner.key:
            continue
        alias_env = f"HAL0_TOOLBOX_IMAGE_{alias_key.upper()}"
        alias_val = os.environ.get(alias_env, "").strip()
        if alias_val:
            _warn_once(
                "runner_images.legacy_env_override",
                alias_env,
                "runner_images.legacy_env_override var=%s use=HAL0_TOOLBOX_IMAGE_%s",
                alias_env,
                runner.key.upper(),
            )
            return alias_val
    if runner.manifest_key:
        # Local import: hal0.config.loader is a much heavier module
        # (reads TOML config, paths, etc.) than this leaf registry should
        # pull in at import time, and it has no need of us at module
        # scope either — this keeps hal0.runners a cheap, side-effect-free
        # import for anything that just wants RUNNER_IMAGES.
        from hal0.config.loader import manifest_image_ref

        try:
            pinned = manifest_image_ref(runner.manifest_key)
        except Exception:
            pinned = None
        if pinned:
            return pinned
    return runner.image


def runner_for_backend(backend: str | None, device_class: str | None = None) -> Runner:
    """The HW-gated llama-server runner for a ``(backend, device_class)`` pair.

    Replaces the old :func:`hal0.config.schema.resolve_default_image` gate
    logic one-for-one (that function is now a thin shim over
    ``resolve_runner_image(runner_for_backend(...))``):

      * ``backend == "cuda"`` → the ``cuda`` runner.
      * ``device_class == "cpu"`` or ``backend == "cpu"`` → the ``cpu`` runner.
      * ``backend == "rocm"``, ``backend == "vulkan"``, or unspecified GPU →
        the ``rocmfpx`` runner (one Vulkan-portable image serves both GPU
        lanes; the slot's ``device`` picks the lane).
    """
    be = (backend or "").lower()
    dc = (device_class or "").lower()
    if be == "cuda":
        return get_runner("cuda")
    if dc == "cpu" or be == "cpu":
        return get_runner("cpu")
    return get_runner("rocmfpx")


#: The only runner key whose image is :data:`DEFAULT_ROCMFPX_IMAGE` — the
#: single fork build that understands the custom GGML tensor type ids (100 /
#: 103) a ROCmFPX-family GGUF (``hal0-brain-sft-q8-rocmfpx`` and friends) is
#: packed with. Every other runner (``cpu``, ``cuda``, …) runs stock
#: llama.cpp, which SIGSEGVs on those tensor types instead of rejecting them
#: cleanly (hal0#1790). Used by the launch-time quant/runner compatibility
#: guard in :func:`hal0.providers.container._resolve_llama_scalars`.
FPX_RUNNER_KEYS = frozenset({"rocmfpx"})


#: Family view of :data:`RUNNER_ALIASES`, kept for the runner-images
#: catalogue surfaces (the family strip, ``_effective_defaults``'s override
#: fold, ``_resolve_image_ref``'s defaults-map fold). Since the vulkanfpx
#: collapse there is exactly ONE key per image lineage, so "family of a
#: key" and "canonical runner key" are the same fold — this is the same
#: dict, not a second table that could drift.
CANONICAL_FAMILY: dict[str, str] = RUNNER_ALIASES


def canonical_family(key: str) -> str:
    """The default-images/strip family a runner key belongs to.

    Post-collapse this is identical to :func:`canonical_runner_key`; it
    survives as a name because the catalogue surfaces speak in families.
    """
    return canonical_runner_key(key)


def runner_matches(runner: Runner, *, device_class: str | None, backend: str | None) -> bool:
    """True when ``runner`` is a valid choice for a given device/backend lane.

    ``device_class`` is matched exactly when provided; ``backend`` is checked against
    ``supported_backends`` when the runner declares any (the single ``backend`` field is only
    the default lane); otherwise against ``backend`` when both sides declare one (npu/cpu/img
    runners are backend-agnostic, and a caller with no opinion on backend
    never vetoes a runner over it). Shared "does this runner fit this device/
    backend lane" predicate — used by the slot ``binary`` fit-check
    (spec-hw-slot-ownership §4) and :func:`hal0.slots.profile_adopt.runner_fits_slot`.
    """
    if device_class and runner.device_class != device_class:
        return False
    if not backend:
        return True
    if runner.supported_backends:
        return backend in runner.supported_backends
    return not (runner.backend and runner.backend != backend)


__all__ = [
    "CANONICAL_FAMILY",
    "FPX_RUNNER_KEYS",
    "RUNNER_ALIASES",
    "RUNNER_IMAGES",
    "STALE_RUNNER_IMAGE_REFS",
    "Runner",
    "RunnerSupports",
    "RuntimeFamily",
    "canonical_family",
    "canonical_runner_key",
    "get_runner",
    "resolve_runner_image",
    "runner_for_backend",
    "runner_matches",
]
