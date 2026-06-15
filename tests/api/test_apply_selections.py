"""POST /api/install/apply-selections — tier-less orchestrated install.

Mirrors the app/client + ``app.state`` fakes used by tests/api/test_install_apply.py
(the ``isolated_app_client`` fixture wires real ``slot_manager`` /
``model_registry`` / ``model_pull_jobs`` via the lifespan; we override
``hardware_probe`` with a ROCm-capable fake and stub ``run_pull`` so the test
is hermetic — assert orchestration, not network).
"""

from __future__ import annotations

from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo


def _gpu_hardware() -> HardwareInfo:
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


def test_apply_selections_creates_only_selected_slots(
    isolated_app_client, tmp_hal0_home, monkeypatch
):
    app, client = isolated_app_client
    app.state.hardware_probe = _FakeProbe()

    import hal0.api.routes.installer as inst

    async def _fake_run_pull(job, **kw):
        job.state = "completed"

    monkeypatch.setattr(inst, "run_pull", _fake_run_pull)

    payload = {
        "storage_dir": tmp_hal0_home,
        "npu_opt_in": False,
        "extensions": {},
        "slots": [
            {
                "capability": "chat",
                "slot_name": "chat",
                "port": 8081,
                "model_id": "qwen3-4b",
                "device": None,
                "profile": None,
            },
        ],
    }
    r = client.post("/api/install/apply-selections", json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "chat" in [s["slot"] for s in data["slots"]]
    # ONLY the one selected slot is created — not a whole tier.
    assert len(data["slots"]) == 1
    assert "qwen3-4b" in data["model_ids"]
