"""Unit tests for ComfyUIProvider.

Covers: build_env / start_cmd / image_ref / container_spec / health
(/system_stats), and the infer() pipeline (submit → poll history → fetch
PNGs) via mocked httpx. ComfyUI's own runtime is not exercised here; the
toolbox image itself is built and published out of band (Hal0ai/hal0-runner-images).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from hal0.comfyui import progress
from hal0.providers import comfyui as comfyui_mod
from hal0.providers.comfyui import (
    ComfyUIInferError,
    ComfyUIProvider,
    _follow_progress,
    _ProgressRelay,
)
from hal0.runners import RUNNER_IMAGES
from tests.fixtures.comfyui_gate import refused_prompt_body


@pytest.fixture
def provider() -> ComfyUIProvider:
    return ComfyUIProvider()


@pytest.fixture(autouse=True)
def _pin_model_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """This module asserts on literal /mnt/ai-models/comfyui/* mount
    sources — pin the resolver to it (ML-3's unified store.store_root()
    default is now paths.models_dir(), not /mnt/ai-models)."""
    monkeypatch.setenv("HAL0_MODEL_STORE", "/mnt/ai-models")


@pytest.fixture(autouse=True)
def _no_data_root_provisioning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Building a spec must not touch the literal paths these tests assert on.

    ``container_spec`` provisions the data root; against ``/mnt/ai-models``
    that would create directories and write files on any box where it is
    writable. Provisioning itself is tested against ``tmp_path`` in
    test_comfyui_provisioning.py.
    """
    monkeypatch.setattr("hal0.providers.comfyui.provision_data_root", lambda: None)


@pytest.fixture
def slot_cfg() -> dict[str, Any]:
    return {"port": 8186, "device": "gpu-rocm", "_paths": {}}


@pytest.fixture
def model_info() -> dict[str, Any]:
    return {
        "path": "/var/lib/hal0/comfyui/models/checkpoints/sd_xl_turbo_1.0_fp16.safetensors",
    }


# ─── build_env / start_cmd / image_ref ────────────────────────────────────────


def test_build_env_uses_hal0_namespace(
    provider: ComfyUIProvider,
    slot_cfg: dict[str, Any],
    model_info: dict[str, Any],
) -> None:
    env = provider.build_env(slot_cfg, model_info)
    assert env["HAL0_PORT"] == "8186"
    assert env["HAL0_BACKEND"] == "rocm"
    assert env["HAL0_COMFYUI_MODEL_PATH"] == model_info["path"]
    assert env["HAL0_COMFYUI_BASE_DIR"] == "/var/lib/hal0/comfyui"


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        # A converted slot file carries only ``device`` (ADR-0029).
        ({"device": "gpu-rocm"}, "rocm"),
        ({"slot": {"device": "gpu-vulkan"}}, "vulkan"),
        # ``device`` outranks a stale ``backend`` key.
        ({"device": "gpu-rocm", "backend": "vulkan"}, "rocm"),
        # A hand-built dict with only ``backend``, then the ROCm floor.
        ({"backend": "vulkan"}, "vulkan"),
        ({}, "rocm"),
    ],
)
def test_build_env_backend_follows_the_slot_device(
    provider: ComfyUIProvider,
    model_info: dict[str, Any],
    cfg: dict[str, Any],
    expected: str,
) -> None:
    assert provider.build_env(cfg, model_info)["HAL0_BACKEND"] == expected


def test_build_env_default_port(
    provider: ComfyUIProvider,
    model_info: dict[str, Any],
) -> None:
    env = provider.build_env({}, model_info)
    # Default ComfyUI port (8188), not the hal0 slot port (8186); the
    # default applies when no slot config is provided.
    assert env["HAL0_PORT"] == "8188"


def test_start_cmd_emits_required_flags(
    provider: ComfyUIProvider,
    slot_cfg: dict[str, Any],
    model_info: dict[str, Any],
) -> None:
    env = provider.build_env(slot_cfg, model_info)
    cmd = provider.start_cmd(env)
    assert cmd[0] == "python"
    assert cmd[1] == "main.py"
    assert "--listen" in cmd
    assert "--port" in cmd
    assert "--base-directory" in cmd


def test_image_ref_follows_the_comfyui_runner_pin(provider: ComfyUIProvider) -> None:
    # The ref is the ``comfyui`` row of manifest.json ``runners`` (ADR-0028):
    # the pin ref once published, the bare repo while the row is a placeholder.
    from hal0.runners import resolve_runner_image

    ref = provider.image_ref({})
    assert ref == resolve_runner_image(RUNNER_IMAGES["comfyui"])
    assert ref.startswith("ghcr.io/hal0ai/hal0-runner-comfyui")


def test_image_ref_slot_cfg_override_wins(provider: ComfyUIProvider) -> None:
    cfg = {"image": "hal0-toolbox-comfyui:dev"}
    assert provider.image_ref(cfg) == "hal0-toolbox-comfyui:dev"


def test_image_ref_honors_image_pin(provider: ComfyUIProvider) -> None:
    """#2172: image_pin is the canonical per-slot escape hatch — it must
    launch verbatim, not the resolver default (live ct105: pinned img slot
    silently launched the old kyuz0 default)."""
    cfg = {"image_pin": "ghcr.io/hal0ai/hal0-comfyui@sha256:" + "f" * 64}
    assert provider.image_ref(cfg) == "ghcr.io/hal0ai/hal0-comfyui@sha256:" + "f" * 64


def test_image_ref_honors_nested_image_pin(provider: ComfyUIProvider) -> None:
    """[slot]-nested image_pin works too (same shape as flm/kokoro/qwen3tts)."""
    cfg = {"slot": {"image_pin": "ghcr.io/dev/comfyui-pin:test"}}
    assert provider.image_ref(cfg) == "ghcr.io/dev/comfyui-pin:test"


def test_image_ref_pin_beats_plain_image_key(provider: ComfyUIProvider) -> None:
    """Precedence: image_pin → plain image key → resolver default."""
    cfg = {"image_pin": "ghcr.io/dev/pin:a", "image": "hal0-toolbox-comfyui:dev"}
    assert provider.image_ref(cfg) == "ghcr.io/dev/pin:a"


def test_image_ref_empty_or_non_string_pin_falls_through(provider: ComfyUIProvider) -> None:
    """An empty/non-string image_pin is 'no pin' — same contract as
    container._resolve_image_ref (never str(dict), never empty ref)."""
    assert provider.image_ref({"image_pin": "", "image": "x:dev"}) == "x:dev"
    ref = provider.image_ref({"image_pin": {"oops": 1}})
    assert isinstance(ref, str)
    assert "hal0ai/hal0-runner-comfyui" in ref  # the runner pin


# ─── container_spec ──────────────────────────────────────────────────────────


def test_container_spec_passes_gpu_device_nodes(
    provider: ComfyUIProvider,
    slot_cfg: dict[str, Any],
    model_info: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Explicit nodes from resolve_gpu_device_paths (podman doesn't recurse
    # /dev/dri — same fix class as #674). Pinned here: dev/CI boxes vary.
    monkeypatch.setattr(
        "hal0.providers.comfyui.resolve_gpu_device_paths",
        lambda: ["/dev/kfd", "/dev/dri/renderD128"],
    )
    spec = provider.container_spec(slot_cfg, model_info)
    assert spec.port == 8186
    assert spec.devices == ["/dev/kfd", "/dev/dri/renderD128"]
    # Group_add must be numeric GIDs (resolve_gpu_group_ids on the host).
    assert all(g.isdigit() for g in spec.group_add)


def test_container_spec_mounts_persistent_data_dirs(
    provider: ComfyUIProvider,
    slot_cfg: dict[str, Any],
    model_info: dict[str, Any],
) -> None:
    spec = provider.container_spec(slot_cfg, model_info)
    # The data root holds models/, custom_nodes/, output/, input/, user/
    # — losing it on a `docker rm` would discard 6+ GB of weights.
    pairs = {(m.source, m.target) for m in spec.mounts}
    assert ("/mnt/ai-models/comfyui/models", "/root/comfy-models") in pairs
    assert ("/mnt/ai-models/comfyui/custom_nodes", "/opt/ComfyUI/custom_nodes") in pairs


def test_container_spec_command_runs_python_main(
    provider: ComfyUIProvider,
    slot_cfg: dict[str, Any],
    model_info: dict[str, Any],
) -> None:
    spec = provider.container_spec(slot_cfg, model_info)
    # bash -lc so the image's /opt/venv login-shell activation runs.
    assert spec.command[:2] == ["bash", "-lc"]
    assert "exec python main.py" in spec.command[2]
    # The slot port (not the ComfyUI default) is what we listen on.
    assert "--port 8186" in spec.command[2]


# ─── health ──────────────────────────────────────────────────────────────────


def _mock_response(
    *,
    status_code: int = 200,
    json_payload: Any = None,
    text: str = "",
    content: bytes = b"",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = lambda: json_payload
    resp.text = text
    resp.content = content
    resp.headers = headers or {}
    if status_code < 400:
        resp.raise_for_status = MagicMock(return_value=None)
    else:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                f"http {status_code}", request=MagicMock(), response=resp
            )
        )
    return resp


@pytest.mark.asyncio
async def test_health_ok_with_python_version(provider: ComfyUIProvider) -> None:
    body = {"system": {"python_version": "3.12.3", "os": "linux"}}

    async def _fake_get(url: str) -> httpx.Response:
        assert url.endswith("/system_stats")
        return _mock_response(status_code=200, json_payload=body)

    with patch("hal0.providers.comfyui.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        result = await provider.health(8186)

    assert result["ok"] is True
    assert result["python_version"] == "3.12.3"


@pytest.mark.asyncio
async def test_health_rejects_missing_python_version(provider: ComfyUIProvider) -> None:
    body = {"system": {}}  # no python_version

    async def _fake_get(url: str) -> httpx.Response:
        return _mock_response(status_code=200, json_payload=body)

    with patch("hal0.providers.comfyui.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        result = await provider.health(8186)

    assert result["ok"] is False
    assert "python_version" in result["status"]


@pytest.mark.asyncio
async def test_health_5xx_returns_status(provider: ComfyUIProvider) -> None:
    async def _fake_get(url: str) -> httpx.Response:
        return _mock_response(status_code=503, text="loading")

    with patch("hal0.providers.comfyui.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        result = await provider.health(8186)

    assert result["ok"] is False
    assert "503" in result["status"]


# ─── infer ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_infer_full_pipeline_returns_png(provider: ComfyUIProvider) -> None:
    """Submit → poll history → fetch /view → return image bytes."""
    submitted_prompt: dict[str, Any] = {}

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        assert url.endswith("/prompt")
        submitted_prompt.update(json)
        return _mock_response(
            status_code=200,
            json_payload={"prompt_id": "abc123", "number": 1},
        )

    completed_history = {
        "abc123": {
            "status": {
                "status_str": "success",
                "completed": True,
                "messages": [],
            },
            "outputs": {
                "9": {
                    "images": [
                        {
                            "filename": "hal0-test_00001_.png",
                            "subfolder": "",
                            "type": "output",
                        }
                    ]
                }
            },
        }
    }

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 64

    async def _fake_get(url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        if "/history/" in url:
            return _mock_response(status_code=200, json_payload=completed_history)
        if "/view" in url:
            return _mock_response(
                status_code=200,
                content=png_bytes,
                headers={"content-type": "image/png"},
            )
        raise AssertionError(f"unexpected GET {url}")

    with patch("hal0.providers.comfyui.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.post = _fake_post
        client.get = _fake_get
        result = await provider.infer(
            8186,
            {
                "model": "sdxl-turbo",
                "prompt": "a cat in a hat",
                "n": 1,
                "size": "1024x1024",
                "_hal0_template": "sdxl_turbo_simple",
            },
        )

    assert "images" in result
    assert len(result["images"]) == 1
    assert result["images"][0]["png"] == png_bytes
    # The translator must have populated the workflow we sent.
    assert "prompt" in submitted_prompt
    sent_graph = submitted_prompt["prompt"]
    # Node 6 holds the positive prompt in our SDXL Turbo template.
    assert sent_graph["6"]["inputs"]["text"] == "a cat in a hat"
    # Node 4 is the CheckpointLoaderSimple — the template's baked checkpoint is kept.
    assert sent_graph["4"]["inputs"]["ckpt_name"] == "sd_xl_turbo_1.0_fp16.safetensors"


@pytest.mark.asyncio
async def test_infer_requires_template(provider: ComfyUIProvider) -> None:
    with pytest.raises(ComfyUIInferError) as exc:
        await provider.infer(8186, {"prompt": "x"})
    assert "_hal0_template" in exc.value.message


@pytest.mark.asyncio
async def test_infer_rejects_non_string_ckpt_override(provider: ComfyUIProvider) -> None:
    with pytest.raises(ComfyUIInferError) as exc:
        await provider.infer(
            8186,
            {"prompt": "x", "_hal0_template": "sdxl_simple", "_hal0_ckpt_override": 7},
        )
    assert "_hal0_ckpt_override" in exc.value.message


@pytest.mark.asyncio
async def test_infer_patches_ckpt_override(provider: ComfyUIProvider) -> None:
    """``_hal0_ckpt_override`` reaches the template's ``ckpt_name`` pointer."""
    submitted: dict[str, Any] = {}

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        submitted.update(json)
        raise ComfyUIInferError("stop after submit")

    with patch("hal0.providers.comfyui.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.post = _fake_post
        with pytest.raises(ComfyUIInferError):
            await provider.infer(
                8186,
                {
                    "prompt": "x",
                    "_hal0_template": "sdxl_simple",
                    "_hal0_ckpt_override": "mine.safetensors",
                },
            )
    assert submitted["prompt"]["4"]["inputs"]["ckpt_name"] == "mine.safetensors"


@pytest.mark.asyncio
async def test_infer_surfaces_workflow_error(provider: ComfyUIProvider) -> None:
    """ComfyUI workflow validation failure → typed dispatch.upstream_failed."""

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        return _mock_response(
            status_code=200,
            json_payload={"prompt_id": "fail1", "number": 1},
        )

    error_history = {
        "fail1": {
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [
                    ["execution_error", {"node_id": "4", "exception_message": "ckpt missing"}]
                ],
            },
            "outputs": {},
        }
    }

    async def _fake_get(url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        if "/history/" in url:
            return _mock_response(status_code=200, json_payload=error_history)
        raise AssertionError(f"unexpected GET {url}")

    with patch("hal0.providers.comfyui.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.post = _fake_post
        client.get = _fake_get
        with pytest.raises(ComfyUIInferError) as exc:
            await provider.infer(
                8186,
                {
                    "prompt": "x",
                    "_hal0_template": "sdxl_turbo_simple",
                },
            )
    assert exc.value.code == "dispatch.upstream_failed"


# ─── step progress over ComfyUI's websocket ───────────────────────────────────

_LISTENER_FAILED = "comfyui.progress_listener_failed"
_CLOSE = object()


class _FakeWS:
    """Stands in for ``hal0.providers.comfyui._ws_connect`` and the socket it opens.

    Frames are queued with :meth:`push`; the socket yields them in order and then
    stays open, like a ComfyUI that has nothing more to say, until :meth:`hang_up`.
    ``connect_error`` makes the connect fail, ``connect_hangs`` makes the handshake
    never finish. ``events`` records the order of connect, close and (in the infer
    tests) submit, ``urls`` what was dialled and ``cancelled`` whether the listener
    was cancelled while waiting for a frame.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[object] = asyncio.Queue()
        self.connect_error: Exception | None = None
        self.connect_hangs = False
        self.urls: list[str] = []
        self.events: list[str] = []
        self.cancelled = False

    def push(self, frame: str | bytes) -> None:
        self.queue.put_nowait(frame)

    def push_json(self, kind: str, data: dict[str, Any]) -> None:
        self.push(json.dumps({"type": kind, "data": data}))

    def hang_up(self) -> None:
        self.queue.put_nowait(_CLOSE)

    async def flush(self) -> None:
        """Let the listener task consume everything queued so far."""
        for _ in range(5):
            await asyncio.sleep(0)

    # the connector: ``async with _ws_connect(url) as socket``
    def __call__(self, url: str) -> _FakeWS:
        self.urls.append(url)
        return self

    async def __aenter__(self) -> _FakeWS:
        if self.connect_hangs:
            await asyncio.Event().wait()
        if self.connect_error is not None:
            raise self.connect_error
        self.events.append("connect")
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.events.append("close")
        return False

    # the socket: ``async for frame in socket``
    def __aiter__(self) -> Any:
        return self._frames()

    async def _frames(self) -> Any:
        while True:
            try:
                item = await self.queue.get()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            if item is _CLOSE:
                return
            yield item


@pytest.fixture(autouse=True)
def fake_ws(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeWS]:
    """Every test in this module runs without a network: an idle socket by default."""
    progress._reset()
    fake = _FakeWS()
    monkeypatch.setattr("hal0.providers.comfyui._ws_connect", fake)
    yield fake
    progress._reset()


def _listener_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == _LISTENER_FAILED]


def _recording_update(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, int, str | None]]:
    """Record every ``progress.update`` the listener makes, still applying it."""
    calls: list[tuple[str, int, int, str | None]] = []
    real = progress.update

    def _spy(prompt_id: str, *, step: int, total: int, node: str | None) -> None:
        calls.append((prompt_id, step, total, node))
        real(prompt_id, step=step, total=total, node=node)

    monkeypatch.setattr(progress, "update", _spy)
    return calls


def _progress_frame(
    ws: _FakeWS,
    value: int,
    total: int,
    *,
    prompt_id: str | None = "p1",
    node: str | None = "3",
) -> None:
    data: dict[str, Any] = {"value": value, "max": total}
    if prompt_id is not None:
        data["prompt_id"] = prompt_id
    if node is not None:
        data["node"] = node
    ws.push_json("progress", data)


async def _follow(
    ws_url: str = "ws://127.0.0.1:8186/ws?clientId=c1",
) -> tuple[_ProgressRelay, bool]:
    """Run the listener over what the fake socket holds; returns the relay and ``ready``."""
    relay = _ProgressRelay("sdxl-turbo")
    relay.claim("p1")
    ready = asyncio.Event()
    await asyncio.wait_for(_follow_progress(ws_url, relay, ready), timeout=5)
    return relay, ready.is_set()


async def test_listener_maps_progress_messages(
    fake_ws: _FakeWS, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two progress frames become two tracker updates; everything else is ignored."""
    calls = _recording_update(monkeypatch)
    fake_ws.push_json("status", {"status": {"exec_info": {"queue_remaining": 1}}})
    fake_ws.push_json("execution_start", {"prompt_id": "p1"})
    fake_ws.push(b"\x00\x00\x00\x01\xff\xd8preview-jpeg")
    fake_ws.push_json("executing", {"node": "3", "prompt_id": "p1"})
    _progress_frame(fake_ws, 1, 4)
    _progress_frame(fake_ws, 2, 4)
    fake_ws.push_json("executed", {"node": "9", "prompt_id": "p1", "output": {}})
    fake_ws.hang_up()

    await _follow()

    assert calls == [("p1", 1, 4, "3"), ("p1", 2, 4, "3")]
    assert progress.current() == {
        "model": "sdxl-turbo",
        "step": 2,
        "total": 4,
        "node": "3",
        "elapsed_s": pytest.approx(0.0, abs=1.0),
        "prompt_id": "p1",
    }


async def test_listener_uses_the_executing_node_when_progress_names_none(
    fake_ws: _FakeWS, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _recording_update(monkeypatch)
    fake_ws.push_json("executing", {"node": "17", "prompt_id": "p1"})
    _progress_frame(fake_ws, 3, 8, node=None)
    fake_ws.push_json("executing", {"node": None, "prompt_id": "p1"})
    _progress_frame(fake_ws, 4, 8, node=None)
    fake_ws.hang_up()

    await _follow()

    # A finished prompt's ``executing`` (node null) does not erase the last node.
    assert calls == [("p1", 3, 8, "17"), ("p1", 4, 8, "17")]


async def test_listener_ignores_frames_for_another_prompt(
    fake_ws: _FakeWS, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _recording_update(monkeypatch)
    _progress_frame(fake_ws, 9, 9, prompt_id="someone-elses")
    _progress_frame(fake_ws, 1, 4, prompt_id="p1")
    _progress_frame(fake_ws, 2, 4, prompt_id=None)  # older ComfyUI: no prompt_id on the frame
    fake_ws.hang_up()

    await _follow()

    assert calls == [("p1", 1, 4, "3"), ("p1", 2, 4, "3")]


async def test_listener_keeps_frames_that_arrive_before_the_prompt_id_is_known(
    fake_ws: _FakeWS,
) -> None:
    """The socket is open before /prompt answers, so the first frames can beat the prompt id."""
    relay = _ProgressRelay("sdxl-turbo")
    ready = asyncio.Event()
    task = asyncio.create_task(_follow_progress("ws://x/ws?clientId=c", relay, ready))
    await ready.wait()
    _progress_frame(fake_ws, 1, 4, prompt_id="p1")
    _progress_frame(fake_ws, 2, 4, prompt_id="p1")
    await fake_ws.flush()
    assert progress.current() is None  # nothing to attach the frames to yet

    relay.claim("p1")

    current = progress.current()
    assert current is not None
    assert (current["step"], current["total"], current["node"]) == (2, 4, "3")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_listener_ignores_early_frames_that_turn_out_to_be_for_another_prompt(
    fake_ws: _FakeWS,
) -> None:
    relay = _ProgressRelay("sdxl-turbo")
    ready = asyncio.Event()
    task = asyncio.create_task(_follow_progress("ws://x/ws?clientId=c", relay, ready))
    await ready.wait()
    _progress_frame(fake_ws, 5, 9, prompt_id="someone-elses")
    await fake_ws.flush()

    relay.claim("p1")

    current = progress.current()
    assert current is not None
    assert (current["step"], current["total"]) == (None, None)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "bad_frame",
    [
        "this is not json",
        "[1, 2, 3]",
        json.dumps({"type": "progress", "data": "nope"}),
        json.dumps({"type": "progress", "data": {"value": "2", "max": 4}}),
        json.dumps({"type": "progress", "data": {"value": 2}}),
        json.dumps({"type": "progress", "data": {"value": True, "max": 4}}),
    ],
)
async def test_listener_bad_frame_logs_one_warning_and_stops(
    fake_ws: _FakeWS,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    bad_frame: str,
) -> None:
    calls = _recording_update(monkeypatch)
    caplog.set_level(logging.WARNING, logger="hal0.providers.comfyui")
    fake_ws.push(bad_frame)
    _progress_frame(fake_ws, 1, 4)  # never read: the listener stopped
    fake_ws.hang_up()

    await _follow()

    assert calls == []
    assert len(_listener_warnings(caplog)) == 1


async def test_listener_close_mid_render_logs_one_warning(
    fake_ws: _FakeWS, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="hal0.providers.comfyui")
    _progress_frame(fake_ws, 1, 4)
    fake_ws.hang_up()  # ComfyUI closes the socket while the render is still going

    await _follow()

    warnings = _listener_warnings(caplog)
    assert len(warnings) == 1
    assert "closed" in warnings[0].reason  # type: ignore[attr-defined]
    # What arrived before the close is kept.
    current = progress.current()
    assert current is not None
    assert current["step"] == 1


async def test_listener_connection_failure_logs_one_warning_and_signals_ready(
    fake_ws: _FakeWS, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="hal0.providers.comfyui")
    fake_ws.connect_error = ConnectionRefusedError("refused")

    _relay, ready = await _follow()

    assert ready  # a failed connect must not leave infer waiting for it
    warnings = _listener_warnings(caplog)
    assert len(warnings) == 1
    assert "ConnectionRefusedError" in warnings[0].reason  # type: ignore[attr-defined]


async def test_listener_cancellation_is_silent(
    fake_ws: _FakeWS, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="hal0.providers.comfyui")
    relay = _ProgressRelay("m")
    ready = asyncio.Event()
    task = asyncio.create_task(_follow_progress("ws://x/ws?clientId=c", relay, ready))
    await ready.wait()

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert fake_ws.cancelled
    assert fake_ws.events == ["connect", "close"]
    assert _listener_warnings(caplog) == []


# ─── infer + progress ─────────────────────────────────────────────────────────

_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


def _completed_history(prompt_id: str) -> dict[str, Any]:
    return {
        prompt_id: {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {
                "9": {
                    "images": [
                        {"filename": "hal0-test_00001_.png", "subfolder": "", "type": "output"}
                    ]
                }
            },
        }
    }


async def _render(
    provider: ComfyUIProvider,
    *,
    fake_ws: _FakeWS | None = None,
    prompt_id: str = "abc123",
    body: dict[str, Any] | None = None,
    on_submit: Callable[[], Awaitable[None]] | None = None,
    on_history: Callable[[], Awaitable[None]] | None = None,
    history: dict[str, Any] | None = None,
    port: int = 8186,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run ``infer`` against a mocked ComfyUI HTTP API. Returns (result, submitted /prompt body)."""
    submitted: dict[str, Any] = {}

    async def _post(url: str, json: dict[str, Any]) -> httpx.Response:
        assert url.endswith("/prompt")
        submitted.update(json)
        if fake_ws is not None:
            fake_ws.events.append("submit")
        if on_submit is not None:
            await on_submit()
        return _mock_response(status_code=200, json_payload={"prompt_id": prompt_id, "number": 1})

    async def _get(url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        if "/history/" in url:
            if on_history is not None:
                await on_history()
            return _mock_response(
                status_code=200, json_payload=history or _completed_history(prompt_id)
            )
        if "/view" in url:
            return _mock_response(
                status_code=200, content=_PNG, headers={"content-type": "image/png"}
            )
        raise AssertionError(f"unexpected GET {url}")

    request_body = {
        "model": "sdxl-turbo",
        "prompt": "a cat in a hat",
        "size": "1024x1024",
        "_hal0_template": "sdxl_turbo_simple",
        **(body or {}),
    }
    with patch("hal0.providers.comfyui.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.post = _post
        client.get = _get
        result = await provider.infer(port, request_body)
    return result, submitted


async def test_ws_failure_does_not_fail_render(
    provider: ComfyUIProvider, fake_ws: _FakeWS, caplog: pytest.LogCaptureFixture
) -> None:
    """A refused websocket costs the progress read-out, never the picture."""
    caplog.set_level(logging.WARNING, logger="hal0.providers.comfyui")
    fake_ws.connect_error = ConnectionRefusedError("connection refused")

    result, _ = await _render(provider, fake_ws=fake_ws)

    assert [img["png"] for img in result["images"]] == [_PNG]
    assert len(_listener_warnings(caplog)) == 1
    assert progress.current() is None


async def test_render_survives_a_websocket_that_closes_mid_render(
    provider: ComfyUIProvider, fake_ws: _FakeWS, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="hal0.providers.comfyui")

    async def _hang_up() -> None:
        fake_ws.hang_up()
        await fake_ws.flush()

    result, _ = await _render(provider, fake_ws=fake_ws, on_history=_hang_up)

    assert len(result["images"]) == 1
    assert len(_listener_warnings(caplog)) == 1
    assert progress.current() is None


async def test_progress_clears_after_render(provider: ComfyUIProvider, fake_ws: _FakeWS) -> None:
    seen: list[dict[str, Any] | None] = []

    async def _mid_render() -> None:
        seen.append(progress.current())

    await _render(provider, fake_ws=fake_ws, on_history=_mid_render)

    assert seen[0] is not None
    assert seen[0]["prompt_id"] == "abc123"
    assert progress.current() is None


async def test_progress_clears_when_the_render_fails(
    provider: ComfyUIProvider, fake_ws: _FakeWS
) -> None:
    failed = {
        "abc123": {
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [["execution_error", {"exception_message": "boom"}]],
            },
            "outputs": {},
        }
    }
    seen: list[dict[str, Any] | None] = []

    async def _mid_render() -> None:
        seen.append(progress.current())

    with pytest.raises(ComfyUIInferError):
        await _render(provider, fake_ws=fake_ws, history=failed, on_history=_mid_render)

    assert seen[0] is not None  # it was live while ComfyUI worked
    assert progress.current() is None
    assert fake_ws.events[-1] == "close"


async def test_progress_clears_when_infer_is_cancelled(
    provider: ComfyUIProvider, fake_ws: _FakeWS
) -> None:
    started = asyncio.Event()

    async def _stall() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(_render(provider, fake_ws=fake_ws, on_history=_stall))
    await started.wait()
    assert progress.current() is not None

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert progress.current() is None
    assert fake_ws.cancelled
    assert fake_ws.events[-1] == "close"


async def test_infer_relays_steps_while_it_waits_for_history(
    provider: ComfyUIProvider, fake_ws: _FakeWS
) -> None:
    seen: list[dict[str, Any] | None] = []

    async def _mid_render() -> None:
        fake_ws.push_json("executing", {"node": "3", "prompt_id": "abc123"})
        _progress_frame(fake_ws, 2, 8, prompt_id="abc123", node="3")
        await fake_ws.flush()
        seen.append(progress.current())
        _progress_frame(fake_ws, 3, 8, prompt_id="abc123", node="3")
        await fake_ws.flush()
        seen.append(progress.current())

    await _render(provider, fake_ws=fake_ws, on_history=_mid_render)

    assert [(s["step"], s["total"], s["node"]) for s in seen if s] == [(2, 8, "3"), (3, 8, "3")]
    assert seen[0] is not None
    assert seen[0]["model"] == "sdxl-turbo"
    assert seen[0]["prompt_id"] == "abc123"


async def test_infer_keeps_frames_that_beat_the_submit_response(
    provider: ComfyUIProvider, fake_ws: _FakeWS
) -> None:
    """Connect-first is what lets the first frames land: they may precede /prompt's answer."""
    seen: list[dict[str, Any] | None] = []

    async def _during_submit() -> None:
        _progress_frame(fake_ws, 1, 4, prompt_id="abc123", node="3")
        await fake_ws.flush()

    async def _mid_render() -> None:
        seen.append(progress.current())

    await _render(provider, fake_ws=fake_ws, on_submit=_during_submit, on_history=_mid_render)

    assert seen[0] is not None
    assert (seen[0]["step"], seen[0]["total"]) == (1, 4)


async def test_infer_connects_before_it_submits(
    provider: ComfyUIProvider, fake_ws: _FakeWS
) -> None:
    await _render(provider, fake_ws=fake_ws)

    assert fake_ws.events == ["connect", "submit", "close"]


async def test_infer_does_not_wait_forever_for_a_hung_handshake(
    provider: ComfyUIProvider, fake_ws: _FakeWS, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hal0.providers.comfyui._WS_READY_WAIT_S", 0.05)
    fake_ws.connect_hangs = True

    result, _ = await asyncio.wait_for(_render(provider, fake_ws=fake_ws), timeout=5)

    assert len(result["images"]) == 1
    assert fake_ws.events == ["submit"]
    assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]


async def test_infer_leaves_no_listener_task_behind(
    provider: ComfyUIProvider, fake_ws: _FakeWS
) -> None:
    await _render(provider, fake_ws=fake_ws)

    assert fake_ws.cancelled
    assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]


async def test_infer_listens_on_the_providers_own_host_and_the_requests_client_id(
    provider: ComfyUIProvider, fake_ws: _FakeWS
) -> None:
    _, submitted = await _render(provider, fake_ws=fake_ws, port=8199)

    assert fake_ws.urls == [f"ws://127.0.0.1:8199/ws?clientId={submitted['client_id']}"]


async def test_each_request_listens_under_its_own_client_id(
    provider: ComfyUIProvider, fake_ws: _FakeWS
) -> None:
    _, first = await _render(provider, fake_ws=fake_ws)
    _, second = await _render(provider, fake_ws=fake_ws)

    assert first["client_id"] != second["client_id"]
    assert len({u.split("clientId=")[1] for u in fake_ws.urls}) == 2


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # The route hands down the resolved id; it wins over whatever the client typed.
        ({"_hal0_model": "sdxl-lightning", "model": " SDXL-L "}, "sdxl-lightning"),
        ({"_hal0_model": "ckpt/anime/waifu"}, "ckpt/anime/waifu"),
        # No resolved id (a caller other than the route): the request's own model, trimmed.
        ({"model": "  sd-1.5-pruned-emaonly "}, "sd-1.5-pruned-emaonly"),
        # Neither: the workflow template, never an empty label.
        ({"model": ""}, "sdxl_turbo_simple"),
        ({"_hal0_model": 7, "model": ""}, "sdxl_turbo_simple"),
    ],
)
async def test_progress_names_the_model_the_render_used(
    provider: ComfyUIProvider, fake_ws: _FakeWS, body: dict[str, Any], expected: str
) -> None:
    seen: list[dict[str, Any] | None] = []

    async def _mid_render() -> None:
        seen.append(progress.current())

    await _render(provider, fake_ws=fake_ws, body=body, on_history=_mid_render)

    assert seen[0] is not None
    assert seen[0]["model"] == expected


async def test_hal0_model_never_reaches_the_workflow_builder(
    provider: ComfyUIProvider, fake_ws: _FakeWS
) -> None:
    seen_bodies: list[dict[str, Any]] = []
    real_build = comfyui_mod.build_workflow

    def _spy(*, body: dict[str, Any], **kwargs: Any) -> Any:
        seen_bodies.append(dict(body))
        return real_build(body=body, **kwargs)

    with patch("hal0.providers.comfyui.build_workflow", _spy):
        await _render(provider, fake_ws=fake_ws, body={"_hal0_model": "sdxl-lightning"})

    assert seen_bodies
    assert not any(k.startswith("_hal0_") for k in seen_bodies[0])


# ─── the GPU gate's refusal of a prompt ───────────────────────────────────────

_GATE_REASON = (
    "hal0 requires authentication and ComfyUI's GPU gate has no credential (HTTP 401), so the "
    "prompt was not queued. Run `hal0 slot restart img` to give the gate its current credential"
)


def _refused_prompt(reason: str = _GATE_REASON) -> dict[str, Any]:
    """The body the hal0_gpu_gate node answers ``/prompt`` with, built by the node itself."""
    return refused_prompt_body(reason)


async def _submit_rejected(provider: ComfyUIProvider, response: MagicMock) -> Any:
    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        assert url.endswith("/prompt")
        return response

    with patch("hal0.providers.comfyui.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.post = _fake_post
        return await provider.infer(
            8186, {"model": "sdxl-turbo", "prompt": "a cat", "_hal0_template": "sdxl_turbo_simple"}
        )


async def test_provider_maps_gate_refusal_503_to_image_gate_refused(
    provider: ComfyUIProvider,
) -> None:
    """The gate's 503 is told apart from any other failed submit by the shape of its body
    (``refused_prompt_body``), and its reason reaches the caller verbatim."""
    from hal0.errors import Hal0Error
    from hal0.providers.comfyui import ImageGateRefused

    response = _mock_response(status_code=503, json_payload=_refused_prompt())

    with pytest.raises(ImageGateRefused) as exc:
        await _submit_rejected(provider, response)

    assert isinstance(exc.value, Hal0Error)
    assert exc.value.code == "image.gate_refused"
    assert exc.value.status == 503
    assert exc.value.message == _GATE_REASON


async def test_the_gate_refusal_mapping_follows_the_body_shape_not_the_text(
    provider: ComfyUIProvider,
) -> None:
    from hal0.providers.comfyui import ImageGateRefused

    reason = "something the gate has never said before"
    with pytest.raises(ImageGateRefused) as exc:
        await _submit_rejected(
            provider, _mock_response(status_code=503, json_payload=_refused_prompt(reason))
        )
    assert exc.value.message == reason

    # The words alone are not the gate: a 503 from anything else stays an upstream failure.
    lookalike = {"error": {"message": _GATE_REASON}}
    with pytest.raises(ComfyUIInferError) as plain:
        await _submit_rejected(provider, _mock_response(status_code=503, json_payload=lookalike))
    assert plain.value.code == "dispatch.upstream_failed"


@pytest.mark.parametrize(
    "response",
    [
        _mock_response(status_code=500, json_payload=_refused_prompt()),
        _mock_response(status_code=502, json_payload=_refused_prompt()),
        _mock_response(status_code=503, json_payload=None),
        _mock_response(status_code=503, json_payload=["not", "an", "object"]),
        _mock_response(status_code=503, json_payload={"error": "overloaded"}),
        _mock_response(status_code=503, json_payload={"error": {"type": "other"}}),
        _mock_response(status_code=503, json_payload={"error": {"type": "hal0_gpu_not_ready"}}),
    ],
    ids=[
        "500",
        "502",
        "no-body",
        "list-body",
        "string-error",
        "other-type",
        "no-node-errors",
    ],
)
async def test_any_other_failed_submit_stays_an_upstream_failure(
    provider: ComfyUIProvider, response: MagicMock
) -> None:
    with pytest.raises(ComfyUIInferError) as exc:
        await _submit_rejected(provider, response)

    assert exc.value.code == "dispatch.upstream_failed"
    assert exc.value.status == 502
    assert f"HTTP {response.status_code}" in exc.value.message


async def test_a_gate_refusal_without_a_reason_still_maps(provider: ComfyUIProvider) -> None:
    from hal0.providers.comfyui import ImageGateRefused

    body = {"error": {"type": "hal0_gpu_not_ready"}, "node_errors": {}}

    with pytest.raises(ImageGateRefused) as exc:
        await _submit_rejected(provider, _mock_response(status_code=503, json_payload=body))

    assert exc.value.message  # a fixed line, never empty


async def test_a_503_that_is_not_json_stays_an_upstream_failure(
    provider: ComfyUIProvider,
) -> None:
    response = _mock_response(status_code=503, text="<html>busy</html>")

    def _not_json() -> Any:
        raise ValueError("not json")

    response.json = _not_json

    with pytest.raises(ComfyUIInferError):
        await _submit_rejected(provider, response)


def test_the_gate_reason_the_provider_reads_is_the_one_the_node_writes() -> None:
    """Contract with the asset: ``refused_prompt_body`` puts the reason under
    ``error.details`` and marks the body ``hal0_gpu_not_ready``."""
    body = _refused_prompt("because")

    assert body["error"]["type"] == "hal0_gpu_not_ready"
    assert body["error"]["details"] == "because"
    assert comfyui_mod.gate_refusal_reason(503, body) == "because"
    assert comfyui_mod.gate_refusal_reason(500, body) is None


@pytest.mark.parametrize("field", ["details", "message"])
async def test_a_lone_surrogate_in_the_gates_reason_is_replaced(
    provider: ComfyUIProvider, field: str
) -> None:
    """The 503 body comes from inside the container. ``"\\ud800"`` is valid JSON and not
    valid UTF-8; kept, it breaks every later envelope, log line and Sentry event."""
    from hal0.providers.comfyui import ImageGateRefused

    body = _refused_prompt("x")
    body["error"]["details"] = "\ud800 refused" if field == "details" else ""
    body["error"]["message"] = "\ud800 refused" if field == "message" else "constant"

    with pytest.raises(ImageGateRefused) as exc:
        await _submit_rejected(provider, _mock_response(status_code=503, json_payload=body))

    assert exc.value.message == "? refused"
    exc.value.message.encode("utf-8")


async def test_an_overlong_gate_reason_is_cut_to_500_characters(provider: ComfyUIProvider) -> None:
    from hal0.providers.comfyui import ImageGateRefused

    body = _refused_prompt("g" * 2000)

    with pytest.raises(ImageGateRefused) as exc:
        await _submit_rejected(provider, _mock_response(status_code=503, json_payload=body))

    assert exc.value.message == "g" * 500


def test_image_gate_refused_cleans_whatever_it_is_built_with() -> None:
    from hal0.providers.comfyui import ImageGateRefused

    err = ImageGateRefused("a\ud800b" + "c" * 600, details={"port": 1})

    assert err.message == "a?b" + "c" * 497
    assert str(err) == err.message
    assert err.details == {"port": 1}
    assert (err.code, err.status) == ("image.gate_refused", 503)


def test_clean_reason() -> None:
    from hal0.comfyui.gate_reason import REASON_MAX, clean_reason

    assert REASON_MAX == 500
    assert clean_reason("plain") == "plain"
    assert clean_reason("café ☃ \U0001f600") == "café ☃ \U0001f600"
    assert clean_reason("\ud800") == "?"
    assert clean_reason("\udfff\ud800") == "??"
    assert clean_reason("r" * 501) == "r" * 500
    assert clean_reason(42) == "42"
    assert clean_reason("") == ""
