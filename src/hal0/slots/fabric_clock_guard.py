"""The GPU fabric-clock hazard: NPU work beside GPU work on Strix Halo.

On AMD Strix Halo (gfx1151 iGPU + XDNA2 NPU), running NPU inference while the
GPU is also active can hang the machine or corrupt NPU results when the GPU's
fabric clock (fclk) changes DPM level mid-flight (upstream
ROCm/TheRock#8709; a fix is expected in BIOS/SMU firmware, not in hal0). The
known mitigation holds the fabric clock pinned at its top DPM level —
``power_dpm_force_performance_level=manual`` with the last ``pp_dpm_fclk``
level selected, or ``power_dpm_force_performance_level=high`` — for as long as
NPU and GPU slots may run together.

hal0 runs inside a privileged LXC on Proxmox. The sysfs files that control the
clock (``/sys/class/drm/cardN/device/power_dpm_force_performance_level`` and
``pp_dpm_fclk``) are READABLE but NOT writable in the container: the hold must
be applied on the Proxmox HOST (``scripts/proxmox-ve/hal0-fabric-clock``,
installed as a systemd oneshot). This module only verifies the state from
inside the guest and composes the operator-facing message; it never writes to
sysfs. Pure on purpose, mirroring :mod:`hal0.slots.gtt_guard`: no I/O beyond
the explicit ``sys_root`` passed in by the caller, so the manager hook and the
doctor check share one rule that is testable without the hardware.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Slot backend tokens that mean "this slot runs on the NPU": the ``flm``
#: recipe, the ggml-xdna runner (ADR-0057; its Runner entry reports backend
#: ``npu`` and the slot listing does not always carry ``device_class``), and
#: ``xdna2``/``xdna`` as spellings of the same hardware. The fclk hazard is a
#: property of any NPU workload, not of one engine.
_NPU_BACKENDS = frozenset({"flm", "npu", "xdna", "xdna2"})

#: Slot backend tokens that mean "this slot runs on the GPU" (gfx1151 iGPU).
_GPU_BACKENDS = frozenset({"vulkan", "rocm", "hip"})


@dataclass(frozen=True)
class FabricClockState:
    """A read of one amdgpu device's fabric-clock controls.

    ``levels`` is the raw ``pp_dpm_fclk`` lines (``"0: 400Mhz"``, …);
    ``active_index`` the index carrying the ``*`` marker, ``top_index`` the
    highest index available. ``held`` is true exactly when
    ``power_dpm_force_performance_level`` is ``high``, or is ``manual`` with
    exactly one active marker and it sits on the top level — the same
    predicate the host-side script and ``halogen-fabric-clock`` use.
    ``readable`` is false when the device or its control files could not be
    read at all (unreadable means neither held nor not-held: a doctor/guard
    caller must treat it as "cannot be told", never as a hazard).
    """

    device: str | None
    perf_level: str | None
    levels: tuple[str, ...] = field(default_factory=tuple)
    active_index: int | None = None
    top_index: int | None = None
    held: bool = False
    readable: bool = False


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _parse_levels(raw: str) -> tuple[tuple[str, ...], int | None, int | None]:
    """``("0: 400Mhz", "1: 800Mhz*", …) -> (lines, active_index, top_index)``.

    ``active_index`` is only ever a single, unambiguous marker: a tree with
    zero or more than one ``*`` marker (e.g. a torn read, or firmware
    reporting two active levels) is treated as having no reliable active
    index at all, mirroring the shell side's ``exactly one`` requirement.
    """
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    active_indexes: list[int] = []
    top_index: int | None = None
    for i, line in enumerate(lines):
        head = line.split(":", 1)[0].strip()
        if head.isdigit():
            top_index = i if top_index is None or i > top_index else top_index
        if "*" in line:
            active_indexes.append(i)
    active_index = active_indexes[0] if len(active_indexes) == 1 else None
    return tuple(lines), active_index, top_index


def _soc_device(sys_root: Path) -> Path | None:
    """The gfx1151 amdgpu device by KFD topology, else ``None``.

    Mirrors ``soc_gpu()`` in ``scripts/proxmox-ve/hal0-fabric-clock``: reads
    ``gfx_target_version`` from every KFD topology node, matches a Strix Halo
    value (``1105xx``), and maps its ``drm_render_minor`` to a
    ``/sys/class/drm/cardN/device`` directory via the render node symlink.
    """
    nodes_dir = sys_root / "class" / "kfd" / "kfd" / "topology" / "nodes"
    if not nodes_dir.is_dir():
        return None
    try:
        node_dirs = sorted(nodes_dir.iterdir())
    except OSError:
        return None
    for node in node_dirs:
        props = _read_text(node / "properties")
        if not props:
            continue
        values: dict[str, str] = {}
        for line in props.splitlines():
            parts = line.split()
            if len(parts) == 2:
                values[parts[0]] = parts[1]
        gfx = values.get("gfx_target_version", "")
        if not (gfx.isdigit() and gfx.startswith("1105")):
            continue
        minor = values.get("drm_render_minor", "")
        if not minor.isdigit():
            continue
        render_device = sys_root / "class" / "drm" / f"renderD{minor}" / "device"
        try:
            target = render_device.resolve()
        except OSError:
            continue
        if target.exists():
            return target
    return None


def _candidate_devices(sys_root: Path) -> list[Path]:
    drm = sys_root / "class" / "drm"
    if not drm.is_dir():
        return []
    out = []
    try:
        cards = sorted(drm.glob("card*/device"))
    except OSError:
        return []
    for device in cards:
        if (device / "pp_dpm_fclk").exists() and (
            device / "power_dpm_force_performance_level"
        ).exists():
            out.append(device)
    return out


def read_fabric_clock(sys_root: str | Path = "/sys") -> FabricClockState:
    """Read the fabric-clock hold state for this box's SoC GPU (or the first readable amdgpu device).

    Prefers the device KFD topology names as the gfx1151 SoC GPU; falls back
    to the first ``/sys/class/drm/card*/device`` that exposes both control
    files when the topology is absent (a container, or a non-Strix-Halo box).
    Returns ``readable=False`` when nothing with a fabric-clock control could
    be found or read — never raises.
    """
    root = Path(sys_root)
    device = _soc_device(root)
    if device is None:
        candidates = _candidate_devices(root)
        device = candidates[0] if candidates else None
    if device is None:
        return FabricClockState(device=None, perf_level=None)

    perf = _read_text(device / "power_dpm_force_performance_level")
    fclk_raw = _read_text(device / "pp_dpm_fclk")
    if perf is None or fclk_raw is None:
        return FabricClockState(device=str(device), perf_level=perf, readable=False)

    levels, active_index, top_index = _parse_levels(fclk_raw)
    held = perf == "high" or (
        perf == "manual"
        and active_index is not None
        and top_index is not None
        and active_index == top_index
    )
    return FabricClockState(
        device=str(device),
        perf_level=perf,
        levels=levels,
        active_index=active_index,
        top_index=top_index,
        held=held,
        readable=True,
    )


_HOST_INSTALL_COMMANDS = (
    "sudo install -m 755 scripts/proxmox-ve/hal0-fabric-clock /usr/local/sbin/ && "
    "sudo install -m 644 scripts/proxmox-ve/hal0-fabric-clock.service /etc/systemd/system/ && "
    "sudo systemctl daemon-reload && sudo systemctl enable --now hal0-fabric-clock.service"
)

_HOST_ONE_OFF_COMMAND = "hal0-fabric-clock hold"


def fabric_clock_message(
    state: FabricClockState,
    npu_slot: str | None,
    gpu_slots: Iterable[str],
) -> str:
    """The operator message for an unheld (or unreadable) fabric clock.

    Names the NPU slot and the GPU slot(s) that would run alongside it, and
    the exact commands to run ON THE PROXMOX HOST — hal0's guest container can
    read the sysfs files but never write them (``state.readable`` says
    whether this read could tell).
    """
    gpu_names = ", ".join(gpu_slots) or "a GPU slot"
    npu_name = npu_slot or "an NPU slot"
    if state.readable and state.held:
        return (
            f"fabric clock is held ({state.perf_level!r}); NPU slot {npu_name!r} and GPU slot(s) "
            f"{gpu_names} may run together safely."
        )
    if not state.readable:
        return (
            f"fabric-clock state could not be read for NPU slot {npu_name!r} alongside GPU "
            f"slot(s) {gpu_names}: this is normal inside hal0's container (the sysfs files are "
            "host-only); the hazard (ROCm/TheRock#8709) cannot be ruled out. On the PROXMOX "
            f"HOST, install the hold once: `{_HOST_INSTALL_COMMANDS}` — or hold it for this "
            f"session only with `{_HOST_ONE_OFF_COMMAND}`."
        )
    return (
        f"NPU slot {npu_name!r} would run alongside GPU slot(s) {gpu_names} with the GPU fabric "
        f"clock NOT held (power_dpm_force_performance_level={state.perf_level!r}): on Strix Halo "
        "this can hang the machine or corrupt NPU results when the clock changes DPM level "
        "mid-flight (ROCm/TheRock#8709). On the PROXMOX HOST (not in this container — the sysfs "
        f"write is host-only), run once: `{_HOST_INSTALL_COMMANDS}` — or for this session only: "
        f"`{_HOST_ONE_OFF_COMMAND}`. Costs about 1 W of idle power while held."
    )


def _field(slot: Any, name: str) -> Any:
    """``slot[name]`` for an API dict, ``slot.name`` for a ``Slot``."""
    if isinstance(slot, Mapping):
        return slot.get(name)
    return getattr(slot, name, None)


def _backend_tokens(slot: Any) -> tuple[str, str]:
    """``(backend, device_class)`` lower-cased, from either slot shape."""
    metadata = _field(slot, "metadata")
    meta = metadata if isinstance(metadata, Mapping) else {}
    backend = str(_field(slot, "backend") or meta.get("backend") or "").lower()
    device_class = str(_field(slot, "device_class") or meta.get("device_class") or "").lower()
    return backend, device_class


def slot_uses_npu(slot: Any) -> bool:
    """True when ``slot`` runs on the NPU (FLM or the ggml-xdna runner)."""
    backend, device_class = _backend_tokens(slot)
    return backend in _NPU_BACKENDS or device_class == "npu"


def slot_uses_gpu(slot: Any) -> bool:
    """True when ``slot`` runs on the GPU (vulkan/rocm/hip, i.e. the gfx1151 iGPU)."""
    backend, device_class = _backend_tokens(slot)
    if device_class == "npu" or backend in _NPU_BACKENDS:
        return False
    if backend == "cuda":
        # CUDA runs on a discrete NVIDIA GPU, not the gfx1151 iGPU whose
        # fabric clock this guard protects; never fold it into the generic
        # device_class == "gpu" fallback below.
        return False
    if device_class == "gpu":
        return True
    return backend in _GPU_BACKENDS


__all__ = [
    "FabricClockState",
    "fabric_clock_message",
    "read_fabric_clock",
    "slot_uses_gpu",
    "slot_uses_npu",
]
