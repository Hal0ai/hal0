"""The GTT-leak rule: when unexplained GPU memory means "reboot the host".

An engine that dies badly can leave tens of GiB of GPU memory held with no
process behind it (halogen-flash-server issue #79, the upstream report). The
memory comes back only on a host reboot, and until then the next weight
allocation fails deep inside the engine with an error that says nothing about
the cause. :func:`gtt_leak_message` names the state before a unit is written;
:meth:`hal0.slots.manager.SlotManager._guard_gtt_leak` enforces it at load and
the ``gtt_leak`` row of ``hal0 doctor all`` reports it.

Pure on purpose: no I/O, every figure passed in, so the manager and the doctor
row share one rule and the rule is testable without a GPU.

The rule is narrow because a false positive tells an operator to reboot a
healthy box. Memory counts as leaked only when ALL of these hold:

* the amdgpu GTT counter exceeds what resident slots account for by more than
  :data:`GTT_LEAK_THRESHOLD_MB`;
* no GPU slot is resident (or loading) at all; and
* no process holds the compute device (``/sys/class/kfd/kfd/proc`` is empty).

The middle condition is load-bearing. A Vulkan slot holds its GTT through the
DRM render node and never appears under the KFD proc table, so "GPU memory in
use and no KFD process" is what every healthy Vulkan-only box looks like. Only
the absence of any slot that could hold the memory separates a leak from that.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Unexplained GPU memory below this is ordinary slack (compositor buffers, a
#: desktop session, a model another tool loaded), not a leaked engine.
GTT_LEAK_THRESHOLD_MB = 8 * 1024

#: KFD ``gfx_target_version`` of Strix Halo's iGPU (gfx1151).
GFX1151 = 110501

#: Slot states in which a slot holds, or is about to hold, GPU memory. The same
#: states ``capacity.build_per_slot(include_starting=True)`` counts; a test pins
#: the two together.
_HOLDING_STATES = frozenset({"starting", "warming", "ready", "serving", "idle"})

#: Effective-backend tokens of slots that hold no GTT: CPU weights live in system
#: RAM and the NPU's footprint is not GTT. Counting either as a GPU holder would
#: hide a real leak on exactly the boxes that run an FLM slot.
_NON_GPU_BACKENDS = frozenset({"cpu", "flm", "npu"})


def _gib(mb: float) -> str:
    """``mb`` MiB as GiB, one decimal only when it carries one (``40``, ``8.5``)."""
    text = f"{mb / 1024.0:.1f}"
    return text[:-2] if text.endswith(".0") else text


def gtt_leak_message(
    *,
    gtt_used_mb: float | None,
    resident_gpu_mb: float | None,
    resident_gpu_slots: int | None,
    kfd_procs: int | None,
) -> str | None:
    """The refusal text when GPU memory is leaked, else ``None``.

    ``gtt_used_mb`` is the amdgpu GTT counter, ``resident_gpu_mb`` and
    ``resident_gpu_slots`` what resident GPU slots account for, ``kfd_procs`` the
    number of processes holding the compute device. Any input that is ``None``
    (the figure could not be read) returns ``None``: the guard refuses a load
    only on proof, never on a gap in the data.
    """
    if gtt_used_mb is None or resident_gpu_mb is None:
        return None
    if resident_gpu_slots is None or kfd_procs is None:
        return None
    unexplained_mb = gtt_used_mb - resident_gpu_mb
    if unexplained_mb <= GTT_LEAK_THRESHOLD_MB:
        return None
    if resident_gpu_slots != 0 or kfd_procs != 0:
        return None
    return (
        f"{_gib(unexplained_mb)} GiB of GPU memory is held that no hal0 slot accounts "
        "for and no process holds the GPU: memory a previous GPU process did not give "
        "back (halogen-flash-server issue #79). A host reboot is required before this "
        "slot can start."
    )


def _field(slot: Any, name: str) -> Any:
    """``slot[name]`` for an API dict, ``slot.name`` for a ``Slot``."""
    if isinstance(slot, Mapping):
        return slot.get(name)
    return getattr(slot, name, None)


def slot_may_hold_gpu_memory(slot: Any) -> bool:
    """True when ``slot`` is up (or coming up) on something that holds GTT.

    Accepts a :class:`~hal0.slots.manager.Slot` or the dict ``GET /api/slots``
    returns for one. A backend that cannot be read counts as a holder: the guard
    treats the unprovable as "may hold", so it refuses a load on proof only.
    """
    state = _field(slot, "state")
    state = str(getattr(state, "value", state) or "").lower()
    if state not in _HOLDING_STATES:
        return False
    metadata = _field(slot, "metadata")
    meta = metadata if isinstance(metadata, Mapping) else {}
    backend = str(_field(slot, "backend") or meta.get("backend") or "").lower()
    provider = str(meta.get("provider") or "").lower()
    return backend not in _NON_GPU_BACKENDS and provider != "flm"


__all__ = [
    "GFX1151",
    "GTT_LEAK_THRESHOLD_MB",
    "gtt_leak_message",
    "slot_may_hold_gpu_memory",
]
