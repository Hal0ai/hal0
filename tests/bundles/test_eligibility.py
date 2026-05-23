"""Tests for hal0.bundles.eligibility — RAM probe + tier filtering."""

from __future__ import annotations

import pytest

from hal0.bundles import eligibility
from hal0.bundles import tiers as bundle_tiers


def setup_function(_):
    eligibility.reset_cache()
    bundle_tiers.reset_cache()


def _write_meminfo(tmp_path, kb: int):
    """Write a minimal /proc/meminfo-shaped fixture file."""

    path = tmp_path / "meminfo"
    path.write_text(f"MemTotal:       {kb} kB\nMemFree:        100 kB\n", encoding="utf-8")
    return path


def test_read_meminfo_parses_memtotal(tmp_path):
    path = _write_meminfo(tmp_path, 16332620)
    # ~16 GiB
    assert eligibility._read_meminfo_gb(path) == 15


def test_read_meminfo_handles_missing_file(tmp_path):
    missing = tmp_path / "nope"
    assert eligibility._read_meminfo_gb(missing) == 0


def test_read_meminfo_handles_empty_file(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text("", encoding="utf-8")
    assert eligibility._read_meminfo_gb(path) == 0


def test_read_meminfo_handles_garbage(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text("MemTotal: notanumber kB\n", encoding="utf-8")
    assert eligibility._read_meminfo_gb(path) == 0


def test_override_env_var_wins(monkeypatch):
    monkeypatch.setenv("HAL0_HOST_RAM_GB", "128")
    assert eligibility._read_meminfo_gb() == 128


def test_override_env_var_rejects_garbage(monkeypatch):
    monkeypatch.setenv("HAL0_HOST_RAM_GB", "lots")
    assert eligibility._read_meminfo_gb() == 0


def test_host_ram_gb_caches_probe(monkeypatch):
    monkeypatch.setenv("HAL0_HOST_RAM_GB", "32")
    assert eligibility.host_ram_gb() == 32
    monkeypatch.setenv("HAL0_HOST_RAM_GB", "64")
    # Without resetting the cache, the second probe is the cached 32.
    assert eligibility.host_ram_gb() == 32
    eligibility.reset_cache()
    assert eligibility.host_ram_gb() == 64


def test_eligible_tiers_at_16gb_yields_only_lite(monkeypatch):
    monkeypatch.setenv("HAL0_HOST_RAM_GB", "16")
    assert eligibility.eligible_tiers() == ["hal0-Lite"]


def test_eligible_tiers_at_32gb_yields_lite_and_default(monkeypatch):
    monkeypatch.setenv("HAL0_HOST_RAM_GB", "32")
    assert eligibility.eligible_tiers() == ["hal0-Lite", "hal0-Default"]


def test_eligible_tiers_at_64gb_yields_up_to_pro(monkeypatch):
    monkeypatch.setenv("HAL0_HOST_RAM_GB", "64")
    assert eligibility.eligible_tiers() == [
        "hal0-Lite",
        "hal0-Default",
        "hal0-Pro",
    ]


def test_eligible_tiers_at_128gb_yields_all_five(monkeypatch):
    monkeypatch.setenv("HAL0_HOST_RAM_GB", "128")
    assert eligibility.eligible_tiers() == [
        "hal0-Lite",
        "hal0-Default",
        "hal0-Pro",
        "hal0-Max",
        "LMX-Omni-52B-Halo",
    ]


def test_eligible_tiers_unknown_ram_returns_all_tiers(monkeypatch):
    """When the probe fails (returns 0), the picker shouldn't lock the
    operator out — every tier surfaces."""

    monkeypatch.setenv("HAL0_HOST_RAM_GB", "0")
    assert eligibility.eligible_tiers() == list(bundle_tiers.BUNDLES)


def test_eligible_tiers_at_8gb_yields_empty_list(monkeypatch):
    """A box below the Lite floor gets no tiers — the picker still shows
    them all (UI greys), but the eligibility list is empty."""

    monkeypatch.setenv("HAL0_HOST_RAM_GB", "8")
    assert eligibility.eligible_tiers() == []


@pytest.mark.parametrize(
    "ram_gb,expected_count",
    [
        (15, 0),
        (16, 1),
        (31, 1),
        (32, 2),
        (63, 2),
        (64, 3),
        (99, 3),
        (100, 5),
    ],
)
def test_eligibility_boundaries(monkeypatch, ram_gb, expected_count):
    monkeypatch.setenv("HAL0_HOST_RAM_GB", str(ram_gb))
    assert len(eligibility.eligible_tiers()) == expected_count
