"""Single-owner memory envelope for context-window / capacity math (#1868).

hal0 had no one place answering "how much memory can a model+KV-cache
actually spend on this box" — the seeds copied a flat ``context_size =
65536`` verbatim (#1868), the capacity ruler guessed at VRAM/RAM split
independently, and the anchor/extraction context floors had no notion of
what a box could afford at all. This module is that one function; every
other module that needs a memory budget calls it rather than re-deriving
its own fraction.

The formula mirrors ODS's ``usable_memory_gb`` (permission granted by its
author to port; ``ods/scripts/select-model.py:134-144``): a unified-memory
box (APU/UMA — shares RAM with the OS, container runtime and other slots)
only gets a bounded share of total RAM, not the whole pool; a discrete GPU
gets its own VRAM; a CPU-only box gets a small, bounded slice of RAM so the
host and other slots are not starved.
"""

from __future__ import annotations

from dataclasses import dataclass

from hal0.config.schema import GPUInfo, HardwareInfo

#: Share of total RAM budgeted to model weights + KV cache on unified-memory
#: hardware (Strix Halo and any other UMA APU). Mirrors ODS's ratio
#: (``ods/scripts/select-model.py:141``): the pool is shared with the OS,
#: container runtime, and every other slot, so budgeting the full total
#: starves the host under load well before the model itself is the limit.
UNIFIED_MEMORY_FRACTION = 0.55

#: Floor under the unified-memory fraction so a small-RAM UMA box (e.g. an
#: 8 GiB APU) is not handed an unusably tiny envelope by the fraction alone.
UNIFIED_MEMORY_FLOOR_MIB = 2048.0

#: CPU-only bounds (``ods/scripts/select-model.py:143``): a fraction of RAM,
#: clamped to ``[floor, ceiling]`` so a huge-RAM CPU box is not handed the
#: whole pool (OS + container headroom) and a small one still gets a usable
#: minimum.
CPU_MEMORY_FRACTION = 0.35
CPU_MEMORY_FLOOR_MIB = 3072.0
CPU_MEMORY_CEILING_MIB = 8192.0

#: Coarse KV-cache footprint estimate shared with
#: :mod:`hal0.slots.capacity` (``_KV_MIB_PER_1K_TOKENS``): bytes per context
#: token, summed across K and V, for a quantised mid-size model. Deliberately
#: the SAME constant capacity.py already uses for the resident-memory
#: estimate — two different numbers here would make "how much context can
#: this box afford" disagree with "how much memory is this slot using".
KV_MIB_PER_1K_TOKENS = 0.5

#: The platform string that means "this box's GPU memory IS system RAM,
#: shared via GTT" — the same signal
#: :func:`hal0.install.profile_derive.derive_device` already keys the ROCm
#: lane on. Kept local (rather than imported) to dodge an
#: install→hardware import direction; the two are a single string constant
#: apart, not independent logic.
UNIFIED_PLATFORM = "strix-halo"


@dataclass(frozen=True, slots=True)
class MemoryEnvelope:
    """The memory budget available for a model's weights + KV cache."""

    usable_mib: float
    """How much memory (MiB) a model + its context window may spend."""

    source: str
    """Where the budget comes from: ``"unified system memory"`` |
    ``"GPU VRAM"`` | ``"system RAM"`` — echoes the ODS wording so a message
    built off this reads the same on both projects."""


def _primary_capable_gpu(hw: HardwareInfo) -> GPUInfo | None:
    """The first GPU this host can actually run inference on, or ``None``.

    "Capable" mirrors :func:`hal0.slots.capacity._host_has_capable_gpu`:
    Vulkan- or compute-capable. A GPU row with neither (a display-only card,
    or a probe that only got the PCI id) contributes no inference memory.
    """
    for gpu in hw.gpus:
        if gpu.vulkan_capable or gpu.compute_capable:
            return gpu
    return None


def memory_envelope(hw: HardwareInfo) -> MemoryEnvelope:
    """Return the memory budget this host can spend on one model + KV cache.

    Ladder (mirrors ODS's ``usable_memory_gb``):

    1. **Unified memory** (``hw.platform == "strix-halo"``, or a capable GPU
       whose reported VRAM is actually the GTT pool sized like host RAM —
       the generalised UMA APU case): :data:`UNIFIED_MEMORY_FRACTION` of
       total RAM, floored at :data:`UNIFIED_MEMORY_FLOOR_MIB`.
    2. **Discrete GPU** (a capable GPU with its own smaller VRAM pool): the
       GPU's reported ``vram_mb``.
    3. **CPU-only** (no capable GPU at all): :data:`CPU_MEMORY_FRACTION` of
       total RAM, clamped to ``[CPU_MEMORY_FLOOR_MIB, CPU_MEMORY_CEILING_MIB]``.

    The ``FLOOR``/``CEILING`` constants are bounds on the FRACTION, never a
    promise about the box: both branches also cap at ``hw.ram_mb`` itself, so
    a box smaller than the floor (a sub-3 GiB CPU-only VM, say) never gets
    handed an envelope bigger than its actual RAM.

    Never raises — an all-defaults ``HardwareInfo()`` (no probe yet) reads
    as CPU-only, which is the conservative floor rather than an invented
    GPU pool.
    """
    gpu = _primary_capable_gpu(hw)
    is_unified = hw.platform == UNIFIED_PLATFORM or (
        gpu is not None and gpu.vram_mb > 0 and gpu.vram_mb >= hw.ram_mb * 0.9
    )
    if is_unified and hw.ram_mb > 0:
        usable = min(max(hw.ram_mb * UNIFIED_MEMORY_FRACTION, UNIFIED_MEMORY_FLOOR_MIB), hw.ram_mb)
        return MemoryEnvelope(usable, "unified system memory")
    if gpu is not None and gpu.vram_mb > 0:
        return MemoryEnvelope(float(gpu.vram_mb), "GPU VRAM")
    usable = min(
        max(hw.ram_mb * CPU_MEMORY_FRACTION, CPU_MEMORY_FLOOR_MIB),
        CPU_MEMORY_CEILING_MIB,
        float(hw.ram_mb) if hw.ram_mb > 0 else CPU_MEMORY_FLOOR_MIB,
    )
    return MemoryEnvelope(usable, "system RAM")


def max_affordable_context_tokens(
    hw: HardwareInfo,
    *,
    model_mib: float = 0.0,
    kv_mib_per_1k_tokens: float = KV_MIB_PER_1K_TOKENS,
) -> int:
    """How many context tokens' worth of KV cache this box's envelope affords.

    ``model_mib`` is the model weights' footprint (0.0 when unknown — the
    seed-time caller has no loaded model to measure yet, so the whole
    envelope is treated as available for KV). Never negative; a box whose
    envelope is smaller than the model itself affords 0 tokens rather than
    a negative number.
    """
    envelope = memory_envelope(hw)
    remaining_mib = max(envelope.usable_mib - model_mib, 0.0)
    if kv_mib_per_1k_tokens <= 0:
        return 0
    return int((remaining_mib / kv_mib_per_1k_tokens) * 1000)


def clamp_context_size(
    requested_tokens: int,
    hw: HardwareInfo,
    *,
    floor_tokens: int = 0,
    model_mib: float = 0.0,
) -> tuple[int, str | None]:
    """Clamp a slot's ``context_size`` to what this box's envelope affords.

    Returns ``(clamped_tokens, warning)``. ``warning`` is ``None`` when
    ``requested_tokens`` already fit; otherwise it names the envelope and the
    value it was clamped to, for a caller to log or hand to ``hal0 doctor``.

    The clamp never goes below ``floor_tokens`` (a hard requirement — e.g.
    Hermes' context floor for a chat-capable slot, #1868's "a box that
    cannot afford 64K should say so loudly rather than silently seeding
    less"): when the affordable ceiling is itself under the floor, the
    caller gets ``floor_tokens`` back plus a warning that says the box
    cannot actually afford it, rather than a silently-shrunk value that
    would make the slot un-chattable again (#1827).
    """
    if requested_tokens <= 0:
        return requested_tokens, None
    affordable = max_affordable_context_tokens(hw, model_mib=model_mib)
    envelope = memory_envelope(hw)
    if requested_tokens <= affordable:
        return requested_tokens, None
    clamped = max(affordable, floor_tokens) if floor_tokens > 0 else affordable
    clamped = max(clamped, 0)
    if floor_tokens > 0 and affordable < floor_tokens:
        return (
            floor_tokens,
            (
                f"context_size {requested_tokens:,} exceeds this box's "
                f"{envelope.usable_mib / 1024.0:.1f} GiB memory envelope "
                f"({envelope.source}) — even the {floor_tokens:,}-token floor may "
                f"not fit; consider a smaller model or more memory"
            ),
        )
    return (
        clamped,
        (
            f"context_size {requested_tokens:,} exceeds this box's "
            f"{envelope.usable_mib / 1024.0:.1f} GiB memory envelope "
            f"({envelope.source}) — clamped to {clamped:,}"
        ),
    )


__all__ = [
    "CPU_MEMORY_CEILING_MIB",
    "CPU_MEMORY_FLOOR_MIB",
    "CPU_MEMORY_FRACTION",
    "KV_MIB_PER_1K_TOKENS",
    "UNIFIED_MEMORY_FLOOR_MIB",
    "UNIFIED_MEMORY_FRACTION",
    "UNIFIED_PLATFORM",
    "MemoryEnvelope",
    "clamp_context_size",
    "max_affordable_context_tokens",
    "memory_envelope",
]
