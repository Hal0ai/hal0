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
        rec.calls.append({"job": job, "model_id": model.id, "hf_token": hf_token})
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
    assert "engine" not in body  # added with the generation work, not here
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
        (HardwareInfo(unified_memory_mb=96 * 1024, ram_mb=8 * 1024), "qwen-image-lightning"),
        (HardwareInfo(), "sdxl-lightning"),  # never probed: nothing to size a pool from
        (RuntimeError("hardware.json is unreadable"), "sdxl-lightning"),
    ],
    ids=["128gb", "64gb", "8gb-ram", "unified-wins-over-ram", "unprobed", "unreadable"],
)
def test_models_marks_recommended_by_pool(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, hardware: Any, expected: str
) -> None:
    def load() -> HardwareInfo:
        if isinstance(hardware, Exception):
            raise hardware
        return hardware

    monkeypatch.setattr(f"{_BASE}.load_hardware_info", load)

    models = client.get("/api/comfyui/models").json()["models"]

    assert [m["id"] for m in models if m["recommended"]] == [expected]


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
        ]
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
    assert resp.json() == {"removed": []}


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


def test_remove_route_deletes_off_the_event_loop(client: TestClient) -> None:
    model = CATALOG["esrgan-4x"]
    _land(model)

    # ``asyncio.to_thread`` is patched process-wide, so unrelated background work (the
    # app's own samplers) can show up in the call list too: look for the delete itself.
    with patch(f"{_BASE}.asyncio.to_thread", wraps=asyncio.to_thread) as to_thread:
        resp = client.delete("/api/comfyui/models/esrgan-4x")

    assert resp.status_code == 200
    assert any(
        call.args == (comfyui_install.remove_model, model) for call in to_thread.call_args_list
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

    def slow_remove(target: ImageModel) -> list[str]:
        started.set()
        assert release.wait(5), "the test never released the removal"
        return real_remove(target)

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
