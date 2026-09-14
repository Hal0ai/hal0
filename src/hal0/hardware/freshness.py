"""Staleness detection + live-probe fallback for ``/etc/hal0/hardware.json`` (#1862).

``hal0.slots.capacity._host_has_capable_gpu`` (the VRAM/RAM attribution gate
behind ``/api/slots/capacity``) and the ROCm/Vulkan lane derivation both
trust the cached hardware fact blindly:

* **File absent.** :func:`hal0.config.loader.load_hardware_info` returns an
  all-defaults ``HardwareInfo()`` (no ``gpus``) rather than raising, so any
  install where ``hal0 probe``/``hal0 setup`` has not run yet reads as
  GPU-less.
* **File stale.** A ``hardware.json`` written before a kernel upgrade + reboot
  (new driver, GPU added/removed) or before a schema field existed (e.g. a
  pre-#1799 file with no ``vulkan_capable``/``compute_capable`` keys) is never
  revisited — it is read and trusted forever.

Either way a box with a real, usable GPU gets treated as GPU-less: resident
slot memory books under ``ram_mb`` instead of ``vram_mb``, so
``/api/slots/capacity`` under-reports VRAM pressure and the dashboard's fit
warnings go quiet (#1862's "a real GPU box books VRAM as RAM").

This module answers "is the cached fact good enough to trust" and, when it
is not, probes live instead — the conservative direction stays intact (a
probe that itself fails degrades to the stale cache, never to an invented
GPU), but a missing/stale cache no longer silently wins over a live answer.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import structlog

from hal0.config.schema import HardwareInfo

log = structlog.get_logger(__name__)

#: Verdicts from :func:`staleness_reason`. ``None`` means "trust the cache".
STALE_MISSING = "missing"
STALE_KEYLESS = "predates-capability-fields"
STALE_KERNEL_MISMATCH = "kernel-changed-since-probe"
STALE_ACROSS_REBOOT = "probed-before-this-boot"


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def current_kernel_string() -> str:
    """The running kernel's ``/proc/version`` string, same shape as the probe.

    Mirrors :func:`hal0.hardware.probe.HardwareProbe.probe`'s own
    ``uname`` extraction exactly (first token before ``" ("``), so a
    like-for-like string comparison against the cached ``HardwareInfo.kernel``
    only fires on an actual kernel change, never on formatting drift between
    the two readers.
    """
    text = _read_text(Path("/proc/version"))
    if not text:
        return ""
    return text.strip().split(" (", 1)[0]


def _boot_time_epoch_s() -> float | None:
    """Wall-clock time this boot started, or ``None`` if unreadable."""
    text = _read_text(Path("/proc/uptime"))
    if not text:
        return None
    try:
        uptime_s = float(text.split()[0])
    except (IndexError, ValueError):
        return None
    return _dt.datetime.now(_dt.UTC).timestamp() - uptime_s


def staleness_reason(
    info: HardwareInfo,
    *,
    has_file: bool,
    running_kernel: str | None = None,
    boot_time_epoch_s: float | None = None,
) -> str | None:
    """Why the cached ``info`` should not be trusted, or ``None`` if it can be.

    Checked in order — the first one that applies wins:

    1. :data:`STALE_MISSING` — ``has_file`` is False (no probe has ever run).
    2. :data:`STALE_KEYLESS` — the cache predates the capability fields
       (#1799) VRAM/RAM attribution and the ROCm lane derivation both read:
       a GPU row with neither ``vulkan_capable`` nor ``compute_capable`` set
       True, on a cache that also recorded no ``kernel``/``probed_at`` at
       all (the pre-#1799-era shape — a MODERN probe that legitimately found
       zero capable GPUs also has empty ``gpus``/False flags but DOES carry
       ``kernel``/``probed_at``, so it is not flagged here).
    3. :data:`STALE_KERNEL_MISMATCH` — the running kernel differs from the one
       recorded at probe time (a kernel upgrade + reboot can add or remove
       GPU driver support).
    4. :data:`STALE_ACROSS_REBOOT` — ``probed_at`` predates this boot (the
       probe ran in a previous boot cycle; hardware could have changed
       across the reboot that followed it, e.g. a passthrough device added).

    Pure and injectable (``running_kernel`` / ``boot_time_epoch_s``) for
    tests; both default to live reads of this host.
    """
    if not has_file:
        return STALE_MISSING
    if not info.kernel and not info.probed_at and not info.gpus:
        return STALE_KEYLESS
    running = running_kernel if running_kernel is not None else current_kernel_string()
    if running and info.kernel and info.kernel != running:
        return STALE_KERNEL_MISMATCH
    boot_time = boot_time_epoch_s if boot_time_epoch_s is not None else _boot_time_epoch_s()
    if boot_time is not None and info.probed_at:
        try:
            probed_dt = _dt.datetime.fromisoformat(info.probed_at)
        except ValueError:
            return None
        if probed_dt.timestamp() < boot_time:
            return STALE_ACROSS_REBOOT
    return None


#: Reasons already logged this process — a live-probe fallback runs on every
#: capacity read while a box stays stale, and warning on every one of those
#: would flood the journal. Logged once per DISTINCT reason per process.
_warned_reasons: set[str] = set()


def resolve_fresh_hardware_info() -> tuple[HardwareInfo, str | None]:
    """The hardware fact to trust right now: cached if fresh, live if not.

    Returns ``(info, stale_reason)``. ``stale_reason`` is ``None`` when the
    cache was trusted as-is. Never raises: a live probe that itself fails
    (e.g. sandboxed test environment, ``/proc`` oddities) degrades to the
    stale cache — the conservative "prefer an old real fact over inventing
    one" direction preserved from :func:`hal0.slots.capacity._host_has_capable_gpu`'s
    original contract.

    Logs a warning (journal) the first time a given staleness reason is seen
    this process — surfaced again, unconditionally, by
    ``hal0 doctor``'s ``hardware_freshness`` check on every run.
    """
    from hal0.config import paths
    from hal0.config.loader import load_hardware_info

    has_file = paths.hardware_json().exists()
    try:
        cached = load_hardware_info()
    except Exception as exc:
        log.warning("hal0.hardware.cache_unreadable", error=str(exc))
        cached = HardwareInfo()
        has_file = False

    reason = staleness_reason(cached, has_file=has_file)
    if reason is None:
        return cached, None

    try:
        from hal0.hardware.probe import HardwareProbe

        live = HardwareProbe().probe()
    except Exception as exc:
        log.warning(
            "hal0.hardware.live_probe_fallback_failed",
            reason=reason,
            error=str(exc),
        )
        return cached, reason

    if reason not in _warned_reasons:
        _warned_reasons.add(reason)
        log.warning("hal0.hardware.cache_stale_live_probed", reason=reason)
    return live, reason


__all__ = [
    "STALE_ACROSS_REBOOT",
    "STALE_KERNEL_MISMATCH",
    "STALE_KEYLESS",
    "STALE_MISSING",
    "current_kernel_string",
    "resolve_fresh_hardware_info",
    "staleness_reason",
]
