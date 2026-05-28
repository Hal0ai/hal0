"""Unit tests for hal0.hardware.pve.

Minimum-scope coverage (per the 2026-05-21 design grill):
  - _summarise() shape correctness against a /cluster/resources fixture
  - project_slim() strips the heavy fields and is a no-op when unconfigured
  - save_pve_config round-trip, 0600 perms, no .tmp residue
  - delete_pve_config existed-bool semantics
  - pop_transition state machine (primes silently, fires on flip)
  - pve_status cache TTL + invalidation

Skipped on purpose (defer to v0.2):
  - PUT route token-preservation glue (covered by save+load round-trip
    invariant; the route-level "use existing if blank" rule is glue)
  - Real Proxmox integration (the canary lives in /api/stats/hardware
    on a configured deployment — see commit message)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from hal0.hardware import pve

# ── /cluster/resources fixture ─────────────────────────────────────────────────


# Trimmed sample of what Proxmox 8.x returns from /api2/json/cluster/resources.
# One node + one running LXC + one stopped LXC + one running VM.
_CLUSTER_RESOURCES: list[dict[str, Any]] = [
    {
        "type": "node",
        "node": "pve",
        "status": "online",
        "maxmem": 128 * 1024 * 1024 * 1024,  # 128 GiB
        "mem": 72 * 1024 * 1024 * 1024,  # 72 GiB used
        "maxcpu": 32,
        "cpu": 0.109,
        "uptime": 1_089_410,
    },
    {
        "type": "lxc",
        "vmid": 105,
        "name": "hal0",
        "status": "running",
        "node": "pve",
        "maxmem": 96 * 1024 * 1024 * 1024,
        "mem": int(3.78 * 1024 * 1024 * 1024),
        "maxcpu": 16,
        "cpu": 0.023,
    },
    {
        "type": "qemu",
        "vmid": 104,
        "name": "hal0-dev",
        "status": "running",
        "node": "pve",
        "maxmem": 16 * 1024 * 1024 * 1024,
        "mem": int(4.2 * 1024 * 1024 * 1024),
        "maxcpu": 8,
        "cpu": 0.05,
    },
    {
        "type": "lxc",
        "vmid": 200,
        "name": "stopped-lxc",
        "status": "stopped",
        "node": "pve",
        "maxmem": 2 * 1024 * 1024 * 1024,
        "mem": 0,
        "maxcpu": 2,
        "cpu": 0.0,
    },
    # Ignored entries — _summarise should skip these.
    {"type": "storage", "storage": "local-zfs"},
    {"type": "pool", "pool": "default"},
]


def test_summarise_host_block() -> None:
    out = pve._summarise(_CLUSTER_RESOURCES)
    assert out["configured"] is True
    assert out["ok"] is True
    assert out["node"] == "pve"
    assert out["host_mem_total_mb"] == pytest.approx(128 * 1024, rel=0.01)
    assert out["host_mem_used_mb"] == pytest.approx(72 * 1024, rel=0.01)
    assert out["host_mem_free_mb"] == pytest.approx(56 * 1024, rel=0.01)
    assert out["host_cpu_pct"] == pytest.approx(10.9, abs=0.1)
    assert out["host_cpu_count"] == 32
    assert out["host_uptime_s"] == 1_089_410


def test_summarise_tenants_filtered_and_sorted() -> None:
    out = pve._summarise(_CLUSTER_RESOURCES)
    # storage + pool ignored; only lxc + qemu kept.
    assert out["tenants_total"] == 3
    assert out["tenants_running"] == 2
    # Sort: running first (status != 'running' is the primary key, ascending),
    # then by descending maxmem. So running-96G hal0 first, running-16G hal0-dev
    # second, stopped 2G last.
    names = [t["name"] for t in out["tenants"]]
    assert names == ["hal0", "hal0-dev", "stopped-lxc"]
    # Allocated = sum of running maxmem only.
    assert out["tenants_allocated_mb"] == pytest.approx((96 + 16) * 1024, rel=0.01)


def test_summarise_empty_cluster() -> None:
    out = pve._summarise([])
    assert out["configured"] is True
    assert out["host_mem_total_mb"] == 0
    assert out["tenants_total"] == 0
    assert out["tenants"] == []


# ── Slim projection ────────────────────────────────────────────────────────────


def test_project_slim_strips_heavy_fields() -> None:
    full = pve._summarise(_CLUSTER_RESOURCES)
    slim = pve.project_slim(full)
    # Heavy / unused fields gone.
    for k in ("tenants", "host_cpu_count", "host_uptime_s", "tenants_allocated_mb"):
        assert k not in slim
    # Dashboard-relevant fields kept.
    for k in (
        "configured",
        "ok",
        "node",
        "host_mem_total_mb",
        "host_mem_used_mb",
        "host_mem_free_mb",
        "host_cpu_pct",
        "tenants_running",
        "tenants_total",
    ):
        assert k in slim


def test_project_slim_unconfigured_passthrough() -> None:
    assert pve.project_slim({"configured": False}) == {"configured": False}


def test_project_slim_error_shape_passthrough() -> None:
    err = {"configured": True, "ok": False, "error": "URLError: timed out"}
    assert pve.project_slim(err) == err


# ── Config file write / read / delete ──────────────────────────────────────────


@pytest.fixture
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect pve_config_path() to tmp_path and clear the cache."""
    target = tmp_path / "proxmox.json"
    monkeypatch.setattr(pve, "pve_config_path", lambda: target)
    pve.invalidate_pve_cache()
    # Reset transition state so tests don't poison each other.
    monkeypatch.setattr(pve, "_prev_ok", None)
    return target


_PAYLOAD = {
    "host": "10.0.1.110",
    "port": 8006,
    "user": "root@pam",
    "token_name": "hal0-readonly",
    "token_value": "00000000-0000-0000-0000-000000000000",
    "verify_ssl": False,
}


def test_save_pve_config_roundtrip(isolated_config: Path) -> None:
    pve.save_pve_config(_PAYLOAD)
    assert isolated_config.exists()
    # On-disk shape: nested {proxmox, auth} so it's haloai-compatible.
    raw = json.loads(isolated_config.read_text())
    assert raw["proxmox"]["host"] == "10.0.1.110"
    assert raw["proxmox"]["port"] == 8006
    assert raw["proxmox"]["verify_ssl"] is False
    assert raw["auth"]["user"] == "root@pam"
    assert raw["auth"]["token_name"] == "hal0-readonly"
    assert raw["auth"]["token_value"] == _PAYLOAD["token_value"]
    # _load_pve_config flattens it back.
    loaded = pve._load_pve_config()
    assert loaded is not None
    assert loaded["host"] == "10.0.1.110"
    assert loaded["token_value"] == _PAYLOAD["token_value"]


def test_save_pve_config_chmod_0600(isolated_config: Path) -> None:
    """Token is sensitive — file must not be world- or group-readable."""
    pve.save_pve_config(_PAYLOAD)
    mode = isolated_config.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_save_pve_config_no_tmpfile_residue(isolated_config: Path) -> None:
    """Atomic write — the .tmp sibling must be cleaned up after replace."""
    pve.save_pve_config(_PAYLOAD)
    leftovers = list(isolated_config.parent.glob(f".{isolated_config.name}.*"))
    assert leftovers == [], f"residue: {leftovers}"


def test_delete_pve_config_returns_existed(isolated_config: Path) -> None:
    # Empty dir → False, no error.
    assert pve.delete_pve_config() is False
    # Write then delete → True, file is gone.
    pve.save_pve_config(_PAYLOAD)
    assert isolated_config.exists()
    assert pve.delete_pve_config() is True
    assert not isolated_config.exists()
    # Second delete is still False (idempotent).
    assert pve.delete_pve_config() is False


def test_load_pve_config_missing_returns_none(isolated_config: Path) -> None:
    assert pve._load_pve_config() is None


def test_load_pve_config_malformed_returns_none(isolated_config: Path) -> None:
    isolated_config.write_text("{not json")
    assert pve._load_pve_config() is None


# ── Transition detection ──────────────────────────────────────────────────────


def test_pop_transition_primes_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pve, "_prev_ok", None)
    # First observation just primes — no transition.
    assert pve.pop_transition({"configured": True, "ok": True}) is None
    # Second matching observation — still no transition.
    assert pve.pop_transition({"configured": True, "ok": True}) is None


def test_pop_transition_ok_to_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pve, "_prev_ok", None)
    pve.pop_transition({"configured": True, "ok": True})  # prime
    assert pve.pop_transition({"configured": True, "ok": False, "error": "x"}) == "became_broken"
    # Subsequent broken observations don't re-fire.
    assert pve.pop_transition({"configured": True, "ok": False}) is None


def test_pop_transition_broken_to_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pve, "_prev_ok", None)
    pve.pop_transition({"configured": True, "ok": False})  # prime broken
    assert pve.pop_transition({"configured": True, "ok": True}) == "recovered"
    assert pve.pop_transition({"configured": True, "ok": True}) is None


def test_pop_transition_unconfigured_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing the config wipes the transition memory.

    Otherwise a configure→use→remove→configure cycle could fire a stale
    'recovered' event pulled from the prior session.
    """
    monkeypatch.setattr(pve, "_prev_ok", True)
    assert pve.pop_transition({"configured": False}) is None
    # After unconfigure, the next configured+ok observation must prime,
    # not emit recovery.
    assert pve.pop_transition({"configured": True, "ok": True}) is None


# ── Cache TTL ──────────────────────────────────────────────────────────────────


def _make_fetch_counter() -> tuple[list[int], Any]:
    """Return (counter, fetch_fn) — counter[0] increments on each call."""
    counter = [0]

    def fake_fetch(_cfg: dict[str, Any]) -> list[dict[str, Any]]:
        counter[0] += 1
        return _CLUSTER_RESOURCES

    return counter, fake_fetch


def test_pve_status_caches_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
    isolated_config: Path,
) -> None:
    pve.save_pve_config(_PAYLOAD)
    counter, fake_fetch = _make_fetch_counter()
    monkeypatch.setattr(pve, "_fetch_pve_resources", fake_fetch)

    async def run() -> tuple[dict[str, Any], dict[str, Any]]:
        a = await pve.pve_status()
        b = await pve.pve_status()
        return a, b

    a, b = asyncio.run(run())
    assert a["ok"] is True
    assert b is a  # same cached object
    assert counter[0] == 1, "should have fetched exactly once within TTL"


def test_pve_status_refetches_after_invalidate(
    monkeypatch: pytest.MonkeyPatch,
    isolated_config: Path,
) -> None:
    pve.save_pve_config(_PAYLOAD)
    counter, fake_fetch = _make_fetch_counter()
    monkeypatch.setattr(pve, "_fetch_pve_resources", fake_fetch)

    async def run() -> int:
        await pve.pve_status()
        pve.invalidate_pve_cache()
        await pve.pve_status()
        return counter[0]

    fetches = asyncio.run(run())
    assert fetches == 2


def test_pve_status_unconfigured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pve, "pve_config_path", lambda: tmp_path / "absent.json")
    pve.invalidate_pve_cache()
    out = asyncio.run(pve.pve_status())
    assert out == {"configured": False}


def test_pve_status_fetch_error_recorded(
    monkeypatch: pytest.MonkeyPatch, isolated_config: Path
) -> None:
    pve.save_pve_config(_PAYLOAD)

    def boom(_cfg: dict[str, Any]) -> list[dict[str, Any]]:
        raise TimeoutError("upstream down")

    monkeypatch.setattr(pve, "_fetch_pve_resources", boom)
    out = asyncio.run(pve.pve_status())
    assert out["configured"] is True
    assert out["ok"] is False
    assert "TimeoutError" in out["error"]
    assert "upstream down" in out["error"]


# ── detect_proxmox_host ────────────────────────────────────────────────────


class TestDetectProxmoxHost:
    """detect_proxmox_host() is best-effort, signal-driven, and never raises."""

    def test_returns_detected_when_pve_kernel_and_lxc_cgroup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        version = tmp_path / "version"
        version.write_text("Linux version 7.0.0-8-pve (root@…)\n")
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("0::/lxc.payload.105/init.scope\n")
        monkeypatch.setattr(pve, "_PROC_VERSION", version)
        monkeypatch.setattr(pve, "_PROC_1_CGROUP", cgroup)
        assert pve.detect_proxmox_host() == pve.PveDetectionState.DETECTED

    def test_returns_uncertain_when_only_pve_kernel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        version = tmp_path / "version"
        version.write_text("Linux version 7.0.0-8-pve\n")
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("0::/\n")  # not lxc-shaped
        monkeypatch.setattr(pve, "_PROC_VERSION", version)
        monkeypatch.setattr(pve, "_PROC_1_CGROUP", cgroup)
        assert pve.detect_proxmox_host() == pve.PveDetectionState.UNCERTAIN

    def test_returns_not_detected_on_bare_metal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        version = tmp_path / "version"
        version.write_text("Linux version 6.10.5-arch1-1\n")
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("0::/\n")
        monkeypatch.setattr(pve, "_PROC_VERSION", version)
        monkeypatch.setattr(pve, "_PROC_1_CGROUP", cgroup)
        assert pve.detect_proxmox_host() == pve.PveDetectionState.NOT_DETECTED

    def test_missing_files_return_not_detected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pve, "_PROC_VERSION", tmp_path / "missing-1")
        monkeypatch.setattr(pve, "_PROC_1_CGROUP", tmp_path / "missing-2")
        assert pve.detect_proxmox_host() == pve.PveDetectionState.NOT_DETECTED

    def test_never_raises_on_unreadable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # PermissionError, EncodingError, etc. all collapse to NOT_DETECTED.
        bad = tmp_path / "bad"
        bad.write_bytes(b"\xff\xfe\x00invalid")
        monkeypatch.setattr(pve, "_PROC_VERSION", bad)
        monkeypatch.setattr(pve, "_PROC_1_CGROUP", bad)
        assert pve.detect_proxmox_host() == pve.PveDetectionState.NOT_DETECTED

    def test_never_raises_on_permission_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Real OSError from read_text() must also collapse to NOT_DETECTED."""
        from unittest.mock import patch

        # Patch read_text on the Path class so both _PROC_VERSION and
        # _PROC_1_CGROUP raise when the helpers try to read them.
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            assert pve.detect_proxmox_host() == pve.PveDetectionState.NOT_DETECTED
