"""Phase 4 TDD tests — control + monitoring routes for ComfyUI.

Covers:
  POST /api/comfyui/render/cancel   — clears queue + interrupts
  POST /api/comfyui/restart         — shells out to comfy-up.sh
  GET  /api/comfyui/logs?tail=N     — container log lines
  POST /api/comfyui/workflows/{name}/launch — reads workflow file + posts /prompt
  GET  /api/comfyui/preview         — proxies latest output image bytes

All network + subprocess calls are mocked — no real ComfyUI or container.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Module-state isolation (same pattern as test_comfyui_proxy.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_comfyui_state():
    from hal0.api.routes import comfyui as mod

    mod._reset_state()
    yield
    mod._reset_state()


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
    def test_restart_calls_comfy_up_sh_returns_202(self, client: TestClient):
        """restart must invoke /opt/comfyui/comfy-up.sh via subprocess."""
        called_args = []

        async def fake_subprocess(*args, **kwargs):
            called_args.extend(args)
            return _make_proc(returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
            r = client.post("/api/comfyui/restart")

        assert r.status_code == 202
        assert any("comfy-up.sh" in str(a) for a in called_args), (
            f"comfy-up.sh not in called args: {called_args}"
        )

    def test_restart_returns_202_even_when_script_fails(self, client: TestClient):
        """Script non-zero exit must still answer 202 (background op)."""

        async def fake_subprocess(*args, **kwargs):
            return _make_proc(returncode=1)

        with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
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

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            r = client.get("/api/comfyui/logs?tail=60")

        assert r.status_code == 200
        body = r.json()
        assert "lines" in body
        assert body["lines"] == log_lines

    def test_logs_default_tail_60(self, client: TestClient):
        """Default tail when not supplied must be 60."""
        call_args_store = []

        async def capture(*args, **kwargs):
            call_args_store.extend(args)
            return _make_proc(stdout=b"line1\nline2")

        with patch("asyncio.create_subprocess_exec", side_effect=capture):
            r = client.get("/api/comfyui/logs")

        assert r.status_code == 200
        joined = " ".join(str(a) for a in call_args_store)
        assert "60" in joined, f"tail=60 not found in subprocess args: {call_args_store}"

    def test_logs_custom_tail(self, client: TestClient):
        """tail= query param must be forwarded to the container runtime."""
        call_args_store = []

        async def capture(*args, **kwargs):
            call_args_store.extend(args)
            return _make_proc(stdout=b"x")

        with patch("asyncio.create_subprocess_exec", side_effect=capture):
            r = client.get("/api/comfyui/logs?tail=10")

        assert r.status_code == 200
        joined = " ".join(str(a) for a in call_args_store)
        assert "10" in joined

    def test_logs_empty_when_no_container_runtime(self, client: TestClient):
        """If docker/podman not found, return empty lines not a 500."""
        with patch("shutil.which", return_value=None):
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

        monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(wf_dir))

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
        monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(wf_dir))

        r = client.post("/api/comfyui/workflows/nonexistent/launch")

        assert r.status_code == 404

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

        # Point primary to the dir that does NOT have the file
        monkeypatch.setenv("COMFYUI_WORKFLOWS_DIR", str(primary))
        # Fallback is derived relative to the workflows dir's parent's parent
        # The implementation should infer it from COMFYUI_MODELS_DIR base path
        monkeypatch.setenv("COMFYUI_MODELS_DIR", str(tmp_path / "comfyui" / "models"))
        # Override to point at the right base
        monkeypatch.setenv("COMFYUI_DATA_DIR", str(tmp_path / "comfyui"))

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

    def _patch_status(self, stats, queue):
        base = _BASE

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
        )

    def test_status_memory_fields_present(self, client: TestClient):
        c, s, f = self._patch_status(self._SYSTEM_STATS, self._QUEUE_IDLE)
        with c, s, f:
            body = client.get("/api/comfyui/status").json()

        mem = body["memory"]
        assert mem is not None
        assert "gtt_used_gb" in mem
        assert "gtt_ceil_gb" in mem
        assert "ram_used_gb" in mem
        assert "ram_ceil_gb" in mem

    def test_util_is_none_or_zero_when_no_running_job(self, client: TestClient):
        """gpu_busy_percent is forced-high artifact — util must be 0/None when idle."""
        c, s, f = self._patch_status(self._SYSTEM_STATS, self._QUEUE_IDLE)
        with c, s, f:
            body = client.get("/api/comfyui/status").json()

        # util key may be absent or be 0/None — it must NOT be 100 when idle
        util = body.get("util") or (body.get("memory") or {}).get("util")
        assert util in (None, 0, 0.0), f"util should be null/0 when idle, got {util}"

    def test_it_s_eta_step_absent_is_acceptable(self, client: TestClient):
        """it/s, eta, step are websocket-derived — absent is OK (UI degrades gracefully)."""
        c, s, f = self._patch_status(self._SYSTEM_STATS, self._QUEUE_BUSY)
        with c, s, f:
            body = client.get("/api/comfyui/status").json()

        # These fields are optional — test just asserts that if present they're sensible
        for field in ("it_s", "eta", "step"):
            val = body.get(field)
            if val is not None:
                assert isinstance(val, (int, float, str)), f"{field} unexpected type"
