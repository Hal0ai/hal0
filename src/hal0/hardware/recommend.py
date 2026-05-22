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

# Curated chat models suitable for the primary slot, ordered from
# largest-context / most capable down to the smallest. We pick the
# largest model whose ``vram_gb_min`` fits the detected hardware. All
# three live in :mod:`hal0.registry.curated`; cross-referenced below.
_PRIMARY_TIERS: list[tuple[str, float]] = [
    # (curated_id, vram_gb_min) — must mirror CURATED_MODELS in
    # src/hal0/registry/curated.py. Keep this sorted descending by
    # capability, since recommend_primary_slot iterates and takes the
    # first that fits.
    ("qwen3-4b", 4.0),
    ("phi3-mini", 3.0),
    ("llama32-3b", 3.0),
]

# Strix Halo (UMA) is detected as an AMD GPU with no separate VRAM
# carve-out, where the model-loading pool comes from GTT. The unified
# memory pool is essentially "host RAM the GPU can address." We treat
# anything ≥ this many GB as "Strix Halo class" — enough headroom to
# justify Vulkan over CPU even when there's no discrete VRAM.
_UMA_UNIFIED_GB_MIN = 32


def _pick_chat_model(available_gb: float) -> str:
    """Return the curated id of the largest chat model that fits."""
    for cid, min_gb in _PRIMARY_TIERS:
        if available_gb + 0.01 >= min_gb:
            return cid
    # Fallback: smallest entry. Better to install something downloadable
    # than to ship an empty default field.
    return _PRIMARY_TIERS[-1][0]


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
    budget_gb = _vram_budget_gb(hw)
    model_id = _pick_chat_model(budget_gb)

    # Context size: Qwen3 supports 32k natively, Llama 3.2 a wild 128k,
    # Phi-3 only 4k. Default to 8k for the primary slot — long enough for
    # most chats, short enough to keep KV cache predictable. Operators
    # can bump it in the rendered TOML.
    context_size = 8192

    # v0.2: emit ``device`` as the canonical hardware-preference field
    # (ADR-0006 §7). ``backend`` is also emitted so a downgrade to
    # v0.1.x can still read the file before re-running the recommender.
    device = map_backend_to_device(backend)

    return {
        "name": "primary",
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
            "rationale_model": (
                f"{model_id}: largest curated chat model fitting ~{budget_gb:.1f} GB budget"
            ),
            "vram_budget_gb": round(budget_gb, 1),
        },
    }


__all__ = ["recommend_primary_slot"]
