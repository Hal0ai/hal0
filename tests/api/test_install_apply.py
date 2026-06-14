"""POST /api/install/apply — orchestrated multi-slot install (FirstRun v2 D3)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from hal0.api.routes.installer import _build_slot_cfg


def test_build_slot_cfg_sets_device_profile_model():
    cfg = _build_slot_cfg(
        slot="chat",
        model_id="qwen3.6-27b",
        device="gpu-rocm",
        profile="rocm-mtp",
        port=8081,
        context_size=32768,
    )
    assert cfg["name"] == "chat"
    assert cfg["device"] == "gpu-rocm"
    assert cfg["profile"] == "rocm-mtp"
    assert cfg["enabled"] is True
    assert cfg["model"]["default"] == "qwen3.6-27b"
    assert cfg["model"]["context_size"] == 32768
    # v2 sets device+profile, NOT the legacy `backend` field.
    assert "backend" not in cfg


def _gpu_hardware():
    """A ROCm-capable box so derive_device yields gpu-rocm (this CI VM has no GPU)."""
    from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo

    return HardwareInfo(
        cpu_model="x",
        cpu_cores=8,
        cpu_threads=16,
        ram_mb=131072,
        unified_memory_mb=131072,
        platform="strix-halo",
        gpus=[
            GPUInfo(
                vendor="amd", name="g", vram_mb=80000, compute_capable=True, vulkan_capable=True
            )
        ],
        npu=NPUInfo(present=False),
    )


class _FakeProbe:
    def probe(self):
        return _gpu_hardware()


def test_apply_seeds_jobs_and_creates_slots(isolated_app_client, tmp_hal0_home, monkeypatch):
    app, client = isolated_app_client
    app.state.hardware_probe = _FakeProbe()

    # Stub the actual download so the test is hermetic — assert orchestration,
    # not network. run_pull is patched to mark the job completed immediately.
    import hal0.api.routes.installer as inst

    async def _fake_run_pull(job, **kw):
        job.state = "completed"

    monkeypatch.setattr(inst, "run_pull", _fake_run_pull)

    r = client.post(
        "/api/install/apply",
        json={"tier": "hal0-default", "storage_dir": tmp_hal0_home, "npu_opt_in": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # The default tier's primary is a curated chat model → a job + slot exist.
    assert "qwen3.5-9b" in body["model_ids"]
    chat = next(s for s in body["slots"] if s["slot"] == "chat")
    assert chat["profile"] in ("rocm-mtp", "vulkan")
    assert chat["created"] is True
    # Slot TOML written OFFLINE (not started).
    toml = Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "chat.toml"
    cfg = tomllib.loads(toml.read_text())
    assert cfg["model"]["default"] == "qwen3.5-9b"
    assert "profile" in cfg


def test_apply_unknown_tier_400(isolated_app_client):
    _app, client = isolated_app_client
    r = client.post("/api/install/apply", json={"tier": "nope", "storage_dir": "/tmp"})
    assert r.status_code in (400, 404)
