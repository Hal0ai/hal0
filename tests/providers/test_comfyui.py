"""Unit tests for ComfyUIProvider.

Covers: build_env / start_cmd / image_ref / container_spec / health
(/system_stats), and the infer() pipeline (submit → poll history → fetch
PNGs) via mocked httpx. ComfyUI's own runtime is not exercised here; the
toolbox image itself is built and published out of band, and its pinned
digest is refreshed by scripts/update-toolbox-digests.sh before a release.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from hal0.providers.comfyui import (
    _HAL0_COMFYUI_IMAGE,
    ComfyUIInferError,
    ComfyUIProvider,
)


@pytest.fixture
def provider() -> ComfyUIProvider:
    return ComfyUIProvider()


@pytest.fixture
def slot_cfg() -> dict[str, Any]:
    return {"port": 8186, "backend": "rocm", "_paths": {}}


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


def test_image_ref_follows_manifest_pin_or_fallback(provider: ComfyUIProvider) -> None:
    # Phase D (#599): manifest.json repins comfyui to the kyuz0 Strix Halo
    # build. With the repo manifest visible (HAL0_HOME unset), image_ref
    # returns that digest pin; without a manifest it falls back to
    # _HAL0_COMFYUI_IMAGE.
    ref = provider.image_ref({})
    assert (
        ref
        == (
            "docker.io/kyuz0/amd-strix-halo-comfyui"
            "@sha256:0066678ae9043f69a1c8c7699e70626ceffd35c1a8ca03227a05640ad0241ed2"
        )
        or ref == _HAL0_COMFYUI_IMAGE
    )


def test_image_ref_slot_cfg_override_wins(provider: ComfyUIProvider) -> None:
    cfg = {"image": "hal0-toolbox-comfyui:dev"}
    assert provider.image_ref(cfg) == "hal0-toolbox-comfyui:dev"


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
    # bash -lc so the kyuz0 image's /opt/venv login-shell activation runs.
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
                "_hal0_model_class": "sdxl-turbo",
                "_hal0_ckpt_filename": "sd_xl_turbo_1.0_fp16.safetensors",
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
    # Node 4 is the CheckpointLoaderSimple — ckpt_filename must be patched.
    assert sent_graph["4"]["inputs"]["ckpt_name"] == "sd_xl_turbo_1.0_fp16.safetensors"


@pytest.mark.asyncio
async def test_infer_requires_ckpt_filename(provider: ComfyUIProvider) -> None:
    with pytest.raises(ComfyUIInferError) as exc:
        await provider.infer(8186, {"prompt": "x"})
    assert "ckpt_filename" in exc.value.message


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
                    "_hal0_model_class": "sdxl-turbo",
                    "_hal0_ckpt_filename": "ghost.safetensors",
                },
            )
    assert exc.value.code == "dispatch.upstream_failed"
