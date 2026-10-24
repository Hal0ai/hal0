"""The LLM registry refuses image models: ``ImageAsset`` (422 ``model.image_asset``).

ComfyUI image models belong to the image catalog (``hal0.comfyui``). A pull that
names one, or that asks for the ``image`` capability, is refused before a byte
moves, so nothing lands in ComfyUI's tree or under ``<store>/image/``.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from hal0.comfyui import paths as comfyui_paths
from hal0.errors import Hal0Error
from hal0.registry.pull import ImageAsset, make_job, refuse_image_asset, run_pull
from hal0.registry.store import ModelRegistry


@pytest.fixture
def store(tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = Path(tmp_hal0_home) / "model-store"
    monkeypatch.setenv("HAL0_MODEL_STORE", str(root))
    return root


def _nothing_landed(store: Path) -> bool:
    return not (store / "image").exists() and not any(comfyui_paths.models_root().rglob("*.*"))


def test_image_asset_default_message_points_at_the_catalog() -> None:
    err = ImageAsset()
    assert isinstance(err, Hal0Error)
    assert (err.code, err.status) == ("model.image_asset", 422)
    assert "Image Gen" in err.message
    assert "hal0 comfyui models install <id>" in err.message


def test_image_asset_keeps_an_explicit_message_and_details() -> None:
    err = ImageAsset("custom", details={"path": "/x"})
    assert err.message == "custom"
    assert err.details == {"path": "/x"}


@pytest.mark.parametrize("model_id", ["sdxl-turbo", "esrgan-4x", "sd-1.5-pruned-emaonly"])
def test_refuse_image_asset_refuses_a_catalog_id(model_id: str) -> None:
    with pytest.raises(ImageAsset) as exc:
        refuse_image_asset(model_id)
    assert exc.value.details == {"model_id": model_id}


@pytest.mark.parametrize("capability", ["image", "img", " Image "])
def test_refuse_image_asset_refuses_the_image_capability(capability: str) -> None:
    with pytest.raises(ImageAsset):
        refuse_image_asset("user.some-model", capability)


@pytest.mark.parametrize("capability", [None, "", "chat", "embed", "tts"])
def test_refuse_image_asset_lets_llm_pulls_through(capability: str | None) -> None:
    refuse_image_asset("qwen3-4b", capability)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_id", "capability"),
    [("sdxl-turbo", None), ("user.some-model", "image"), ("user.some-model", "img")],
)
async def test_run_pull_refuses_image_models_and_moves_no_bytes(
    store: Path, model_id: str, capability: str | None
) -> None:
    requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return httpx.Response(200, content=b"x" * 64, headers={"Content-Length": "64"})

    job = make_job(model_id)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await run_pull(
            job,
            hf_repo="stabilityai/sdxl-turbo",
            hf_file="sd_xl_turbo_1.0_fp16.safetensors",
            registry=ModelRegistry(),
            client=client,
            capability=capability,
        )
    finally:
        await client.aclose()

    assert job.state == "failed"
    assert job.error_code == "model.image_asset"
    assert requests == []
    assert _nothing_landed(store)
