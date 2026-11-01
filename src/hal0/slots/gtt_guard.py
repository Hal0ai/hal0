"""The GTT-leak signature: when unexplained GPU memory may mean "reboot the host".

An engine that dies badly can leave tens of GiB of GPU memory held with no
process behind it (halogen-flash-server issue #79, the upstream report). The
memory comes back only on a host reboot, and a start on top of it can hang at
"reserving the KV pool" with nothing in the log naming the cause.
:func:`gtt_leak_message` names the suspicion. :meth:`hal0.slots.manager.SlotManager._guard_gtt_leak`
warns with it before a load and the ``gtt_leak`` row of ``hal0 doctor all`` reports it.

It is a WARNING, never a refusal, because the signature can never be certain.
The amdgpu GTT counter and the KFD proc table are host-wide while hal0's slot
list covers one guest; on a Proxmox host with several LXC guests sharing the
GPU, a sibling guest's Vulkan slot holds GTT this guest cannot account for and
that never appears in ``/sys/class/kfd/kfd/proc``. The message says so.

Pure on purpose: no I/O, every figure passed in, so the manager and the doctor
row share one rule and the rule is testable without a GPU.

The rule stays narrow so the warning is rare. Memory counts as unexplained only
when ALL of these hold:

* the amdgpu GTT counter exceeds what resident slots account for by more than
  :data:`GTT_LEAK_THRESHOLD_MB`;
* no GPU slot of this box can hold it (none resident, loading or unloading); and
* no process holds the compute device (``/sys/class/kfd/kfd/proc`` is empty).

The middle condition is load-bearing. A Vulkan slot holds its GTT through the
DRM render node and never appears under the KFD proc table, so "GPU memory in
use and no KFD process" is what every healthy Vulkan-only box looks like. Only
the absence of any slot that could hold the memory separates a suspected leak
from that.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Unexplained GPU memory below this is ordinary slack (compositor buffers, a
#: desktop session, a model another tool loaded), not a leaked engine.
GTT_LEAK_THRESHOLD_MB = 8 * 1024

#: KFD ``gfx_target_version`` of Strix Halo's iGPU (gfx1151).
GFX1151 = 110501

#: Slot states in which a slot always holds, or is about to hold, GPU memory: the
#: states ``capacity.build_per_slot(include_starting=True)`` counts, plus
#: ``unloading`` (the stop is issued but the process may still be up). A test pins
#: the two together. ``error`` is conditional, see :func:`slot_may_hold_gpu_memory`.
_HOLDING_STATES = frozenset({"starting", "warming", "ready", "serving", "idle", "unloading"})

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
    """The warning text when GPU memory looks leaked, else ``None``.

    ``gtt_used_mb`` is the amdgpu GTT counter, ``resident_gpu_mb`` and
    ``resident_gpu_slots`` what this box's GPU slots account for, ``kfd_procs``
    the number of processes holding the compute device. Any input that is
    ``None`` (the figure could not be read) returns ``None``: a gap in the data
    is never a suspicion. The text names both explanations, because a sibling
    guest sharing the GPU produces the same signature as a real leak.
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
        f"{_gib(unexplained_mb)} GiB of GPU memory is held that no slot on this box accounts "
        "for and no process holds the GPU: either another guest on this host holds it, or a "
        "previous GPU process did not give it back (halogen-flash-server issue #79). If no "
        "other guest runs GPU work, a host reboot frees it; a start on top of a real leak can "
        "hang at 'reserving the KV pool'."
    )


def _field(slot: Any, name: str) -> Any:
    """``slot[name]`` for an API dict, ``slot.name`` for a ``Slot``."""
    if isinstance(slot, Mapping):
        return slot.get(name)
    return getattr(slot, name, None)


def slot_may_hold_gpu_memory(slot: Any, *, unit_active: bool = False) -> bool:
    """True when ``slot`` is up, coming up or going down on something that holds GTT.

    Accepts a :class:`~hal0.slots.manager.Slot` or the dict ``GET /api/slots``
    returns for one. ``unloading`` counts: the stop is issued but the process may
    still be up. ``error`` counts only when the process may still be alive, which
    is when the slot carries ``slow_stop`` (hal0 stopped waiting on a teardown that
    may still finish) or the caller says the unit is active (``unit_active``, a
    probe only the manager can make). A crashed engine in ``error`` whose process
    is gone is the leak case, so it must NOT shield the box.

    A backend that cannot be read counts as a holder: unprovable reads as "may
    hold", so the warning fires on a clear signature only.
    """
    state = _field(slot, "state")
    state = str(getattr(state, "value", state) or "").lower()
    metadata = _field(slot, "metadata")
    meta = metadata if isinstance(metadata, Mapping) else {}
    if state == "error":
        if not (meta.get("slow_stop") or unit_active):
            return False
    elif state not in _HOLDING_STATES:
        return False
    backend = str(_field(slot, "backend") or meta.get("backend") or "").lower()
    provider = str(meta.get("provider") or "").lower()
    return backend not in _NON_GPU_BACKENDS and provider != "flm"


__all__ = [
    "GFX1151",
    "GTT_LEAK_THRESHOLD_MB",
    "gtt_leak_message",
    "slot_may_hold_gpu_memory",
]
