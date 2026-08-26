"""Install-time provisioning of the hal0 brain model (v1.0).

The brain is the platform steward, not a user slot: the dashboard's sidebar
chat targets the virtual model ``hal0/brain``, so it is the one slot that must
WORK out of the box instead of shipping as a grey model-less tile. This module
is what makes that true — ``installer/install.sh`` runs it as
``python -m hal0.install.brain_model`` right after the first-run seeding step.

Four properties are deliberate:

**Never fatal.** No network, no disk, an unreadable ``hardware.json``, a
missing curated entry — every failure path returns a non-zero exit code with a
warning and leaves the brain slot MODEL-LESS. The install still succeeds. This
mirrors the absent-``HF_TOKEN`` posture in install.sh: an optional model pull
must not be able to fail a platform install.

**One default, hardware-blind (rc.10).** The default is
:data:`BRAIN_MODEL_DEFAULT` (LFM2.5-2.6B Q8_0) on every box: plain GGUF, no
custom tensor types, loadable by the FPX runner and stock llama.cpp alike.
The hardware fork only survives for ``HAL0_BRAIN_MODEL`` overrides naming an
sft build — the sft Q8/Q4 files carry custom GGML tensor type ids (103 / 100)
that **stock llama.cpp rejects at load** (ROCmFPX runner only,
``DEFAULT_ROCMFPX_IMAGE``); the sft F16 is the plain-GGUF one. An operator
forcing an FPX-only quant onto a CPU box gets the crash-loop they asked for —
the default can no longer produce it.

**Reuses the existing activation gate.** The pull runs through
:func:`~hal0.install.orchestrate.run_pull_and_activate`, which stamps
``[model].default`` on the slot only AFTER the bytes land and marks
``[meta].pull_failed`` when they don't. Since model-presence is the activation
signal (#1369), that is exactly the "seed it model-less on failure" behaviour —
no bespoke rollback needed here.

**Idempotent across install.sh re-runs.** The pull engine has no
already-on-disk short circuit — ``run_pull``/``_download_one`` stream from HF
unconditionally, and a COMPLETED pull leaves no ``.part`` for the resume path
to reuse. :func:`already_pulled` is the guard that stops a re-run from
re-downloading gigabytes it already has.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from hal0.config.schema import HardwareInfo

log = logging.getLogger(__name__)

#: The slot the steward lives in (mirrors ``setup_command._BRAIN_SLOT`` and
#: ``installer/etc-hal0/slots/brain.toml``).
BRAIN_SLOT_NAME = "brain"

#: Public GGUF repo holding the hal0-brain-sft variants. The
#: ``Hal0ai/hal0-brain-sft`` BASE repo is private AND safetensors-only — no
#: chat runner consumes safetensors, so the installer must never reach for it.
BRAIN_HF_REPO = "Hal0ai/hal0-brain-sft-ROCmFPX-GGUF"

#: The default brain on EVERY box (rc.10): LFM2.5-2.6B Q8_0. Plain GGUF —
#: no custom tensor types — so the FPX runner and stock llama.cpp both load
#: it and the hardware split below no longer forks the default. 2.87 GB
#: (disclosed at the install.sh "Brain model" step; up from the 1.1 GB sft
#: Q8). Requires the hal0-combined:0826+ runner for native tool-call
#: parsing and think-extraction (#2073) — do not roll this default onto
#: older image pins.
BRAIN_MODEL_DEFAULT = "lfm2.5-2.6b"

#: hal0-brain-sft Q8_0 agent/tool-use preset — the pre-rc.10 GPU default,
#: kept as an override. Custom GGML tensor type 103 — ROCmFPX runner ONLY.
BRAIN_MODEL_ROCMFPX = "hal0-brain-sft-q8-rocmfpx"

#: hal0-brain-sft plain F16 GGUF — the pre-rc.10 CPU default, kept as an
#: override. Loadable by a stock llama.cpp image.
BRAIN_MODEL_PORTABLE = "hal0-brain-sft-f16"

#: Smallest variant. Custom GGML tensor type 100 — ROCmFPX runner ONLY. NOT
#: auto-selected: an operator switches to it by hand when memory is tight.
BRAIN_MODEL_SMALL = "hal0-brain-sft-q4-rocmfp4"

#: Every brain variant, for callers that want to validate an override.
BRAIN_MODEL_IDS = (
    BRAIN_MODEL_DEFAULT,
    BRAIN_MODEL_ROCMFPX,
    BRAIN_MODEL_SMALL,
    BRAIN_MODEL_PORTABLE,
)


def rocmfpx_capable(hw: HardwareInfo) -> bool:
    """True when this box can run the ROCmFPX runner (custom tensor types).

    Mirrors the GPU branch of
    :func:`~hal0.install.profile_derive.derive_device`: a Strix-Halo platform,
    any compute-capable GPU (ROCm/CUDA runtime detected), or any Vulkan-capable
    GPU lands on ``gpu-rocm``/``gpu-vulkan``, and BOTH of those device classes
    resolve to a runner row whose image is ``DEFAULT_ROCMFPX_IMAGE``
    (``rocmfpx`` / ``vulkanfpx`` in :data:`hal0.runners.RUNNER_IMAGES`).
    Anything else derives ``cpu``, whose runner is a stock llama.cpp image that
    rejects tensor types 100/103.
    """
    if hw.platform == "strix-halo":
        return True
    return any(g.compute_capable or g.vulkan_capable for g in hw.gpus)


def brain_model_for_hardware(hw: HardwareInfo, *, override: str | None = None) -> str:
    """Return the curated brain model id to pull on this box.

    *override* (``HAL0_BRAIN_MODEL`` at the install.sh call site) wins when it
    names a known variant, so an operator can force an sft build (mind the
    ROCmFPX-only quants on a CPU box). An unrecognised override is ignored
    with a warning rather than honoured — a typo must not turn into a 404
    pull.

    ``hw`` no longer forks the default: :data:`BRAIN_MODEL_DEFAULT` is plain
    Q8_0 GGUF that every runner class loads, so hardware only matters to
    override users now. The parameter stays — dropping it would ripple
    through every call site for no behavioural gain, and a future
    memory-tiered default would need it right back.
    """
    del hw  # retained for call-site stability; see docstring
    if override:
        override = override.strip()
        if override in BRAIN_MODEL_IDS:
            return override
        log.warning(
            "install.brain_model_override_unknown id=%s known=%s", override, BRAIN_MODEL_IDS
        )
    return BRAIN_MODEL_DEFAULT


def already_pulled(model_id: str, *, capability: str = "chat") -> Path | None:
    """Path of a COMPLETE, existing pull of exactly *model_id*, else ``None``.

    Why this exists: neither :func:`hal0.registry.pull.run_pull` nor
    :func:`~hal0.install.orchestrate.run_pull_and_activate` checks whether the
    destination already holds the file. ``_download_one`` streams from HF
    unconditionally, and its ``.part`` resume is keyed on ``(model_id,
    filename)`` — a completed pull leaves no ``.part``, so there is nothing to
    resume from. Without this guard, every re-run of ``install.sh`` on a box
    that already has the brain re-downloads it in full, which quietly
    contradicts the documented "re-running install.sh is safe/idempotent"
    contract.

    The test is provenance, not just presence: the ``meta.json`` sidecar
    :func:`~hal0.registry.pull.write_model_meta` writes beside every
    capability-grouped pull must name THIS ``curated_id`` and ``hf_file``, and
    the recorded ``size_bytes`` must match what is on disk. A truncated,
    hand-replaced, or differently-sourced file therefore re-pulls rather than
    being silently trusted — the failure mode this guard must not create is
    "install completes, slot activates, container crash-loops on a bad file".

    SCOPE — this dedupes an id against ITSELF, not against equivalent bytes
    under a different id. hal0's store is id-addressed
    (``<store>/<capability>/<id>/model.gguf``) and nothing is content-addressed,
    so e.g. a pre-existing ``hal0-brain-sft`` row does NOT satisfy a
    ``hal0-brain-sft-f16`` pull even when the artefact is identical. Fixing
    that means content-addressing the store, which is a registry-wide design
    change, not an installer one.
    """
    from hal0.registry.curated import get_curated

    # Private imports from sibling modules, same convention as the
    # `_build_offline_deps` import in main() below: these are the exact
    # functions run_pull would use to place the file, so re-deriving the path
    # any other way would be a second resolver that can drift from the writer.
    from hal0.registry.pull import _final_path_for_entry, read_model_meta

    curated = get_curated(model_id)
    if curated is None:
        return None
    try:
        dest: Path = _final_path_for_entry(model_id, curated.hf_file, None, capability)
        if not dest.is_file():
            return None
        meta = read_model_meta(dest)
        if not meta:
            return None
        if meta.get("curated_id") != model_id or meta.get("hf_file") != curated.hf_file:
            return None
        size = meta.get("size_bytes")
        if isinstance(size, int) and size > 0 and dest.stat().st_size != size:
            return None
    except OSError:
        return None
    return dest


def _load_hardware() -> HardwareInfo:
    """Prefer the ``hardware.json`` the first-run seeding step just wrote.

    That file is authoritative (it records the functional ``flm validate``
    result, #1097) and reading it avoids a second multi-second probe. Fall back
    to a live probe when it is absent or unreadable — e.g. a ``--dev`` tree, or
    an install where the seeding step was skipped.
    """
    from hal0.config.loader import load_hardware_info

    try:
        return load_hardware_info()
    except Exception as exc:
        log.info("install.brain_hardware_json_unavailable err=%s — probing live", exc)
        from hal0.hardware.probe import HardwareProbe

        return HardwareProbe().probe()


async def provision_brain_model(
    *,
    hw: HardwareInfo,
    slot_manager,
    registry,
    hf_token: str | None = None,
    override: str | None = None,
) -> str:
    """Pull the brain model and bind it to the ``brain`` slot.

    Returns the curated id that landed. Raises on failure — the caller
    (:func:`main`) turns that into a warning + non-zero exit so install.sh's
    ``|| warn`` keeps the install alive.
    """
    from hal0.install.orchestrate import PullPlan, run_pull_and_activate
    from hal0.registry.curated import get_curated
    from hal0.registry.pull import make_job

    model_id = brain_model_for_hardware(hw, override=override)
    curated = get_curated(model_id)
    if curated is None:  # pragma: no cover — guarded by test_brain_model.py
        raise RuntimeError(
            f"brain model {model_id!r} is not in the curated catalogue "
            "(registry/curated.py) — nothing to pull from"
        )

    # Create the registry row up front so a failed pull still leaves a
    # resolvable id the operator (or the dashboard) can retry against, exactly
    # as apply_setup does for its own picks.
    if hasattr(registry, "ensure"):
        registry.ensure(model_id)

    # Already on disk from an earlier install run? Bind it and stop. Nothing
    # below dedupes, so without this a re-run re-downloads the whole file.
    existing = already_pulled(model_id)
    if existing is not None:
        from hal0.install.orchestrate import _activate_slot_model

        log.info("install.brain_model_already_present id=%s path=%s", model_id, existing)
        await _activate_slot_model(slot_manager, BRAIN_SLOT_NAME, model_id, failed=False)
        return model_id

    plan = PullPlan(
        model_id=model_id,
        job=make_job(model_id),
        kwargs=dict(
            hf_repo=curated.hf_repo,
            hf_file=curated.hf_file,
            registry=registry,
            hf_token=hf_token or None,
            capability="chat",
        ),
        slot_names=[BRAIN_SLOT_NAME],
    )
    # run_pull_and_activate stamps [model].default only on success and marks
    # [meta].pull_failed otherwise — the model-less-on-failure guarantee.
    await run_pull_and_activate(plan, slot_manager=slot_manager)
    if getattr(plan.job, "state", None) != "completed":
        raise RuntimeError(
            f"brain model pull ended in state {getattr(plan.job, 'state', '?')!r}: "
            f"{getattr(plan.job, 'error', None)}"
        )
    return model_id


def main(argv: list[str] | None = None) -> int:
    """``python -m hal0.install.brain_model`` — the install.sh entry point.

    Prints one human line per outcome (install.sh's transcript is the UX here)
    and returns 0 on success, 1 on any failure. NEVER raises: install.sh runs
    under ``set -euo pipefail``, and a traceback escaping this module would
    abort the whole install over an optional model — the exact failure mode
    ruling 7 forbids.
    """
    del argv  # no options; configuration is via env (see install.sh)
    logging.basicConfig(level=logging.WARNING, format="  %(message)s")
    override = (os.environ.get("HAL0_BRAIN_MODEL") or "").strip() or None
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or None
    try:
        from hal0.cli.setup_command import _build_offline_deps

        hw = _load_hardware()
        chosen = brain_model_for_hardware(hw, override=override)
        from hal0.registry.curated import get_curated as _get_curated

        entry = _get_curated(chosen)
        size = f", {entry.size_gb:g} GB download" if entry is not None else ""
        why = "override" if override and chosen == override else "default — loads on every runner class"
        print(f"  brain model: {chosen} ({why}{size})")
        if already_pulled(chosen) is not None:
            print("  already on disk from an earlier run — binding it, no download")
        slot_manager, registry = _build_offline_deps()
        landed = asyncio.run(
            provision_brain_model(
                hw=hw,
                slot_manager=slot_manager,
                registry=registry,
                hf_token=hf_token,
                override=override,
            )
        )
    except BaseException as exc:
        print(f"  brain model pull failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        print("  the brain slot stays model-less; the install continues", file=sys.stderr)
        return 1
    print(f"  brain model ready: {landed} bound to the '{BRAIN_SLOT_NAME}' slot")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised by install.sh
    raise SystemExit(main())
