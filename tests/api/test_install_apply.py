"""POST /api/install/apply — orchestrated multi-slot install (FirstRun v2 D3)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.api.routes.installer import (
    CuratedModelNotFound,
    _build_slot_cfg,
    _resolve_tier,
)


@pytest.mark.parametrize(
    "sent, canonical",
    [
        # Canonical names (what the docstring originally documented).
        ("hal0-Pro", "hal0-Pro"),
        ("hal0-pro", "hal0-Pro"),
        # Bare display names — what firstrun.jsx actually POSTs (tierObj.name).
        # Regression: these all 404'd with "unknown tier" before the fix.
        ("Lite", "hal0-Lite"),
        ("Default", "hal0-Default"),
        ("Pro", "hal0-Pro"),
        ("Max", "hal0-Max"),
        ("pro", "hal0-Pro"),
        # Vendor kit keeps its verbatim mixed-case name (no hal0- prefix).
        ("LMX-Omni-52B-Halo", "LMX-Omni-52B-Halo"),
        ("lmx-omni-52b-halo", "LMX-Omni-52B-Halo"),
    ],
)
def test_resolve_tier_accepts_bare_and_canonical(sent, canonical):
    assert _resolve_tier(sent) == canonical


def test_resolve_tier_rejects_unknown():
    with pytest.raises(CuratedModelNotFound, match="unknown tier 'Nope'"):
        _resolve_tier("Nope")


def test_build_slot_cfg_sets_device_profile_model():
    cfg = _build_slot_cfg(
        slot="chat",
        model_id="qwen3.6-27b",
        device="gpu-rocm",
        profile="rocm-dnse",
        port=8081,
        context_size=32768,
    )
    assert cfg["name"] == "chat"
    assert cfg["device"] == "gpu-rocm"
    assert cfg["profile"] == "rocm-dnse"
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
    assert chat["profile"] in ("rocm-dnse", "vulkan")
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


def test_apply_honors_per_slot_override(isolated_app_client, tmp_hal0_home, monkeypatch):
    """The Advanced-drawer overrides (model_id + profile + device) win over the
    auto-derived defaults and land in the slot TOML."""
    app, client = isolated_app_client
    app.state.hardware_probe = _FakeProbe()

    import hal0.api.routes.installer as inst

    async def _fake_run_pull(job, **kw):
        job.state = "completed"

    monkeypatch.setattr(inst, "run_pull", _fake_run_pull)

    r = client.post(
        "/api/install/apply",
        json={
            "tier": "hal0-default",
            "storage_dir": tmp_hal0_home,
            "npu_opt_in": False,
            # Override chat: a different curated model + the vulkan profile/device
            # (coherent pair) instead of the auto-derived rocm-dnse.
            "overrides": {
                "chat": {"model_id": "qwen3.6-27b", "profile": "vulkan", "device": "gpu-vulkan"}
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    chat = next(s for s in body["slots"] if s["slot"] == "chat")
    assert chat["model_id"] == "qwen3.6-27b"
    assert chat["profile"] == "vulkan"
    assert chat["device"] == "gpu-vulkan"
    assert "qwen3.6-27b" in body["model_ids"]

    cfg = tomllib.loads((Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "chat.toml").read_text())
    assert cfg["model"]["default"] == "qwen3.6-27b"
    assert cfg["profile"] == "vulkan"
    assert cfg["device"] == "gpu-vulkan"
