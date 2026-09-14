"""Tests for hal0.hardware.freshness — hardware.json staleness + live-probe
fallback (#1862/H9): a missing or stale cache must never win over a live
answer, and a probe that itself fails must never invent one.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from hal0.config.schema import GPUInfo, HardwareInfo
from hal0.hardware.freshness import (
    STALE_ACROSS_REBOOT,
    STALE_KERNEL_MISMATCH,
    STALE_KEYLESS,
    STALE_MISSING,
    resolve_fresh_hardware_info,
    staleness_reason,
)

_NOW = dt.datetime(2026, 9, 5, 12, 0, 0, tzinfo=dt.UTC)
_BOOT_TIME = (_NOW - dt.timedelta(hours=2)).timestamp()


def _fresh_info(*, probed_at: str | None = None) -> HardwareInfo:
    return HardwareInfo(
        kernel="Linux version 7.0.14-8-pve",
        probed_at=probed_at or _NOW.isoformat(timespec="seconds"),
        gpus=[GPUInfo(vendor="amd", vulkan_capable=True, compute_capable=True)],
    )


# ── staleness_reason ─────────────────────────────────────────────────────────


def test_missing_file_is_stale():
    assert (
        staleness_reason(HardwareInfo(), has_file=False, running_kernel="x", boot_time_epoch_s=0)
        == STALE_MISSING
    )


def test_keyless_pre_1799_cache_is_stale():
    """A cache with no kernel/probed_at and no GPU rows predates the
    capability fields — the pre-#1799 shape."""
    info = HardwareInfo()
    assert (
        staleness_reason(info, has_file=True, running_kernel="x", boot_time_epoch_s=0)
        == STALE_KEYLESS
    )


def test_modern_cache_with_zero_gpus_is_not_flagged_keyless():
    """A MODERN probe that legitimately found no capable GPU still carries
    kernel/probed_at — that is a real fact, not a keyless cache."""
    info = _fresh_info()
    info = info.model_copy(update={"gpus": []})
    assert (
        staleness_reason(
            info,
            has_file=True,
            running_kernel=info.kernel,
            boot_time_epoch_s=_BOOT_TIME,
        )
        is None
    )


def test_kernel_mismatch_is_stale():
    info = _fresh_info()
    assert (
        staleness_reason(
            info,
            has_file=True,
            running_kernel="Linux version 7.1.0-1-pve",
            boot_time_epoch_s=_BOOT_TIME,
        )
        == STALE_KERNEL_MISMATCH
    )


def test_probed_before_this_boot_is_stale():
    info = _fresh_info(probed_at=(_NOW - dt.timedelta(days=1)).isoformat(timespec="seconds"))
    assert (
        staleness_reason(
            info,
            has_file=True,
            running_kernel=info.kernel,
            boot_time_epoch_s=_BOOT_TIME,
        )
        == STALE_ACROSS_REBOOT
    )


def test_fresh_cache_is_trusted():
    info = _fresh_info()
    assert (
        staleness_reason(
            info,
            has_file=True,
            running_kernel=info.kernel,
            boot_time_epoch_s=_BOOT_TIME,
        )
        is None
    )


# ── resolve_fresh_hardware_info ─────────────────────────────────────────────


def test_resolve_trusts_a_fresh_cache_without_probing():
    info = _fresh_info()
    with (
        patch("hal0.config.paths.hardware_json") as mock_path,
        patch("hal0.config.loader.load_hardware_info", return_value=info),
        patch(
            "hal0.hardware.freshness.current_kernel_string",
            return_value=info.kernel,
        ),
        patch("hal0.hardware.freshness._boot_time_epoch_s", return_value=_BOOT_TIME),
        patch("hal0.hardware.probe.HardwareProbe") as mock_probe_cls,
    ):
        mock_path.return_value.exists.return_value = True
        resolved, reason = resolve_fresh_hardware_info()
    assert resolved is info
    assert reason is None
    mock_probe_cls.assert_not_called()


def test_resolve_falls_back_to_live_probe_when_missing():
    live_info = _fresh_info()
    with (
        patch("hal0.config.paths.hardware_json") as mock_path,
        patch("hal0.config.loader.load_hardware_info", return_value=HardwareInfo()),
        patch("hal0.hardware.probe.HardwareProbe") as mock_probe_cls,
    ):
        mock_path.return_value.exists.return_value = False
        mock_probe_cls.return_value.probe.return_value = live_info
        resolved, reason = resolve_fresh_hardware_info()
    assert resolved is live_info
    assert reason == STALE_MISSING


def test_resolve_degrades_to_stale_cache_when_live_probe_fails():
    """A probe that itself fails must never invent hardware — it falls back
    to the (possibly stale) cached answer, never to a made-up capable GPU."""
    stale_cache = HardwareInfo()
    with (
        patch("hal0.config.paths.hardware_json") as mock_path,
        patch("hal0.config.loader.load_hardware_info", return_value=stale_cache),
        patch("hal0.hardware.probe.HardwareProbe") as mock_probe_cls,
    ):
        mock_path.return_value.exists.return_value = False
        mock_probe_cls.return_value.probe.side_effect = RuntimeError("no /proc")
        resolved, reason = resolve_fresh_hardware_info()
    assert resolved is stale_cache
    assert reason == STALE_MISSING


def test_resolve_never_raises_on_unreadable_cache():
    with (
        patch("hal0.config.paths.hardware_json") as mock_path,
        patch("hal0.config.loader.load_hardware_info", side_effect=RuntimeError("bad json")),
        patch("hal0.hardware.probe.HardwareProbe") as mock_probe_cls,
    ):
        mock_path.return_value.exists.return_value = True
        mock_probe_cls.return_value.probe.side_effect = RuntimeError("no /proc")
        resolved, reason = resolve_fresh_hardware_info()
    assert resolved == HardwareInfo()
    assert reason == STALE_MISSING
