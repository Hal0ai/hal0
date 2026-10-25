"""Unit tests for hal0.api.routes.hardware — flatten + platform pass-through.

The flatten shape is consumed by the Vue Hardware + FirstRun views; we
freeze its contract here so a future refactor of HardwareInfo doesn't
silently regress the dashboard.
"""

from __future__ import annotations

import asyncio
import threading
import time
import types
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

import hal0.api.routes.comfyui as comfyui_mod
import hal0.api.routes.hardware as hw_mod
import hal0.hardware.pve as pve_mod
from hal0.api.routes.hardware import (
    _PVE_CONFIGURE_HINT,
    _cached_snapshot,
    _flatten_for_ui,
    _platform_label,
)
from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.hardware.gpu_view import GPUMemorySample
from hal0.hardware.memory_budget import RamBudget


def _uma_sample(**overrides: object) -> GPUMemorySample:
    """A Strix Halo-shaped live sample (tiny VRAM carve-out, huge GTT)."""
    fields: dict = dict(
        vendor="amd",
        is_uma=True,
        vram_total_mb=512.0,
        gtt_total_mb=81920.0,
        total_mb=81920.0,
        vram_used_mb=100.0,
        gtt_used_mb=2048.0,
        used_mb=2048.0,
        gpu_busy=1.0,
        util_is_forced_high=True,
    )
    fields.update(overrides)
    return GPUMemorySample(**fields)


def _discrete_sample() -> GPUMemorySample:
    """A discrete-GPU-shaped sample (big dedicated VRAM, no GTT pool)."""
    return GPUMemorySample(
        vendor="amd",
        is_uma=False,
        vram_total_mb=24576.0,
        gtt_total_mb=None,
        total_mb=24576.0,
        vram_used_mb=8192.0,
        gtt_used_mb=None,
        used_mb=8192.0,
        gpu_busy=0.3,
        util_is_forced_high=False,
    )


def test_flatten_pass_through_kvm_with_virtio_gpu() -> None:
    info = HardwareInfo(
        cpu_model="QEMU Virtual CPU version 2.5+",
        cpu_cores=4,
        cpu_threads=4,
        ram_mb=16384,
        unified_memory_mb=16384,
        gpus=[GPUInfo(vendor="unknown", name="Red Hat, Inc. Virtio 1.0 GPU")],
        npu=NPUInfo(present=False),
        platform="kvm",
    ).model_dump(mode="python")
    flat = _flatten_for_ui(info)
    assert flat["cpu_name"] == "QEMU Virtual CPU version 2.5+"
    assert flat["ram_total_mb"] == 16384
    assert flat["gpu_name"].startswith("Red Hat")
    assert flat["platform"] == "kvm"
    assert flat["platform_label"] == "KVM virtual machine"
    assert flat["memory_kind"] == "system"
    assert flat["npu_present"] is False


def test_flatten_passes_through_kfd_present() -> None:
    """``kfd_present`` is a top-level HardwareInfo field (host-truth ROCm
    feasibility signal, matching the backend's own
    ``_reconcile_device_profile`` gate) — the flatten step spreads ``**info``
    first and never overwrites it, so it must survive to the UI payload even
    when the GPU's own ``compute_capable`` is False (the FE/BE mismatch this
    field exists to fix: a kfd-present, host-rocminfo-less box actively runs
    ROCm slots but ``compute_capable`` alone reports it as infeasible)."""
    info = HardwareInfo(
        cpu_model="AMD Ryzen AI Max+ PRO 395",
        gpus=[GPUInfo(vendor="amd", name="Radeon 8060S", compute_capable=False)],
        npu=NPUInfo(present=False),
        platform="strix-halo",
        kfd_present=True,
    ).model_dump(mode="python")
    flat = _flatten_for_ui(info)
    assert flat["kfd_present"] is True
    assert flat["gpus"][0]["compute_capable"] is False


def test_flatten_strix_halo_is_unified() -> None:
    """is_uma comes from the live gpu_view sample (#703) — the old
    ``vram > ram*0.5`` route heuristic is deleted. For this fixture both
    derivations agree (96GB pooled vram > 128GB ram * 0.5)."""
    # Mirror production: the cached probe's pooled vram_mb is STALE (80 GiB,
    # frozen at boot) while the live sysfs sample is fresh (96 GiB). The live
    # sample must win so the GTT total tracks amdgpu's grown GART limit.
    info = HardwareInfo(
        cpu_model="AMD Ryzen AI Max+ PRO 395",
        cpu_cores=16,
        cpu_threads=32,
        ram_mb=128 * 1024,
        unified_memory_mb=128 * 1024,
        gpus=[GPUInfo(vendor="amd", name="Radeon 8060S", vram_mb=80 * 1024)],
        npu=NPUInfo(present=True, vendor="amd", name="AMD NPU (XDNA)"),
        platform="strix-halo",
    ).model_dump(mode="python")
    flat = _flatten_for_ui(info, _uma_sample(gtt_total_mb=96 * 1024, total_mb=96 * 1024))
    assert flat["platform"] == "strix-halo"
    assert flat["platform_label"] == "Strix Halo (unified memory)"
    assert flat["memory_kind"] == "unified"
    assert flat["is_uma"] is True
    # UMA split: the GTT pool surfaces as GTT (not VRAM), sourced from the
    # LIVE sample (96 GiB), not the stale cached vram_mb (80 GiB).
    assert flat["vram_total_mb"] == 0
    assert flat["gtt_total_mb"] == 96 * 1024


def test_flatten_discrete_sample_is_not_uma() -> None:
    """A discrete-shaped live sample keeps the dedicated-VRAM split even
    when the box has lots of VRAM relative to RAM (the case the old
    heuristic misclassified)."""
    info = HardwareInfo(
        cpu_model="AMD Ryzen 9 7950X",
        cpu_cores=16,
        cpu_threads=32,
        ram_mb=32 * 1024,
        unified_memory_mb=32 * 1024,
        gpus=[GPUInfo(vendor="amd", name="Radeon RX 7900 XTX", vram_mb=24 * 1024)],
        npu=NPUInfo(present=False),
        platform="bare-metal-amd-gpu",
    ).model_dump(mode="python")
    # Old heuristic would have said UMA (24576 > 32768 * 0.5); the
    # carve-out signature says discrete.
    flat = _flatten_for_ui(info, _discrete_sample())
    assert flat["is_uma"] is False
    assert flat["vram_total_mb"] == 24 * 1024
    assert flat["gtt_total_mb"] == 0
    assert flat["memory_kind"] == "system"


def test_flatten_without_sample_defaults_to_not_uma() -> None:
    """No live sample available (no stats singleton) → inert default."""
    info = HardwareInfo(
        cpu_model="AMD Ryzen AI Max+ PRO 395",
        ram_mb=128 * 1024,
        gpus=[GPUInfo(vendor="amd", name="Radeon 8060S", vram_mb=96 * 1024)],
        npu=NPUInfo(present=False),
        platform="strix-halo",
    ).model_dump(mode="python")
    flat = _flatten_for_ui(info)
    assert flat["is_uma"] is False
    # Platform label still drives the memory_kind for strix-halo.
    assert flat["memory_kind"] == "unified"


def test_flatten_bare_metal_nvidia_promotes_gpu_into_label() -> None:
    info = HardwareInfo(
        cpu_model="Intel i9-13900K",
        cpu_cores=8,
        cpu_threads=24,
        ram_mb=64 * 1024,
        unified_memory_mb=64 * 1024,
        gpus=[GPUInfo(vendor="nvidia", name="NVIDIA GeForce RTX 4080", vram_mb=16 * 1024)],
        npu=NPUInfo(present=False),
        platform="bare-metal-nvidia-gpu",
    ).model_dump(mode="python")
    flat = _flatten_for_ui(info)
    assert flat["memory_kind"] == "system"
    assert flat["platform_label"] == "Bare metal — NVIDIA GeForce RTX 4080"
    assert flat["vram_total_mb"] == 16 * 1024
    assert flat["gtt_total_mb"] == 0


def test_flatten_handles_legacy_payload_without_platform() -> None:
    """A pre-platform /etc/hal0/hardware.json on disk should still flatten
    cleanly — we don't want stale caches to crash the dashboard.
    """
    info = HardwareInfo(
        cpu_model="Generic x86_64",
        ram_mb=8192,
        gpus=[],
        npu=NPUInfo(present=False),
    ).model_dump(mode="python")
    # Simulate a HardwareInfo missing the platform key altogether
    info.pop("platform", None)
    flat = _flatten_for_ui(info)
    assert flat["platform"] == "unknown"
    assert flat["platform_label"] == "Unknown platform"
    assert flat["memory_kind"] == "system"


def test_platform_label_for_known_strings() -> None:
    assert _platform_label("wsl2", {}) == "WSL 2"
    assert _platform_label("proxmox-kvm", {}) == "Proxmox VM (KVM)"
    assert _platform_label("lxc", {}) == "Linux container (LXC)"
    assert _platform_label("nonsense-value", {}) == "Unknown platform"


# ── cgroup_max_mb surfacing (#372) ───────────────────────────────────────────


def test_flatten_includes_cgroup_max_mb_when_set() -> None:
    info = HardwareInfo(
        cpu_model="x86_64",
        ram_mb=96 * 1024,
        unified_memory_mb=96 * 1024,
        gpus=[],
        npu=NPUInfo(present=False),
        platform="lxc",
        cgroup_max_mb=80 * 1024,
    ).model_dump(mode="python")
    flat = _flatten_for_ui(info)
    assert flat["cgroup_max_mb"] == 80 * 1024


def test_flatten_cgroup_max_mb_none_when_unlimited() -> None:
    info = HardwareInfo(
        cpu_model="x86_64",
        ram_mb=96 * 1024,
        unified_memory_mb=96 * 1024,
        gpus=[],
        npu=NPUInfo(present=False),
        platform="lxc",
    ).model_dump(mode="python")
    flat = _flatten_for_ui(info)
    # When unlimited/unreadable, the field is absent (or None): no cap.
    assert flat.get("cgroup_max_mb") in (None,)


class TestHostDetectionInStatsHardware:
    """When proxmox.json is missing, /api/stats/hardware surfaces detection
    state so the dashboard's MemoryMap can render a Configure → nudge."""

    def test_unconfigured_detected_includes_detection_hint(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No proxmox.json → _load_pve_config returns None.
        monkeypatch.setattr(pve_mod, "_load_pve_config", lambda: None)
        monkeypatch.setattr(
            pve_mod,
            "detect_proxmox_host",
            lambda: pve_mod.PveDetectionState.DETECTED,
        )
        # Avoid pve_status cache flake across tests.
        pve_mod.invalidate_pve_cache()
        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        host = resp.json()["host"]
        assert host == {
            "configured": False,
            "detected": True,
            "detection": "detected",
            "hint": _PVE_CONFIGURE_HINT,
        }

    def test_unconfigured_not_detected_stays_silent(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pve_mod, "_load_pve_config", lambda: None)
        monkeypatch.setattr(
            pve_mod,
            "detect_proxmox_host",
            lambda: pve_mod.PveDetectionState.NOT_DETECTED,
        )
        pve_mod.invalidate_pve_cache()
        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        host = resp.json()["host"]
        # Bare-metal — keep the legacy single-key shape so older code that
        # checks `host.configured` still works, but add `detected: false`
        # so the UI's shape-discriminator branch (configured vs detected vs
        # off) stays consistent.
        assert host == {"configured": False, "detected": False}

    def test_configured_pass_through_unchanged(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When pve_status() returns configured=true, the detection block
        must not modify it — the slim projection from project_slim() flows
        through to the response untouched."""
        configured_full = {
            "configured": True,
            "ok": True,
            "node": "pve",
            "host_mem_total_mb": 131072.0,
            "host_mem_used_mb": 24576.0,
            "host_mem_free_mb": 106496.0,
            "host_cpu_pct": 5.2,
            "host_cpu_count": 32,
            "host_uptime_s": 1_089_410,
            "tenants_running": 1,
            "tenants_total": 1,
            "tenants_allocated_mb": 8192.0,
            "tenants": [
                {
                    "type": "lxc",
                    "vmid": 105,
                    "name": "hal0",
                    "status": "running",
                    "maxmem_mb": 98304.0,
                    "mem_mb": 9216.0,
                    "maxcpu": 16,
                    "cpu_pct": 2.3,
                    "node": "pve",
                }
            ],
        }

        async def fake_pve_status() -> dict[str, object]:
            return configured_full

        monkeypatch.setattr(pve_mod, "pve_status", fake_pve_status)

        # detect_proxmox_host MUST NOT be consulted in the configured path —
        # patch it to a sentinel that would fail the assertion if it ran.
        def _should_not_run() -> pve_mod.PveDetectionState:
            raise AssertionError("detect_proxmox_host called on configured host")

        monkeypatch.setattr(pve_mod, "detect_proxmox_host", _should_not_run)
        pve_mod.invalidate_pve_cache()

        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        host = resp.json()["host"]

        # The new detection keys MUST NOT appear when configured=true.
        # The configured-case path is project_slim(full) — assert equality
        # so an additive regression on the dict shape is caught.
        from hal0.hardware.pve import project_slim

        assert host == project_slim(configured_full)

    def test_unconfigured_uncertain_also_nudges(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """UNCERTAIN — one weak signal — must still surface the nudge.
        The pve.py docstring says the UI nudges on both DETECTED and
        UNCERTAIN. Only the detection field distinguishes them."""
        monkeypatch.setattr(pve_mod, "_load_pve_config", lambda: None)
        monkeypatch.setattr(
            pve_mod,
            "detect_proxmox_host",
            lambda: pve_mod.PveDetectionState.UNCERTAIN,
        )
        pve_mod.invalidate_pve_cache()
        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        host = resp.json()["host"]
        assert host == {
            "configured": False,
            "detected": True,
            "detection": "uncertain",
            "hint": _PVE_CONFIGURE_HINT,
        }


# ── _cached_snapshot coalescing + thread-offload (FIX-B) ─────────────
# ── _cached_snapshot stale-while-revalidate (FIX-#428) ───────────────


class _SnapStub:
    """Stand-in for the HardwareStats singleton on app.state. Records how
    many times snapshot() ran and which thread it ran on."""

    def __init__(self, sleep_s: float = 0.05) -> None:
        self.calls = 0
        self.threads: set[int] = set()
        # Mutable payload so we can detect that a refresh actually replaced
        # the cache (the SWR background refresh produces a different value).
        self._n = 0
        self._sleep_s = sleep_s

    def snapshot(self) -> dict:
        self.calls += 1
        self.threads.add(threading.get_ident())
        # Simulate a slow synchronous probe so concurrent callers overlap.
        time.sleep(self._sleep_s)
        self._n += 1
        return {"ram_used_gb": float(self._n), "gpu_util": 0.5}


def _fake_request(stats: object) -> object:
    """Minimal object exposing the .app.state.hardware_stats chain that
    _cached_snapshot reads."""
    state = types.SimpleNamespace(hardware_stats=stats)
    app = types.SimpleNamespace(state=state)
    return types.SimpleNamespace(app=app)


async def _wait_until(predicate, *, timeout_s: float = 1.0, step_s: float = 0.01) -> bool:
    """Poll predicate() in an event-loop-friendly sleep loop until True."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step_s)
    return predicate()


@pytest.mark.asyncio
async def test_cached_snapshot_coalesces_concurrent_polls() -> None:
    """~4 concurrent dashboard polls into a cold cache must trigger
    exactly one snapshot() probe (lock + double-checked TTL)."""
    stub = _SnapStub()
    req = _fake_request(stub)

    results = await asyncio.gather(*(_cached_snapshot(req) for _ in range(4)))

    assert stub.calls == 1
    assert all(r == {"ram_used_gb": 1.0, "gpu_util": 0.5} for r in results)
    # snapshot() ran off the event loop (in a worker thread, not the main one).
    assert stub.threads and threading.get_ident() not in stub.threads


@pytest.mark.asyncio
async def test_cached_snapshot_fresh_hits_no_extra_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Within the TTL window, repeated polls return the cached value
    and do not invoke snapshot() at all."""
    stub = _SnapStub()
    req = _fake_request(stub)

    fake_now = {"t": 500.0}
    # Patch ONLY the SWR clock seam (hw_mod._now), never time.monotonic
    # globally — asyncio's loop.time() reads time.monotonic, so freezing it
    # would hang every asyncio.sleep (incl. _wait_until + the background path).
    monkeypatch.setattr(hw_mod, "_now", lambda: fake_now["t"])

    await _cached_snapshot(req)
    await _cached_snapshot(req)  # within TTL -> cached
    assert stub.calls == 1

    # Advance halfway through TTL — still no extra probe.
    fake_now["t"] += hw_mod._SNAPSHOT_TTL_S / 2
    await _cached_snapshot(req)
    assert stub.calls == 1


@pytest.mark.asyncio
async def test_cached_snapshot_stale_returns_cached_and_refreshes_in_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #428: past the TTL, a poll returns the cached snapshot
    IMMEDIATELY (no blocking on a fresh probe) and schedules a
    background refresh. The refresh fires async; a follow-up poll
    within the new TTL window sees the updated value."""
    stub = _SnapStub()
    req = _fake_request(stub)

    fake_now = {"t": 500.0}
    # Patch ONLY the SWR clock seam (hw_mod._now), never time.monotonic
    # globally — asyncio's loop.time() reads time.monotonic, so freezing it
    # would hang every asyncio.sleep (incl. _wait_until + the background path).
    monkeypatch.setattr(hw_mod, "_now", lambda: fake_now["t"])

    # Cold start.
    r1 = await _cached_snapshot(req)
    assert stub.calls == 1
    assert r1 == {"ram_used_gb": 1.0, "gpu_util": 0.5}

    # Advance well past TTL.
    fake_now["t"] += hw_mod._SNAPSHOT_TTL_S + 0.01

    # Stale poll: MUST return the cached value synchronously, NOT block.
    t0 = time.monotonic()
    r2 = await _cached_snapshot(req)
    elapsed = time.monotonic() - t0
    # The probe takes 50ms; SWR should return essentially instantly.
    assert elapsed < 0.01, f"stale poll took {elapsed * 1000:.1f}ms — SWR returned a blocking call"
    assert r2 == {"ram_used_gb": 1.0, "gpu_util": 0.5}  # stale value preserved

    # Background refresh fires asynchronously. Wait for both the probe
    # to complete AND the in-flight flag to be cleared (cleared via
    # done_callback after the task transitions to done — the latter
    # is sequenced AFTER the probe body returns).
    assert await _wait_until(
        lambda: stub.calls >= 2 and not getattr(stub, "_refresh_in_flight", False),
        timeout_s=2.0,
    ), (
        f"background refresh not fully done "
        f"(stub.calls={stub.calls}, in_flight={getattr(stub, '_refresh_in_flight', False)})"
    )

    # Advance past TTL again — a fresh background refresh should be
    # allowed to fire (single-flight guarantee cleared).
    fake_now["t"] += hw_mod._SNAPSHOT_TTL_S + 0.01
    r3 = await _cached_snapshot(req)
    assert r3 == {"ram_used_gb": 2.0, "gpu_util": 0.5}  # newer value
    assert await _wait_until(lambda: stub.calls >= 3, timeout_s=2.0), (
        f"second background refresh did not fire (stub.calls={stub.calls})"
    )


@pytest.mark.asyncio
async def test_cached_snapshot_concurrent_stale_polls_no_wedge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #428 wedge regression: 4 concurrent stale polls must all
    return within bounded latency (no self-DoS on the event loop) and
    trigger at most ONE background refresh."""
    stub = _SnapStub(sleep_s=0.05)  # 50ms per probe
    req = _fake_request(stub)

    fake_now = {"t": 500.0}
    # Patch ONLY the SWR clock seam (hw_mod._now), never time.monotonic
    # globally — asyncio's loop.time() reads time.monotonic, so freezing it
    # would hang every asyncio.sleep (incl. _wait_until + the background path).
    monkeypatch.setattr(hw_mod, "_now", lambda: fake_now["t"])

    # Cold start.
    await _cached_snapshot(req)
    assert stub.calls == 1

    # Advance past TTL to make every subsequent call stale.
    fake_now["t"] += hw_mod._SNAPSHOT_TTL_S + 0.01

    # 4 concurrent stale polls — none should block on the probe.
    t0 = time.monotonic()
    results = await asyncio.gather(*(_cached_snapshot(req) for _ in range(4)))
    elapsed = time.monotonic() - t0
    # All return the cached stale value, none waited for the 50ms probe.
    assert all(r == {"ram_used_gb": 1.0, "gpu_util": 0.5} for r in results)
    assert elapsed < 0.02, (
        f"concurrent stale polls took {elapsed * 1000:.1f}ms — SWR self-DoS wedge"
    )

    # Exactly one background refresh fires (single-flight), not 4.
    assert await _wait_until(lambda: stub.calls >= 2, timeout_s=2.0), (
        f"single-flight background refresh did not fire (stub.calls={stub.calls})"
    )
    # Give the single in-flight task time to finish; no second one should spawn.
    await asyncio.sleep(0.15)
    assert stub.calls == 2, (
        f"expected exactly 1 background refresh for 4 concurrent stale polls, "
        f"got stub.calls={stub.calls} (single-flight broken)"
    )


@pytest.mark.asyncio
async def test_cached_snapshot_no_stats_returns_empty() -> None:
    """Missing hardware_stats singleton degrades to an empty dict."""
    req = _fake_request(None)
    assert await _cached_snapshot(req) == {}


# ── #703: typed GPU sample feeds the route — no sysfs re-reads ───────────────


class _GpuStatsStub:
    """HardwareStats stand-in carrying a Strix Halo-shaped live sample."""

    def snapshot(self) -> dict:
        return {
            "ram_used_gb": 4.0,
            "ram_available_gb": 90.0,
            "gpu_util": 1.0,
            "gpu_vram_used_mb": 2048.0,
            "gpu_vram_total_mb": 81920.0,
            "gtt_used_mb": 2048.0,
            "vram_used_mb": 100.0,
            "util_is_forced_high": True,
        }

    def gpu_sample(self) -> GPUMemorySample:
        return _uma_sample()


class TestStatsHardwareGpuSample:
    def test_stats_hardware_surfaces_util_is_forced_high(self, client: TestClient) -> None:
        """The forced-high flag is an ADDITIVE field on /api/stats/hardware;
        gpu_util stays RAW (flat 1.0 here — the lie is flagged, not fixed)."""
        client.app.state.hardware_stats = _GpuStatsStub()
        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        body = resp.json()
        assert body["util_is_forced_high"] is True
        assert body["gpu_util"] == 1.0

    def test_stats_hardware_split_comes_from_snapshot(self, client: TestClient) -> None:
        """gtt_used_mb / vram_used_mb are sourced from the cached snapshot
        (typed off the sample) — the route no longer re-reads sysfs."""
        client.app.state.hardware_stats = _GpuStatsStub()
        resp = client.get("/api/stats/hardware")
        body = resp.json()
        assert body["gtt_used_mb"] == 2048.0
        assert body["vram_used_mb"] == 100.0
        assert body["is_uma"] is True  # via _flatten_for_ui(gpu_sample)

    def test_route_module_has_no_private_probe_imports(self) -> None:
        """#703 acceptance: zero private probe helpers in the route module."""
        import inspect

        src = inspect.getsource(hw_mod)
        assert "_amd_drm_device" not in src
        assert "_read_sysfs_mb" not in src
        assert "from hal0.hardware.probe import" not in src


class TestLiveUptime:
    """#1905: uptime_s was frozen at the last ``hal0 probe`` write — a box
    that never re-probes since an upgrade reports a stale value forever."""

    def test_get_hardware_reports_live_uptime_not_cached_value(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hal0.config import paths
        from hal0.config.loader import save_hardware_info
        from hal0.config.schema import HardwareInfo

        # A hardware.json written long ago with a wildly stale uptime.
        save_hardware_info(HardwareInfo(hostname="ct150", uptime_s=4110))
        assert paths.hardware_json().exists()

        monkeypatch.setattr(hw_mod, "read_uptime_s", lambda: 326305)

        resp = client.get("/api/hardware")
        assert resp.status_code == 200, resp.text
        assert resp.json()["uptime_s"] == 326305

    def test_stats_hardware_also_reports_live_uptime(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hal0.config.loader import save_hardware_info
        from hal0.config.schema import HardwareInfo

        save_hardware_info(HardwareInfo(hostname="ct150", uptime_s=4110))
        monkeypatch.setattr(hw_mod, "read_uptime_s", lambda: 326305)

        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200, resp.text
        assert resp.json()["uptime_s"] == 326305

    def test_unreadable_proc_uptime_keeps_cached_value(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#1905 review nit: read_uptime_s() returns 0 when /proc/uptime is
        unreadable — a stale-but-plausible cached value degrades better
        than clobbering it with a hard 0."""
        from hal0.config.loader import save_hardware_info
        from hal0.config.schema import HardwareInfo

        save_hardware_info(HardwareInfo(hostname="ct150", uptime_s=4110))
        monkeypatch.setattr(hw_mod, "read_uptime_s", lambda: 0)

        resp = client.get("/api/hardware")
        assert resp.status_code == 200, resp.text
        assert resp.json()["uptime_s"] == 4110


def test_flatten_uma_carries_live_gtt_used_and_free() -> None:
    """UMA flatten surfaces the live host-truth GTT occupancy (feat: GTT
    feasibility). The sysfs counters are host truth even inside an LXC
    container, where the cgroup-shaped meminfo lies about what a GTT
    allocation can actually get."""
    info = HardwareInfo(
        cpu_model="AMD Ryzen AI Max+ PRO 395",
        ram_mb=128 * 1024,
        gpus=[GPUInfo(vendor="amd", name="Radeon 8060S", vram_mb=96 * 1024)],
        npu=NPUInfo(present=False),
        platform="strix-halo",
    ).model_dump(mode="python")
    sample = _uma_sample(gtt_total_mb=96.0 * 1024, gtt_used_mb=30.0 * 1024)
    flat = _flatten_for_ui(info, sample)
    assert flat["gtt_used_mb"] == 30.0 * 1024
    assert flat["gtt_free_mb"] == 66.0 * 1024


def test_flatten_non_uma_gtt_occupancy_is_none() -> None:
    """Discrete / no-sample shapes report None (unknown), not a fake 0."""
    info = HardwareInfo(
        cpu_model="AMD Ryzen 9 7950X",
        ram_mb=32 * 1024,
        gpus=[GPUInfo(vendor="amd", name="Radeon RX 7900 XTX", vram_mb=24 * 1024)],
        npu=NPUInfo(present=False),
        platform="bare-metal-amd-gpu",
    ).model_dump(mode="python")
    assert _flatten_for_ui(info, _discrete_sample())["gtt_used_mb"] is None
    assert _flatten_for_ui(info, _discrete_sample())["gtt_free_mb"] is None
    assert _flatten_for_ui(info)["gtt_used_mb"] is None
    assert _flatten_for_ui(info)["gtt_free_mb"] is None


# ── memory: one budget, built here, served to the dashboard (ADR-0039) ───────

_GIB = 1024**3
_RAM = RamBudget(limit_gb=64.0, used_gb=20.0, free_gb=44.0, source="container")


class _Manager:
    """``app.state.slot_manager`` stand-in: ``list()`` plus raw configs."""

    def __init__(self, slots: list[Any], configs: list[dict[str, Any]]) -> None:
        self._slots = slots
        self._configs = configs

    async def list(self) -> list[Any]:
        return list(self._slots)

    async def iter_configs(self) -> list[dict[str, Any]]:
        return list(self._configs)


class _Registry:
    """Every model is ``size_gib`` GiB on disk."""

    def __init__(self, size_gib: int) -> None:
        self._size = size_gib * _GIB

    def get(self, model_id: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(size_bytes=self._size)


def _slot(name: str, state: str = "ready") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        name=name,
        state=state,
        model_id=f"{name}-model",
        backend="rocm",
        slot_id=None,
        metadata={"provider": "llama-server", "backend": "rocm"},
    )


_BRAIN_CFG = {"name": "brain", "device": "gpu-rocm"}
_IMG_CFG = {"name": "img", "device": "gpu-rocm", "provider": "comfyui", "type": "image"}


def _system_stats(used_gib: int, total_gib: int = 80) -> dict[str, Any]:
    return {
        "devices": [
            {"vram_total": total_gib * _GIB, "vram_free": (total_gib - used_gib) * _GIB},
        ]
    }


class _NoComfyUI:
    """A ComfyUI client that never connects (the real one dials ``COMFYUI_BASE_URL``)."""

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("no ComfyUI in unit tests")


@pytest.fixture(autouse=True)
def _no_live_comfyui(monkeypatch: pytest.MonkeyPatch) -> Any:
    """No test in this module may reach a live ComfyUI, or read its engine cache.

    ``/api/stats/hardware`` asks ComfyUI for its memory on a shared GPU pool with an img
    slot. A test that wants an answer patches ``_fetch_engine_stats`` (the ``memory_box``
    fixture does) or ``_get_client`` itself; either wins over this stand-in.
    """
    comfyui_mod._reset_state()
    monkeypatch.setattr(comfyui_mod, "_get_client", lambda: _NoComfyUI())
    yield
    comfyui_mod._reset_state()


@pytest.fixture
def memory_box(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, real_memory_budget: None
) -> types.SimpleNamespace:
    """A GPU box with a brain slot and an img slot, ComfyUI not answering.

    ``box.engine`` is what ComfyUI's ``/system_stats`` returns (``None`` = down);
    ``box.engine_calls`` counts the reads that reached the engine. These tests are about the
    real collector's output, so they opt out of the suite-wide ``memory_budget`` stub.
    """
    box = types.SimpleNamespace(client=client, engine=None, engine_calls=0)

    async def fake_fetch() -> dict[str, Any] | None:
        box.engine_calls += 1
        return box.engine

    comfyui_mod._reset_state()
    monkeypatch.setattr(comfyui_mod, "_fetch_engine_stats", fake_fetch)
    monkeypatch.setattr("hal0.api.memory_source.read_ram", lambda: _RAM)
    monkeypatch.setattr("hal0.slots.capacity._host_has_capable_gpu", lambda: True)
    monkeypatch.setattr(
        "hal0.slots.capacity._container_cgroup_mem_bytes", AsyncMock(return_value=0)
    )
    client.app.state.hardware_stats = _GpuStatsStub()  # 80 GiB pool, 2 GiB used
    client.app.state.model_registry = _Registry(size_gib=8)
    client.app.state.slot_manager = _Manager([_slot("brain"), _slot("img")], [_BRAIN_CFG, _IMG_CFG])
    yield box
    comfyui_mod._reset_state()


class TestStatsHardwareMemory:
    def test_stats_hardware_has_memory_block(self, memory_box: types.SimpleNamespace) -> None:
        memory_box.engine = _system_stats(used_gib=3)

        resp = memory_box.client.get("/api/stats/hardware")

        assert resp.status_code == 200, resp.text
        memory = resp.json()["memory"]
        assert memory["gpu"]["total_gb"] == 80.0
        assert memory["gpu"]["kind"] == "shared"
        assert memory["gpu"]["image_engine_gb"] == 3.0  # ComfyUI's own figure
        assert memory["gpu"]["image_engine_slot"] == "img"
        assert memory["gpu"]["image_engine_estimated"] is False
        assert [(s["slot"], s["state"]) for s in memory["gpu"]["this_box"]] == [("brain", "ready")]
        assert memory["ram"]["source"] == "container"
        assert memory["ram"]["limit_gb"] == 64.0
        # 80 pool - 0 other guests - 0 starting - 2 margin: the 3 GB engine is room, not a cost.
        assert memory["render_free_gb"] == 78.0

    def test_per_slot_keeps_its_shape_beside_the_budget(
        self, memory_box: types.SimpleNamespace
    ) -> None:
        body = memory_box.client.get("/api/stats/hardware").json()

        assert set(body["per_slot"]) == {"brain", "img"}
        assert body["per_slot"]["brain"]["mem_mb"] >= 8192.0

    def test_a_starting_slot_is_in_the_budget_but_not_in_per_slot(
        self, memory_box: types.SimpleNamespace
    ) -> None:
        memory_box.client.app.state.slot_manager = _Manager(
            [_slot("brain", state="starting"), _slot("img")], [_BRAIN_CFG, _IMG_CFG]
        )

        body = memory_box.client.get("/api/stats/hardware").json()

        assert [(s["slot"], s["state"]) for s in body["memory"]["gpu"]["this_box"]] == [
            ("brain", "starting")
        ]
        assert "brain" not in body["per_slot"]  # the public per_slot map is resident-only

    def test_memory_block_is_always_local(
        self, memory_box: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def upstreams(request: object, path: str) -> dict[str, Any]:
            return {"remote": {"gpu_name": "elsewhere", "memory": {"stale": True}}}

        monkeypatch.setattr(hw_mod, "_proxy_upstream_endpoint", upstreams)

        body = memory_box.client.get("/api/stats/hardware").json()

        assert body["gpu_name"] == "elsewhere"  # the upstream merge still happened
        assert "stale" not in body["memory"]
        assert body["memory"]["gpu"]["total_gb"] == 80.0
        assert body["memory"]["ram"]["source"] == "container"

    def test_a_failing_collector_leaves_memory_out_not_a_500(
        self, memory_box: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def upstreams(request: object, path: str) -> dict[str, Any]:
            return {"remote": {"gpu_name": "elsewhere", "memory": {"stale": True}}}

        async def broken(*_a: object, **_k: object) -> object:
            raise RuntimeError("boom")

        monkeypatch.setattr(hw_mod, "_proxy_upstream_endpoint", upstreams)
        monkeypatch.setattr("hal0.api.memory_source.collect_memory_budget", broken)

        resp = memory_box.client.get("/api/stats/hardware")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["gpu_name"] == "elsewhere"
        assert "memory" not in body  # not another box's budget either

    @pytest.mark.timing
    def test_stats_hardware_does_not_wait_for_a_dead_engine(
        self, memory_box: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def never_answers() -> dict[str, Any] | None:
            await asyncio.sleep(3)
            return _system_stats(used_gib=70)

        monkeypatch.setattr(comfyui_mod, "_fetch_engine_stats", never_answers)

        started = time.perf_counter()
        resp = memory_box.client.get("/api/stats/hardware")
        elapsed = time.perf_counter() - started

        assert resp.status_code == 200, resp.text
        # The engine read is capped at 0.75 s; the dead engine would sleep 3 s.
        assert elapsed < 2.0
        # Falls back to the img slot's own attribution (8 GiB file + KV estimate).
        img_gb = resp.json()["per_slot"]["img"]["mem_mb"] / 1024
        assert resp.json()["memory"]["gpu"]["image_engine_gb"] == round(img_gb, 1)
        assert resp.json()["memory"]["gpu"]["image_engine_estimated"] is True

    def test_no_gpu_lane_means_no_gpu_block_and_no_engine_read(
        self, memory_box: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("hal0.slots.capacity._host_has_capable_gpu", lambda: False)

        memory = memory_box.client.get("/api/stats/hardware").json()["memory"]

        assert memory["gpu"] is None
        assert memory["render_free_gb"] is None
        assert memory["ram"]["limit_gb"] == 64.0
        assert memory_box.engine_calls == 0

    def test_a_discrete_card_never_takes_comfyuis_device_wide_figure(
        self, memory_box: types.SimpleNamespace
    ) -> None:
        """On a discrete card ComfyUI's ``vram_total - vram_free`` is the whole device
        (every slot's weights, other processes). It is neither read nor believed."""
        stats = _GpuStatsStub()
        stats.gpu_sample = _discrete_sample  # type: ignore[method-assign]
        memory_box.client.app.state.hardware_stats = stats
        memory_box.engine = _system_stats(used_gib=20, total_gib=24)  # device-wide

        body = memory_box.client.get("/api/stats/hardware").json()

        gpu = body["memory"]["gpu"]
        assert gpu["kind"] == "dedicated"
        assert memory_box.engine_calls == 0  # not even asked
        # The img slot's own row is the engine's figure, and it says so.
        assert gpu["image_engine_gb"] == round(body["per_slot"]["img"]["vram_mb"] / 1024, 1)
        assert gpu["image_engine_gb"] != 20.0
        assert gpu["image_engine_estimated"] is True

    def test_the_gpu_capability_is_probed_once_per_request_off_the_loop(
        self, memory_box: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``build_per_slot`` and the collector both need it; neither probes again."""
        probes: list[str] = []

        def probe() -> bool:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                probes.append("worker thread")
            else:
                probes.append("event loop")
            return True

        monkeypatch.setattr("hal0.slots.capacity._host_has_capable_gpu", probe)

        resp = memory_box.client.get("/api/stats/hardware")

        assert resp.status_code == 200, resp.text
        assert resp.json()["memory"]["gpu"] is not None
        assert probes == ["worker thread"]


# ── engine_used_mb: ComfyUI's own GPU usage, cached and fail-soft ────────────


@pytest.fixture
def engine_clock(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    clock = types.SimpleNamespace(now=1000.0, calls=0, stats=None)

    async def fake_fetch() -> dict[str, Any] | None:
        clock.calls += 1
        return clock.stats

    comfyui_mod._reset_state()
    monkeypatch.setattr(comfyui_mod, "_now", lambda: clock.now)
    monkeypatch.setattr(comfyui_mod, "_fetch_engine_stats", fake_fetch)
    yield clock
    comfyui_mod._reset_state()


async def test_engine_used_mb_parses_system_stats(engine_clock: types.SimpleNamespace) -> None:
    engine_clock.stats = _system_stats(used_gib=54)

    assert await comfyui_mod.engine_used_mb() == 54 * 1024.0


@pytest.mark.parametrize(
    "stats",
    [
        None,  # ComfyUI down
        {},
        {"devices": []},
        {"devices": ["not-a-device"]},
        {"devices": [{"vram_total": 80 * _GIB}]},  # no vram_free
        {"devices": [{"vram_total": "80", "vram_free": "26"}]},
        {"devices": [{"vram_total": 10 * _GIB, "vram_free": 20 * _GIB}]},  # free > total
    ],
)
async def test_engine_used_mb_is_none_without_a_usable_device_read(
    engine_clock: types.SimpleNamespace, stats: Any
) -> None:
    engine_clock.stats = stats

    assert await comfyui_mod.engine_used_mb() is None


@pytest.mark.parametrize("device_type", ["cpu", "CPU"])
async def test_engine_used_mb_is_none_when_comfyui_runs_on_the_cpu(
    engine_clock: types.SimpleNamespace, device_type: str
) -> None:
    """On the CPU ``vram_*`` describe system RAM: not the GPU pool, never the engine's share."""
    engine_clock.stats = {
        "devices": [{"type": device_type, "vram_total": 64 * _GIB, "vram_free": 20 * _GIB}]
    }

    assert await comfyui_mod.engine_used_mb() is None


async def test_engine_used_mb_reads_a_gpu_device(engine_clock: types.SimpleNamespace) -> None:
    engine_clock.stats = {
        "devices": [{"type": "cuda", "vram_total": 24 * _GIB, "vram_free": 20 * _GIB}]
    }

    assert await comfyui_mod.engine_used_mb() == 4 * 1024.0


async def test_engine_used_mb_caches_for_5s(engine_clock: types.SimpleNamespace) -> None:
    engine_clock.stats = _system_stats(used_gib=10)
    assert await comfyui_mod.engine_used_mb() == 10 * 1024.0

    engine_clock.stats = _system_stats(used_gib=20)
    engine_clock.now += 4.9
    assert await comfyui_mod.engine_used_mb() == 10 * 1024.0  # still the cached read
    assert engine_clock.calls == 1

    engine_clock.now += 0.2
    assert await comfyui_mod.engine_used_mb() == 20 * 1024.0  # expired: read again
    assert engine_clock.calls == 2


async def test_engine_used_mb_caches_a_failed_read_too(
    engine_clock: types.SimpleNamespace,
) -> None:
    engine_clock.stats = None
    assert await comfyui_mod.engine_used_mb() is None
    assert await comfyui_mod.engine_used_mb() is None

    assert engine_clock.calls == 1  # a dead engine costs one attempt per 5 s, not per poll


async def test_engine_used_mb_caches_a_cancelled_read(
    monkeypatch: pytest.MonkeyPatch, engine_clock: types.SimpleNamespace
) -> None:
    """The caller's ``wait_for`` cancels a slow read; the next poll must not wait again."""
    calls = 0

    async def slow() -> dict[str, Any] | None:
        nonlocal calls
        calls += 1
        await asyncio.sleep(3)
        return _system_stats(used_gib=1)

    monkeypatch.setattr(comfyui_mod, "_fetch_engine_stats", slow)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(comfyui_mod.engine_used_mb(), 0.05)

    assert await comfyui_mod.engine_used_mb() is None
    assert calls == 1


async def test_reset_state_drops_the_engine_cache(engine_clock: types.SimpleNamespace) -> None:
    engine_clock.stats = _system_stats(used_gib=10)
    await comfyui_mod.engine_used_mb()

    comfyui_mod._reset_state()
    await comfyui_mod.engine_used_mb()

    assert engine_clock.calls == 2


async def test_engine_read_uses_short_timeouts_without_touching_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class FakeClient:
        async def get(self, url: str, **kwargs: Any) -> httpx.Response:
            seen.update(kwargs, url=url)
            return httpx.Response(200, json=_system_stats(used_gib=1))

    comfyui_mod._reset_state()
    monkeypatch.setattr(comfyui_mod, "_get_client", lambda: FakeClient())
    monkeypatch.setenv("COMFYUI_BASE_URL", "http://engine.invalid:8188")

    stats = await comfyui_mod._fetch_engine_stats()

    assert stats == _system_stats(used_gib=1)
    assert seen["url"] == "http://engine.invalid:8188/system_stats"
    assert seen["timeout"].connect == 0.25
    assert seen["timeout"].read == 0.5
    # /status keeps its own, longer budget.
    assert comfyui_mod._DEFAULT_TIMEOUT.connect == 1.5
    assert comfyui_mod._DEFAULT_TIMEOUT.read == 4.0


@pytest.mark.parametrize(
    "outcome",
    [
        httpx.Response(503, json={}),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json=["a", "list"]),
        httpx.ConnectTimeout("slow"),
        httpx.ConnectError("refused"),
    ],
)
async def test_engine_read_is_none_on_any_failure(
    monkeypatch: pytest.MonkeyPatch, outcome: object
) -> None:
    class FakeClient:
        async def get(self, url: str, **kwargs: Any) -> httpx.Response:
            if isinstance(outcome, Exception):
                raise outcome
            return outcome  # type: ignore[return-value]

    monkeypatch.setattr(comfyui_mod, "_get_client", lambda: FakeClient())

    assert await comfyui_mod._fetch_engine_stats() is None


def test_device_used_bytes_is_the_parse_behind_engine_used_mb() -> None:
    """``vram_total - vram_free`` of the first device, the one parse ``engine_used_mb`` reads.

    ``/status`` no longer parses it for a memory block of its own (its figures are the budget's).
    """
    stats = _system_stats(used_gib=54)

    assert comfyui_mod._device_used_bytes(stats) == 54 * _GIB
    assert not hasattr(comfyui_mod, "_parse_memory")
