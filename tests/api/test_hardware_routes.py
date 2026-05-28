"""Unit tests for hal0.api.routes.hardware — flatten + platform pass-through.

The flatten shape is consumed by the Vue Hardware + FirstRun views; we
freeze its contract here so a future refactor of HardwareInfo doesn't
silently regress the dashboard.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import hal0.hardware.pve as pve_mod
from hal0.api.routes.hardware import _PVE_CONFIGURE_HINT, _flatten_for_ui, _platform_label
from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo


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


def test_flatten_strix_halo_is_unified() -> None:
    info = HardwareInfo(
        cpu_model="AMD Ryzen AI Max+ PRO 395",
        cpu_cores=16,
        cpu_threads=32,
        ram_mb=128 * 1024,
        unified_memory_mb=128 * 1024,
        gpus=[GPUInfo(vendor="amd", name="Radeon 8060S", vram_mb=96 * 1024)],
        npu=NPUInfo(present=True, vendor="amd", name="AMD NPU (XDNA)"),
        platform="strix-halo",
    ).model_dump(mode="python")
    flat = _flatten_for_ui(info)
    assert flat["platform"] == "strix-halo"
    assert flat["platform_label"] == "Strix Halo (unified memory)"
    assert flat["memory_kind"] == "unified"
    assert flat["is_uma"] is True


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
