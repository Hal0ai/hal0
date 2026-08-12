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

# Context-window fallbacks are imported from the launch path so the capacity
# estimator can never drift from what a slot will actually launch with
# (providers.container._resolve_context_size: explicit > min(native, dense
# cap) > safe fallback).  The names are underscore-private in container.py;
# importing them anyway is the lesser evil vs. a re-declared constant
# silently diverging again (container.py is owned by another workstream, so
# the constants cannot be moved to a neutral module from here).
from hal0.providers.container import _CTX_DENSE_CAP
from hal0.slots.metrics_collect import systemd_props
from hal0.slots.naming import slot_unit_name
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
# pins one.  Sourced from the launch path (see the _CTX_DENSE_CAP import
# above): an unpinned slot actually launches at min(native, _CTX_DENSE_CAP)
# — never more — so the capacity estimate must budget the same ceiling.
# The previous private 65536 here disagreed with the launcher's 8192/32768
# fallbacks and over-reported KV for slots that launch at ≤32768.
_DEFAULT_CTX_TOKENS = _CTX_DENSE_CAP

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


def estimate_file_size_kv_mb(model_mb: float, ctx_meta: dict[str, Any] | None) -> float:
    """Model-file-size + KV-cache footprint estimate, in MiB.

    This is the same baseline/fallback formula :func:`build_per_slot` uses
    (its path 3, and the floor under path 2's cgroup probe): model file
    size plus a coarse per-context-token KV estimate (:func:`_kv_estimate_mb`
    / :func:`_ctx_tokens_for`). Factored out so other callers that must size
    a model BEFORE it is resident — and therefore have no cgroup/FLM figure
    to read yet, e.g. pre-load eviction (:mod:`hal0.slots.preload_evict`)
    deciding whether an incoming load will fit — reuse exactly this
    estimator instead of a second, divergent one.
    """
    kv_mb = _kv_estimate_mb(_ctx_tokens_for(ctx_meta))
    return round(model_mb + kv_mb, 1)


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
    """Cgroup-wide ``memory.current`` for the container/unit backing *slot_name*.

    Two probes, tried in order:

      1. :func:`_runtime_inspect_mem_bytes` — ``<runtime> inspect`` the
         podman/docker container directly (highest fidelity: reads the
         *container's* cgroup, not the systemd unit's).
      2. :func:`_systemd_unit_mem_bytes` — the slot's own
         ``hal0-slot@<name>.service`` unit's ``MemoryCurrent`` property,
         read via ``systemctl show``.

    Why two: on a standard install ``hal0-api`` runs unprivileged
    (``User=hal0``, P3-perms) while slot containers are ROOTFUL podman
    (Quadlet-launched, root's container store). ``podman inspect`` issued
    from the API process is therefore permission-denied against root's
    store and probe 1 silently returns 0 on *every* standard install, not
    just when the container is absent (#1839). ``systemctl show`` has no
    such problem — systemd (running as root) tracks each unit's cgroup
    memory accounting as a property that any user can read, which is also
    why ``/api/slots/metrics`` (:func:`hal0.slots.metrics_collect.collect_local`,
    which already leans on this exact fallback) reports accurate RSS while
    this probe used to silently under-report.

    Returns 0 only when BOTH probes come up empty (container absent AND
    the unit itself isn't loaded / has no cgroup) — not exceptional, it
    just means the slot isn't backed by a live container/unit.

    The returned value includes model weights + KV-cache + runtime
    overhead as measured by the cgroup; callers MUST NOT add an
    additional KV estimate on top.
    """
    mem_bytes = await _runtime_inspect_mem_bytes(slot_name)
    if mem_bytes > 0:
        return mem_bytes
    return await _systemd_unit_mem_bytes(slot_name)


async def _runtime_inspect_mem_bytes(slot_name: str) -> int:
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

    Returns 0 on any error (including permission denied — a rootless
    caller inspecting a rootful container) so the caller
    (:func:`_container_cgroup_mem_bytes`) can fall back to the systemd
    unit's own ``MemoryCurrent``.
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


async def _systemd_unit_mem_bytes(slot_name: str) -> int:
    """``MemoryCurrent`` of the slot's own ``hal0-slot@<name>.service`` unit.

    Read via ``systemctl show -p MemoryCurrent`` (:func:`hal0.slots.metrics_collect.systemd_props`)
    — a plain systemd property query, not a container-runtime inspect, so it
    needs no elevated privilege even when the container itself is ROOTFUL
    podman (#1839). Quadlet places the container's workload cgroup under the
    generated unit, so ``MemoryCurrent`` tracks the same memory the runtime
    probe above tries (and often fails) to read directly.

    Returns 0 when the unit isn't loaded, accounting is disabled
    (``MemoryCurrent=[not set]``), or ``systemctl`` itself is unavailable —
    same fail-soft contract as :func:`_runtime_inspect_mem_bytes`.
    """
    unit = slot_unit_name(slot_name)
    props = await systemd_props(unit, "MemoryCurrent")
    try:
        mem = int(props.get("MemoryCurrent", "") or 0)
    except (TypeError, ValueError):
        return 0
    return mem if mem > 0 else 0


def _host_has_capable_gpu() -> bool:
    """True if the hardware probe found at least one usable GPU on this box.

    "Usable" means Vulkan- or compute- (ROCm/CUDA) capable — the same
    ``vulkan_capable`` / ``compute_capable`` signal #1799 added to
    :class:`~hal0.config.schema.GPUInfo` and that :mod:`hal0.hardware.recommend`
    already gates device recommendations on. Reads the cached
    ``/etc/hal0/hardware.json`` written by ``hal0 probe`` via
    :func:`hal0.config.loader.load_hardware_info` — cheap (one small JSON
    file, no re-probe) and honest: an absent file / no probed GPU degrades
    to ``HardwareInfo(gpus=[])``, which correctly reads as "no capable GPU"
    rather than raising.

    Never raises: any error probing/loading hardware state degrades to
    ``False`` (the conservative choice — books memory as RAM rather than
    risking phantom VRAM on a box we couldn't positively confirm has one).
    """
    try:
        from hal0.config.loader import load_hardware_info

        info = load_hardware_info()
    except Exception:
        return False
    return any(g.vulkan_capable or g.compute_capable for g in info.gpus)


async def build_per_slot(
    slots: list[Any],
    *,
    registry: Any | None = None,
    flm_catalog: dict[str, dict[str, Any]] | None = None,
    gpu_capable: bool | None = None,
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

    ``gpu_capable`` (#1839) gates the VRAM/RAM split: even a slot
    *configured* for a GPU backend (``vulkan``/``rocm``/``cuda``) has no
    discrete VRAM pool to charge on a box with no usable GPU — the shipped
    static seeds hardcode ``device = "gpu-vulkan"`` regardless of
    hardware, so a GPU-less install (``HAL0_ALLOW_CPU_ONLY=1``) would
    otherwise book resident memory as VRAM purely from the configured
    token, never consulting whether a GPU actually exists. When ``None``
    (the default) this is resolved once via :func:`_host_has_capable_gpu`,
    which reads the cached hardware probe (``vulkan_capable`` /
    ``compute_capable`` on any detected GPU, #1799's signal). Callers that
    already have a fresher :class:`~hal0.config.schema.HardwareInfo` may
    pass the resolved bool directly to skip the re-read.
    """
    if gpu_capable is None:
        gpu_capable = _host_has_capable_gpu()
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
        estimate_mb = estimate_file_size_kv_mb(model_mb, ctx_meta)

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
        #
        # Artefact token, not display name (#1839 review): on an id-keyed box
        # (post ``hal0 slot migrate-id-keying``) the container/unit is named
        # off the durable ``slot_id``, not the mutable display ``name`` — the
        # two diverge the moment a slot is renamed. Mirrors
        # :func:`hal0.slots.naming.slot_instance_token`'s id-over-name
        # preference without needing a full config mapping.
        artefact_token = str(getattr(s, "slot_id", None) or s.name)
        cgroup_bytes = await _container_cgroup_mem_bytes(artefact_token)
        cgroup_mb = round(cgroup_bytes / (1024.0 * 1024.0), 1)
        resident_mb = max(cgroup_mb, estimate_mb)

        # ── VRAM vs. RAM attribution (#1796, #1839) ─────────────────────
        # ``backend`` here is the normalized effective-backend token from
        # ``_cfg_effective_backend`` ("rocm" | "vulkan" | "cuda" | "cpu" |
        # "flm"), NOT the raw slot.backend passed straight through — it is
        # the single source the dashboard's own backend chip trusts. A
        # ``cpu`` slot has no discrete VRAM pool to charge: its weights are
        # resident in system RAM, so booking them under vram_mb reads as
        # phantom GPU usage on a GPU-less box (RAM MB 0.0 alongside a
        # nonzero VRAM MB). Attribute to ram_mb instead; every GPU backend
        # (and the flm/NPU fallback above, which shares this UMA-style
        # accounting) keeps the historical vram_mb attribution.
        #
        # #1839: ``backend`` is the *configured* device token — it says
        # nothing about whether a usable GPU actually exists. The shipped
        # static seeds hardcode ``device = "gpu-vulkan"`` regardless of
        # hardware, so a GPU-declared slot on a GPU-less box (no
        # vulkan/compute-capable GPU probed) must ALSO book to ram_mb —
        # otherwise a CPU-only install shows phantom VRAM usage even
        # though ``backend`` never says "cpu". ``is_npu`` slots are
        # EXCLUDED from this gate: an FLM slot that fell through to this
        # common path (catalog miss / zero ``footprint_gb``) still uses the
        # NPU's own UMA-style vram_mb accounting regardless of classic GPU
        # capability — an NPU-only host has ``gpu_capable=False`` but its
        # NPU slots never held phantom *GPU* VRAM in the first place, so
        # the #1839 gate does not apply to them.
        if backend == "cpu" or (not is_npu and not gpu_capable):
            vram_mb, ram_mb = 0.0, resident_mb
        else:
            vram_mb, ram_mb = resident_mb, 0.0

        out[s.name] = {
            "vram_mb": vram_mb,
            "ram_mb": ram_mb,
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
    "_host_has_capable_gpu",
    "build_per_slot",
    "estimate_file_size_kv_mb",
]
