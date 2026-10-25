"""The ComfyUI image-model routes: catalog, installs, jobs and the on-disk file listing (ADR-0037).

``run_install`` is patched with small fakes that drive the job the way the real one does; its
streaming, resume and checksum behaviour is covered by ``tests/comfyui/test_install.py``. The
routes under test decide *whether* an install may start and how its job is surfaced.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import namedtuple
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.comfyui import install as comfyui_install
from hal0.comfyui import inventory, paths
from hal0.comfyui.catalog import CATALOG, ImageModel, install_state
from hal0.config.schema import HardwareInfo
from hal0.registry.pull import PullJob, _tmp_dir

_BASE = "hal0.api.routes.comfyui"
_SDXL = "sdxl-lightning"


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Model store, ComfyUI data root and registry staging dir all under ``tmp_path``."""
    root = tmp_path / "store"
    monkeypatch.setenv("HAL0_MODEL_STORE", str(root))
    monkeypatch.delenv("HAL0_COMFYUI_DATA_ROOT", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    assert paths.models_root().is_relative_to(tmp_path)
    # Staging and the models tree share ``tmp_path``, so the same-filesystem guard passes
    # on any machine without being patched.
    assert _tmp_dir().is_relative_to(tmp_path)
    return root


_Usage = namedtuple("_Usage", "total used free")


@pytest.fixture(autouse=True)
def plenty_of_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a huge free space, so no route test depends on the machine's real free disk.

    ``qwen-image`` alone needs about 51 GB; CI runners have less. A test of the 507 path
    overrides this.
    """
    monkeypatch.setattr(
        comfyui_install.shutil, "disk_usage", lambda path: _Usage(10**16, 0, 10**16)
    )


@pytest.fixture(autouse=True)
def _reset_comfyui_module_state() -> Iterator[None]:
    # The status route's shared httpx client and switchover tracker are module-global.
    from hal0.api.routes import comfyui as comfyui_mod

    comfyui_mod._reset_state()
    yield
    comfyui_mod._reset_state()


class _FollowUps:
    """What an install or a removal did after the files changed, in the order it did it.

    The real auto-select and Open WebUI reconcile are replaced for every test here: the
    first would apply a capability on whatever lane the machine running the suite has, the
    second would restart whatever Open WebUI unit it has. Their own behaviour is covered by
    ``tests/comfyui/test_autoselect.py`` and ``tests/components/test_openwebui_arm.py``.
    """

    def __init__(self) -> None:
        self.events: list[Any] = []
        self.fail: set[str] = set()

    def step(self, name: str, event: Any = None) -> None:
        self.events.append(name if event is None else event)
        if name in self.fail:
            raise RuntimeError(f"{name} broke")


@pytest.fixture(autouse=True)
def follow_ups(monkeypatch: pytest.MonkeyPatch) -> _FollowUps:
    rec = _FollowUps()

    async def auto_select(model: ImageModel, *, orchestrator: Any) -> None:
        rec.step("auto_select", ("auto_select", model.id, orchestrator))

    monkeypatch.setattr(f"{_BASE}.auto_select_first_image_model", auto_select)
    monkeypatch.setattr(
        "hal0.components.openwebui_arm.reconcile_openwebui_env_background",
        lambda: rec.step("owui"),
    )
    return rec


def _land(model: ImageModel, *, only: int | None = None) -> None:
    """Put the model's weight files on disk (all of them, or just the first ``only``)."""
    for component in model.components[:only]:
        component.dest.parent.mkdir(parents=True, exist_ok=True)
        component.dest.write_bytes(b"w")


def _wait_for(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")


def _job_state(client: TestClient, model_id: str) -> str | None:
    jobs = client.get("/api/comfyui/jobs").json()["jobs"]
    return next((j["state"] for j in jobs if j["model_id"] == model_id), None)


class _Installs:
    """Records what the route handed ``run_install`` and lets a test choose how a job ends."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.behaviour: Callable[[PullJob], Any] = self.hold

    @staticmethod
    async def hold(job: PullJob) -> None:
        """Stay ``running`` until cancelled, as a long download does."""
        await asyncio.sleep(3600)

    @staticmethod
    async def until_cancel_requested(job: PullJob) -> None:
        while not job.cancel_requested:
            await asyncio.sleep(0.01)
        job.state = "cancelled"
        job.finished_at = time.time()

    @staticmethod
    async def finish(job: PullJob) -> None:
        job.state = "completed"
        job.finished_at = time.time()

    @staticmethod
    async def fail(job: PullJob) -> None:
        job.state = "failed"
        job.error = "boom"
        job.error_code = "comfyui.install_failed"
        job.finished_at = time.time()


@pytest.fixture
def installs(monkeypatch: pytest.MonkeyPatch) -> _Installs:
    rec = _Installs()

    async def fake_run_install(
        job: PullJob,
        model: ImageModel,
        *,
        hf_token: str | None,
        client: Any = None,
        on_complete: Any = None,
    ) -> None:
        rec.calls.append(
            {"job": job, "model_id": model.id, "hf_token": hf_token, "on_complete": on_complete}
        )
        job.state = "running"
        job._signal()
        try:
            await rec.behaviour(job)
        except asyncio.CancelledError:
            job.state = "cancelled"
            job.finished_at = time.time()
            raise

    monkeypatch.setattr(comfyui_install, "run_install", fake_run_install)
    return rec


def _provision_workflow(model: ImageModel) -> Path:
    """Leave the model's provisioned workflow where the launcher lists it."""
    workflow = paths.workflows_dir() / model.workflow_filename
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("{}", encoding="utf-8")
    return workflow


@pytest.fixture
def requests_made(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Give the real ``run_install`` a client that records every request and fails it."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500)

    monkeypatch.setattr(
        comfyui_install,
        "_new_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return seen


def _post_install(client: TestClient, model_id: str = _SDXL) -> Any:
    return client.post(f"/api/comfyui/models/{model_id}/install")


# ── GET /models ─────────────────────────────────────────────────────────────


def test_models_lists_catalog_with_state(client: TestClient) -> None:
    _land(CATALOG[_SDXL], only=1)  # the checkpoint, not the LoRA or the VAE
    _land(CATALOG["esrgan-4x"])

    body = client.get("/api/comfyui/models").json()

    assert [m["id"] for m in body["models"]] == list(CATALOG)
    assert set(body) == {"models", "engine"}
    by_id = {m["id"]: m for m in body["models"]}

    sdxl = by_id[_SDXL]
    model = CATALOG[_SDXL]
    assert sdxl["label"] == model.label
    assert sdxl["task"] == "txt2img"
    assert sdxl["api_renderable"] is True
    assert sdxl["license"] == model.license
    assert sdxl["est_seconds"] == model.est_seconds
    assert sdxl["sizes"] == list(model.sizes)
    assert sdxl["approx_gb"] == model.approx_gb
    assert sdxl["state"] == "partial"
    assert sdxl["missing"] == [
        "loras/sdxl_lightning_8step_lora.safetensors",
        "vae/sdxl_vae_fp16_fix.safetensors",
    ]
    assert sdxl["missing_gb"] == install_state(model).missing_gb
    assert sdxl["workflow"] == "sdxl-lightning.json"
    assert sdxl["job"] is None

    assert by_id["esrgan-4x"]["state"] == "complete"
    assert by_id["esrgan-4x"]["missing"] == []
    assert by_id["esrgan-4x"]["missing_gb"] == 0
    assert by_id["qwen-image"]["state"] == "none"


def test_models_rows_carry_the_templates_default_parameters(client: TestClient) -> None:
    by_id = {m["id"]: m for m in client.get("/api/comfyui/models").json()["models"]}

    sd15 = by_id["sd-1.5-pruned-emaonly"]
    assert sd15["defaults"]["steps"] == 20
    assert sd15["defaults"]["sampler_name"] == "euler"
    assert all(isinstance(m["defaults"], dict) for m in by_id.values())


def test_models_row_carries_its_last_job(client: TestClient, installs: _Installs) -> None:
    installs.behaviour = _Installs.finish
    job_id = _post_install(client).json()["job"]["id"]
    _wait_for(lambda: _job_state(client, _SDXL) == "completed")

    row = next(m for m in client.get("/api/comfyui/models").json()["models"] if m["id"] == _SDXL)

    assert row["job"]["id"] == job_id
    assert row["job"]["state"] == "completed"


@pytest.mark.parametrize(
    ("hardware", "expected"),
    [
        (HardwareInfo(unified_memory_mb=128 * 1024), "qwen-image-lightning"),
        (HardwareInfo(unified_memory_mb=64 * 1024), "sdxl-lightning"),
        (HardwareInfo(ram_mb=8 * 1024), "sd-1.5-pruned-emaonly"),  # no unified memory: RAM
        (HardwareInfo(unified_memory_mb=96 * 1024, ram_mb=96 * 1024), "qwen-image-lightning"),
        # A guest capped below the host's pool is ranked by what it can use (#3057): the
        # probe reads the host's 128 GiB as unified memory and the guest's limit as ram_mb.
        (HardwareInfo(unified_memory_mb=128 * 1024, ram_mb=48 * 1024), "sdxl-lightning"),
        (HardwareInfo(), "sdxl-lightning"),  # never probed: nothing to size a pool from
        (RuntimeError("hardware.json is unreadable"), "sdxl-lightning"),
    ],
    ids=[
        "128gb",
        "64gb",
        "8gb-ram",
        "unified-and-ram-agree",
        "capped-guest",
        "unprobed",
        "unreadable",
    ],
)
def test_models_marks_recommended_by_pool(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, hardware: Any, expected: str
) -> None:
    def load() -> HardwareInfo:
        if isinstance(hardware, Exception):
            raise hardware
        return hardware

    monkeypatch.setattr("hal0.comfyui.picker.load_hardware_info", load)

    models = client.get("/api/comfyui/models").json()["models"]

    assert [m["id"] for m in models if m["recommended"]] == [expected]


_NO_ROCM_REASON = "no ROCm lane on this host (ComfyUI needs /dev/kfd and a render node)"


def _host(monkeypatch: pytest.MonkeyPatch, *, rocm_lane: bool, amd: bool = True) -> None:
    """A host that advertises every GPU lane; ``rocm_lane`` is whether kfd and a render node exist."""
    from hal0.capabilities import catalog

    monkeypatch.setattr(
        catalog,
        "available_backends",
        lambda: [{"id": "gpu-vulkan"}, {"id": "gpu-rocm"}, {"id": "cpu"}],
    )
    monkeypatch.setattr(catalog, "host_is_amd_gpu", lambda: amd)
    monkeypatch.setattr(catalog, "rocm_lane_present", lambda: rocm_lane)


def test_models_route_reports_engine_unavailable_on_a_vulkan_host(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An NVIDIA or Intel box with a Vulkan node has no ROCm lane either; the reason is exact."""
    _host(monkeypatch, rocm_lane=False, amd=False)

    body = client.get("/api/comfyui/models").json()

    assert body["engine"] == {"available": False, "reason": _NO_ROCM_REASON}


def test_models_route_reports_engine_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host(monkeypatch, rocm_lane=False)

    body = client.get("/api/comfyui/models").json()

    assert body["engine"] == {"available": False, "reason": _NO_ROCM_REASON}
    # The list itself is unchanged: the catalog is still browsable on a box that cannot run it.
    assert [m["id"] for m in body["models"]] == list(CATALOG)


def test_models_route_reports_engine_available(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host(monkeypatch, rocm_lane=True)

    body = client.get("/api/comfyui/models").json()

    assert body["engine"] == {"available": True, "reason": None}
    assert [m["id"] for m in body["models"]] == list(CATALOG)


# ── GET /status ─────────────────────────────────────────────────────────────

_SLOT_ID_CASES = [
    # The img slot's config as the slot manager reads it. An id-keyed box stores the
    # durable id; a name-keyed one has only the display name.
    pytest.param({"name": "img", "id": 10}, "10", id="id-keyed"),
    pytest.param({"name": "img"}, "img", id="name-keyed"),
]


@pytest.mark.parametrize(("slot_cfg", "token"), _SLOT_ID_CASES)
def test_status_container_name_follows_slot_id(
    client: TestClient, slot_cfg: dict[str, Any], token: str
) -> None:
    """The container and its unit are named after the slot id on a migrated box (#2302)."""
    client.app.state.slot_manager.get_config = AsyncMock(return_value=slot_cfg)
    probed: list[str] = []

    async def active(unit: str) -> bool:
        probed.append(unit)
        return unit == f"hal0-slot@{token}.service"

    with (
        patch(f"{_BASE}._systemd_active", side_effect=active),
        patch(f"{_BASE}._fetch_json", new_callable=AsyncMock, return_value=None),
    ):
        body = client.get("/api/comfyui/status").json()

    assert body["container"] == {"name": f"hal0-slot-{token}", "state": "running"}
    assert f"hal0-slot@{token}.service" in probed


def test_status_container_name_falls_back_to_the_seeded_name_without_a_slot_manager(
    client: TestClient,
) -> None:
    client.app.state.slot_manager = None

    with (
        patch(f"{_BASE}._systemd_active", new_callable=AsyncMock, return_value=False),
        patch(f"{_BASE}._fetch_json", new_callable=AsyncMock, return_value=None),
    ):
        body = client.get("/api/comfyui/status").json()

    assert body["container"] == {"name": "hal0-slot-img", "state": "absent"}


@pytest.mark.parametrize(("slot_cfg", "token"), _SLOT_ID_CASES)
def test_logs_read_the_unit_that_follows_slot_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, slot_cfg: dict[str, Any], token: str
) -> None:
    client.app.state.slot_manager.get_config = AsyncMock(return_value=slot_cfg)
    argv: list[tuple[Any, ...]] = []

    class _Proc:
        async def communicate(self) -> tuple[bytes, bytes]:
            return b"2026-10-24T00:00:00 started\n", b""

    async def fake_exec(*args: Any, **_kw: Any) -> _Proc:
        argv.append(args)
        return _Proc()

    monkeypatch.setattr(f"{_BASE}.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(f"{_BASE}.asyncio.create_subprocess_exec", fake_exec)

    body = client.get("/api/comfyui/logs").json()

    assert body["lines"] == ["2026-10-24T00:00:00 started"]
    assert argv and f"hal0-slot@{token}.service" in argv[0]


def test_status_inventory_derived_from_files(client: TestClient) -> None:
    """``/status`` ``inventory`` is a projection of the file listing, not a second scan."""
    paths.models_root().mkdir(parents=True)
    listing = [
        {"kind": "checkpoint", "subdir": "checkpoints", "relpath": f"c{i}.safetensors"}
        for i in range(3)
    ] + [{"kind": "clip", "subdir": "clip", "relpath": "t5.safetensors"}]

    with (
        patch.object(inventory, "list_files", return_value=listing),
        patch(f"{_BASE}._systemd_active", new_callable=AsyncMock, return_value=False),
        patch(f"{_BASE}._fetch_json", new_callable=AsyncMock, return_value=None),
    ):
        body = client.get("/api/comfyui/status").json()

    assert body["inventory"] == {
        "checkpoints": 3,
        "diffusion": 0,
        "loras": 0,
        "vae": 0,
        "controlnet": 0,
        "upscale": 0,
        "text_encoders": 1,  # clip folds into text_encoders
    }


def test_status_does_not_read_checkpoint_headers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/status`` only needs counts, so a poll must not open every user checkpoint."""
    import json
    import struct

    raw = json.dumps(
        {"conditioner.embedders.1.x": {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}}
    ).encode("utf-8")
    ckpt = paths.models_root() / "checkpoints" / "mine.safetensors"
    ckpt.parent.mkdir(parents=True)
    ckpt.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\x00\x00")

    def boom(_path: Path) -> None:
        raise AssertionError("/status sniffed a checkpoint header")

    real_sniff = inventory.sniff_checkpoint_arch
    monkeypatch.setattr(inventory, "sniff_checkpoint_arch", boom)
    with (
        patch(f"{_BASE}._systemd_active", new_callable=AsyncMock, return_value=False),
        patch(f"{_BASE}._fetch_json", new_callable=AsyncMock, return_value=None),
    ):
        status = client.get("/api/comfyui/status").json()

    assert status["inventory"]["checkpoints"] == 1
    # The files route is the one that sniffs, and still does.
    monkeypatch.setattr(inventory, "sniff_checkpoint_arch", real_sniff)
    (entry,) = client.get("/api/comfyui/files").json()["files"]
    assert (entry["arch"], entry["api_id"]) == ("sdxl", "ckpt/mine")


def test_status_inventory_is_none_without_a_models_tree(client: TestClient) -> None:
    assert not paths.models_root().exists()
    with (
        patch(f"{_BASE}._systemd_active", new_callable=AsyncMock, return_value=False),
        patch(f"{_BASE}._fetch_json", new_callable=AsyncMock, return_value=None),
    ):
        body = client.get("/api/comfyui/status").json()

    assert body["inventory"] is None


def test_status_survives_inventory_failure(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A scan that blows up costs the pane its counts, never the whole poll.

    The counts are hidden (``None``), not reported as zeros for a tree nobody could read.
    """
    with (
        patch.object(inventory, "list_files", side_effect=RuntimeError("scan blew up")),
        patch(f"{_BASE}._systemd_active", new_callable=AsyncMock, return_value=False),
        patch(f"{_BASE}._fetch_json", new_callable=AsyncMock, return_value=None),
        caplog.at_level("WARNING", logger=_BASE),
    ):
        without_tree = client.get("/api/comfyui/status")
        paths.models_root().mkdir(parents=True)
        with_tree = client.get("/api/comfyui/status")

    assert without_tree.status_code == 200
    assert without_tree.json()["inventory"] is None
    assert with_tree.status_code == 200
    assert with_tree.json()["inventory"] is None  # a tree exists, but the scan failed
    assert "comfyui.inventory_scan_failed" in caplog.text


# ── POST /models/{id}/install ───────────────────────────────────────────────


def test_install_route_202_creates_job(
    client: TestClient, installs: _Installs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")

    resp = _post_install(client)

    assert resp.status_code == 202, resp.text
    job = resp.json()["job"]
    assert job["model_id"] == _SDXL
    assert job["state"] == "queued"
    assert set(job) >= {"id", "bytes_downloaded", "bytes_total", "started_at", "files", "error"}
    stored = client.app.state.image_install_jobs[_SDXL]
    assert stored.job_id == job["id"]
    _wait_for(lambda: bool(installs.calls))
    (call,) = installs.calls
    assert call["job"] is stored
    assert call["model_id"] == _SDXL
    assert call["hf_token"] == "hf_test_token"
    assert _job_state(client, _SDXL) == "running"


def test_install_route_409_while_running(client: TestClient, installs: _Installs) -> None:
    first = _post_install(client).json()["job"]

    resp = _post_install(client)

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "comfyui.install_running"
    assert error["details"]["job"]["id"] == first["id"]
    assert error["details"]["job"]["model_id"] == _SDXL
    _wait_for(lambda: bool(installs.calls))
    assert len(installs.calls) == 1  # no second install was started
    assert client.app.state.image_install_jobs[_SDXL].job_id == first["id"]


def test_install_route_allows_a_retry_after_a_finished_job(
    client: TestClient, installs: _Installs
) -> None:
    installs.behaviour = _Installs.fail
    first = _post_install(client).json()["job"]
    _wait_for(lambda: _job_state(client, _SDXL) == "failed")
    installs.behaviour = _Installs.hold

    resp = _post_install(client)

    assert resp.status_code == 202
    assert resp.json()["job"]["id"] != first["id"]  # the new job replaced the failed one
    assert client.app.state.image_install_jobs[_SDXL].job_id == resp.json()["job"]["id"]


def test_install_route_409_when_complete_and_workflow_present(
    client: TestClient, installs: _Installs
) -> None:
    _land(CATALOG[_SDXL])
    _provision_workflow(CATALOG[_SDXL])

    resp = _post_install(client)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "comfyui.model_installed"
    assert installs.calls == []
    assert client.app.state.image_install_jobs == {}


def test_install_complete_model_without_workflow_writes_workflow(
    client: TestClient, requests_made: list[httpx.Request]
) -> None:
    """``ltx2-t2v`` leaves ``ltx2-i2v`` complete with no workflow; the launcher lists only
    provisioned workflows, so the install must run to write it (and download nothing)."""
    model = CATALOG["ltx2-i2v"]
    _land(model)
    workflow = paths.workflows_dir() / model.workflow_filename
    assert install_state(model).state == "complete"
    assert not workflow.exists()

    resp = _post_install(client, model.id)

    assert resp.status_code == 202, resp.text
    assert resp.json()["job"]["model_id"] == model.id
    _wait_for(lambda: _job_state(client, model.id) == "completed")
    assert workflow.is_file()
    assert requests_made == []  # every weight was already there
    # With the workflow in place it is "already installed" from now on.
    again = _post_install(client, model.id)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "comfyui.model_installed"


def test_install_complete_model_without_workflow_skips_the_download_guards(
    client: TestClient,
    requests_made: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is downloaded, so a full disk or a read-only models tree is not a reason to refuse."""
    model = CATALOG["ltx2-i2v"]
    _land(model)
    monkeypatch.setattr(comfyui_install.shutil, "disk_usage", lambda path: _Usage(0, 0, 1))

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a download-only guard ran for a model with nothing to download")

    monkeypatch.setattr(comfyui_install, "require_writable_destinations", forbidden)
    monkeypatch.setattr(comfyui_install, "require_one_filesystem", forbidden)

    resp = _post_install(client, model.id)

    assert resp.status_code == 202, resp.text
    _wait_for(lambda: _job_state(client, model.id) == "completed")
    assert (paths.workflows_dir() / model.workflow_filename).is_file()


def test_install_partial_model_still_runs_every_download_guard(
    client: TestClient, installs: _Installs, monkeypatch: pytest.MonkeyPatch
) -> None:
    _land(CATALOG[_SDXL], only=1)
    _provision_workflow(CATALOG[_SDXL])  # a workflow does not make a partial model installed
    monkeypatch.setattr(comfyui_install.shutil, "disk_usage", lambda path: _Usage(0, 0, 1))

    resp = _post_install(client)

    assert resp.status_code == 507
    assert resp.json()["error"]["code"] == "comfyui.insufficient_disk"


def test_install_route_404_unknown(client: TestClient, installs: _Installs) -> None:
    resp = _post_install(client, "no-such-model")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "comfyui.model_unknown"
    assert installs.calls == []


def test_install_route_507_when_disk_short(
    client: TestClient, installs: _Installs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(comfyui_install.shutil, "disk_usage", lambda path: _Usage(0, 0, 1))

    resp = _post_install(client)

    assert resp.status_code == 507
    assert resp.json()["error"]["code"] == "comfyui.insufficient_disk"
    assert installs.calls == []
    assert client.app.state.image_install_jobs == {}  # refused before a job existed


def test_install_route_409_cross_device(
    client: TestClient,
    installs: _Installs,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A staging dir on another filesystem is refused synchronously, with no job created."""
    staging = tmp_path / "other-disk" / ".tmp"
    staging.mkdir(parents=True)
    monkeypatch.setattr(comfyui_install, "_tmp_dir", lambda: staging)
    monkeypatch.setattr(
        comfyui_install,
        "_device_of",
        lambda p: 2 if Path(p).is_relative_to(staging.parent) else 1,
    )

    resp = _post_install(client)

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "comfyui.staging_cross_device"
    assert error["details"]["staging_dir"] == str(staging)
    assert installs.calls == []
    assert client.app.state.image_install_jobs == {}


def test_install_route_409_models_dir_not_writable(
    client: TestClient, installs: _Installs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A destination the hal0 user cannot write is refused synchronously, with no job (I2)."""
    blocked = CATALOG[_SDXL].components[0].dest.parent
    blocked.mkdir(parents=True)
    real_access = os.access

    def access(path: Any, mode: int, **kwargs: Any) -> bool:
        if mode & os.W_OK and Path(path) == blocked:
            return False
        return real_access(path, mode, **kwargs)

    monkeypatch.setattr(comfyui_install.os, "access", access)

    resp = _post_install(client)

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "comfyui.models_dir_not_writable"
    assert error["details"] == {"path": str(blocked)}
    assert str(blocked) in error["message"]
    assert installs.calls == []
    assert client.app.state.image_install_jobs == {}  # refused before a job existed


# ── DELETE /models/{id} ─────────────────────────────────────────────────────


def test_remove_route_deletes_and_409_while_running(
    client: TestClient, installs: _Installs
) -> None:
    model = CATALOG[_SDXL]
    _land(model)
    workflow = paths.workflows_dir() / model.workflow_filename
    workflow.parent.mkdir(parents=True)
    workflow.write_text("{}", encoding="utf-8")

    resp = client.delete(f"/api/comfyui/models/{_SDXL}")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "removed": [
            "checkpoints/sd_xl_base_1.0.safetensors",
            "loras/sdxl_lightning_8step_lora.safetensors",
            "vae/sdxl_vae_fp16_fix.safetensors",
            "workflows/sdxl-lightning.json",
        ],
        "kept": [],
        "affected": [],
    }
    assert install_state(model).state == "none"
    assert not workflow.exists()

    # An install in flight owns the files: removing under it is refused.
    assert _post_install(client).status_code == 202
    _land(model)
    blocked = client.delete(f"/api/comfyui/models/{_SDXL}")

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "comfyui.install_running"
    assert install_state(model).state == "complete"  # nothing was deleted


def test_remove_route_after_a_finished_job_deletes(client: TestClient, installs: _Installs) -> None:
    installs.behaviour = _Installs.finish
    _post_install(client)
    _wait_for(lambda: _job_state(client, _SDXL) == "completed")
    _land(CATALOG[_SDXL])

    resp = client.delete(f"/api/comfyui/models/{_SDXL}")

    assert resp.status_code == 200
    assert install_state(CATALOG[_SDXL]).state == "none"


def test_remove_route_404_unknown(client: TestClient) -> None:
    resp = client.delete("/api/comfyui/models/no-such-model")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "comfyui.model_unknown"


def test_remove_route_of_a_model_with_nothing_on_disk_removes_nothing(
    client: TestClient,
) -> None:
    resp = client.delete(f"/api/comfyui/models/{_SDXL}")

    assert resp.status_code == 200
    assert resp.json() == {"removed": [], "kept": [], "affected": []}


def test_remove_route_409_when_shared_component_installing(
    client: TestClient, installs: _Installs
) -> None:
    """qwen-image-edit and qwen-image load the same text encoder and VAE: while one installs,
    removing the other could pull out a file the installer just skipped as present."""
    installing, removing = CATALOG["qwen-image"], CATALOG["qwen-image-edit"]
    shared = {c.dest for c in installing.components} & {c.dest for c in removing.components}
    assert len(shared) == 2, "the catalog shares the Qwen text encoder and VAE"
    _land(removing)
    assert _post_install(client, installing.id).status_code == 202

    resp = client.delete(f"/api/comfyui/models/{removing.id}")

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "comfyui.install_running"
    assert error["details"]["busy_model_id"] == installing.id
    assert error["details"]["job"]["model_id"] == installing.id
    assert installing.label in error["message"]
    assert install_state(removing).state == "complete"  # not one file was deleted


def test_remove_route_allowed_again_once_the_shared_install_ends(
    client: TestClient, installs: _Installs
) -> None:
    installs.behaviour = _Installs.until_cancel_requested
    installing, removing = CATALOG["qwen-image"], CATALOG["qwen-image-edit"]
    _land(removing)
    assert _post_install(client, installing.id).status_code == 202
    assert client.delete(f"/api/comfyui/models/{removing.id}").status_code == 409
    client.post(f"/api/comfyui/jobs/{installing.id}/cancel")
    _wait_for(lambda: _job_state(client, installing.id) == "cancelled")

    resp = client.delete(f"/api/comfyui/models/{removing.id}")

    assert resp.status_code == 200
    assert install_state(removing).state == "none"


def test_remove_route_ignores_an_install_that_shares_nothing(
    client: TestClient, installs: _Installs
) -> None:
    _land(CATALOG["esrgan-4x"])
    assert _post_install(client, _SDXL).status_code == 202

    resp = client.delete("/api/comfyui/models/esrgan-4x")

    assert resp.status_code == 200
    assert resp.json()["removed"] == ["upscale_models/4x-UltraSharp.pth"]


def test_remove_route_409_reports_a_partial_removal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that cannot be deleted is a typed 409 with what already went, not a 500 (I2)."""
    model = CATALOG[_SDXL]
    _land(model)
    stuck = model.components[1].dest
    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == stuck:
            raise PermissionError(13, "Permission denied")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    resp = client.delete(f"/api/comfyui/models/{_SDXL}")

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "comfyui.remove_failed"
    assert error["details"] == {
        "removed": ["checkpoints/sd_xl_base_1.0.safetensors"],
        "failed_path": str(stuck),
        "error": "[Errno 13] Permission denied",
    }


def test_remove_route_keeps_shared_files_and_lists_them_by_default(client: TestClient) -> None:
    """``ltx2-t2v`` and ``ltx2-i2v`` load one file set: removing one frees only its workflow."""
    t2v, i2v = CATALOG["ltx2-t2v"], CATALOG["ltx2-i2v"]
    _land(t2v)
    _provision_workflow(t2v)

    resp = client.delete(f"/api/comfyui/models/{t2v.id}")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "removed": ["workflows/ltx2-t2v.json"],
        "kept": [
            {"relpath": f"{c.kind.subdir}/{c.relpath}", "models": ["ltx2-i2v"]}
            for c in t2v.components
        ],
        "affected": [],
    }
    assert install_state(i2v).state == "complete"


def test_remove_route_include_shared_deletes_shared_files_and_reports_affected(
    client: TestClient,
) -> None:
    t2v, i2v = CATALOG["ltx2-t2v"], CATALOG["ltx2-i2v"]
    _land(t2v)
    _provision_workflow(t2v)
    i2v_workflow = _provision_workflow(i2v)

    resp = client.delete(f"/api/comfyui/models/{t2v.id}", params={"include_shared": "true"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "removed": [
            *(f"{c.kind.subdir}/{c.relpath}" for c in t2v.components),
            "workflows/ltx2-t2v.json",
            "workflows/ltx2-i2v.json",  # the affected model's workflow goes with its weights
        ],
        "kept": [],
        "affected": ["ltx2-i2v"],
    }
    assert install_state(t2v).state == "none"
    assert install_state(i2v).state == "none"
    assert not i2v_workflow.exists()


def test_remove_route_include_shared_false_is_the_default(client: TestClient) -> None:
    t2v, i2v = CATALOG["ltx2-t2v"], CATALOG["ltx2-i2v"]
    _land(t2v)

    resp = client.delete(f"/api/comfyui/models/{t2v.id}", params={"include_shared": "false"})

    assert resp.status_code == 200
    assert resp.json()["affected"] == []
    assert install_state(i2v).state == "complete"


def test_remove_route_include_shared_still_409s_while_a_sharing_install_runs(
    client: TestClient, installs: _Installs
) -> None:
    """The refusal is about the files, not about which mode asked to delete them."""
    installing, removing = CATALOG["qwen-image"], CATALOG["qwen-image-edit"]
    _land(removing)
    assert _post_install(client, installing.id).status_code == 202

    resp = client.delete(f"/api/comfyui/models/{removing.id}", params={"include_shared": "true"})

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "comfyui.install_running"
    assert install_state(removing).state == "complete"  # not one file was deleted


def test_remove_route_deletes_off_the_event_loop(client: TestClient) -> None:
    model = CATALOG["esrgan-4x"]
    _land(model)

    # ``asyncio.to_thread`` is patched process-wide, so unrelated background work (the
    # app's own samplers) can show up in the call list too: look for the delete itself.
    with patch(f"{_BASE}.asyncio.to_thread", wraps=asyncio.to_thread) as to_thread:
        resp = client.delete("/api/comfyui/models/esrgan-4x")

    assert resp.status_code == 200
    assert any(
        call.args == (comfyui_install.remove_model, model)
        and call.kwargs == {"include_shared": False}
        for call in to_thread.call_args_list
    )
    assert install_state(model).state == "none"


def test_remove_route_resolves_the_models_root_a_fixed_number_of_times(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared-install check and the delete used to re-read the config ~1000 times (M11)."""
    _land(CATALOG["qwen-image"])
    calls = 0
    real = paths.models_root

    def counting() -> Path:
        nonlocal calls
        calls += 1
        return real()

    monkeypatch.setattr(paths, "models_root", counting)

    assert client.delete("/api/comfyui/models/qwen-image").status_code == 200

    assert calls <= 2  # once for the blocking-install check, once for the delete


def test_install_waits_for_a_removal_in_flight(
    client: TestClient, installs: _Installs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delete runs in a worker thread, so an install must not start under it."""
    model = CATALOG[_SDXL]
    _land(model)
    started, release = threading.Event(), threading.Event()
    real_remove = comfyui_install.remove_model

    def slow_remove(target: ImageModel, **kwargs: Any) -> dict[str, Any]:
        started.set()
        assert release.wait(5), "the test never released the removal"
        return real_remove(target, **kwargs)

    monkeypatch.setattr(comfyui_install, "remove_model", slow_remove)
    results: dict[str, Any] = {}
    deleter = threading.Thread(
        target=lambda: results.update(delete=client.delete(f"/api/comfyui/models/{_SDXL}"))
    )
    installer = threading.Thread(target=lambda: results.update(install=_post_install(client)))
    deleter.start()
    try:
        assert started.wait(5)
        installer.start()
        time.sleep(0.3)
        assert "install" not in results, "the install ran while the removal was in flight"
    finally:
        release.set()
        deleter.join(5)
        if installer.ident is not None:
            installer.join(5)

    assert results["delete"].status_code == 200
    # The install saw the finished removal: nothing on disk, so it starts a job.
    assert results["install"].status_code == 202


# ── jobs ────────────────────────────────────────────────────────────────────


def test_jobs_and_cancel(client: TestClient, installs: _Installs) -> None:
    assert client.get("/api/comfyui/jobs").json() == {"jobs": []}
    installs.behaviour = _Installs.until_cancel_requested
    job_id = _post_install(client).json()["job"]["id"]
    _wait_for(lambda: _job_state(client, _SDXL) == "running")

    listed = client.get("/api/comfyui/jobs").json()["jobs"]
    assert [j["id"] for j in listed] == [job_id]
    assert listed[0]["model_id"] == _SDXL

    resp = client.post(f"/api/comfyui/jobs/{_SDXL}/cancel")

    assert resp.status_code == 202
    assert resp.json()["job"]["id"] == job_id
    assert client.app.state.image_install_jobs[_SDXL].cancel_requested is True
    _wait_for(lambda: _job_state(client, _SDXL) == "cancelled")
    # The finished job stays listed until the next install of that model replaces it.
    assert [j["state"] for j in client.get("/api/comfyui/jobs").json()["jobs"]] == ["cancelled"]


def test_cancel_404_without_a_job(client: TestClient) -> None:
    resp = client.post(f"/api/comfyui/jobs/{_SDXL}/cancel")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "comfyui.job_not_found"


def test_cancel_of_a_finished_job_changes_nothing(client: TestClient, installs: _Installs) -> None:
    installs.behaviour = _Installs.finish
    _post_install(client)
    _wait_for(lambda: _job_state(client, _SDXL) == "completed")

    resp = client.post(f"/api/comfyui/jobs/{_SDXL}/cancel")

    assert resp.status_code == 202
    assert resp.json()["job"]["state"] == "completed"
    assert client.app.state.image_install_jobs[_SDXL].cancel_requested is False


def test_jobs_lists_one_job_per_model(client: TestClient, installs: _Installs) -> None:
    _post_install(client, _SDXL)
    _post_install(client, "qwen-image")

    jobs = client.get("/api/comfyui/jobs").json()["jobs"]

    assert sorted(j["model_id"] for j in jobs) == ["qwen-image", _SDXL]


# ── shutdown ────────────────────────────────────────────────────────────────


def test_shutdown_cancels_an_inflight_install(tmp_hal0_home: str, installs: _Installs) -> None:
    app: FastAPI = create_app()
    cm = TestClient(app)
    client = cm.__enter__()
    try:
        assert _post_install(client).status_code == 202
        _wait_for(lambda: _job_state(client, _SDXL) == "running")
        task = app.state.image_install_tasks[_SDXL]
        assert not task.done()
        started = time.monotonic()
    finally:
        cm.__exit__(None, None, None)

    assert time.monotonic() - started < 5.0
    assert task.done()
    assert app.state.image_install_jobs[_SDXL].state == "cancelled"
    assert _SDXL not in app.state.image_install_tasks  # the finished task untracked itself


# ── GET /files ──────────────────────────────────────────────────────────────


def test_files_lists_kinds_and_owners(client: TestClient) -> None:
    _land(CATALOG[_SDXL])
    stray = paths.models_root() / "checkpoints" / "my_finetune.safetensors"
    stray.write_bytes(b"x" * 11)
    hand_dropped = paths.models_root() / "controlnet" / "canny.safetensors"
    hand_dropped.parent.mkdir(parents=True)
    hand_dropped.write_bytes(b"c")

    body = client.get("/api/comfyui/files").json()

    assert body["root"] == str(paths.models_root())
    files = {f"{f['subdir']}/{f['relpath']}": f for f in body["files"]}
    assert set(files) == {
        "checkpoints/my_finetune.safetensors",
        "checkpoints/sd_xl_base_1.0.safetensors",
        "controlnet/canny.safetensors",
        "loras/sdxl_lightning_8step_lora.safetensors",
        "vae/sdxl_vae_fp16_fix.safetensors",
    }
    assert files["checkpoints/my_finetune.safetensors"] == {
        "kind": "checkpoint",
        "subdir": "checkpoints",
        "relpath": "my_finetune.safetensors",
        "size_bytes": 11,
        "models": [],
        "symlink": False,
    }
    assert files["checkpoints/sd_xl_base_1.0.safetensors"]["models"] == [_SDXL]
    assert files["loras/sdxl_lightning_8step_lora.safetensors"]["kind"] == "lora"
    assert files["controlnet/canny.safetensors"]["kind"] == "controlnet"
    assert files["controlnet/canny.safetensors"]["models"] == []


def test_files_is_empty_without_a_models_tree(client: TestClient) -> None:
    body = client.get("/api/comfyui/files").json()

    assert body == {"root": str(paths.models_root()), "files": []}


# ── removed ─────────────────────────────────────────────────────────────────


def test_fetch_route_gone(client: TestClient) -> None:
    resp = client.post("/api/comfyui/models/fetch", json={"auto": True})

    assert resp.status_code in (404, 405)


def test_app_state_has_the_install_registries(client: TestClient) -> None:
    assert client.app.state.image_install_jobs == {}
    assert client.app.state.image_install_tasks == {}


# ── after an install or a removal ───────────────────────────────────────────


def _complete_install(client: TestClient, installs: _Installs, model_id: str) -> None:
    """Start an install that finishes at once, then run the ``on_complete`` the route handed it.

    The chain runs in a loop of its own, so it gets a lock of its own: the app's lock belongs
    to the TestClient's loop. That the route hands the chain the app's lock is checked apart.
    """
    installs.behaviour = _Installs.finish
    assert _post_install(client, model_id).status_code == 202
    _wait_for(lambda: bool(installs.calls))
    on_complete = installs.calls[-1]["on_complete"]
    assert on_complete.keywords["tree_lock"] is client.app.state.image_tree_lock
    asyncio.run(
        on_complete.func(CATALOG[model_id], **{**on_complete.keywords, "tree_lock": asyncio.Lock()})
    )


def test_install_on_complete_provisions_selects_then_rewires_open_webui(
    client: TestClient,
    installs: _Installs,
    follow_ups: _FollowUps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = object()
    client.app.state.capability_orchestrator = orchestrator
    monkeypatch.setattr(
        comfyui_install, "ensure_workflows", lambda: follow_ups.step("workflows") or []
    )

    _complete_install(client, installs, _SDXL)

    assert follow_ups.events == ["workflows", ("auto_select", _SDXL, orchestrator), "owui"]


def test_install_on_complete_leaves_open_webui_alone_for_a_model_the_api_cannot_render(
    client: TestClient, installs: _Installs, follow_ups: _FollowUps
) -> None:
    video = next(m.id for m in CATALOG.values() if not m.api_renderable)

    _complete_install(client, installs, video)

    assert "owui" not in follow_ups.events
    assert [e for e in follow_ups.events if isinstance(e, tuple)] == [
        ("auto_select", video, client.app.state.capability_orchestrator)
    ]  # auto-select decides for itself that a video model is not a pick


def test_install_on_complete_provisions_hand_installed_models_too(
    client: TestClient, installs: _Installs
) -> None:
    """A model whose weights were copied in by hand has no install to write its workflow."""
    by_hand = CATALOG["sd-1.5-pruned-emaonly"]
    _land(by_hand)

    _complete_install(client, installs, _SDXL)

    assert (paths.workflows_dir() / by_hand.workflow_filename).is_file()


@pytest.mark.parametrize("broken", ["workflows", "auto_select", "owui"])
def test_install_on_complete_steps_are_independent(
    client: TestClient,
    installs: _Installs,
    follow_ups: _FollowUps,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    broken: str,
) -> None:
    """One step's failure is logged and neither skips the steps after it nor fails the job."""
    monkeypatch.setattr(
        comfyui_install, "ensure_workflows", lambda: follow_ups.step("workflows") or []
    )
    follow_ups.fail = {broken}

    with caplog.at_level("ERROR"):
        _complete_install(client, installs, _SDXL)

    ran = [e[0] if isinstance(e, tuple) else e for e in follow_ups.events]
    assert ran == ["workflows", "auto_select", "owui"]
    assert _job_state(client, _SDXL) == "completed"
    step = {"workflows": "ensure_workflows", "auto_select": "auto_select", "owui": "open_webui"}
    assert any(
        r.getMessage() == "comfyui.follow_up_failed" and r.step == step[broken]
        for r in caplog.records
    )


def test_install_on_complete_provisions_workflows_after_a_removal_in_flight(
    follow_ups: _FollowUps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the tree lock: no workflow is written for a model a removal is deleting."""
    from hal0.api.routes import comfyui as comfyui_mod

    monkeypatch.setattr(
        comfyui_install, "ensure_workflows", lambda: follow_ups.step("workflows") or []
    )

    async def scenario() -> None:
        lock = asyncio.Lock()
        await lock.acquire()  # a removal holds the tree
        chain = asyncio.create_task(
            comfyui_mod._after_install(CATALOG[_SDXL], orchestrator=None, tree_lock=lock)
        )
        await asyncio.sleep(0.1)
        assert follow_ups.events == [], "provisioned while a removal held the tree"
        lock.release()
        await chain

    asyncio.run(scenario())

    assert follow_ups.events[0] == "workflows"


def test_install_without_a_capability_orchestrator_still_runs_the_other_steps(
    client: TestClient, installs: _Installs, follow_ups: _FollowUps
) -> None:
    client.app.state.capability_orchestrator = None

    _complete_install(client, installs, _SDXL)

    assert follow_ups.events == [("auto_select", _SDXL, None), "owui"]


def test_remove_route_rewires_open_webui_after_a_removal(
    client: TestClient, follow_ups: _FollowUps
) -> None:
    _land(CATALOG[_SDXL])

    assert client.delete(f"/api/comfyui/models/{_SDXL}").status_code == 200

    assert follow_ups.events == ["owui"]


def test_remove_route_rewires_open_webui_after_a_removal_that_stopped_partway(
    client: TestClient, follow_ups: _FollowUps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files may already be gone when a delete fails, so what Open WebUI is wired to may be too."""

    def stuck(model: ImageModel, **kwargs: Any) -> dict[str, Any]:
        raise comfyui_install.RemoveFailed("could not delete", details={"removed": ["a"]})

    monkeypatch.setattr(comfyui_install, "remove_model", stuck)

    resp = client.delete(f"/api/comfyui/models/{_SDXL}")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "comfyui.remove_failed"
    assert follow_ups.events == ["owui"]


def test_remove_route_409_install_running_changes_nothing_so_rewires_nothing(
    client: TestClient, installs: _Installs, follow_ups: _FollowUps
) -> None:
    assert _post_install(client).status_code == 202
    _wait_for(lambda: _job_state(client, _SDXL) == "running")

    resp = client.delete(f"/api/comfyui/models/{_SDXL}")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "comfyui.install_running"
    assert follow_ups.events == []


def test_remove_route_succeeds_when_the_open_webui_rewire_breaks(
    client: TestClient, follow_ups: _FollowUps
) -> None:
    follow_ups.fail = {"owui"}
    _land(CATALOG[_SDXL])

    resp = client.delete(f"/api/comfyui/models/{_SDXL}")

    assert resp.status_code == 200
    assert install_state(CATALOG[_SDXL]).state == "none"


# ── startup ─────────────────────────────────────────────────────────────────


def test_startup_provisions_workflows_of_hand_installed_models(tmp_hal0_home: str) -> None:
    by_hand = CATALOG["sd-1.5-pruned-emaonly"]
    _land(by_hand)
    assert not (paths.workflows_dir() / by_hand.workflow_filename).exists()

    with TestClient(create_app()):
        pass

    assert (paths.workflows_dir() / by_hand.workflow_filename).is_file()


def test_startup_survives_a_workflow_provisioning_failure(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> list[str]:
        raise RuntimeError("workflows dir is on fire")

    monkeypatch.setattr(comfyui_install, "ensure_workflows", boom)

    with TestClient(create_app()) as client:
        assert client.get("/api/comfyui/models").status_code == 200


# ── the tree lock ───────────────────────────────────────────────────────────


def test_each_app_owns_its_tree_lock(tmp_hal0_home: str) -> None:
    """A module-level lock binds to the first event loop that contends on it; the app's does not."""
    from hal0.api.routes import comfyui as comfyui_mod

    assert not hasattr(comfyui_mod, "_TREE_LOCK")
    first, second = create_app(), create_app()
    with TestClient(first), TestClient(second):
        assert isinstance(first.state.image_tree_lock, asyncio.Lock)
        assert first.state.image_tree_lock is not second.state.image_tree_lock


# ── a models tree the hal0 user cannot read ─────────────────────────────────


def _denied(*_args: Any, **_kwargs: Any) -> Any:
    raise PermissionError(13, "Permission denied", "/srv/models/checkpoints")


@pytest.mark.parametrize(
    ("method", "url", "target"),
    [
        ("get", "/api/comfyui/models", f"{_BASE}.install_state"),
        ("post", f"/api/comfyui/models/{_SDXL}/install", f"{_BASE}.install_state"),
        ("delete", f"/api/comfyui/models/{_SDXL}", "hal0.comfyui.install.remove_model"),
    ],
    ids=["list", "install", "remove"],
)
def test_unreadable_models_tree_is_a_409_not_a_500(
    client: TestClient,
    installs: _Installs,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    url: str,
    target: str,
) -> None:
    monkeypatch.setattr(target, _denied)

    resp = getattr(client, method)(url)

    assert resp.status_code == 409, resp.text
    error = resp.json()["error"]
    assert error["code"] == "comfyui.models_dir_not_writable"
    assert error["details"] == {"path": "/srv/models/checkpoints"}
    assert "/srv/models/checkpoints" in error["message"]
    assert installs.calls == []
    assert client.app.state.image_install_jobs == {}


# ── the models root ─────────────────────────────────────────────────────────


def test_models_list_resolves_the_models_root_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every row of the catalog is measured against one root, not one config read per model."""
    calls = 0
    real = paths.models_root

    def counting() -> Path:
        nonlocal calls
        calls += 1
        return real()

    monkeypatch.setattr(paths, "models_root", counting)

    assert client.get("/api/comfyui/models").status_code == 200

    assert calls == 1


# ── an operator's removal outlives a restart ────────────────────────────────


def test_remove_route_leaves_a_workflow_only_removal_standing_across_a_restart(
    tmp_hal0_home: str,
) -> None:
    """The reviewer's case: ``qwen-image`` under ``qwen-image-lightning`` loses only its workflow."""
    _land(CATALOG["qwen-image-lightning"])
    with TestClient(create_app()) as client:
        assert (paths.workflows_dir() / "qwen-image.json").is_file()  # startup provisioned it

        resp = client.delete("/api/comfyui/models/qwen-image")

        assert resp.status_code == 200
        assert resp.json()["removed"] == ["workflows/qwen-image.json"]
        assert not (paths.workflows_dir() / "qwen-image.json").exists()

    with TestClient(create_app()):  # the next API boot
        pass

    assert not (paths.workflows_dir() / "qwen-image.json").exists()
    assert (paths.workflows_dir() / "qwen-image-lightning.json").is_file()


def test_install_route_clears_the_operator_removal(client: TestClient, installs: _Installs) -> None:
    """Installing the model again, even with every weight on disk, is asking for it back."""
    _land(CATALOG["qwen-image-lightning"])
    comfyui_install.mark_removed("qwen-image")

    resp = _post_install(client, "qwen-image")

    assert resp.status_code == 202, resp.text
    assert not comfyui_install.is_removed("qwen-image")


def test_refused_install_keeps_the_operator_removal(
    client: TestClient, installs: _Installs
) -> None:
    comfyui_install.mark_removed(_SDXL)
    assert _post_install(client).status_code == 202
    _wait_for(lambda: _job_state(client, _SDXL) == "running")
    comfyui_install.mark_removed(_SDXL)  # a removal recorded while the install is still running

    resp = _post_install(client)

    assert resp.status_code == 409
    assert comfyui_install.is_removed(_SDXL)
