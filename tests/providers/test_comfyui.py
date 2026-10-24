"""Unit tests for ComfyUIProvider.

Covers: build_env / start_cmd / image_ref / container_spec / health
(/system_stats), and the infer() pipeline (submit → poll history → fetch
PNGs) via mocked httpx. ComfyUI's own runtime is not exercised here; the
toolbox image itself is built and published out of band (Hal0ai/hal0-runner-images).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from hal0.providers.comfyui import (
    ComfyUIInferError,
    ComfyUIProvider,
)
from hal0.runners import RUNNER_IMAGES


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
