"""hal0.slots.npu — NPU FLM-trio shadow reconciler (P3-slots §1d).

See :mod:`hal0.slots.npu.trio` for the predicate + reconciler. Re-exported
here so ``from hal0.slots.npu import is_npu_trio_shadow`` works alongside
the historical ``from hal0.slots.manager import is_npu_trio_shadow``.
"""

from __future__ import annotations

from hal0.slots.npu.trio import NpuTrioHost, is_npu_trio_shadow, reconcile_trio_slots

__all__ = [
    "NpuTrioHost",
    "is_npu_trio_shadow",
    "reconcile_trio_slots",
]
