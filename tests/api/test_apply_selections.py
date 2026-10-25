"""POST /api/install/apply-selections — tier-less orchestrated install.

Mirrors the app/client + ``app.state`` fakes used by tests/api/test_install_apply.py
(the ``isolated_app_client`` fixture wires real ``slot_manager`` /
``model_registry`` / ``model_pull_jobs`` via the lifespan; we override
``hardware_probe`` with a ROCm-capable fake and stub ``run_pull`` so the test
is hermetic — assert orchestration, not network).
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI

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


def _wait_for_image_install(app: FastAPI, model_id: str, timeout: float = 5.0) -> None:
    """Wait for the detached install the route scheduled to finish.

    ``_schedule_install`` starts it with ``asyncio.create_task`` on the app loop and
    returns first, so under ``TestClient`` it runs on the loop thread concurrently with
    the test. The finished task untracks itself from ``app.state.image_install_tasks``.
    """
    deadline = time.monotonic() + timeout
    while model_id in app.state.image_install_tasks:
        assert time.monotonic() < deadline, f"the {model_id} install never finished"
        time.sleep(0.01)


def test_apply_selections_creates_only_selected_slots(
    isolated_app_client, tmp_hal0_home, monkeypatch
):
    app, client = isolated_app_client
    app.state.hardware_probe = _FakeProbe()

    # WS-E (#1108): run_pull_and_activate imports run_pull lazily from
    # hal0.registry.pull — patch it there.
    async def _fake_run_pull(job, **kw):
        job.state = "completed"

    monkeypatch.setattr("hal0.registry.pull.run_pull", _fake_run_pull)

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


def test_apply_selections_writes_first_run_sentinel(
    isolated_app_client, tmp_hal0_home, monkeypatch
):
    """WS-I (#1101): the apply-selections endpoint itself writes the
    first-run sentinel on success — this is the endpoint the `hal0 setup`
    API-up branch and answer-file installs POST to, so an install that
    never hits the wizard's explicit POST /complete must still converge
    with the CLI in-process path (which has always written it directly)."""
    app, client = isolated_app_client
    app.state.hardware_probe = _FakeProbe()

    async def _fake_run_pull(job, **kw):
        job.state = "completed"

    monkeypatch.setattr("hal0.registry.pull.run_pull", _fake_run_pull)

    sentinel = Path(tmp_hal0_home) / "var-lib" / "hal0" / ".first_run_done"
    assert not sentinel.exists()

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
    assert sentinel.exists()

    state = client.get("/api/install/state")
    assert state.json()["first_run"] is False


# ── image models: installed through the ComfyUI image catalog ─────────────────


def test_installer_apply_starts_image_install_jobs(
    isolated_app_client, tmp_hal0_home, monkeypatch, image_store
):
    """An ``img`` selection starts the same catalog install ``POST /api/comfyui/models/{id}/install``
    does, and the response says so: the id and its job ride in ``image_installs``, not in
    ``model_ids`` (those are for ``/api/models/{id}/pull/stream``)."""
    from hal0.comfyui import install as comfyui_install
    from hal0.registry.pull import PullJob

    app, client = isolated_app_client
    app.state.hardware_probe = _FakeProbe()
    monkeypatch.setattr("hal0.install.profile_derive.rocm_lane_present", lambda *a, **k: True)

    ran: list[str] = []
    on_completes: list = []

    async def _fake_run_install(job: PullJob, model, *, hf_token, client=None, on_complete=None):
        ran.append(model.id)
        on_completes.append(on_complete)
        job.state = "completed"

    monkeypatch.setattr(comfyui_install, "run_install", _fake_run_install)
    comfyui_install.mark_removed("sdxl-lightning")  # an earlier removal of the model

    payload = {
        "storage_dir": tmp_hal0_home,
        "slots": [
            {
                "capability": "img",
                "slot_name": "img",
                "port": 8188,
                "model_id": "sdxl-lightning",
            },
        ],
    }
    r = client.post("/api/install/apply-selections", json=payload)

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["model_ids"] == []  # nothing for the registry pull stream
    job = app.state.image_install_jobs["sdxl-lightning"]
    assert data["image_installs"] == [{"model_id": "sdxl-lightning", "job_id": job.job_id}]
    assert [s["slot"] for s in data["slots"]] == ["img"]
    assert data["slots"][0]["pull_job_id"] is None
    _wait_for_image_install(app, "sdxl-lightning")
    assert ran == ["sdxl-lightning"]
    # It is the route's install, not a copy: asking for the model clears its removal record,
    # and the follow-ups (workflows, auto-select, Open WebUI) run once it completes.
    assert not comfyui_install.is_removed("sdxl-lightning")
    assert on_completes[0].keywords["tree_lock"] is app.state.image_tree_lock
    # The catalog's job list is where a client follows it.
    jobs = client.get("/api/comfyui/jobs").json()["jobs"]
    assert [j["model_id"] for j in jobs] == ["sdxl-lightning"]


def test_installer_apply_skips_an_image_install_the_catalog_refuses(
    isolated_app_client, tmp_hal0_home, monkeypatch, image_store
):
    """A refused catalog install (here: already running) is logged and skipped, never a 5xx."""
    from hal0.registry.pull import make_job

    app, client = isolated_app_client
    app.state.hardware_probe = _FakeProbe()
    monkeypatch.setattr("hal0.install.profile_derive.rocm_lane_present", lambda *a, **k: True)
    running = make_job("sdxl-lightning")
    running.state = "running"
    app.state.image_install_jobs["sdxl-lightning"] = running

    payload = {
        "storage_dir": tmp_hal0_home,
        "slots": [
            {"capability": "img", "slot_name": "img", "port": 8188, "model_id": "sdxl-lightning"}
        ],
    }
    r = client.post("/api/install/apply-selections", json=payload)

    assert r.status_code == 200, r.text
    assert r.json()["image_installs"] == [
        {"model_id": "sdxl-lightning", "job_id": None, "skipped": "comfyui.install_running"}
    ]
    assert app.state.image_install_jobs["sdxl-lightning"] is running  # not replaced
