"""Phase 4 TDD tests — control + monitoring routes for ComfyUI.

Covers:
  POST /api/comfyui/render/cancel   — clears queue + interrupts
  POST /api/comfyui/restart         — restarts the slot-managed img runtime
  GET  /api/comfyui/logs?tail=N     — img-slot journal lines
  POST /api/comfyui/workflows/{name}/launch — reads workflow file + posts /prompt
  GET  /api/comfyui/preview         — proxies latest output image bytes

All network + subprocess calls are mocked — no real ComfyUI or container.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from hal0.comfyui import progress

# ---------------------------------------------------------------------------
# Module-state isolation (same pattern as test_comfyui_proxy.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_comfyui_state():
    from hal0.api.routes import comfyui as mod

    mod._reset_state()
    yield
    mod._reset_state()


@pytest.fixture(autouse=True)
def _reset_progress():
    progress._reset()
    yield
    progress._reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = "hal0.api.routes.comfyui"


def _make_proc(returncode: int = 0, stdout: bytes = b"") -> MagicMock:
    """Return an AsyncMock that looks like asyncio.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


# ---------------------------------------------------------------------------
# POST /api/comfyui/render/cancel
# ---------------------------------------------------------------------------


class TestRenderCancel:
    def test_cancel_posts_clear_and_interrupt_returns_202(self, client: TestClient):
        """cancel must POST {base}/queue?clear=true AND {base}/interrupt."""
        posted = []

        async def fake_post(url, **kwargs):
            posted.append(url)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch(f"{_BASE}._get_client") as mock_client:
            http = MagicMock()
            http.post = AsyncMock(side_effect=fake_post)
            mock_client.return_value = http

            r = client.post("/api/comfyui/render/cancel")

        assert r.status_code == 202
        # both endpoints called
        assert any("/queue" in u for u in posted), f"no /queue in {posted}"
        assert any("/interrupt" in u for u in posted), f"no /interrupt in {posted}"

    def test_cancel_is_fail_soft_when_comfyui_unreachable(self, client: TestClient):
        """Network errors must still return 202 (fail-soft)."""
        import httpx

        with patch(f"{_BASE}._get_client") as mock_client:
            http = MagicMock()
            http.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client.return_value = http

            r = client.post("/api/comfyui/render/cancel")

        assert r.status_code == 202


# ---------------------------------------------------------------------------
# POST /api/comfyui/restart
# ---------------------------------------------------------------------------


class TestRestart:
    def test_restart_uses_slot_manager_img_restart(self, isolated_app_client):
        """restart must use the slot-owned img runtime, not /opt/comfyui scripts."""
        app, client = isolated_app_client
        restarted = []

        class _SlotManager:
            async def restart(self, name):
                restarted.append(name)

        app.state.slot_manager = _SlotManager()

        with patch("asyncio.create_subprocess_exec") as create_proc:
            r = client.post("/api/comfyui/restart")

        assert r.status_code == 202
        assert restarted == ["img"]
        create_proc.assert_not_called()

    def test_restart_returns_503_when_slot_manager_unavailable(self, isolated_app_client):
        app, client = isolated_app_client
        app.state.slot_manager = None

        r = client.post("/api/comfyui/restart")

        assert r.status_code == 503
        assert r.json()["error"]["code"] == "comfyui.arbiter_unavailable"

    def test_restart_background_failure_is_fail_soft(self, isolated_app_client):
        app, client = isolated_app_client

        class _SlotManager:
            async def restart(self, name):
                raise RuntimeError("boom")

        app.state.slot_manager = _SlotManager()

        r = client.post("/api/comfyui/restart")

        assert r.status_code == 202


# ---------------------------------------------------------------------------
# GET /api/comfyui/logs?tail=N
# ---------------------------------------------------------------------------


class TestLogs:
    def _make_log_proc(self, lines: list[str]) -> MagicMock:
        out = "\n".join(lines).encode()
        return _make_proc(returncode=0, stdout=out)

    def test_logs_returns_lines_list(self, client: TestClient):
        log_lines = ["2026-06-16 startup ok", "loading model", "ready"]
        proc = self._make_log_proc(log_lines)

        with (
            patch("shutil.which", return_value="/usr/bin/journalctl"),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            r = client.get("/api/comfyui/logs?tail=60")

        assert r.status_code == 200
        body = r.json()
        assert "lines" in body
        assert body["lines"] == log_lines

    def test_logs_reads_img_slot_journal_unit(self, client: TestClient):
        """Logs must come from the hal0-slot@img journal, not `podman logs`.

        Post-D9 the img container uses the 'none' log driver, so its output
        is only reachable via journalctl on its systemd unit.
        """
        call_args_store = []

        async def capture(*args, **kwargs):
            call_args_store.extend(args)
            return _make_proc(stdout=b"line1\nline2")

        with (
            patch("shutil.which", return_value="/usr/bin/journalctl"),
            patch("asyncio.create_subprocess_exec", side_effect=capture),
        ):
            r = client.get("/api/comfyui/logs")

        assert r.status_code == 200
        assert call_args_store[0] == "journalctl"
        assert "hal0-slot@img.service" in call_args_store

    def test_logs_default_tail_60(self, client: TestClient):
        """Default tail when not supplied must be 60."""
        call_args_store = []

        async def capture(*args, **kwargs):
            call_args_store.extend(args)
            return _make_proc(stdout=b"line1\nline2")

        with (
            patch("shutil.which", return_value="/usr/bin/journalctl"),
            patch("asyncio.create_subprocess_exec", side_effect=capture),
        ):
            r = client.get("/api/comfyui/logs")

        assert r.status_code == 200
        joined = " ".join(str(a) for a in call_args_store)
        assert "60" in joined, f"tail=60 not found in subprocess args: {call_args_store}"

    def test_logs_custom_tail(self, client: TestClient):
        """tail= query param must be forwarded to journalctl (-n)."""
        call_args_store = []

        async def capture(*args, **kwargs):
            call_args_store.extend(args)
            return _make_proc(stdout=b"x")

        with (
            patch("shutil.which", return_value="/usr/bin/journalctl"),
            patch("asyncio.create_subprocess_exec", side_effect=capture),
        ):
            r = client.get("/api/comfyui/logs?tail=10")

        assert r.status_code == 200
        joined = " ".join(str(a) for a in call_args_store)
        assert "10" in joined

    def test_logs_empty_when_no_journalctl(self, client: TestClient):
        """If journalctl is not found, return empty lines not a 500."""
        with patch("shutil.which", return_value=None):
            r = client.get("/api/comfyui/logs")

        assert r.status_code == 200
        assert r.json() == {"lines": []}

    def test_logs_empty_on_no_journal_entries(self, client: TestClient):
        """journalctl's '-- No entries --' placeholder normalises to []."""
        proc = _make_proc(stdout=b"-- No entries --")

        with (
            patch("shutil.which", return_value="/usr/bin/journalctl"),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            r = client.get("/api/comfyui/logs")

        assert r.status_code == 200
        assert r.json() == {"lines": []}


# ---------------------------------------------------------------------------
# POST /api/comfyui/workflows/{name}/launch
# ---------------------------------------------------------------------------

_SAMPLE_WORKFLOW = {
    "1": {
        "inputs": {"text": "a dog", "clip": ["2", 1]},
        "class_type": "CLIPTextEncode",
    }
}


class TestWorkflowLaunch:
    def test_launch_reads_workflow_and_posts_to_prompt(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Happy path: workflow file found, POSTed to /prompt, 202 returned."""
        wf_dir = tmp_path / "comfyui" / "workflows"
        wf_dir.mkdir(parents=True)
        wf_file = wf_dir / "test_wf.json"
        wf_file.write_text(json.dumps(_SAMPLE_WORKFLOW))

        monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))

        prompt_id = "abc-123"

        async def fake_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json = Mock(return_value={"prompt_id": prompt_id})
            return resp

        with patch(f"{_BASE}._get_client") as mock_client:
            http = MagicMock()
            http.post = AsyncMock(side_effect=fake_post)
            mock_client.return_value = http

            r = client.post("/api/comfyui/workflows/test_wf/launch")

        assert r.status_code == 202
        body = r.json()
        assert body["prompt_id"] == prompt_id

    def test_launch_404_when_workflow_missing(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        wf_dir = tmp_path / "comfyui" / "workflows"
        wf_dir.mkdir(parents=True)
        monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))

        r = client.post("/api/comfyui/workflows/nonexistent/launch")

        assert r.status_code == 404
        body = r.json()
        assert body["error"]["message"] == "Workflow not found."
        assert "nonexistent" not in body["error"]["message"]

    def test_launch_rejects_invalid_workflow_name_before_filesystem_lookup(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))

        with patch("hal0.api.routes.comfyui.os.path.isfile") as isfile:
            r = client.post("/api/comfyui/workflows/..secret/launch")

        assert r.status_code == 404
        assert r.json()["error"]["message"] == "Workflow not found."
        isfile.assert_not_called()

    def test_launch_rejects_slash_workflow_name_without_filesystem_lookup(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))

        with patch("hal0.api.routes.comfyui.os.path.isfile") as isfile:
            r = client.post("/api/comfyui/workflows/bad%2Fname/launch")

        assert r.status_code in {404, 422}
        isfile.assert_not_called()

    def test_launch_read_error_does_not_leak_raw_exception(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        wf_dir = tmp_path / "comfyui" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "broken.json").write_text("{not json")
        monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))

        r = client.post("/api/comfyui/workflows/broken/launch")

        assert r.status_code == 500
        body = r.json()
        assert body["error"] == {
            "code": "comfyui.workflow_read_error",
            "message": "Workflow could not be read.",
        }
        assert "Expecting property name" not in r.text

    def test_launch_falls_back_to_user_default_dir(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """If primary dir has no match, fall back to user/default/workflows/."""
        primary = tmp_path / "comfyui" / "workflows"
        primary.mkdir(parents=True)
        fallback = tmp_path / "comfyui" / "user" / "default" / "workflows"
        fallback.mkdir(parents=True)
        wf_file = fallback / "fb_wf.json"
        wf_file.write_text(json.dumps(_SAMPLE_WORKFLOW))

        # The primary dir has no match; the fallback is under the same data root.
        monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))

        async def fake_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json = Mock(return_value={"prompt_id": "xyz"})
            return resp

        with patch(f"{_BASE}._get_client") as mock_client:
            http = MagicMock()
            http.post = AsyncMock(side_effect=fake_post)
            mock_client.return_value = http

            r = client.post("/api/comfyui/workflows/fb_wf/launch")

        assert r.status_code == 202
        assert r.json()["prompt_id"] == "xyz"


class TestWorkflowList:
    def test_lists_json_files_from_primary_dir(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        wf_dir = tmp_path / "comfyui" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "alpha.json").write_text("{}")
        (wf_dir / "beta.json").write_text("{}")
        (wf_dir / "notes.txt").write_text("ignore me")  # non-json skipped
        # One data root drives both the primary dir and the user/default
        # fallback, so real workflow files on the host cannot leak in.
        monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))

        r = client.get("/api/comfyui/workflows")
        assert r.status_code == 200
        names = {w["name"]: w["source"] for w in r.json()["workflows"]}
        assert names == {"alpha": "primary", "beta": "primary"}

    def test_primary_wins_over_user_fallback(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        primary = tmp_path / "comfyui" / "workflows"
        primary.mkdir(parents=True)
        (primary / "shared.json").write_text("{}")
        fallback = tmp_path / "comfyui" / "user" / "default" / "workflows"
        fallback.mkdir(parents=True)
        (fallback / "shared.json").write_text("{}")
        (fallback / "extra.json").write_text("{}")
        monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))

        r = client.get("/api/comfyui/workflows")
        assert r.status_code == 200
        by_name = {w["name"]: w["source"] for w in r.json()["workflows"]}
        assert by_name["shared"] == "primary"  # primary shadows the fallback copy
        assert by_name["extra"] == "user"

    def test_missing_dir_returns_empty_list_not_error(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "does-not-exist"))
        r = client.get("/api/comfyui/workflows")
        assert r.status_code == 200
        assert r.json() == {"workflows": []}

    def test_traversal_names_are_filtered(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        wf_dir = tmp_path / "comfyui" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "good.json").write_text("{}")
        # A file whose stem carries a path-ish char must not surface.
        (wf_dir / "a b.json").write_text("{}")  # space fails the name regex
        monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))

        r = client.get("/api/comfyui/workflows")
        names = {w["name"] for w in r.json()["workflows"]}
        assert names == {"good"}


# ---------------------------------------------------------------------------
# GET /api/comfyui/preview
# ---------------------------------------------------------------------------

_HISTORY_RESP = {
    "abc123": {
        "outputs": {
            "9": {"images": [{"filename": "ComfyUI_00001_.png", "subfolder": "", "type": "output"}]}
        },
        "timestamp": 1718530000.0,
    }
}


class TestPreview:
    def test_preview_404_when_no_history(self, client: TestClient):
        """Empty history → 404."""

        async def fetch(path):
            if "/history" in path:
                return {}
            return None

        with patch(f"{_BASE}._fetch_json", new_callable=AsyncMock, side_effect=fetch):
            r = client.get("/api/comfyui/preview")

        assert r.status_code == 404

    def test_preview_streams_image_bytes(self, client: TestClient):
        """When history has output, the image bytes are proxied back."""
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20

        async def fetch(path):
            if "/history" in path:
                return _HISTORY_RESP
            return None

        async def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = png_bytes
            resp.headers = {"content-type": "image/png"}
            return resp

        with (
            patch(f"{_BASE}._fetch_json", new_callable=AsyncMock, side_effect=fetch),
            patch(f"{_BASE}._get_client") as mock_client,
        ):
            http = MagicMock()
            http.get = AsyncMock(side_effect=fake_get)
            mock_client.return_value = http

            r = client.get("/api/comfyui/preview")

        assert r.status_code == 200
        assert r.content == png_bytes
        assert "image" in r.headers.get("content-type", "")

    def test_preview_404_when_history_has_no_outputs(self, client: TestClient):
        """History entry with empty outputs → 404."""
        history = {"abc": {"outputs": {}, "timestamp": 1.0}}

        async def fetch(path):
            if "/history" in path:
                return history
            return None

        with patch(f"{_BASE}._fetch_json", new_callable=AsyncMock, side_effect=fetch):
            r = client.get("/api/comfyui/preview")

        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Telemetry: status payload checks (4.2)
# ---------------------------------------------------------------------------


class TestStatusTelemetry:
    """Ensure /status fields needed by the pane are present and well-formed."""

    _SYSTEM_STATS: ClassVar[dict] = {
        "system": {"ram_total": 128 * 1024**3, "ram_free": 46 * 1024**3},
        "devices": [
            {
                "name": "Radeon 8060S",
                "type": "cuda",
                "vram_total": 80 * 1024**3,
                "vram_free": 26 * 1024**3,
            }
        ],
    }
    _QUEUE_IDLE: ClassVar[dict] = {"queue_running": [], "queue_pending": []}
    _QUEUE_BUSY: ClassVar[dict] = {
        "queue_running": [[0, "abc", {}, {}, {}]],
        "queue_pending": [],
    }

    def _patch_status(
        self,
        stats,
        queue,
        *,
        gpu_busy: float | None = 0.97,
        power: dict | None = None,
    ):
        base = _BASE
        power = power or {"gpu_temp_c": 68.5, "gpu_sclk_mhz": 2700.0}

        async def fetch(path):
            if "system_stats" in path:
                return stats
            if "queue" in path:
                return queue
            return None

        return (
            patch(f"{base}._container_state", new_callable=AsyncMock, return_value="running"),
            patch(f"{base}._systemd_active", new_callable=AsyncMock, return_value=False),
            patch(f"{base}._fetch_json", new_callable=AsyncMock, side_effect=fetch),
            patch(
                f"{base}.gpu_view.sample",
                return_value=SimpleNamespace(gpu_busy=gpu_busy),
            ),
            patch(f"{base}._probe_power", return_value=power),
        )

    def test_status_memory_fields_present(self, client: TestClient):
        patches = self._patch_status(self._SYSTEM_STATS, self._QUEUE_IDLE)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            body = client.get("/api/comfyui/status").json()

        mem = body["memory"]
        assert mem is not None
        assert "gtt_used_gb" in mem
        assert "gtt_ceil_gb" in mem
        assert "ram_used_gb" in mem
        assert "ram_ceil_gb" in mem

    def test_util_is_none_or_zero_when_no_running_job(self, client: TestClient):
        """gpu_busy_percent is forced-high artifact — util must be 0/None when idle."""
        patches = self._patch_status(self._SYSTEM_STATS, self._QUEUE_IDLE, gpu_busy=1.0)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            body = client.get("/api/comfyui/status").json()

        assert body["util"] == 0

    def test_running_status_surfaces_live_gpu_telemetry(self, client: TestClient):
        patches = self._patch_status(
            self._SYSTEM_STATS,
            self._QUEUE_BUSY,
            gpu_busy=0.63,
            power={"gpu_temp_c": 68.5, "gpu_sclk_mhz": 2700.0},
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            body = client.get("/api/comfyui/status").json()

        assert body["util"] == 63.0
        assert body["temp"] == 68.5
        assert body["clock"] == 2.7

    def test_status_reports_progress_not_nulls(self, client: TestClient):
        """A hal0-submitted render's step shows up as ``progress``; the old null keys are gone."""
        progress.start("abc", "sdxl-lightning")
        progress.update("abc", step=3, total=8, node="12")
        patches = self._patch_status(self._SYSTEM_STATS, self._QUEUE_BUSY)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            body = client.get("/api/comfyui/status").json()

        assert body["progress"]["model"] == "sdxl-lightning"
        assert (body["progress"]["step"], body["progress"]["total"]) == (3, 8)
        assert body["progress"]["node"] == "12"
        assert body["progress"]["prompt_id"] == "abc"
        assert isinstance(body["progress"]["elapsed_s"], float)
        for gone in ("it_s", "eta", "step"):
            assert gone not in body

    def test_status_progress_is_null_when_no_hal0_render_is_live(self, client: TestClient):
        """Editor renders have no entry; the pane shows queue-level state for them."""
        patches = self._patch_status(self._SYSTEM_STATS, self._QUEUE_BUSY)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            body = client.get("/api/comfyui/status").json()

        assert "progress" in body
        assert body["progress"] is None
        assert body["queue"]["running"] == 1

    def test_status_reports_the_render_that_is_stepping(self, client: TestClient):
        """Two hal0 renders queued: the older one is running, so it is the one reported."""
        progress.start("running", "sd-1.5-pruned-emaonly")
        progress.start("queued", "sdxl-lightning")
        progress.update("running", step=4, total=20, node="3")
        patches = self._patch_status(self._SYSTEM_STATS, self._QUEUE_BUSY)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            body = client.get("/api/comfyui/status").json()

        assert body["progress"]["prompt_id"] == "running"
        assert body["progress"]["step"] == 4

    def test_status_reports_the_oldest_render_before_any_has_stepped(self, client: TestClient):
        progress.start("old", "sd-1.5-pruned-emaonly")
        progress.start("new", "sdxl-lightning")
        patches = self._patch_status(self._SYSTEM_STATS, self._QUEUE_BUSY)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            body = client.get("/api/comfyui/status").json()

        assert body["progress"]["prompt_id"] == "old"
        assert body["progress"]["step"] is None

    def test_telemetry_probe_failure_is_fail_soft(self, client: TestClient):
        base = _BASE

        async def fetch(path):
            if "system_stats" in path:
                return self._SYSTEM_STATS
            if "queue" in path:
                return self._QUEUE_BUSY
            return None

        with (
            patch(f"{base}._container_state", new_callable=AsyncMock, return_value="running"),
            patch(f"{base}._systemd_active", new_callable=AsyncMock, return_value=False),
            patch(f"{base}._fetch_json", new_callable=AsyncMock, side_effect=fetch),
            patch(f"{base}.gpu_view.sample", side_effect=RuntimeError("gpu unavailable")),
            patch(f"{base}._probe_power", side_effect=RuntimeError("hwmon unavailable")),
        ):
            response = client.get("/api/comfyui/status")

        assert response.status_code == 200
        body = response.json()
        assert body["util"] is None
        assert body["temp"] is None
        assert body["clock"] is None


# ---------------------------------------------------------------------------
# GET /api/comfyui/arbiter and /status: the arbiter's phase fields (image readiness API)
# ---------------------------------------------------------------------------


class _FakeSlots:
    """The slice of SlotManager a real GpuArbiter's status() reads."""

    def __init__(self, ready: set[str]) -> None:
        self.ready = ready

    def is_ready_for_dispatch(self, name: str) -> bool:
        return name in self.ready

    def in_flight_count(self, name: str) -> int:
        return 0


def test_arbiter_route_passes_new_fields(client: TestClient, tmp_path: Path):
    """Both routes hand the arbiter's block over as is: a real arbiter, in the
    grace after a render, answers with every field the GPU status line reads."""
    import time

    from hal0.slots.arbiter import GpuArbiter

    state_path = tmp_path / "gpu_arbiter.json"
    state_path.write_text(
        json.dumps({"mode": "img", "pinned": False, "saved_llm_slots": ["chat", "agent"]}),
        encoding="utf-8",
    )
    arbiter = GpuArbiter(_FakeSlots(set()), state_path=state_path)  # type: ignore[arg-type]
    arbiter.set_pin(False)  # starts the quiet clock: image mode, nothing rendering
    client.app.state.slot_manager = SimpleNamespace(arbiter=arbiter)

    expected = {
        "phase",
        "restore_at",
        "handoff_until",
        "drain_in_flight",
        "unload_set",
        "llm_waiters",
    }
    direct = arbiter.status()
    assert expected <= set(direct)

    gate_body = client.get("/api/comfyui/arbiter").json()
    assert gate_body["mode"] == "generation"
    assert set(gate_body["arbiter"]) == set(direct)
    block = gate_body["arbiter"]
    assert block["phase"] == "grace"
    assert block["handoff_until"] is None
    assert time.time() < block["restore_at"] <= time.time() + 10.0
    assert block["drain_in_flight"] == 0
    assert block["unload_set"] == ["chat", "agent"]
    assert block["llm_waiters"] == {"count": 0, "oldest_s": None}

    async def _fetch(_path):
        return None

    with (
        patch(f"{_BASE}._container_state", new_callable=AsyncMock, return_value="running"),
        patch(f"{_BASE}._systemd_active", new_callable=AsyncMock, return_value=False),
        patch(f"{_BASE}._fetch_json", new_callable=AsyncMock, side_effect=_fetch),
        patch(f"{_BASE}.gpu_view.sample", return_value=SimpleNamespace(gpu_busy=0.0)),
        patch(f"{_BASE}._probe_power", return_value={}),
    ):
        status_body = client.get("/api/comfyui/status").json()
    assert set(status_body["arbiter"]) == set(direct)
    assert status_body["arbiter"]["phase"] == "grace"
    assert status_body["arbiter"]["unload_set"] == ["chat", "agent"]


# ---------------------------------------------------------------------------
# The gate's version and its refusals (image readiness API)
# ---------------------------------------------------------------------------

_VERSION = "X-Hal0-Gate-Version"


def _status(client: TestClient, headers: dict[str, str] | None = None) -> dict:
    """``GET /status`` with every probe it makes stubbed out."""

    async def _fetch(_path):
        return None

    with (
        patch(f"{_BASE}._container_state", new_callable=AsyncMock, return_value="running"),
        patch(f"{_BASE}._systemd_active", new_callable=AsyncMock, return_value=False),
        patch(f"{_BASE}._fetch_json", new_callable=AsyncMock, side_effect=_fetch),
        patch(f"{_BASE}.gpu_view.sample", return_value=SimpleNamespace(gpu_busy=0.0)),
        patch(f"{_BASE}._probe_power", return_value={}),
    ):
        response = client.get("/api/comfyui/status", headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()


def test_no_gate_call_is_unknown(client: TestClient):
    """Nothing has called the gate's routes since hal0-api started."""
    body = _status(client)

    assert body["gate"] == {"state": "unknown", "version_seen": None}
    assert body["gate_refusal"] is None


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/comfyui/arbiter"), ("POST", "/api/comfyui/prompt-handoff")],
)
def test_headerless_call_is_stale(client: TestClient, method: str, path: str):
    """The gate-only routes were called by a gate that sends no version: an older one.
    It is told by its client: the gate builds its requests with urllib."""
    client.request(method, path, headers=_GATE_UA)

    assert _status(client)["gate"] == {"state": "stale", "version_seen": None}


_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


@pytest.mark.parametrize("user_agent", ["curl/8.5.0", _BROWSER_UA, "testclient", ""])
@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/comfyui/arbiter"), ("POST", "/api/comfyui/prompt-handoff")],
)
def test_headerless_call_from_curl_or_a_browser_is_nobody(
    client: TestClient, method: str, path: str, user_agent: str
):
    """An operator's curl, a browser tab: not an engine to restart."""
    client.request(method, path, headers={"User-Agent": user_agent})

    assert _status(client)["gate"] == {"state": "unknown", "version_seen": None}


def test_v165_gate_status_read_is_stale(client: TestClient):
    """The released v1.6.5 gate never calls /arbiter or /prompt-handoff: it reads
    GET /status and posts /switchover, without the version header, through urllib."""
    assert _status(client, _GATE_UA)["gate"] == {"state": "stale", "version_seen": None}
    # Later dashboard polls do not undo it.
    assert _status(client)["gate"]["state"] == "stale"


def test_v165_gate_switchover_is_stale(client: TestClient):
    client.post("/api/comfyui/switchover", json={"mode": "generation"}, headers=_GATE_UA)

    assert _status(client)["gate"] == {"state": "stale", "version_seen": None}


@pytest.mark.parametrize("user_agent", ["curl/8.5.0", _BROWSER_UA])
def test_a_browsers_status_poll_and_switchover_are_not_a_stale_gate(
    client: TestClient, user_agent: str
):
    """The dashboard polls /status and posts /switchover, both without the header."""
    headers = {"User-Agent": user_agent}
    assert _status(client, headers)["gate"] == {"state": "unknown", "version_seen": None}
    client.post("/api/comfyui/switchover", json={"mode": "generation"}, headers=headers)

    assert _status(client, headers)["gate"] == {"state": "unknown", "version_seen": None}


def test_current_header_is_current(client: TestClient):
    from hal0.comfyui.gate_version import GATE_VERSION

    client.get("/api/comfyui/arbiter", headers={_VERSION: GATE_VERSION})

    assert _status(client)["gate"] == {"state": "current", "version_seen": GATE_VERSION}


def test_an_older_version_is_stale(client: TestClient):
    client.get("/api/comfyui/arbiter", headers={_VERSION: "1"})

    assert _status(client)["gate"] == {"state": "stale", "version_seen": "1"}


def test_the_gates_latest_call_decides(client: TestClient):
    """Restarting the engine swaps the gate: its first call replaces what hal0 saw."""
    client.get("/api/comfyui/arbiter", headers=_GATE_UA)
    assert _status(client)["gate"]["state"] == "stale"

    client.get("/api/comfyui/arbiter", headers={**_GATE_UA, _VERSION: "2"})
    assert _status(client)["gate"]["state"] == "current"

    client.get("/api/comfyui/arbiter", headers=_GATE_UA)
    assert _status(client)["gate"]["state"] == "stale"


def test_a_headerless_switchover_is_the_dashboards_not_a_stale_gate(client: TestClient):
    """The dashboard's Start button posts /switchover with no gate header."""
    client.request("POST", "/api/comfyui/switchover", json={"mode": "generation"})
    assert _status(client)["gate"] == {"state": "unknown", "version_seen": None}

    client.post("/api/comfyui/switchover", json={"mode": "generation"}, headers={_VERSION: "2"})
    assert _status(client)["gate"] == {"state": "current", "version_seen": "2"}


def test_the_version_hal0_keeps_is_cut_to_a_sane_length(client: TestClient):
    client.get("/api/comfyui/arbiter", headers={_VERSION: "9" * 300})

    gate = _status(client)["gate"]
    assert gate["state"] == "stale"
    assert gate["version_seen"] == "9" * 32


def test_gate_refusal_post_records_what_the_gate_said(client: TestClient, monkeypatch):
    monkeypatch.setattr(f"{_BASE}._wall_clock", lambda: 1_760_000_000.5)

    r = client.post(
        "/api/comfyui/gate-refusal",
        json={"reason": "hal0 kept the GPU in inference mode"},
        headers={_VERSION: "2"},
    )

    assert r.status_code == 202
    expected = {"at": 1_760_000_000.5, "reason": "hal0 kept the GPU in inference mode"}
    body = _status(client)
    assert body["gate_refusal"] == expected
    assert body["gate"] == {"state": "current", "version_seen": "2"}
    assert client.get("/api/comfyui/arbiter").json()["gate_refusal"] == expected


def test_arbiter_has_no_refusal_until_the_gate_reports_one(client: TestClient):
    assert client.get("/api/comfyui/arbiter").json()["gate_refusal"] is None


def test_gate_refusal_reason_is_capped(client: TestClient):
    """The reason comes from the gate; hal0 keeps 500 characters of it."""
    client.post("/api/comfyui/gate-refusal", json={"reason": "r" * 600})

    assert _status(client)["gate_refusal"]["reason"] == "r" * 500


@pytest.mark.parametrize("body", [None, {}, {"reason": ""}, {"reason": "  "}, {"reason": 5}])
def test_gate_refusal_needs_a_reason(client: TestClient, body):
    r = client.post("/api/comfyui/gate-refusal", json=body)

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "comfyui.invalid_gate_refusal"
    assert _status(client)["gate_refusal"] is None


def test_the_latest_refusal_replaces_the_last(client: TestClient):
    client.post("/api/comfyui/gate-refusal", json={"reason": "first"})
    client.post("/api/comfyui/gate-refusal", json={"reason": "second"})

    assert _status(client)["gate_refusal"]["reason"] == "second"


def test_reset_state_forgets_the_gate(client: TestClient):
    from hal0.api.routes import comfyui as mod

    client.get("/api/comfyui/arbiter", headers={_VERSION: "2"})
    client.post("/api/comfyui/gate-refusal", json={"reason": "x"})
    assert mod._gate_seen["version"] == "2" and mod._gate_refusal is not None

    mod._reset_state()

    assert mod._gate_seen == {"version": None, "at": None}
    assert mod._gate_refusal is None


@pytest.mark.parametrize(
    ("body", "status", "mentions"),
    [
        ({"mode": "nonsense"}, 422, "body must be"),
        ({}, 422, "body must be"),
    ],
)
def test_switchover_rejection_from_gate_records_refusal(
    client: TestClient, body: dict, status: int, mentions: str
):
    """A switch request that hal0 rejects, from a caller that carries the gate header."""
    r = client.post("/api/comfyui/switchover", json=body, headers={_VERSION: "2"})

    assert r.status_code == status
    refusal = _status(client)["gate_refusal"]
    assert refusal is not None
    assert mentions in refusal["reason"]
    assert f"HTTP {status}" in refusal["reason"]


def test_switchover_rejection_without_the_gate_header_is_not_a_gate_refusal(client: TestClient):
    r = client.post("/api/comfyui/switchover", json={"mode": "nonsense"})

    assert r.status_code == 422
    assert _status(client)["gate_refusal"] is None


def test_switchover_with_no_arbiter_from_the_gate_records_refusal(client: TestClient):
    client.app.state.slot_manager = None

    r = client.post("/api/comfyui/switchover", json={"mode": "generation"}, headers={_VERSION: "2"})

    assert r.status_code == 503
    refusal = _status(client)["gate_refusal"]
    assert refusal is not None and "HTTP 503" in refusal["reason"]
    assert "arbiter" in refusal["reason"]


def test_a_switch_already_running_is_not_a_gate_refusal(client: TestClient):
    """The gate counts a 409 as accepted and polls on."""
    from hal0.api.routes import comfyui as mod

    mod._switch.update(active=True, target="generation")

    r = client.post("/api/comfyui/switchover", json={"mode": "generation"}, headers={_VERSION: "2"})

    assert r.status_code == 409
    assert _status(client)["gate_refusal"] is None


def test_an_accepted_switch_from_the_gate_is_not_a_refusal(client: TestClient):
    class _Arbiter:
        def status(self):
            return {"mode": "llm", "switching": False, "pinned": False}

        async def ensure_img(self, *, pin=False):
            return None

    client.app.state.slot_manager = SimpleNamespace(arbiter=_Arbiter())

    r = client.post("/api/comfyui/switchover", json={"mode": "generation"}, headers={_VERSION: "2"})

    assert r.status_code == 202
    assert _status(client)["gate_refusal"] is None


# -- with auth enforced: the gate's token, and the credential the gate lacks ------------------

_ADMIN = {"Authorization": "Bearer admin-key-for-tests"}


@pytest.fixture
def box_client(app):
    """A client on the box itself: the only place the gate's token is admitted from."""
    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        yield c


@pytest.fixture
def auth_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.api import auth as auth_mod

    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", "admin-key-for-tests")
    auth_mod._require_auth_cache = None


def test_switchover_403_for_the_gates_token_records_refusal(
    box_client: TestClient, auth_enforced: None
):
    """#3058: the gate's token may only ask for a plain switch into image mode."""
    from hal0.comfyui import gate_credential

    token = gate_credential.ensure_token()

    r = box_client.post(
        "/api/comfyui/switchover",
        json={"mode": "inference"},
        headers={"Authorization": f"Bearer {token}", _VERSION: "2"},
    )

    assert r.status_code == 403
    refusal = _status(box_client, _ADMIN)["gate_refusal"]
    assert refusal is not None
    assert "HTTP 403" in refusal["reason"]
    assert "may only ask for image mode" in refusal["reason"]


def test_the_gate_token_reaches_the_refusal_route(box_client: TestClient, auth_enforced: None):
    from hal0.comfyui import gate_credential

    token = gate_credential.ensure_token()

    refused = box_client.post("/api/comfyui/gate-refusal", json={"reason": "x"})
    ok = box_client.post(
        "/api/comfyui/gate-refusal",
        json={"reason": "hal0 kept the GPU in inference mode"},
        headers={"Authorization": f"Bearer {token}", _VERSION: "2"},
    )

    assert refused.status_code == 401
    assert ok.status_code == 202
    body = _status(box_client, _ADMIN)
    assert body["gate_refusal"]["reason"] == "hal0 kept the GPU in inference mode"
    assert body["gate"] == {"state": "current", "version_seen": "2"}


def test_status_gate_no_credential_when_auth_required(client: TestClient, auth_enforced: None):
    """Auth is enforced and the gate has no token to present: its first call is refused."""
    from hal0.api.routes import comfyui as mod

    assert _status(client, _ADMIN)["gate"] == {"state": "no_credential", "version_seen": None}

    # It outranks everything the gate may have said before the token went missing.
    mod._gate_seen.update(version="2", at=1.0)
    assert _status(client, _ADMIN)["gate"] == {"state": "no_credential", "version_seen": "2"}
    mod._gate_seen.update(version=None)
    assert _status(client, _ADMIN)["gate"]["state"] == "no_credential"


def test_status_gate_has_a_credential_once_the_token_exists(
    client: TestClient, auth_enforced: None
):
    from hal0.comfyui import gate_credential

    gate_credential.ensure_token()

    assert _status(client, _ADMIN)["gate"] == {"state": "unknown", "version_seen": None}


def test_status_gate_without_auth_needs_no_credential(client: TestClient, monkeypatch):
    """On a box that enforces nothing the gate works with no token at all."""
    from hal0.comfyui import gate_credential

    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")
    assert not gate_credential.credential_present()

    assert _status(client)["gate"]["state"] == "unknown"


@pytest.mark.parametrize(
    ("bind", "token", "state"),
    [
        # One address, with an admin key: the gate dials that address, where the LAN rule applies.
        ("192.0.2.10", False, "no_credential"),
        ("192.0.2.10", True, "unknown"),
        # A wildcard or loopback bind: the gate dials loopback, which the LAN rule exempts.
        ("0.0.0.0", False, "unknown"),
        ("::", False, "unknown"),
        ("127.0.0.1", False, "unknown"),
        # A host name: the gate holds its token back from a name, so a token on disk does not help.
        ("hal0.example.lan", False, "no_credential"),
        ("hal0.example.lan", True, "no_credential"),
    ],
)
def test_status_gate_credential_on_a_box_bound_to_one_address(
    client: TestClient, monkeypatch, bind: str, token: bool, state: str
):
    from hal0.comfyui import gate_credential

    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")
    monkeypatch.setenv("HAL0_ADMIN_KEY", "admin-key-for-tests")
    monkeypatch.setenv("HAL0_BIND_HOST", bind)
    if token:
        gate_credential.ensure_token()

    # The dashboard's own peer (LAN, non-loopback) must not change the answer.
    assert _status(client, _ADMIN)["gate"]["state"] == state


def test_status_gate_by_name_reports_no_credential_even_with_a_token(
    client: TestClient, auth_enforced: None, monkeypatch
):
    """Auth is on, hal0-api is named by a host name, and the gate holds its token back
    from names (the asset's ``_direct_host``): the token exists and cannot be used."""
    from hal0.comfyui import gate_credential

    gate_credential.ensure_token()
    monkeypatch.setenv("HAL0_BIND_HOST", "hal0.example.lan")

    assert _status(client, _ADMIN)["gate"]["state"] == "no_credential"


# -- an old gate that auth turned away reads stale -----------------------------------------

_REFUSED_401 = (
    b'{"error": {"code": "auth.required", "message": "authentication required", "details": {}}}'
)
_REFUSED_403 = (
    b'{"error": {"code": "auth.forbidden", "message": "insufficient scope", "details": {}}}'
)
# The gate builds its requests with urllib; the auth layer reads its default User-Agent.
_GATE_UA = {"User-Agent": "Python-urllib/3.12"}


def test_status_gate_stale_when_auth_refused_an_old_gate(
    box_client: TestClient, auth_enforced: None
):
    """Auth is on, the engine runs a gate that sends no version header: it is turned away
    before it reaches a route, so only the auth layer's marker says the engine needs a restart."""
    from hal0.comfyui import gate_credential

    token = gate_credential.ensure_token()
    assert _status(box_client, _ADMIN)["gate"] == {"state": "unknown", "version_seen": None}

    refused = box_client.get("/api/comfyui/arbiter", headers=_GATE_UA)

    # The refusal is the one the middleware always gave: status, headers and bytes.
    assert refused.status_code == 401
    assert refused.content == _REFUSED_401
    assert refused.headers["content-type"] == "application/json"
    assert _status(box_client, _ADMIN)["gate"] == {"state": "stale", "version_seen": None}

    # The restarted engine's gate calls with the header (and the token): current again.
    ok = box_client.get(
        "/api/comfyui/arbiter",
        headers={**_GATE_UA, "Authorization": f"Bearer {token}", _VERSION: "2"},
    )
    assert ok.status_code == 200
    assert _status(box_client, _ADMIN)["gate"] == {"state": "current", "version_seen": "2"}


@pytest.mark.parametrize(
    "user_agent",
    [
        "curl/8.5.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36",
    ],
)
def test_a_refused_curl_or_browser_on_the_box_is_not_an_old_gate(
    box_client: TestClient, auth_enforced: None, user_agent: str
):
    """An operator's curl, or a dashboard session that expired, hits the same gate paths."""
    from hal0.comfyui import gate_credential

    gate_credential.ensure_token()

    refused = box_client.get("/api/comfyui/arbiter", headers={"User-Agent": user_agent})

    assert refused.status_code == 401
    assert refused.content == _REFUSED_401  # the refusal is the same either way
    assert _status(box_client, _ADMIN)["gate"] == {"state": "unknown", "version_seen": None}


def test_status_gate_stale_for_an_old_gate_refused_with_403(
    box_client: TestClient, auth_enforced: None, monkeypatch
):
    """A caller with the wrong tier (here the client key) is refused 403, not 401."""
    from hal0.comfyui import gate_credential

    gate_credential.ensure_token()
    monkeypatch.setenv("HAL0_CLIENT_KEY", "client-key-for-tests")

    refused = box_client.post(
        "/api/comfyui/prompt-handoff",
        headers={**_GATE_UA, "Authorization": "Bearer client-key-for-tests"},
    )

    assert refused.status_code == 403
    assert refused.content == _REFUSED_403
    assert _status(box_client, _ADMIN)["gate"]["state"] == "stale"


def test_a_marker_older_than_the_gates_latest_call_is_history(auth_enforced: None):
    """A call recorded after the refusal wins: the gate that made it is the one running."""
    from hal0.api import auth as auth_mod
    from hal0.api.routes import comfyui as mod
    from hal0.comfyui import gate_credential

    gate_credential.ensure_token()
    auth_mod._gate_path_refused_at = 100.0

    assert mod._gate_state() == "stale"  # refused, and nothing recorded since
    mod._gate_seen.update(version="2", at=99.0)
    assert mod._gate_state() == "stale"  # the call is older than the refusal
    mod._gate_seen.update(version="2", at=100.0)
    assert mod._gate_state() == "current"  # not newer than the call
    mod._gate_seen.update(version="2", at=101.0)
    assert mod._gate_state() == "current"
    mod._gate_seen.update(version="1", at=101.0)
    assert mod._gate_state() == "stale"  # an older version, on its own account


def test_no_credential_outranks_an_old_gate_that_auth_refused(
    box_client: TestClient, auth_enforced: None
):
    """No token on disk: the first thing to fix is the credential."""
    assert box_client.get("/api/comfyui/arbiter", headers=_GATE_UA).status_code == 401

    assert _status(box_client, _ADMIN)["gate"]["state"] == "no_credential"


def test_a_refused_request_from_another_machine_is_not_an_old_gate(
    client: TestClient, auth_enforced: None
):
    """The default test peer is not the box: nothing about the gate can be read from it."""
    from hal0.comfyui import gate_credential

    gate_credential.ensure_token()

    assert client.get("/api/comfyui/arbiter", headers=_GATE_UA).status_code == 401

    assert _status(client, _ADMIN)["gate"] == {"state": "unknown", "version_seen": None}


def test_a_refused_request_that_is_not_the_gates_is_not_an_old_gate(
    box_client: TestClient, auth_enforced: None
):
    from hal0.comfyui import gate_credential

    gate_credential.ensure_token()

    assert box_client.get("/api/comfyui/files", headers=_GATE_UA).status_code == 401
    pin = box_client.post("/api/comfyui/pin", json={"pinned": False}, headers=_GATE_UA)
    assert pin.status_code == 401

    assert _status(box_client, _ADMIN)["gate"]["state"] == "unknown"


def test_a_refused_gate_that_sends_the_header_is_not_an_old_gate(
    box_client: TestClient, auth_enforced: None
):
    """A current gate whose token hal0 does not accept: not the restart this state asks for."""
    from hal0.comfyui import gate_credential

    gate_credential.ensure_token()

    r = box_client.get(
        "/api/comfyui/arbiter",
        headers={**_GATE_UA, "Authorization": "Bearer nope", _VERSION: "2"},
    )

    assert r.status_code == 401
    assert _status(box_client, _ADMIN)["gate"]["state"] == "unknown"


def test_reset_state_forgets_the_refusal_marker(auth_enforced: None):
    from hal0.api import auth as auth_mod
    from hal0.api.routes import comfyui as mod

    auth_mod._gate_path_refused_at = 123.0

    mod._reset_state()

    assert auth_mod.gate_path_refused_at() is None


# -- a workflow launch the gate refuses is recorded like a /v1 render --------------------------


def _launch_with_prompt_answer(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: int,
    body: object,
):
    wf_dir = tmp_path / "comfyui" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "test_wf.json").write_text(json.dumps(_SAMPLE_WORKFLOW))
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))

    async def fake_post(url, **kwargs):
        resp = MagicMock()
        resp.status_code = status
        resp.json = Mock(return_value=body)
        return resp

    with patch(f"{_BASE}._get_client") as mock_client:
        http = MagicMock()
        http.post = AsyncMock(side_effect=fake_post)
        mock_client.return_value = http
        return client.post("/api/comfyui/workflows/test_wf/launch")


def test_workflow_launch_refused_by_the_gate_is_a_503_and_is_recorded(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The gate answers /prompt with a 503 in its own shape when hal0 did not hand it the GPU.
    The launch says so, as /v1 does, instead of answering 202 "queued"."""
    from tests.fixtures.comfyui_gate import refused_prompt_body

    reason = "hal0 kept the GPU in inference mode after 3 switch request(s)"

    r = _launch_with_prompt_answer(
        client, tmp_path, monkeypatch, status=503, body=refused_prompt_body(reason)
    )

    assert r.status_code == 503
    error = r.json()["error"]
    assert error["code"] == "image.gate_refused"
    assert error["message"] == reason
    assert _status(client)["gate_refusal"]["reason"] == reason


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (503, {"error": "busy"}),
        (503, ["not", "a", "refusal"]),
        (500, None),
    ],
)
def test_workflow_launch_with_another_failure_records_nothing(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, body: object
):
    r = _launch_with_prompt_answer(client, tmp_path, monkeypatch, status=status, body=body)

    assert r.status_code == 202
    assert _status(client)["gate_refusal"] is None


def test_workflow_launch_that_succeeds_records_nothing(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    r = _launch_with_prompt_answer(
        client, tmp_path, monkeypatch, status=200, body={"prompt_id": "abc"}
    )

    assert r.json()["prompt_id"] == "abc"
    assert _status(client)["gate_refusal"] is None


# -- the released v1.6.5 gate, refused by auth ----------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/comfyui/status", None),
        ("POST", "/api/comfyui/switchover", {"mode": "generation"}),
    ],
)
def test_status_gate_stale_when_auth_refused_the_v165_gate(
    box_client: TestClient, auth_enforced: None, method: str, path: str, body
):
    """v1.6.5's gate reads /status and posts /switchover, with no token and no version
    header. On a box that enforces auth it is refused; /status still tells the operator."""
    from hal0.comfyui import gate_credential

    gate_credential.ensure_token()
    assert _status(box_client, _ADMIN)["gate"]["state"] == "unknown"

    refused = box_client.request(method, path, json=body, headers=_GATE_UA)

    assert refused.status_code == 401
    assert refused.content == _REFUSED_401  # the refusal is as it always was
    assert _status(box_client, _ADMIN)["gate"] == {"state": "stale", "version_seen": None}


@pytest.mark.parametrize("user_agent", ["curl/8.5.0", _BROWSER_UA])
def test_a_refused_status_poll_from_a_browser_or_curl_is_not_a_stale_gate(
    box_client: TestClient, auth_enforced: None, user_agent: str
):
    """The dashboard's own poll, signed out, is refused the same way."""
    from hal0.comfyui import gate_credential

    gate_credential.ensure_token()

    refused = box_client.get("/api/comfyui/status", headers={"User-Agent": user_agent})

    assert refused.status_code == 401
    assert refused.content == _REFUSED_401
    assert _status(box_client, _ADMIN)["gate"]["state"] == "unknown"


def test_the_gate_token_still_does_not_open_status(box_client: TestClient, auth_enforced: None):
    """/status is marked, not admitted: the allow-list is still the gate's four requests."""
    from hal0.comfyui import gate_credential

    token = gate_credential.ensure_token()

    r = box_client.get(
        "/api/comfyui/status", headers={**_GATE_UA, "Authorization": f"Bearer {token}"}
    )

    assert r.status_code == 401


# -- what the gate says is untrusted text ---------------------------------------------------


def _post_raw_reason(client: TestClient, raw_json_reason: str):
    """POST a reason as JSON text, escapes and all: a client library would refuse to
    encode a lone surrogate, the gate (json.dumps with ensure_ascii) does not."""
    return client.post(
        "/api/comfyui/gate-refusal",
        content=f'{{"reason": "{raw_json_reason}"}}'.encode("ascii"),
        headers={"Content-Type": "application/json"},
    )


def test_a_lone_surrogate_in_a_gate_refusal_does_not_break_arbiter_or_status(
    client: TestClient,
):
    """The reason is valid JSON and not valid UTF-8. Kept as it came, it made every later
    /arbiter answer a 500, which the gate reads as hal0 being unreachable."""
    r = _post_raw_reason(client, "\\ud800 hi")

    assert r.status_code == 202
    arbiter = client.get("/api/comfyui/arbiter")
    assert arbiter.status_code == 200
    assert arbiter.json()["gate_refusal"]["reason"] == "? hi"
    body = _status(client)
    assert body["gate_refusal"]["reason"] == "? hi"
    body["gate_refusal"]["reason"].encode("utf-8")  # encodable


def test_a_gate_refusal_reason_is_capped_after_it_is_made_safe(client: TestClient):
    _post_raw_reason(client, "\\ud800" + "r" * 600)

    reason = _status(client)["gate_refusal"]["reason"]
    assert reason == "?" + "r" * 499


def test_record_gate_refusal_cleans_and_caps_what_it_keeps():
    from hal0.api.routes import comfyui as mod

    mod.record_gate_refusal("x\ud800y" + "z" * 1000)

    assert mod._gate_refusal is not None
    assert mod._gate_refusal["reason"] == "x?y" + "z" * 497


def test_status_reads_the_gate_state_off_the_event_loop(client: TestClient, monkeypatch):
    """It reads the token file, api.env and the auth config on every poll."""
    import asyncio

    from hal0.api.routes import comfyui as mod

    on_a_loop: list[bool] = []
    real = mod._gate_block

    def _spy():
        try:
            asyncio.get_running_loop()
            on_a_loop.append(True)
        except RuntimeError:
            on_a_loop.append(False)
        return real()

    monkeypatch.setattr(mod, "_gate_block", _spy)

    assert _status(client)["gate"]["state"] == "unknown"
    assert on_a_loop == [False]


@pytest.mark.parametrize("surrogate_in", ["details", "message"])
def test_workflow_launch_refused_with_a_lone_surrogate_is_a_clean_503(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surrogate_in: str
):
    from tests.fixtures.comfyui_gate import refused_prompt_body

    body = refused_prompt_body("placeholder")
    if surrogate_in == "details":
        body["error"]["details"] = "\ud800 refused " + "d" * 800
    else:
        body["error"]["details"] = ""
        body["error"]["message"] = "\ud800 refused"

    r = _launch_with_prompt_answer(client, tmp_path, monkeypatch, status=503, body=body)

    assert r.status_code == 503
    error = r.json()["error"]
    assert error["code"] == "image.gate_refused"
    assert error["message"].startswith("? refused")
    assert len(error["message"]) <= 500
    assert _status(client)["gate_refusal"]["reason"] == error["message"]
