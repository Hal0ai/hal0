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
Wiring ``rocmfpx``/``vulkanfpx`` to those manifest keys would silently
downgrade every fresh/updated install's GPU runner to the old toolbox the
day a manifest ships with real digests. So — unlike the spec's illustrative
pseudocode — the ``rocmfpx``/``vulkanfpx``/``cuda``/``cpu`` entries below
carry ``manifest_key=None`` (env override still applies; the manifest tier
is simply skipped, exactly matching the OLD :func:`resolve_default_image`
behaviour, which never consulted the manifest either). ``flm`` / ``kokoro``
/ ``qwen3tts`` / ``comfyui`` DO have real, correctly-corresponding manifest
keys and are wired accordingly.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

from hal0.config.schema import (
    DEFAULT_ROCMFPX_IMAGE,
    FALLBACK_CUDA_IMAGE,
    FALLBACK_VULKAN_IMAGE,
    STALE_ROCMFPX_IMAGE_REFS,
)
from hal0.errors import NotFound

log = logging.getLogger(__name__)

#: Kept as a plain local Literal (NOT imported from ``hal0.profiles``) so
#: this module has zero import-time coupling to the profiles subsystem —
#: ``hal0.profiles._runtime_family`` imports FROM here (lazily, to look up
#: a runner's family), and a module-level import the other way would risk
#: a cycle the first time either side changes its import shape. The value
#: set is the same vocabulary as ``hal0.profiles.RuntimeFamily``.
RuntimeFamily = Literal["llama-server", "flm", "kokoro", "qwen3tts", "comfyui"]

#: Bundled default image tags for the single-purpose runtimes. Literal
#: constants live HERE (not duplicated in each provider module) — a
#: provider that needs its old ``_DEFAULT_*_IMAGE`` name for back-compat
#: imports it from :data:`RUNNER_IMAGES` instead of redefining the string.
_FLM_IMAGE = "ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44"
_KOKORO_IMAGE = "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1"
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
    #: This is metadata, NOT a selector: ``rocmfpx``/``vulkanfpx`` share one
    #: Vulkan-portable image and therefore both list ``("rocm", "vulkan")`` —
    #: the concrete backend is chosen by the slot's typed ``device``, never by
    #: which key was picked. Empty ``()`` = backend-agnostic (no veto).
    supported_backends: tuple[str, ...] = ()
    #: Model-file format / arch family this runner consumes (``"gguf"`` for the
    #: llama-server fork family, else the single-purpose runtime's own format).
    #: Carries the lxc105 finding — GGUF forks can reject newer GGUF arch
    #: versions — as a coarse first-class marker for the §4 fit-check to refine.
    format_arch: str | None = None


# ── Derive RUNNER_IMAGES from the bundled lifecycle catalog ────────────────
# Each runner in the catalog becomes a Runner entry. Fields not tracked in the
# catalog (supports, manifest_key, hardcoded device_class overrides) are
# supplied by a static supplement map keyed on runner id.
#
# This preserves the existing call signatures — get_runner(),
# runner_for_backend(), runner_matches(), and resolve_runner_image() work as
# before — while the data itself is catalog-sourced and a new "vulkan"
# runner (stock-gguf via amd-strix-halo-toolboxes) is automatically present.


def _build_runners_from_catalog() -> dict[str, Runner]:
    """Derive RUNNER_IMAGES from the bundled catalog.

    Bundled catalog is release authority. Any failure is surfaced, not silently
    swallowed — there is no incomplete hardcoded fallback.
    """
    from hal0.lifecycle.catalog import LifecycleCatalog

    catalog = LifecycleCatalog.load_bundled()

    # Static supplement: fields the catalog does not carry.
    _SUPPLEMENTS: dict[str, dict[str, object]] = {
        "rocmfpx": {
            "image_override": DEFAULT_ROCMFPX_IMAGE,
            "supports": RunnerSupports(mtp=True, jinja=True, mmproj=True),
            "backend_override": "rocm",
            "supported_backends_override": ("rocm", "vulkan"),
            "manifest_key": None,
            "format_arch": "gguf",
        },
        "vulkanfpx": {
            "image_override": DEFAULT_ROCMFPX_IMAGE,
            "supports": RunnerSupports(mtp=True, jinja=True, mmproj=True),
            "backend_override": "vulkan",
            "supported_backends_override": ("rocm", "vulkan"),
            "manifest_key": None,
            "format_arch": "gguf",
        },
        "vulkan": {
            "supports": RunnerSupports(mtp=False, jinja=True, mmproj=True),
            "backend_override": "vulkan",
            "manifest_key": None,
            "format_arch": "gguf",
        },
        "cuda": {
            "image_override": FALLBACK_CUDA_IMAGE,
            "supports": RunnerSupports(mtp=False, jinja=True, mmproj=True),
            "backend_override": "cuda",
            "manifest_key": None,
            "format_arch": "gguf",
        },
        "cpu": {
            "image_override": FALLBACK_VULKAN_IMAGE,
            "supports": RunnerSupports(mtp=False, jinja=True, mmproj=True),
            "backend_override": None,
            "manifest_key": None,
            "format_arch": "gguf",
        },
        "flm": {
            "image_override": _FLM_IMAGE,
            "supports": RunnerSupports(),
            "backend_override": None,
            "manifest_key": "flm",
            "format_arch": "flm",
        },
        "kokoro": {
            "image_override": _KOKORO_IMAGE,
            "supports": RunnerSupports(),
            "backend_override": None,
            "manifest_key": "kokoro",
            "format_arch": "kokoro",
        },
        "qwen3tts": {
            "image_override": _QWEN3TTS_IMAGE,
            "supports": RunnerSupports(mtp=False, jinja=False, mmproj=False),
            "backend_override": "rocm",
            "manifest_key": "qwen3tts",
            "format_arch": "qwen3tts",
        },
        "comfyui": {
            "image_override": _COMFYUI_IMAGE,
            "supports": RunnerSupports(mmproj=False),
            "backend_override": None,
            "manifest_key": "comfyui",
            "format_arch": "safetensors",
        },
    }

    # Device class derived from backends — used only when a supplement
    # does not override.
    _DEVICE_CLASS_FROM_BACKENDS: dict[frozenset[str], str] = {
        frozenset({"rocm"}): "gpu",
        frozenset({"vulkan"}): "gpu",
        frozenset({"cuda"}): "gpu",
        frozenset({"cpu"}): "cpu",
        frozenset({"npu"}): "npu",
        frozenset({"rocm", "vulkan"}): "gpu",
    }

    # Per-runner device class overrides (comfyui → img)
    _DEVICE_CLASS_OVERRIDES: dict[str, str] = {
        "comfyui": "img",
    }

    result: dict[str, Runner] = {}
    for rdef in catalog.envelope.runners:
        supp = _SUPPLEMENTS.get(rdef.id, {})
        pkg = catalog._packages[rdef.package]
        image = supp.get("image_override") or f"{pkg.repository}@{pkg.digest}"

        device_class = _DEVICE_CLASS_OVERRIDES.get(
            rdef.id,
            _DEVICE_CLASS_FROM_BACKENDS.get(rdef.backends, "cpu"),
        )
        backend = supp.get("backend_override")
        if backend is None and len(rdef.backends) == 1:
            backend = next(iter(rdef.backends))
            # HAL0-SUNSET: v1.1.0 normalize CPU/NPU backend representation.
            # Single-backend CPU/NPU runners currently retain backend=None.
            if backend in ("cpu", "npu"):
                backend = None

        supported_backends: tuple[str, ...] = supp.get("supported_backends_override") or tuple(rdef.backends)  # type: ignore[assignment]
        result[rdef.id] = Runner(
            key=rdef.id,
            image=image,
            runtime_family=rdef.runtime_family,  # type: ignore[arg-type]
            supports=supp.get("supports", RunnerSupports()),  # type: ignore[arg-type]
            device_class=device_class,
            backend=backend,
            manifest_key=supp.get("manifest_key"),  # type: ignore[arg-type]
            supported_backends=supported_backends,
            format_arch=supp.get("format_arch"),  # type: ignore[arg-type]
        )
    return result


RUNNER_IMAGES: dict[str, Runner] = _build_runners_from_catalog()

#: Back-compat alias — the old name lived on ``hal0.config.schema``; kept
#: importable from BOTH modules so existing ``from hal0.config.schema
#: import STALE_ROCMFPX_IMAGE_REFS`` call sites (tests, the updater's old
#: import) keep working unchanged.
STALE_RUNNER_IMAGE_REFS = STALE_ROCMFPX_IMAGE_REFS


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

    Precedence: ``HAL0_TOOLBOX_IMAGE_<KEY>`` env override → the release
    manifest's digest pin (only when ``runner.manifest_key`` is set) →
    ``runner.image`` (the bundled default). This is THE single resolver
    every provider (llama-server HW-gated runners, FLM, kokoro, qwen3-tts,
    comfyui) now shares — see the module docstring.
    """
    env_key = f"HAL0_TOOLBOX_IMAGE_{runner.key.upper()}"
    env_val = os.environ.get(env_key, "").strip()
    if env_val:
        return env_val
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
      * ``backend == "vulkan"`` → the ``vulkanfpx`` runner.
      * everything else (rocm / unspecified GPU) → the ``rocmfpx`` runner.

    ``rocmfpx`` and ``vulkanfpx`` resolve to the SAME image
    (:data:`~hal0.config.schema.DEFAULT_ROCMFPX_IMAGE` is Vulkan-portable —
    see that constant's docstring) — the two keys exist so a future runner
    split has somewhere to land, not because they differ today.
    """
    be = (backend or "").lower()
    dc = (device_class or "").lower()
    if be == "cuda":
        return get_runner("cuda")
    if dc == "cpu" or be == "cpu":
        return get_runner("cpu")
    if be == "vulkan":
        return get_runner("vulkanfpx")
    return get_runner("rocmfpx")


def runner_matches(runner: Runner, *, device_class: str | None, backend: str | None) -> bool:
    """True when ``runner`` is a valid choice for a given device/backend lane.

    ``device_class`` is matched exactly when provided; ``backend`` is only
    checked when BOTH the runner and the caller declare one (npu/cpu/img
    runners are backend-agnostic, and a caller with no opinion on backend
    never vetoes a runner over it). Shared "does this runner fit this device/
    backend lane" predicate — used by the slot ``binary`` fit-check
    (spec-hw-slot-ownership §4) and :func:`hal0.slots.profile_adopt.runner_fits_slot`.
    """
    if device_class and runner.device_class != device_class:
        return False
    return not (runner.backend and backend and runner.backend != backend)


__all__ = [
    "RUNNER_IMAGES",
    "STALE_RUNNER_IMAGE_REFS",
    "Runner",
    "RunnerSupports",
    "RuntimeFamily",
    "get_runner",
    "resolve_runner_image",
    "runner_for_backend",
    "runner_matches",
]
