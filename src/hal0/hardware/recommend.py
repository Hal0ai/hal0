"""Hardware-driven recommendation for the default ``slots/primary.toml``.

Given a populated :class:`hal0.config.schema.HardwareInfo`, ``recommend_primary_slot``
returns a dict shaped like a :class:`hal0.config.schema.SlotConfig`. The
installer drops it at ``${ETC_DIR}/slots/primary.toml`` on first install
so a freshly-installed host has a sensible default slot waiting — the
operator only needs to ``hal0 model pull <id>`` and start the slot.

Picks intentionally use models from :mod:`hal0.registry.curated` so the
slot validates against the registry as soon as the model is downloaded.
We do NOT invent model names.

Device choice respects ``hal0.config.schema._VALID_DEVICES`` —
``gpu-rocm``, ``gpu-vulkan``, ``cpu``, ``npu`` (ADR-0006 §7). The
output also emits the legacy ``backend`` field for one release so a
downgrade to v0.1.x reads the recommendation file cleanly.
"""

from __future__ import annotations

from typing import Any

from hal0.config.schema import HardwareInfo, map_backend_to_device
from hal0.registry.curated import get_curated

# Curated chat models suitable for the primary slot, ordered from
# largest-context / most capable down to the smallest. We pick the
# largest model whose ``vram_gb_min`` fits the detected hardware. All
# three live in :mod:`hal0.registry.curated`; cross-referenced below.
_PRIMARY_TIERS: list[tuple[str, float, str]] = [
    # (curated_id, ram_gb_min, kind) — every id MUST exist in
    # CURATED_MODELS (src/hal0/registry/curated.py); enforced by
    # tests/registry/test_curation_drift.py and tests/hardware/test_recommend.py.
    #
    # GPU picks are RAM-gated on the *unified/total* host RAM — the same
    # signal the bundle picker gates tiers on (bundles/eligibility.py),
    # not a half-of-VRAM budget. Largest-that-fits wins, so keep the GPU
    # rows sorted descending by ram_gb_min.
    ("Qwen3.6-35B-A3B-MTP-GGUF", 48.0, "gpu"),  # hybrid MoE, ~45 tok/s, tiny KV
    ("qwen3.5-9b", 16.0, "gpu"),  # dense 9B, comfortable mid-tier
    ("qwen3-4b", 0.0, "gpu"),  # smallest GPU pick — always downloadable
    # CPU/MIT fallbacks for hosts with no usable GPU.
    ("llama32-3b", 0.0, "cpu"),
    ("phi3-mini", 0.0, "cpu"),
]

# Native context windows are read from the curated model's GGUF arch max.
# MoE/MTP primaries have a tiny hybrid KV cache, so they default to the
# full window; dense mid-tier models are capped to keep the KV predictable
# on smaller boxes.
_CTX_FALLBACK = 8192
_DENSE_CTX_CAP = 32768

# Strix Halo (UMA) is detected as an AMD GPU with no separate VRAM
# carve-out, where the model-loading pool comes from GTT. The unified
# memory pool is essentially "host RAM the GPU can address." We treat
# anything ≥ this many GB as "Strix Halo class" — enough headroom to
# justify Vulkan over CPU even when there's no discrete VRAM.
_UMA_UNIFIED_GB_MIN = 32


def _pick_chat_model(ram_gb: float) -> str:
    """Return the curated id of the largest GPU chat model that fits ``ram_gb``."""
    for cid, min_gb, kind in _PRIMARY_TIERS:
        if kind == "gpu" and ram_gb + 0.01 >= min_gb:
            return cid
    # Fallback: smallest GPU entry. Better to ship something downloadable
    # than an empty default field.
    gpu_tiers = [t for t in _PRIMARY_TIERS if t[2] == "gpu"]
    return gpu_tiers[-1][0]


def _pick_cpu_model() -> str:
    """Return the curated id of the default CPU-only chat model."""
    for cid, _min_gb, kind in _PRIMARY_TIERS:
        if kind == "cpu":
            return cid
    return _PRIMARY_TIERS[-1][0]


def _resolve_primary_ctx(model_id: str) -> int:
    """Resolve the primary slot's context window from the curated arch max.

    MoE/MTP primaries (tiny hybrid KV) get the full window; dense models
    are capped at ``_DENSE_CTX_CAP``; unknown models fall back to
    ``_CTX_FALLBACK``. Replaces the old flat hard-coded 8192 (#513).
    """
    curated = get_curated(model_id)
    arch_max = curated.context_length if (curated and curated.context_length) else _CTX_FALLBACK
    is_moe = bool(curated) and ("mtp" in curated.tags or "a3b" in curated.id.lower())
    if is_moe:
        return arch_max
    return min(arch_max, _DENSE_CTX_CAP)


def _backend_for(hw: HardwareInfo) -> tuple[str, str]:
    """Pick (backend, rationale) for the detected hardware.

    Rationale strings end up in the rendered TOML as a comment so the
    operator can see why the installer made each choice without having
    to re-read this file.
    """
    gpus = hw.gpus
    primary = gpus[0] if gpus else None

    # AMD UMA (Strix Halo et al.) — Vulkan is the safest default; ROCm
    # works on the same parts but requires a kernel + userspace combo
    # the installer can't easily verify. Vulkan ships in Mesa.
    if primary and primary.vendor == "amd":
        unified_gb = hw.unified_memory_mb / 1024
        if unified_gb >= _UMA_UNIFIED_GB_MIN and primary.vram_mb <= 4096:
            return "vulkan", f"AMD UMA (Strix Halo class — {unified_gb:.0f} GB unified)"
        if primary.compute_capable:
            return "rocm", "AMD discrete GPU with rocm-smi reachable"
        return "vulkan", "AMD GPU; ROCm not detected, Vulkan via Mesa"

    # NVIDIA — vulkan backend works on the NVIDIA proprietary driver.
    # We deliberately do NOT pick "cuda" because it's not in the v1
    # backend allowlist; that's a Phase 2 backend.
    if primary and primary.vendor == "nvidia":
        return "vulkan", "NVIDIA GPU (CUDA backend deferred to Phase 2; Vulkan works today)"

    # Intel iGPU / unknown vulkan-capable
    if primary and primary.vulkan_capable:
        return "vulkan", f"{primary.vendor or 'unknown'} GPU with Vulkan"

    return "cpu", "no GPU detected — CPU inference only (slow but correct)"


def _vram_budget_gb(hw: HardwareInfo) -> float:
    """Return the GB available for model loading.

    On UMA, that's the unified pool (GTT); on discrete GPUs it's the
    advertised VRAM; on CPU-only hosts it's MemAvailable so the model
    plus FastAPI server fit without OOMing.
    """
    if hw.gpus:
        g = hw.gpus[0]
        if g.vendor == "amd" and hw.unified_memory_mb >= hw.ram_mb * 0.95:
            # Half of unified — leave RAM for the OS, OpenWebUI, etc.
            return (hw.unified_memory_mb / 1024) * 0.5
        if g.vram_mb > 0:
            return g.vram_mb / 1024
    # CPU-only — half of available so the rest of the stack still fits.
    return max(hw.ram_available_mb / 1024, 1.0) * 0.5


def recommend_primary_slot(hw: HardwareInfo) -> dict[str, Any]:
    """Return a slots/primary.toml-shaped dict for ``hw``.

    Output is consumable by ``tomli_w.dumps`` and validates against
    :class:`hal0.config.schema.SlotConfig`. The returned dict carries an
    extra ``_rationale`` key under ``[meta]`` so the installer can write
    a leading TOML comment explaining the picks — the SlotConfig model
    is ``extra="allow"`` so this round-trips cleanly.

    Parameters
    ----------
    hw:
        Populated HardwareInfo (from HardwareProbe.probe()).

    Returns
    -------
    dict
        Slot config keyed in TOML order::

          name, port, backend, provider, enabled, [model], [meta]
    """
    backend, backend_why = _backend_for(hw)
    # RAM-gate the GPU pick on total/unified host RAM (the same signal the
    # bundle picker uses), not a half-of-VRAM budget. CPU-only hosts get a
    # small MIT-licensed fallback. (#512)
    ram_gb = (hw.unified_memory_mb or hw.ram_mb) / 1024
    budget_gb = _vram_budget_gb(hw)  # retained for the rationale only
    if backend == "cpu":
        model_id = _pick_cpu_model()
        model_why = f"{model_id}: CPU-only host — small MIT-licensed fallback"
    else:
        model_id = _pick_chat_model(ram_gb)
        model_why = f"{model_id}: largest curated chat model fitting ~{ram_gb:.0f} GB RAM"

    # Context window resolved from the curated arch max — MoE/MTP primary
    # gets the full window (tiny hybrid KV), dense models are capped. No
    # more flat hard-coded 8192. (#513)
    context_size = _resolve_primary_ctx(model_id)

    # v0.2: emit ``device`` as the canonical hardware-preference field
    # (ADR-0006 §7). ``backend`` is also emitted so a downgrade to
    # v0.1.x can still read the file before re-running the recommender.
    device = map_backend_to_device(backend)

    return {
        "name": "chat",
        "port": 8081,
        "backend": backend,
        "device": device,
        "provider": "llama-server",
        "enabled": False,  # operator runs `hal0 model pull <id>` first
        "model": {
            "default": model_id,
            "context_size": context_size,
        },
        "_meta": {
            "rationale_backend": backend_why,
            "rationale_device": f"{device} (mapped from backend={backend!r})",
            "rationale_model": model_why,
            "rationale_ctx": f"context_size={context_size} (from curated arch max)",
            "host_ram_gb": round(ram_gb, 1),
            "vram_budget_gb": round(budget_gb, 1),
        },
    }


__all__ = ["recommend_primary_slot"]
