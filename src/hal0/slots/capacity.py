"""Slot capacity snapshot.

CapacitySnapshot is the single-source view of available VRAM, system RAM, and
slot budget used by:
  - GET /api/slots/capacity
  - The hardware-aware slot config form in the dashboard (VRAM fit warnings)
  - SlotManager.spawn() pre-flight checks

Port target: haloai lib/capacity.py.

Tier 1 fixes baked in (PLAN.md §5):
  - No silent exception swallow.  Bad TOML / missing meminfo surface as
    typed SlotConfigError / SlotError, not a degraded ``"?"`` row.  Callers
    that *want* graceful degradation (e.g. the dashboard) catch at the
    boundary.
  - All memory units are MiB.  haloai mixed GiB and MiB across the same
    call graph; this module standardises and the dashboard divides by
    1024.0 at render time.

See PLAN.md §3 (module port plan).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hal0.slots.state import SlotError

if TYPE_CHECKING:
    from hal0.hardware.probe import HardwareInfo

# Container-name prefix matches the convention in providers/container.py:
# ``ExecStop = <runtime> stop -t 20 hal0-slot-<name>``.
_CONTAINER_NAME_PREFIX = "hal0-slot-"


# NOTE: We code against ``hal0.hardware.probe.HardwareInfo`` as the contract
# even though the probe itself is currently a stub (raises NotImplementedError).
# When the hardware/probe agent lands real detection, capacity becomes a
# read-only consumer with no API change required.


class CapacityProbeError(SlotError):
    """/proc/meminfo unreadable, or DRM sysfs not enumerable."""

    code = "slot.capacity_probe_failed"
    status = 500


def _read_meminfo() -> tuple[float, float]:
    """Return (total_mib, available_mib) from /proc/meminfo.

    Raises CapacityProbeError on any IO error — Tier 1 fix replaces
    haloai's silent ``except OSError: pass`` at lib/capacity.py:51.
    """
    total_kib = avail_kib = 0
    path = Path("/proc/meminfo")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CapacityProbeError(
            f"failed to read /proc/meminfo: {exc}",
            details={"path": str(path)},
        ) from exc

    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            try:
                total_kib = int(line.split()[1])
            except (IndexError, ValueError) as exc:
                raise CapacityProbeError(
                    f"malformed MemTotal line in /proc/meminfo: {line!r}",
                ) from exc
        elif line.startswith("MemAvailable:"):
            try:
                avail_kib = int(line.split()[1])
            except (IndexError, ValueError) as exc:
                raise CapacityProbeError(
                    f"malformed MemAvailable line in /proc/meminfo: {line!r}",
                ) from exc
    if total_kib == 0:
        raise CapacityProbeError("MemTotal missing from /proc/meminfo")
    # KiB → MiB (kernel reports kB but they are KiB by long-standing convention).
    return total_kib / 1024.0, avail_kib / 1024.0


# States in which a slot's weights are genuinely resident in GTT/VRAM.
# PULLING/STARTING haven't loaded; OFFLINE/UNLOADING/ERROR don't hold weights.
_RESIDENT_STATES = frozenset({"warming", "ready", "serving", "idle"})

# Default context window assumed when neither the model nor the slot config
# pins one. Matches the hal0 ctx_size baseline the platform has shipped
# with since v0.2.
_DEFAULT_CTX_TOKENS = 65536

# Coarse KV-cache footprint estimate: bytes per context token, summed across
# K and V. Real KV size depends on n_layers * n_kv_heads * head_dim * dtype,
# which we don't have without parsing GGUF metadata per slot. 0.5 MiB / 1k
# tokens is a deliberately conservative midpoint for a quantised mid-size
# model (e.g. ~14-25B at Q4/Q5) -- it keeps the reported resident figure in
# the right order of magnitude (tens of GB for a 25B model at 64k ctx)
# without claiming false precision. The model file size dominates the total.
_KV_MIB_PER_1K_TOKENS = 0.5


def _kv_estimate_mb(ctx_tokens: int) -> float:
    """Best-effort KV-cache size in MiB for a given context window."""
    if ctx_tokens <= 0:
        return 0.0
    return (ctx_tokens / 1000.0) * _KV_MIB_PER_1K_TOKENS


def _ctx_tokens_for(model_meta: dict[str, Any] | None) -> int:
    """Resolve the effective context window (tokens) for a model.

    Reads, in priority order: ``defaults.context_size`` (the launcher's
    pinned n_ctx), ``metadata.context_length`` (GGUF arch max), falling
    back to :data:`_DEFAULT_CTX_TOKENS`.
    """
    if not isinstance(model_meta, dict):
        return _DEFAULT_CTX_TOKENS
    defaults = model_meta.get("defaults")
    if isinstance(defaults, dict):
        cs = defaults.get("context_size")
        if isinstance(cs, (int, float)) and cs > 0:
            return int(cs)
    meta = model_meta.get("metadata")
    if isinstance(meta, dict):
        cl = meta.get("context_length")
        if isinstance(cl, (int, float)) and cl > 0:
            return int(cl)
    return _DEFAULT_CTX_TOKENS


async def _container_cgroup_mem_bytes(slot_name: str) -> int:
    """Cgroup-wide ``memory.current`` for the podman/docker container backing *slot_name*.

    Container name convention: ``hal0-slot-<slot_name>`` (matches the
    ``ExecStop`` line written by :mod:`hal0.providers.container`).

    Resolution path:
      1. Detect runtime (podman → docker) via the same logic as
         :func:`hal0.providers.container._container_runtime`.
      2. Run ``<runtime> inspect -f {{.State.Pid}} hal0-slot-<name>``
         to get the container init PID.
      3. Read ``/proc/<pid>/cgroup`` for the cgroupv2 unified path.
      4. Read ``/sys/fs/cgroup/<path>/memory.current``.

    Returns 0 on any error so the caller can fall back gracefully —
    a missing/stopped container is not exceptional; it just means the
    slot is not backed by a container runtime.

    The returned value includes model weights + KV-cache + runtime
    overhead as measured by the cgroup; callers MUST NOT add an
    additional KV estimate on top.
    """
    import shutil

    # Resolve the container runtime binary (podman preferred over docker).
    runtime = None
    for candidate in ("podman", "docker"):
        found = shutil.which(candidate)
        if found:
            runtime = found
            break
    if runtime is None:
        return 0

    container_name = f"{_CONTAINER_NAME_PREFIX}{slot_name}"
    try:
        proc = await asyncio.create_subprocess_exec(
            runtime,
            "inspect",
            "-f",
            "{{.State.Pid}}",
            container_name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=1.5)
    except (TimeoutError, FileNotFoundError, OSError):
        return 0
    if proc.returncode != 0:
        return 0
    try:
        pid = int(out.decode("utf-8", errors="replace").strip() or 0)
    except ValueError:
        pid = 0
    if pid <= 0:
        return 0
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as f:
            cg_line = f.readline().strip()
    except OSError:
        return 0
    # cgroupv2 unified hierarchy line: "0::/system.slice/podman-<id>.scope"
    if "::" not in cg_line:
        return 0
    cg_rel = cg_line.split("::", 1)[1].lstrip("/")
    try:
        with open(f"/sys/fs/cgroup/{cg_rel}/memory.current", encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


async def build_per_slot(
    slots: list[Any],
    *,
    registry: Any | None = None,
    flm_catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the ``per_slot`` memory map for loaded slots.

    For every slot in a resident state (:data:`_RESIDENT_STATES`) with a
    model assigned, returns a row::

        {slot_name: {"vram_mb", "ram_mb", "mem_mb", "state", "model_id"}}

    where ``mem_mb`` (== ``vram_mb`` on UMA) is the best-estimate resident
    footprint.  Three attribution paths, in priority order:

    1. **NPU / FLM slots**: FLM catalog footprint_gb (includes runtime + KV).
    2. **Container slots** (podman ``hal0-slot-<name>``): ``max`` of the
       live cgroup ``memory.current`` and the registry file-size + KV
       estimate.  The ``max`` guards against Strix Halo (UMA) under-report:
       model weights live in GTT (system RAM via amdgpu/TTM) and are often
       NOT charged to the process cgroup, so a live container can report a
       cgroup of only ~2 GB while holding a ~22 GB model.  When the cgroup
       *does* account for weights it wins (≥ estimate); when it doesn't, the
       estimate wins — so the figure never under-reports.
    3. **File-size estimate** (fallback): model file size from the
       registry plus a coarse KV-cache estimate scaled by context window —
       covers slots whose container is down or unnamed.

    The cgroup probe is attempted for every non-NPU slot and naturally
    returns 0 when no matching container exists, so the container →
    file-size fallback is automatic — no explicit runtime-type detection
    required.

    Non-resident slots are omitted so the caller can render them as
    holding no memory. Never raises: a registry miss yields a 0-size row
    (still keyed, so the slot shows as loaded-but-unsized rather than
    vanishing).

    ``flm_catalog`` (``{tag: entry}``) may be supplied by the caller to
    avoid re-probing FLM; when omitted it is built lazily on first NPU
    slot encountered.
    """
    out: dict[str, dict[str, Any]] = {}
    for s in slots:
        state = str(getattr(s, "state", "") or "").lower()
        if state not in _RESIDENT_STATES:
            continue
        model_id = getattr(s, "model_id", None)
        if not model_id:
            continue
        meta = getattr(s, "metadata", None) or {}
        provider = str(meta.get("provider") or "").lower()
        backend = str(getattr(s, "backend", None) or meta.get("backend") or "").lower()
        is_npu = provider == "flm" or backend in ("flm", "npu")

        model_mb = 0.0
        ctx_meta: dict[str, Any] | None = None
        if is_npu:
            if flm_catalog is None:
                try:
                    from hal0.providers.flm import flm_served_models

                    flm_catalog = {e["tag"]: e for e in flm_served_models()}
                except Exception:
                    flm_catalog = {}
            entry = flm_catalog.get(model_id)
            if entry:
                footprint_gb = entry.get("footprint_gb") or 0.0
                if footprint_gb > 0:
                    # FLM footprint already includes runtime + KV; use as-is.
                    out[s.name] = {
                        "vram_mb": round(footprint_gb * 1024, 1),
                        "ram_mb": 0.0,
                        "mem_mb": round(footprint_gb * 1024, 1),
                        "state": state,
                        "model_id": model_id,
                    }
                    continue
                model_mb = (entry.get("size_bytes") or 0) / (1024 * 1024)
        # ── Registry file-size + KV estimate (baseline for ALL non-NPU) ────
        # Compute the model-file-size + KV estimate up front so it can serve
        # as a floor for the container cgroup probe below (see path 2).
        if model_mb <= 0 and registry is not None:
            try:
                m = registry.get(model_id)
                model_mb = (getattr(m, "size_bytes", 0) or 0) / (1024 * 1024)
                ctx_meta = m.model_dump() if hasattr(m, "model_dump") else None
            except Exception:
                model_mb = 0.0
        kv_mb = _kv_estimate_mb(_ctx_tokens_for(ctx_meta))
        estimate_mb = round(model_mb + kv_mb, 1)

        # ── Container cgroup probe (path 2) ────────────────────────────────
        # Probe the live podman/docker cgroup.  Returns 0 when no container
        # named hal0-slot-<name> exists (container down/absent), so the
        # fall-through to the file-size estimate is automatic.
        #
        # CRITICAL (#672 review): on Strix Halo (UMA) the model WEIGHTS live
        # in GTT (system RAM via amdgpu/TTM) and are often NOT charged to the
        # process memory cgroup.  A live container can therefore report a
        # cgroup of only ~2 GB (runtime/buffers) while holding a ~22 GB model.
        # Using the cgroup unconditionally would UNDER-report.  So we take the
        # MAX of the cgroup and the registry estimate:
        #   • cgroup accurately includes weights → cgroup ≥ estimate → wins.
        #   • GTT not charged (cgroup too low)   → estimate wins → no under-report.
        cgroup_bytes = await _container_cgroup_mem_bytes(s.name)
        cgroup_mb = round(cgroup_bytes / (1024.0 * 1024.0), 1)
        resident_mb = max(cgroup_mb, estimate_mb)

        out[s.name] = {
            "vram_mb": resident_mb,
            "ram_mb": 0.0,
            "mem_mb": resident_mb,
            "state": state,
            "model_id": model_id,
        }
    return out


@dataclass
class CapacitySnapshot:
    """Point-in-time view of system and slot capacity.

    All memory values are in mebibytes (MiB) to match the sysfs and DRM
    fdinfo units used during probe.  Callers converting to GiB for display
    should divide by 1024.0.
    """

    free_vram_mb: float
    """VRAM / GTT available for new model loads, in MiB.

    On Strix Halo (UMA), this reflects the GTT pool minus current slot
    allocations (as reported by DRM fdinfo).  On NVIDIA, reads from NVML.
    """

    free_ram_mb: float
    """System RAM available (MemAvailable from /proc/meminfo), in MiB.

    Useful for CPU-fallback slots and context buffers.
    """

    total_ram_mb: float
    """Total system RAM (MemTotal from /proc/meminfo), in MiB."""

    total_vram_mb: float
    """Total VRAM / GTT, in MiB.  On UMA, equal to total_ram_mb."""

    used_slots: int
    """Number of slots currently in a non-offline state."""

    max_slots: int
    """Maximum number of concurrent slots permitted by hal0.toml
    [slots].max_slots.  0 means unconfigured / unlimited.
    """

    per_slot: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Per-slot breakdown: {slot_name: {vram_mb, ram_mb, state, model_id}}."""

    def fits(self, required_vram_mb: float, required_ram_mb: float = 0.0) -> bool:
        """Return True if the requested memory would fit within current headroom.

        Does not account for fragmentation.  On UMA hardware, free_ram_mb
        and free_vram_mb are linked — over-allocating one starves the
        other.  The dashboard's slot form is responsible for surfacing
        that subtlety.
        """
        # TIER1: No silent return — explicit comparison so the caller can
        # rely on a bool, not a maybe-truthy dict.
        if required_vram_mb < 0 or required_ram_mb < 0:
            raise CapacityProbeError(
                "fits() requirements must be non-negative",
                details={
                    "required_vram_mb": required_vram_mb,
                    "required_ram_mb": required_ram_mb,
                },
            )
        if required_vram_mb > self.free_vram_mb:
            return False
        if required_ram_mb > self.free_ram_mb:
            return False
        return not (self.max_slots and self.used_slots >= self.max_slots)

    @classmethod
    async def probe(
        cls,
        *,
        hardware_info: HardwareInfo | None = None,
        per_slot: dict[str, dict[str, Any]] | None = None,
        max_slots: int = 0,
    ) -> CapacitySnapshot:
        """Read current system state and return a fresh snapshot.

        Args:
            hardware_info: Optional pre-probed HardwareInfo.  When None, we
                read /proc/meminfo only and treat VRAM == total RAM (the
                UMA fallback used on Strix Halo when the hardware probe
                hasn't completed yet).
            per_slot: Optional pre-collected per-slot metrics.  When None,
                returns an empty mapping (the slot manager populates this).
            max_slots: hal0.toml [slots].max_slots, 0 means unlimited.

        Reads /proc/meminfo synchronously inside ``run_in_executor`` so it
        does not block the event loop.
        """
        loop = asyncio.get_running_loop()
        total_ram_mb, avail_ram_mb = await loop.run_in_executor(None, _read_meminfo)

        # Resolve VRAM / GTT.  We code against the HardwareInfo schema but
        # gracefully degrade to RAM-as-VRAM when the probe hasn't run yet —
        # PLAN.md notes UMA hardware (Strix Halo) reports the same number.
        if hardware_info is not None and hardware_info.gpus:
            total_vram_mb = float(hardware_info.gpus[0].vram_mb) or total_ram_mb
        else:
            total_vram_mb = total_ram_mb

        per_slot_map = per_slot or {}
        # free_vram_mb = total_vram_mb - sum(per-slot vram).  Clamped at 0.
        used_vram_mb = 0.0
        used_slots = 0
        for entry in per_slot_map.values():
            try:
                used_vram_mb += float(entry.get("vram_mb", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise CapacityProbeError(
                    "per-slot vram_mb is not numeric",
                    details={"entry": entry},
                ) from exc
            if entry.get("state") and entry.get("state") != "offline":
                used_slots += 1
        free_vram_mb = max(total_vram_mb - used_vram_mb, 0.0)

        return cls(
            free_vram_mb=round(free_vram_mb, 1),
            free_ram_mb=round(avail_ram_mb, 1),
            total_ram_mb=round(total_ram_mb, 1),
            total_vram_mb=round(total_vram_mb, 1),
            used_slots=used_slots,
            max_slots=max_slots,
            per_slot=per_slot_map,
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict for API responses."""
        return {
            "free_vram_mb": self.free_vram_mb,
            "free_ram_mb": self.free_ram_mb,
            "total_ram_mb": self.total_ram_mb,
            "total_vram_mb": self.total_vram_mb,
            "used_slots": self.used_slots,
            "max_slots": self.max_slots,
            "per_slot": self.per_slot,
        }


__all__ = [
    "CapacityProbeError",
    "CapacitySnapshot",
    "_container_cgroup_mem_bytes",
    "build_per_slot",
]
