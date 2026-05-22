"""Hardware probe — detect GPU, NPU, RAM, disk, and CPU.

HardwareProbe.probe() writes a HardwareInfo snapshot to
/etc/hal0/hardware.json on first install (via the installer), and can be
re-triggered via `hal0 probe` or the "re-probe" button on the Hardware
dashboard view.

Port target: haloai lib/hardware.py (split: probe + stats).
See PLAN.md §3 and §7 (installer: "Hardware probe → /etc/hal0/hardware.json
+ default slot configs derived from detected NPU/GPU").

HardwareInfo / GPUInfo / NPUInfo are defined in hal0.config.schema (the
canonical home per PLAN §3); this module re-exports them so callers can
``from hal0.hardware.probe import HardwareInfo`` if they prefer.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import structlog

from hal0.config import paths as _paths
from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.errors import Hal0Error

log = structlog.get_logger(__name__)


class HardwareProbeError(Hal0Error):
    """Raised when the probe cannot run at all (rare — most failures degrade)."""

    code = "system.probe_failed"
    status = 500


# ── Low-level helpers ──────────────────────────────────────────────────────────


def _run(cmd: list[str], timeout: float = 4.0) -> tuple[int, str, str]:
    """Run a subprocess, capturing stdout/stderr. Never raises; returns (-1, '', err) on failure.

    # NOTE: callers must inspect the return code; we do NOT swallow into "" so
    # the higher-level helpers can decide whether to log at debug or warn.
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return -1, "", f"binary not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s: {' '.join(cmd)}"
    except OSError as exc:
        return -1, "", f"{type(exc).__name__}: {exc}"


def _read_text(path: Path) -> str | None:
    """Read a file; return None on any I/O error (never raises)."""
    try:
        return path.read_text()
    except OSError:
        return None


def _read_sysfs_mb(path: Path) -> float | None:
    """Read a sysfs byte counter and return MiB, or None on failure.

    # TIER2: returns Optional[float] rather than 0.0 on parse failure so call
    # sites can distinguish "unknown" from "actually zero" (per PLAN §5 Tier 2,
    # the _drm_mem() polish item).
    """
    txt = _read_text(path)
    if txt is None:
        return None
    try:
        return round(int(txt.strip()) / (1024 * 1024), 1)
    except ValueError:
        log.debug("hardware.probe.sysfs_parse_fail", path=str(path), raw=txt[:40])
        return None


def _amd_drm_device() -> Path | None:
    """Find the first AMD DRM card whose sysfs exports VRAM totals."""
    try:
        for p in sorted(Path("/sys/class/drm").glob("card*/device/mem_info_vram_total")):
            return p.parent
    except OSError:
        return None
    return None


# ── CPU + RAM ──────────────────────────────────────────────────────────────────


def _parse_cpuinfo() -> tuple[str, int, int]:
    """Return (model_name, physical_cores, logical_threads).

    Reads /proc/cpuinfo. Falls back to os.cpu_count() for threads if parsing
    fails. On ARM hosts /proc/cpuinfo has no ``model name`` line — we look
    at ``Hardware``, ``Model`` and ``CPU implementer`` fields instead so the
    UI gets a non-empty string instead of a bare "—".
    """
    txt = _read_text(Path("/proc/cpuinfo"))
    model = ""
    arm_hardware = ""
    arm_model = ""
    arm_part = ""
    threads = 0
    physical_ids: set[str] = set()
    cores_per_socket = 0
    if txt:
        for line in txt.splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            if k == "model name" and not model:
                model = v
            elif k == "Model" and not arm_model:
                arm_model = v
            elif k == "Hardware" and not arm_hardware:
                arm_hardware = v
            elif k == "CPU part" and not arm_part:
                arm_part = v
            elif k == "processor":
                threads += 1
            elif k == "physical id":
                physical_ids.add(v)
            elif k == "cpu cores":
                with contextlib.suppress(ValueError):
                    cores_per_socket = max(cores_per_socket, int(v))
    # ARM fallback: stitch whichever ID-shaped field we found into a name.
    if not model:
        model = arm_model or arm_hardware
        if not model and arm_part:
            model = f"ARM CPU ({arm_part})"
    # Last resort: name the architecture so the wizard never shows an empty
    # CPU row when /proc/cpuinfo exists but lacks any of the above.
    if not model:
        with contextlib.suppress(OSError):
            import platform as _platform

            mach = _platform.machine()
            if mach:
                model = f"{mach} CPU"
    if threads == 0:
        threads = os.cpu_count() or 0
    sockets = max(1, len(physical_ids))
    cores = cores_per_socket * sockets if cores_per_socket > 0 else threads
    return model, cores, threads


def _parse_meminfo() -> tuple[int, int]:
    """Return (total_mb, available_mb) from /proc/meminfo, (0, 0) on failure."""
    txt = _read_text(Path("/proc/meminfo"))
    if not txt:
        return 0, 0
    total_kb = 0
    avail_kb = 0
    for line in txt.splitlines():
        if line.startswith("MemTotal:"):
            with contextlib.suppress(IndexError, ValueError):
                total_kb = int(line.split()[1])
        elif line.startswith("MemAvailable:"):
            with contextlib.suppress(IndexError, ValueError):
                avail_kb = int(line.split()[1])
    return total_kb // 1024, avail_kb // 1024


_SIZE_UNITS_MB = {"KB": 1 / 1024, "MB": 1, "GB": 1024, "TB": 1024 * 1024}


def _dmidecode_host_ram_mb() -> int | None:
    """Sum physical DIMM sizes via `dmidecode -t memory`.

    Used to recover the true host RAM when /proc/meminfo reflects an LXC
    cgroup quota rather than the physical pool. Returns None when
    dmidecode is unavailable or returns no usable Memory Device entries.
    """
    rc, out, _ = _run(["dmidecode", "-t", "memory"], timeout=4.0)
    if rc != 0 or not out:
        return None
    total_mb = 0.0
    in_device = False
    for raw in out.splitlines():
        line = raw.strip()
        if line.startswith("Memory Device") or line == "Memory Device":
            in_device = True
            continue
        if not in_device:
            continue
        if not line:
            in_device = False
            continue
        if line.startswith("Size:"):
            val = line.split(":", 1)[1].strip()
            if val.startswith("No Module") or val == "Unknown" or val.startswith("0 "):
                continue
            parts = val.split()
            if len(parts) >= 2:
                try:
                    n = float(parts[0])
                except ValueError:
                    continue
                unit = parts[1].upper()
                mult = _SIZE_UNITS_MB.get(unit)
                if mult:
                    total_mb += n * mult
    if total_mb <= 0:
        return None
    return round(total_mb)


def _derive_unified_memory_mb(ram_mb: int, gpu: GPUInfo | None) -> int:
    """Compute the true unified-memory pool size in MiB.

    On AMD UMA (Strix Halo etc.) the dedicated VRAM is a tiny BAR carve-out
    and the bulk of GPU memory comes from the GTT pool, which is *shared*
    with system RAM. Summing ram_mb + vram_mb would double-count.

    Strategy (in order):
      1. If running in an LXC where /proc/meminfo shows a cgroup quota
         smaller than the host's physical RAM, prefer dmidecode's DIMM sum.
      2. Otherwise return /proc/meminfo's MemTotal (already the right pool
         on bare metal / VMs that see all physical RAM).
      3. On non-UMA hardware (discrete GPUs), unified pool = ram_mb;
         consumers add dedicated VRAM separately.
    """
    dmi = _dmidecode_host_ram_mb()
    if dmi is not None and dmi > ram_mb * 1.1:
        # /proc/meminfo is reporting a cgroup-restricted view; trust the DIMMs.
        return dmi
    return ram_mb


# ── GPU detection ──────────────────────────────────────────────────────────────


def _detect_nvidia() -> GPUInfo | None:
    """Probe nvidia-smi. Returns a populated GPUInfo or None if no NVIDIA GPU."""
    rc, out, err = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ]
    )
    if rc != 0 or not out.strip():
        if rc == -1:
            log.debug("hardware.probe.nvidia_smi_unavailable", err=err)
        return None
    first = out.strip().splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    if len(parts) < 2:
        return None
    name = parts[0]
    try:
        vram_mb = round(float(parts[1]))
    except ValueError:
        vram_mb = 0
    driver = parts[2] if len(parts) >= 3 else ""
    return GPUInfo(
        vendor="nvidia",
        name=name,
        vram_mb=vram_mb,
        driver=f"nvidia {driver}".strip(),
        compute_capable=True,  # nvidia-smi presence => CUDA capable
    )


def _detect_amd() -> GPUInfo | None:
    """Probe AMD GPU via DRM sysfs and (optionally) rocm-smi.

    On Strix Halo (UMA) the vram_total counter reports the small carve-out;
    the real model-loading pool is GTT. We pick the larger of vram_total and
    gtt_total so the slot-form's "will it fit" check uses the right number.
    """
    drm = _amd_drm_device()
    if drm is None:
        return None
    vram_total = _read_sysfs_mb(drm / "mem_info_vram_total") or 0.0
    gtt_total = _read_sysfs_mb(drm / "mem_info_gtt_total") or 0.0
    effective_mb = round(max(vram_total, gtt_total))

    # Name via lspci on the device's PCI slot
    name = ""
    try:
        pci_link = (drm / "uevent").read_text()
        m = re.search(r"PCI_SLOT_NAME=(\S+)", pci_link)
        if m:
            rc, out, _ = _run(["lspci", "-s", m.group(1)])
            if rc == 0 and out:
                # e.g. "c5:00.0 VGA compatible controller: AMD ... [Radeon 890M] (rev cc)"
                after = out.split(":", 2)[-1].strip()
                name = after
    except OSError:
        pass

    # ROCm probe (very lightweight — just presence of binary + non-error exit)
    rc, _, _ = _run(["rocm-smi", "--showproductname"])
    compute_capable = rc == 0

    return GPUInfo(
        vendor="amd",
        name=name or "AMD GPU",
        vram_mb=effective_mb,
        driver="amdgpu",
        drm_path=str(drm),
        compute_capable=compute_capable,
        vulkan_capable=True,  # amdgpu ships Mesa Vulkan
    )


def _detect_vulkan_fallback() -> GPUInfo | None:
    """Use vulkaninfo to identify the primary GPU when vendor probes failed."""
    rc, out, _ = _run(["vulkaninfo", "--summary"], timeout=6.0)
    if rc != 0 or not out:
        return None
    # First "deviceName = ..." line wins.
    name = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("deviceName"):
            _, _, val = line.partition("=")
            name = val.strip()
            break
    if not name:
        return None
    vendor = "unknown"
    lower = name.lower()
    if "nvidia" in lower:
        vendor = "nvidia"
    elif "amd" in lower or "radeon" in lower:
        vendor = "amd"
    elif "intel" in lower:
        vendor = "intel"
    return GPUInfo(vendor=vendor, name=name, vulkan_capable=True)


def _detect_lspci_fallback() -> GPUInfo | None:
    """Last-resort: parse ``lspci -nnk`` (or plain ``lspci``) for any
    VGA / 3D / Display controller. Try the verbose variant first, then
    degrade to bare ``lspci`` so we still produce a name in containers
    where pciutils' device-id database is missing.
    """
    out = ""
    for cmd in (["lspci", "-nnk"], ["lspci"]):
        rc, _out, _ = _run(cmd)
        if rc == 0 and _out:
            out = _out
            break
    if not out:
        return None
    for line in out.splitlines():
        if (
            "VGA compatible controller" in line
            or "3D controller" in line
            or "Display controller" in line
        ):
            after = line.split(":", 2)[-1].strip()
            lower = after.lower()
            vendor = "unknown"
            if "nvidia" in lower:
                vendor = "nvidia"
            elif "amd" in lower or "ati" in lower or "radeon" in lower:
                vendor = "amd"
            elif "intel" in lower:
                vendor = "intel"
            return GPUInfo(vendor=vendor, name=after)
    return None


def _detect_gpu() -> GPUInfo:
    """Run vendor probes in order; return an empty GPUInfo if nothing matched."""
    for fn in (_detect_nvidia, _detect_amd, _detect_vulkan_fallback, _detect_lspci_fallback):
        try:
            info = fn()
        except Exception as exc:  # defensive: never let one probe crash the rest
            log.warning("hardware.probe.detector_fail", detector=fn.__name__, error=str(exc))
            continue
        if info is not None:
            return info
    return GPUInfo(vendor="unknown")


# ── Platform detection ────────────────────────────────────────────────────────
#
# Layered probe — first match wins. The string returned drives UI labels
# (memory "unified" vs "system", docs links, slot recommendation tier) so
# stability of the values matters more than precision: we'd rather
# under-classify (return ``"kvm"`` for a Proxmox VM whose DMI strings the
# kernel hid) than mis-attribute Strix Halo when no NPU is present.


_DMI_PATHS = {
    "product_name": Path("/sys/class/dmi/id/product_name"),
    "sys_vendor": Path("/sys/class/dmi/id/sys_vendor"),
    "board_vendor": Path("/sys/class/dmi/id/board_vendor"),
    "bios_vendor": Path("/sys/class/dmi/id/bios_vendor"),
}


def _read_dmi() -> dict[str, str]:
    """Read DMI strings used by platform detection. Returns lowercased values."""
    out: dict[str, str] = {}
    for key, path in _DMI_PATHS.items():
        txt = _read_text(path)
        if txt:
            out[key] = txt.strip().lower()
    return out


def _is_container() -> bool:
    """True when /proc/1/cgroup or /proc/self/cgroup hints at lxc/docker."""
    for p in (Path("/proc/1/cgroup"), Path("/proc/self/cgroup")):
        txt = _read_text(p)
        if not txt:
            continue
        low = txt.lower()
        if "lxc" in low or "docker" in low or "kubepods" in low:
            return True
    # systemd-nspawn / podman expose /run/.containerenv or /.dockerenv
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def _is_lxc() -> bool:
    """Narrower than _is_container — only matches Proxmox/native LXC."""
    txt = _read_text(Path("/proc/1/cgroup")) or _read_text(Path("/proc/self/cgroup"))
    if txt and "lxc" in txt.lower():
        return True
    # systemd reports lxc via /proc/1/environ on some kernels
    env = _read_text(Path("/proc/1/environ"))
    return bool(env and "container=lxc" in env.lower().replace("\x00", " "))


def _is_wsl() -> bool:
    """True on WSL 1 / WSL 2 (any Microsoft kernel)."""
    for p in (Path("/proc/version"), Path("/proc/sys/kernel/osrelease")):
        txt = _read_text(p)
        if not txt:
            continue
        low = txt.lower()
        if "microsoft" in low or "wsl" in low:
            return True
    return False


def _detect_platform(gpu: GPUInfo, npu: NPUInfo) -> str:
    """Return a short platform string. See HardwareInfo.platform for the
    canonical vocabulary. Detection order is intentional:

      1. Containers first — wsl2 then lxc — because they overlap with KVM
         when the host is itself a VM.
      2. KVM detection via DMI ('QEMU' / 'KVM' sys_vendor + product_name).
      3. Bare metal: classify by GPU vendor + NPU presence.
    """
    # 1. WSL — checked before LXC because WSL's /proc/1/cgroup can mention
    #    other shims; the kernel string is the authoritative signal.
    if _is_wsl():
        return "wsl2"
    if _is_lxc():
        return "lxc"

    dmi = _read_dmi()
    product = dmi.get("product_name", "")
    sysv = dmi.get("sys_vendor", "")
    bios = dmi.get("bios_vendor", "")

    is_qemu_kvm = (
        "qemu" in sysv
        or "qemu" in product
        or "kvm" in product
        or "kvm" in sysv
        or "red hat" in sysv  # virtio devices report Red Hat as vendor
        or "seabios" in bios
        or "edk ii" in bios
        or "ovmf" in bios
    )
    if is_qemu_kvm:
        # Proxmox VMs typically expose "Standard PC (i440FX + PIIX, …)" or
        # "Standard PC (Q35 + ICH9, …)" as product_name plus a generic QEMU
        # sys_vendor. There's no DMI field that says "Proxmox" outright, so
        # we look at the BIOS vendor / SMBIOS oem tags (Proxmox sets
        # ``Type: 1 Manufacturer: ...`` to QEMU regardless). For the user-
        # facing label we'd rather say "Proxmox VM (KVM)" when the host is
        # almost certainly Proxmox; check for the SMBIOS oem string the
        # PVE installer stamps in newer releases.
        oem = _read_text(Path("/sys/class/dmi/id/chassis_vendor"))
        if oem and "proxmox" in oem.lower():
            return "proxmox-kvm"
        # Heuristic: a /etc/pve directory is the strongest live signal but
        # only present on a PVE host, not inside its VMs. For VMs, fall back
        # to the kernel command line which carries a virtio root marker.
        cmdline = _read_text(Path("/proc/cmdline")) or ""
        if "virtio" in cmdline.lower() and "Q35" in (product.upper()):
            # Q35 + virtio is the Proxmox default; users can override but
            # 95%+ of homelab deployments hit this code path.
            return "proxmox-kvm"
        return "kvm"

    # 3. Bare metal.
    vendor = (gpu.vendor or "").lower()
    if npu.present and vendor == "amd":
        # Strix Halo / Hawk Point: AMD iGPU + XDNA NPU + unified memory.
        # The drm_path having a GTT counter is what makes "unified" valid,
        # but at this point we already trusted the GPU detector to report
        # vram_mb=max(vram,gtt) so this is just the platform label.
        return "strix-halo"
    if vendor == "nvidia":
        return "bare-metal-nvidia-gpu"
    if vendor == "amd":
        return "bare-metal-amd-gpu"
    if vendor == "intel":
        return "bare-metal-intel-igpu"
    return "bare-metal-cpu-only"


# ── NPU detection ──────────────────────────────────────────────────────────────


def _detect_npu() -> NPUInfo:
    """Detect AMD XDNA NPU presence via sysfs / /dev nodes.

    Strix Halo / Hawk Point / Phoenix expose /dev/accel/accel* once the amdxdna
    driver is loaded. We don't run `flm validate` here — that's stats territory.
    """
    accel = Path("/dev/accel")
    if accel.exists():
        try:
            entries = list(accel.iterdir())
            if entries:
                return NPUInfo(
                    present=True,
                    vendor="amd",
                    name="AMD NPU (XDNA)",
                    driver="amdxdna",
                )
        except OSError:
            pass
    # Some kernels expose it through /sys/class/accel
    if Path("/sys/module/amdxdna").exists():
        return NPUInfo(present=True, vendor="amd", name="AMD NPU (XDNA)", driver="amdxdna")
    return NPUInfo(present=False)


# ── Disk ───────────────────────────────────────────────────────────────────────


def _disk_free_mb(path: Path) -> int:
    """Return free MiB on the filesystem hosting `path`. 0 if unavailable."""
    try:
        # path may not yet exist (fresh install); walk up to the first existing parent.
        target = path
        while not target.exists() and target != target.parent:
            target = target.parent
        usage = shutil.disk_usage(str(target))
        return usage.free // (1024 * 1024)
    except OSError as exc:
        log.warning("hardware.probe.disk_fail", path=str(path), error=str(exc))
        return 0


# ── HardwareProbe ──────────────────────────────────────────────────────────────


class HardwareProbe:
    """Detects hardware and produces a HardwareInfo snapshot.

    The probe is intentionally synchronous (subprocess + sysfs reads) and
    runs in a threadpool when called from an async context.
    """

    def probe(self) -> HardwareInfo:
        """Run hardware detection and return a HardwareInfo snapshot.

        Detects: GPU (NVIDIA / AMD / Vulkan / lspci fallbacks), NPU
        (/dev/accel + amdxdna), RAM (/proc/meminfo), disk (statvfs on
        HAL0 var_lib), CPU (/proc/cpuinfo).

        Never raises for individual probe failures — each detector returns
        an empty result and logs a warning. Only catastrophic failures
        (e.g. /proc not mounted) raise HardwareProbeError.
        """
        try:
            cpu_model, cpu_cores, cpu_threads = _parse_cpuinfo()
            ram_total_mb, ram_avail_mb = _parse_meminfo()
            gpu = _detect_gpu()
            npu = _detect_npu()
            disk_mb = _disk_free_mb(_paths.var_lib())

            uname = ""
            try:
                uname_txt = _read_text(Path("/proc/version"))
                if uname_txt:
                    uname = uname_txt.strip().split(" (", 1)[0]
            except OSError:
                pass

            unified_mb = _derive_unified_memory_mb(ram_total_mb, gpu if gpu.vendor else None)
            platform = "unknown"
            try:
                platform = _detect_platform(gpu, npu)
            except Exception as exc:  # never let platform classification break the probe
                log.warning("hardware.probe.platform_detect_fail", error=str(exc))

            # We surface a GPU row when EITHER vendor or name is populated;
            # the old logic dropped vendor="unknown" rows even when lspci
            # gave us a perfectly good model string (e.g. a virtio GPU in a
            # Proxmox VM). UI consumers can still gate on gpu.vendor when
            # they need real compute capability.
            include_gpu = bool(gpu.vendor and gpu.vendor != "unknown") or bool(gpu.name)

            return HardwareInfo(
                cpu_model=cpu_model,
                cpu_cores=cpu_cores,
                cpu_threads=cpu_threads,
                ram_mb=ram_total_mb,
                ram_available_mb=ram_avail_mb,
                unified_memory_mb=unified_mb,
                gpus=[gpu] if include_gpu else [],
                npu=npu,
                disk_free_mb=disk_mb,
                platform=platform,
                probed_at=_dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
                extra={"kernel": uname} if uname else {},
            )
        except Exception as exc:
            # We never expect to reach here — every detector is wrapped — but if
            # /proc itself is unreadable, surface a typed error rather than
            # crashing with an opaque trace.
            raise HardwareProbeError(
                "hardware probe failed", {"error": f"{type(exc).__name__}: {exc}"}
            ) from exc

    async def probe_async(self) -> HardwareInfo:
        """Async wrapper that runs probe() in a threadpool executor."""
        return await asyncio.to_thread(self.probe)

    def write(self, info: HardwareInfo, path: Path | None = None) -> Path:
        """Serialize `info` to JSON at /etc/hal0/hardware.json (or `path`).

        Uses atomic replace (tempfile + os.replace) so an interrupted write
        leaves the previous snapshot intact.
        """
        target = path or _paths.hardware_json()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(info.model_dump(mode="json"), indent=2, sort_keys=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            tmp.write_text(payload)
            os.replace(tmp, target)
        except OSError as exc:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise HardwareProbeError(
                "could not write hardware.json",
                {"path": str(target), "error": str(exc)},
            ) from exc
        return target


# ── Card rendering ────────────────────────────────────────────────────────────
#
# format_cards() turns a HardwareInfo into 4 single-line summary cards
# suitable for the installer's "Hardware probe" step. Pure presentation —
# kept here rather than in config/schema.py so the Pydantic model stays
# free of stdout / ANSI concerns.

# Sodium amber #FFB000 — same accent ui.sh uses (256-color 214). Match
# the bash side so the installer's visual language stays consistent
# across step → cards → final box.
_AMBER = "\033[38;5;214m"
_DIM = "\033[2m"
_RST = "\033[0m"


def _plain_mode() -> bool:
    """Mirror installer/lib/ui.sh degradation rules.

    Active when HAL0_PLAIN=1, NO_COLOR is set (per https://no-color.org),
    or when stdout is not a TTY (CI logs, piped output).
    """
    import sys

    if os.environ.get("HAL0_PLAIN") == "1":
        return True
    if os.environ.get("NO_COLOR"):
        return True
    return not sys.stdout.isatty()


def _gb(mb: int) -> str:
    """Format MiB as 'N GB' for cards (1024-based; matches dashboard)."""
    if mb <= 0:
        return "—"
    return f"{round(mb / 1024)} GB"


def format_cards(info: HardwareInfo, *, plain: bool | None = None) -> list[str]:
    """Render a HardwareInfo as 4 single-line cards (CPU / GPU / NPU / DISK).

    Each card is two padded columns plus a description, e.g.::

      ■  CPU   AMD Ryzen 7 PRO 8745HS              8c · 16t
      ■  GPU   Radeon 890M (Strix Halo)            UMA · 96 GB unified
      ■  NPU   AMD NPU (XDNA, amdxdna)             present
      ■  DISK  /var/lib/hal0                       412 GB free

    A leading '■' (ASCII '*' in plain mode) is amber-coloured. Absent
    components render muted: "GPU   none detected" and "NPU   —".

    Parameters
    ----------
    info:
        A populated HardwareInfo from HardwareProbe.probe().
    plain:
        Override the auto-detected plain mode (None = auto via env / TTY).

    Returns
    -------
    list[str]
        Four card lines, no trailing newline. Caller joins with '\\n'.
    """
    plain = _plain_mode() if plain is None else plain
    a = "" if plain else _AMBER
    d = "" if plain else _DIM
    r = "" if plain else _RST
    glyph = "*" if plain else "■"

    def card(label: str, name: str, desc: str, *, muted: bool = False) -> str:
        # 5-char label column ("CPU  ", "GPU  ", "NPU  ", "DISK "), then
        # 36-char name column, then a free-form description. The name
        # column gets truncated rather than overflowing into desc.
        name = (name or "—")[:36].ljust(36)
        if muted:
            return f"  {d}{glyph}  {label:<5} {name}  {desc}{r}"
        return f"  {a}{glyph}{r}  {label:<5} {name}  {d}{desc}{r}"

    # CPU
    cpu_name = info.cpu_model or "unknown"
    cpu_desc = f"{info.cpu_cores}c · {info.cpu_threads}t · {_gb(info.ram_mb)} RAM"
    cpu_line = card("CPU", cpu_name, cpu_desc)

    # GPU — pick the first detected; flag UMA when unified == ram (i.e.
    # AMD iGPU sharing the host pool). Discrete GPUs report vram_mb that
    # isn't part of unified_memory_mb.
    if not info.gpus:
        gpu_line = card("GPU", "none detected", "", muted=True)
    else:
        g = info.gpus[0]
        bits = []
        if g.vram_mb > 0:
            # Strix Halo etc.: the "vram" pool is GTT shared with system
            # RAM. Show "UMA · N GB unified" so users don't worry the
            # dashboard double-counts.
            if g.vendor == "amd" and info.unified_memory_mb >= info.ram_mb * 0.95:
                bits.append(f"UMA · {_gb(info.unified_memory_mb)} unified")
            else:
                bits.append(f"{_gb(g.vram_mb)} VRAM")
        if g.compute_capable:
            # Vendor-specific compute name. nvidia-smi presence implies
            # CUDA; AMD uses ROCm. Anything else falls back to a generic
            # "compute" label rather than mis-attributing CUDA.
            if g.vendor == "nvidia":
                bits.append("CUDA")
            elif g.vendor == "amd":
                bits.append("ROCm")
            else:
                bits.append("compute")
        elif g.vulkan_capable:
            bits.append("Vulkan")
        gpu_line = card("GPU", g.name or g.vendor or "unknown", " · ".join(bits) or "detected")

    # NPU
    if info.npu.present:
        npu_desc = f"{info.npu.driver}" if info.npu.driver else "present"
        npu_line = card("NPU", info.npu.name or info.npu.vendor or "NPU", npu_desc)
    else:
        npu_line = card("NPU", "—", "", muted=True)

    # Disk — show the var-lib path + free space; the installer step ran
    # disk_free_mb against /var/lib/hal0 (or its parent on a fresh install).
    disk_path = "/var/lib/hal0"
    disk_line = card("DISK", disk_path, f"{_gb(info.disk_free_mb)} free")

    return [cpu_line, gpu_line, npu_line, disk_line]


__all__ = [
    "GPUInfo",
    "HardwareInfo",
    "HardwareProbe",
    "HardwareProbeError",
    "NPUInfo",
    "format_cards",
]
