"""Wiring tests for ``POST /v1/images/generations`` + the image cache.

The provider's ``infer()`` is mocked so we don't need a live ComfyUI;
this exercises:

  * dispatcher routing (image-gen path lands on the ``img`` slot legacy
    fallback when no registry binding exists).
  * model resolution (hal0.comfyui.resolve): unknown ids 404, models that are
    not installed 409, models the API cannot render 422, user checkpoints
    resolve as ``ckpt/<path>``.
  * URL response_format → cached PNG + ``/api/images/cache/...`` URL.
  * b64_json response_format → inline base64 PNG.
  * GET /api/images/cache/{name}.png serves the cached bytes.
  * Cache-miss + path-traversal attempt 404 cleanly.
  * Cold-slot path (issue #725): ensure_img() runs before dispatch so the
    img upstream is registered before resolve_by_capability runs.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from hal0.comfyui import paths
from hal0.comfyui.catalog import CATALOG, component_path
from hal0.upstreams.registry import Upstream


@pytest.fixture(autouse=True)
def comfyui_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The ComfyUI models tree under ``tmp_path``: resolution reads it for install state."""
    root = tmp_path / "comfyui-data"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(root))
    assert paths.models_root().is_relative_to(tmp_path)
    return root


def _install(*model_ids: str) -> None:
    """Put every component file of the catalog models where an install would."""
    root = paths.models_root()
    for model_id in model_ids:
        for component in CATALOG[model_id].components:
            path = component_path(component, root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")


def _user_checkpoint(relpath: str, key: str = "conditioner.embedders.1.x") -> None:
    """A user checkpoint whose safetensors header sniffs as SDXL (default) or SD 1.5."""
    raw = json.dumps({key: {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}}).encode("utf-8")
    path = paths.models_root() / "checkpoints" / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\x00\x00")


def _seed_img_upstream(client: TestClient, port: int = 8186) -> None:
    """Register a fake `img` slot upstream so the dispatcher resolves to it."""
    upstreams = client.app.state.upstreams
    upstreams.upsert(
        Upstream(
            name="img",
            kind="slot",
            url=f"http://127.0.0.1:{port}/v1",
            slot_name="img",
            auth_style="none",
        )
    )


def test_v1_images_no_upstream_returns_envelope(client: TestClient) -> None:
    """No img slot configured → dispatch errors out with a 404 envelope."""
    _install("sdxl-turbo")
    r = client.post(
        "/v1/images/generations",
        json={"model": "sdxl-turbo", "prompt": "a cat"},
    )
    # NoRouteFound or LegacyResolutionFailed both surface as dispatch.* envelopes.
    assert r.status_code in (404, 502, 503)
    body = r.json()
    assert "error" in body
    assert body["error"]["code"].startswith(("dispatch.", "image."))


def test_v1_images_empty_prompt_422(client: TestClient) -> None:
    _seed_img_upstream(client)
    r = client.post(
        "/v1/images/generations",
        json={"model": "sdxl-turbo", "prompt": ""},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "image.prompt_required"


def test_v1_images_unknown_model_404(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_img_upstream(client)
    r = client.post(
        "/v1/images/generations",
        json={"model": "not-a-real-image-model", "prompt": "anything"},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "image.model_unknown"


# ─── entries without a renderable catalog template must not render (#1470) ───
#
# The gate used to be a bare ``curated.capability != "image"`` check. esrgan-4x
# is a RealESRGAN .pth upscaler whose catalog entry is not API-renderable, and
# the old class-based fallback rendered it through sdxl_turbo_simple with the
# .pth pinned as the checkpoint: a ComfyUI node failure or garbage output
# instead of a clean 4xx. Resolution now owns the gate: only an installed,
# API-renderable catalog entry (or a user checkpoint) renders.


def test_v1_images_esrgan_upscaler_rejected_not_silently_rendered(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """esrgan-4x is an upscaler: a clean 422 pointing at the editor, never a render."""
    _install("esrgan-4x")
    _seed_img_upstream(client)

    from hal0.providers import get_provider

    # If the gate regresses, this would be called with esrgan-4x's .pth
    # pinned as the checkpoint — fail loudly so a regression can't slip by
    # looking like a (bogus) 200.
    mock_infer = AsyncMock(side_effect=AssertionError("must not reach provider.infer"))
    monkeypatch.setattr(get_provider("comfyui"), "infer", mock_infer)

    r = client.post(
        "/v1/images/generations",
        json={"model": "esrgan-4x", "prompt": "anything"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "image.model_not_renderable"
    mock_infer.assert_not_called()


def _mock_png_infer(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch the provider's ``infer`` with a mock that returns one tiny PNG."""
    from hal0.providers import get_provider

    mock_infer = AsyncMock(
        return_value={
            "images": [
                {
                    "png": b"\x89PNG\r\n\x1a\n" + b"q" * 64,
                    "filename": "hal0-test.png",
                    "subfolder": "",
                    "type": "output",
                }
            ],
            "meta": {},
            "prompt_id": "p1",
        }
    )
    monkeypatch.setattr(get_provider("comfyui"), "infer", mock_infer)
    return mock_infer


@pytest.mark.parametrize(
    ("model", "template"),
    [
        ("sdxl-turbo", "sdxl_turbo_simple"),
        ("sd-1.5-pruned-emaonly", "sd15_simple"),
        # sdxl-lightning is no longer a lone LoRA on this path: the catalog entry's
        # template loads the SDXL base, the LoRA and the VAE together.
        ("sdxl-lightning", "SDXL-Lightning-8step"),
    ],
)
def test_v1_images_renders_the_catalog_entrys_template(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    template: str,
) -> None:
    _install(model)
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)

    r = client.post("/v1/images/generations", json={"model": model, "prompt": "anything"})

    assert r.status_code == 200, r.text
    sent = mock_infer.call_args.args[1]
    assert sent["_hal0_template"] == template
    assert sent["_hal0_ckpt_override"] is None
    assert "_hal0_model_class" not in sent
    assert "_hal0_ckpt_filename" not in sent


def test_v1_images_client_cannot_choose_the_checkpoint(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route always sets the internal body keys, so a client's copies never arrive."""
    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)

    r = client.post(
        "/v1/images/generations",
        json={
            "model": "sd-1.5-pruned-emaonly",
            "prompt": "anything",
            "_hal0_template": "sdxl_simple",
            "_hal0_ckpt_override": "evil.safetensors",
        },
    )

    assert r.status_code == 200, r.text
    sent = mock_infer.call_args.args[1]
    assert sent["_hal0_template"] == "sd15_simple"
    assert sent["_hal0_ckpt_override"] is None


def test_v1_images_user_checkpoint_renders_through_the_generic_template(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ckpt/<path>`` loads the operator's file; the override comes from the scan, not the body."""
    _user_checkpoint("anime/waifu.safetensors")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)

    r = client.post(
        "/v1/images/generations",
        json={
            "model": "ckpt/anime/waifu",
            "prompt": "anything",
            "_hal0_ckpt_override": "../../etc/passwd",
        },
    )

    assert r.status_code == 200, r.text
    sent = mock_infer.call_args.args[1]
    assert sent["_hal0_template"] == "sdxl_simple"
    assert sent["_hal0_ckpt_override"] == "anime/waifu.safetensors"
    assert sent["size"] == "1024x1024"
    assert r.json()["_hal0"]["model"] == "ckpt/anime/waifu"


def test_v1_images_response_names_the_model_and_template(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_hal0`` carries the resolved model id and template, whatever the provider's meta says.

    The provider meta here names no template at all, and ``meta`` stays the provider's own.
    """
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    mock_infer = AsyncMock(
        return_value={
            "images": [{"png": b"\x89PNG\r\n\x1a\n" + b"q" * 64}],
            "meta": {"seed": 7},
            "prompt_id": "p1",
        }
    )
    from hal0.providers import get_provider

    monkeypatch.setattr(get_provider("comfyui"), "infer", mock_infer)

    r = client.post("/v1/images/generations", json={"model": "sdxl-turbo", "prompt": "x"})

    assert r.status_code == 200, r.text
    meta = r.json()["_hal0"]
    assert meta["model"] == "sdxl-turbo"
    assert meta["template"] == "sdxl_turbo_simple"
    assert meta["meta"] == {"seed": 7}


def test_v1_images_response_template_for_a_user_checkpoint(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user_checkpoint("mine.safetensors", "cond_stage_model.transformer.x")
    _seed_img_upstream(client)
    _mock_png_infer(monkeypatch)  # its meta is empty

    r = client.post("/v1/images/generations", json={"model": "ckpt/mine", "prompt": "x"})

    assert r.status_code == 200, r.text
    assert r.json()["_hal0"]["template"] == "sd15_simple"


def test_v1_images_404_unknown_user_ckpt(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user_checkpoint("present.safetensors")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)

    r = client.post("/v1/images/generations", json={"model": "ckpt/absent", "prompt": "x"})

    assert r.status_code == 404, r.text
    error = r.json()["error"]
    assert error["code"] == "image.model_unknown"
    assert error["details"]["model"] == "ckpt/absent"
    assert error["details"]["available"] == ["ckpt/present"]
    mock_infer.assert_not_called()


@pytest.mark.parametrize(
    "crafted",
    [
        "ckpt/../present",
        "ckpt/present.safetensors",
        "ckpt/",
        "ckpt//present",
        "ckpt/sub/../present",
        "ckpt/sub/absent",
    ],
)
def test_v1_images_404_for_crafted_ckpt_ids(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    crafted: str,
) -> None:
    """A ``ckpt/`` id is matched to scanned ids, never joined into a path."""
    _user_checkpoint("present.safetensors")
    _user_checkpoint("sub/real.safetensors")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)

    r = client.post("/v1/images/generations", json={"model": crafted, "prompt": "x"})

    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "image.model_unknown"
    assert r.json()["error"]["details"]["available"] == ["ckpt/present", "ckpt/sub/real"]
    mock_infer.assert_not_called()


def test_v1_images_404_lists_available(client: TestClient) -> None:
    """The 404 names what the caller can actually pass: installed renderable models and ckpts."""
    _install("sd-1.5-pruned-emaonly")  # installed: listed
    # sdxl-turbo is renderable but not installed; esrgan-4x is installed but not renderable.
    _install("esrgan-4x")
    _user_checkpoint("mine.safetensors")
    _seed_img_upstream(client)

    r = client.post(
        "/v1/images/generations",
        json={"model": "not-a-real-image-model", "prompt": "anything"},
    )

    assert r.status_code == 404
    error = r.json()["error"]
    assert error["details"]["available"] == ["sd-1.5-pruned-emaonly", "ckpt/mine"]
    assert "sd-1.5-pruned-emaonly" in error["message"]
    assert "ckpt/mine" in error["message"]
    assert "sdxl-turbo" not in error["message"]
    assert "esrgan-4x" not in error["message"]


def test_v1_images_409_when_the_model_is_not_installed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)

    r = client.post("/v1/images/generations", json={"model": "sdxl-turbo", "prompt": "x"})

    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert error["code"] == "image.model_not_installed"
    assert error["details"]["install"] == "POST /api/comfyui/models/sdxl-turbo/install"
    assert error["details"]["missing_gb"] == CATALOG["sdxl-turbo"].approx_gb
    mock_infer.assert_not_called()


def test_v1_images_422_for_video_model(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)

    r = client.post("/v1/images/generations", json={"model": "wan22-t2v", "prompt": "x"})

    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "image.model_not_renderable"
    assert error["details"]["task"] == "txt2video"
    assert "editor" in error["details"]["hint"]
    mock_infer.assert_not_called()


def test_v1_images_422_when_no_model_is_named_or_selected(client: TestClient) -> None:
    _seed_img_upstream(client)

    r = client.post("/v1/images/generations", json={"prompt": "x"})

    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "image.model_required"


def test_v1_images_non_string_model_is_unknown_not_a_crash(client: TestClient) -> None:
    _seed_img_upstream(client)
    r = client.post("/v1/images/generations", json={"model": 5, "prompt": "x"})
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "image.model_unknown"


class _FakeOrchestrator:
    """Just the ``get_state()`` shape the route reads: the ``img.img`` selection."""

    def __init__(self, model: str, *, enabled: bool = True) -> None:
        self._selection = {"model": model, "enabled": enabled}
        self.calls = 0

    async def get_state(self) -> dict[str, Any]:
        self.calls += 1
        return {"selections": {"img": {"img": self._selection}}}


def test_v1_images_empty_model_uses_the_enabled_img_selection(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    client.app.state.capability_orchestrator = _FakeOrchestrator("sd-1.5-pruned-emaonly")

    r = client.post("/v1/images/generations", json={"prompt": "x"})

    assert r.status_code == 200, r.text
    assert mock_infer.call_args.args[1]["_hal0_template"] == "sd15_simple"
    assert r.json()["_hal0"]["model"] == "sd-1.5-pruned-emaonly"


def test_v1_images_disabled_img_selection_is_no_model(client: TestClient) -> None:
    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    client.app.state.capability_orchestrator = _FakeOrchestrator(
        "sd-1.5-pruned-emaonly", enabled=False
    )

    r = client.post("/v1/images/generations", json={"prompt": "x"})

    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "image.model_required"


def test_v1_images_named_model_does_not_read_the_selection(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The selection is only a fallback: a named model never pays for the capability state."""
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    _mock_png_infer(monkeypatch)
    orchestrator = _FakeOrchestrator("sd-1.5-pruned-emaonly")
    client.app.state.capability_orchestrator = orchestrator

    r = client.post("/v1/images/generations", json={"model": "sdxl-turbo", "prompt": "x"})

    assert r.status_code == 200, r.text
    assert orchestrator.calls == 0


def test_v1_images_no_orchestrator_means_no_selection(client: TestClient) -> None:
    _seed_img_upstream(client)
    client.app.state.capability_orchestrator = None

    r = client.post("/v1/images/generations", json={"prompt": "x"})

    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "image.model_required"


def test_v1_images_unreadable_capability_state_means_no_selection(client: TestClient) -> None:
    class _Broken:
        async def get_state(self) -> dict[str, Any]:
            raise RuntimeError("hardware probe failed")

    _seed_img_upstream(client)
    client.app.state.capability_orchestrator = _Broken()

    r = client.post("/v1/images/generations", json={"prompt": "x"})

    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "image.model_required"


# ─── size and steps precedence (spec D9) ─────────────────────────────────────


def _slot_manager_with_image_section(image: dict[str, Any]) -> Any:
    class _Arbiter:
        def __init__(self) -> None:
            self.ensured = 0
            self.touches = 0

        async def ensure_img(self) -> None:
            self.ensured += 1

        def touch_img_activity(self) -> None:
            self.touches += 1

    class _Manager:
        arbiter = _Arbiter()

        async def iter_configs(self) -> list[dict[str, Any]]:
            return [{"name": "img", "provider": "comfyui", "enabled": True, "image": image}]

    return _Manager()


def test_v1_images_uses_model_native_size(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No size anywhere: the model's native size reaches the translator (SD 1.5 is 512, not 1024)."""
    from hal0.providers.comfyui_workflows import build_workflow

    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)

    r = client.post(
        "/v1/images/generations",
        json={"model": "sd-1.5-pruned-emaonly", "prompt": "x"},
    )

    assert r.status_code == 200, r.text
    sent = mock_infer.call_args.args[1]
    _graph, meta = build_workflow(
        body=sent, template=sent["_hal0_template"], request_tag="t", ckpt_override=None
    )
    assert (meta["width"], meta["height"]) == (512, 512)


def test_v1_images_native_size_is_per_model(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install("sdxl-lightning")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)

    client.post("/v1/images/generations", json={"model": "sdxl-lightning", "prompt": "x"})

    assert mock_infer.call_args.args[1]["size"] == "1024x1024"


def test_v1_images_body_size_beats_slot_and_native_size(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    client.app.state.slot_manager = _slot_manager_with_image_section({"default_size": "768x768"})

    client.post(
        "/v1/images/generations",
        json={"model": "sd-1.5-pruned-emaonly", "prompt": "x", "size": "640x640"},
    )

    assert mock_infer.call_args.args[1]["size"] == "640x640"


def test_v1_images_slot_default_size_beats_native_size(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    client.app.state.slot_manager = _slot_manager_with_image_section({"default_size": "768x768"})

    client.post("/v1/images/generations", json={"model": "sd-1.5-pruned-emaonly", "prompt": "x"})

    assert mock_infer.call_args.args[1]["size"] == "768x768"


def test_v1_images_empty_slot_default_size_means_native(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    client.app.state.slot_manager = _slot_manager_with_image_section(
        {"default_size": "", "default_steps": 0}
    )

    client.post("/v1/images/generations", json={"model": "sd-1.5-pruned-emaonly", "prompt": "x"})

    sent = mock_infer.call_args.args[1]
    assert sent["size"] == "512x512"
    assert "extra_body" not in sent


def test_v1_images_slot_default_steps_fill_an_omitted_step_count(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    client.app.state.slot_manager = _slot_manager_with_image_section({"default_steps": 6})

    client.post("/v1/images/generations", json={"model": "sdxl-turbo", "prompt": "x"})
    assert mock_infer.call_args.args[1]["extra_body"]["steps"] == 6

    client.post(
        "/v1/images/generations",
        json={"model": "sdxl-turbo", "prompt": "x", "extra_body": {"steps": 2}},
    )
    assert mock_infer.call_args.args[1]["extra_body"]["steps"] == 2


def test_v1_images_resolution_errors_do_not_start_the_gpu(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request that cannot render must not cold-start the img container to find out."""
    _seed_img_upstream(client)
    manager = _slot_manager_with_image_section({})
    client.app.state.slot_manager = manager

    for model in ("sdxl-turbo", "wan22-t2v", "nope"):
        r = client.post("/v1/images/generations", json={"model": model, "prompt": "x"})
        assert r.status_code in (404, 409, 422), r.text

    assert manager.arbiter.ensured == 0


def test_v1_images_unreadable_models_tree_is_a_409_not_a_500(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tree the hal0 user cannot traverse answers like the models routes do, not 500."""
    _seed_img_upstream(client)
    manager = _slot_manager_with_image_section({})
    client.app.state.slot_manager = manager
    mock_infer = _mock_png_infer(monkeypatch)

    def denied(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied", "/srv/models/checkpoints")

    monkeypatch.setattr("hal0.comfyui.resolve.install_state", denied)

    r = client.post("/v1/images/generations", json={"model": "sdxl-turbo", "prompt": "x"})

    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert error["code"] == "comfyui.models_dir_not_writable"
    assert error["details"] == {"path": "/srv/models/checkpoints"}
    assert "/srv/models/checkpoints" in error["message"]
    mock_infer.assert_not_called()
    assert manager.arbiter.ensured == 0  # resolution failed before the GPU was touched


def test_v1_images_url_response_format_writes_cache(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    _install("sdxl-turbo")
    _seed_img_upstream(client)

    fake_png = b"\x89PNG\r\n\x1a\n" + b"y" * 256
    mock_infer = AsyncMock(
        return_value={
            "images": [
                {
                    "png": fake_png,
                    "filename": "hal0-test_00001_.png",
                    "subfolder": "",
                    "type": "output",
                }
            ],
            "meta": {"template": "sdxl_turbo_simple", "seed": 1, "width": 1024, "height": 1024},
            "prompt_id": "abc123",
        }
    )

    # Patch the provider singleton's infer method.
    from hal0.providers import get_provider

    provider = get_provider("comfyui")
    monkeypatch.setattr(provider, "infer", mock_infer)

    r = client.post(
        "/v1/images/generations",
        json={
            "model": "sdxl-turbo",
            "prompt": "a cyberpunk cat",
            "size": "1024x1024",
            "n": 1,
            "response_format": "url",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data"]
    assert "url" in body["data"][0]
    url = body["data"][0]["url"]
    assert url.startswith("/api/images/cache/")
    # The cached PNG should be retrievable via the image-cache route.
    cache_get = client.get(url)
    assert cache_get.status_code == 200
    assert cache_get.headers["content-type"] == "image/png"
    assert cache_get.content == fake_png


def test_v1_images_b64_json_returns_inline_base64(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    _install("sdxl-turbo")
    _seed_img_upstream(client)

    import base64

    fake_png = b"\x89PNG\r\n\x1a\n" + b"z" * 128
    expected_b64 = base64.b64encode(fake_png).decode("ascii")

    mock_infer = AsyncMock(
        return_value={
            "images": [
                {
                    "png": fake_png,
                    "filename": "hal0-test.png",
                    "subfolder": "",
                    "type": "output",
                }
            ],
            "meta": {},
            "prompt_id": "xyz",
        }
    )
    from hal0.providers import get_provider

    monkeypatch.setattr(get_provider("comfyui"), "infer", mock_infer)

    r = client.post(
        "/v1/images/generations",
        json={
            "model": "sdxl-turbo",
            "prompt": "a unicorn",
            "response_format": "b64_json",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["data"][0]["b64_json"] == expected_b64


def test_v1_images_provider_error_surfaces(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install("sdxl-turbo")
    _seed_img_upstream(client)

    from hal0.providers import get_provider
    from hal0.providers.comfyui import ComfyUIInferError

    async def _raises(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ComfyUIInferError(
            "workflow execution failed",
            details={"prompt_id": "abc", "messages": []},
        )

    monkeypatch.setattr(get_provider("comfyui"), "infer", _raises)

    r = client.post(
        "/v1/images/generations",
        json={"model": "sdxl-turbo", "prompt": "x"},
    )
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "dispatch.upstream_failed"


# ─── cold-slot dispatch path (issue #725) ────────────────────────────────────


def test_v1_images_cold_slot_ensure_img_before_dispatch(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    """Regression test for #725: ensure_img() must run BEFORE dispatcher.dispatch().

    Repro path (before fix):
      - img upstream NOT registered (cold/idle slot)
      - dispatcher.dispatch() → resolve_by_capability selects "img" (Rule 4)
      - no "img" upstream in registry → LegacyResolutionFailed → 404

    After fix, ensure_img() runs first and registers the upstream, so dispatch
    succeeds.  We simulate this by installing a fake slot_manager/arbiter whose
    ensure_img() side-effect registers the img upstream in app.state.upstreams,
    exactly as the real arbiter does via _register_container_upstream.

    The same order also covers #3048: when the slot is down while the GPU is
    already in image mode, ``ensure_img()`` is what brings it back before dispatch.
    """
    _install("sdxl-turbo")
    fake_png = b"\x89PNG\r\n\x1a\n" + b"c" * 64

    mock_infer = AsyncMock(
        return_value={
            "images": [
                {
                    "png": fake_png,
                    "filename": "cold-slot-test.png",
                    "subfolder": "",
                    "type": "output",
                }
            ],
            "meta": {"template": "sdxl_turbo_simple", "seed": 42, "width": 512, "height": 512},
            "prompt_id": "cold725",
        }
    )
    from hal0.providers import get_provider

    monkeypatch.setattr(get_provider("comfyui"), "infer", mock_infer)

    # Build a fake arbiter whose ensure_img() registers the upstream,
    # simulating what the real arbiter does when it cold-starts the container.
    upstreams = client.app.state.upstreams
    _PORT = 8186

    class _FakeArbiter:
        async def ensure_img(self) -> None:
            # Simulate _register_container_upstream: wire the img upstream
            # so that resolve_by_capability Rule 4 can find it.
            upstreams.upsert(
                Upstream(
                    name="img",
                    kind="slot",
                    url=f"http://127.0.0.1:{_PORT}/v1",
                    slot_name="img",
                    auth_style="none",
                )
            )

        def touch_img_activity(self) -> None:
            pass

    class _FakeSlotManager:
        arbiter = _FakeArbiter()

        async def iter_configs(self) -> list[dict[str, Any]]:
            # Return a config that gpu_exclusive_group() classifies as "img":
            # provider=="comfyui" on a container slot (runtime=="container",
            # device defaults to gpu-rocm).
            return [{"name": "img", "provider": "comfyui", "enabled": True}]

    # Verify that WITHOUT our fix the upstream is NOT pre-registered.
    assert upstreams.get("img") is None, "upstream must be absent at test start"

    client.app.state.slot_manager = _FakeSlotManager()

    r = client.post(
        "/v1/images/generations",
        json={"model": "sdxl-turbo", "prompt": "cold slot test"},
    )
    # Must succeed — not 404 dispatch.no_route / dispatch.legacy_failed.
    assert r.status_code == 200, (
        f"cold-slot dispatch failed with {r.status_code}: {r.text}\n"
        "This is the #725 regression: ensure_img() must run before dispatch()."
    )
    body = r.json()
    assert body["data"], "expected at least one image in the response"


def test_v1_images_a_failed_dispatch_does_not_stamp_img_activity(client: TestClient) -> None:
    """#3048: a request that never reached the img slot must not keep the image-mode
    idle window open (a retrying client used to pin the box in image mode)."""
    _install("sdxl-turbo")
    manager = _slot_manager_with_image_section({})  # ensure_img is a no-op; no upstream seeded
    client.app.state.slot_manager = manager

    r = client.post("/v1/images/generations", json={"model": "sdxl-turbo", "prompt": "x"})

    assert r.status_code in (404, 502, 503)
    assert r.json()["error"]["code"].startswith("dispatch.")
    assert manager.arbiter.touches == 0


def test_v1_images_a_render_stamps_img_activity_at_start_and_at_completion(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    _mock_png_infer(monkeypatch)
    manager = _slot_manager_with_image_section({})
    client.app.state.slot_manager = manager

    r = client.post("/v1/images/generations", json={"model": "sdxl-turbo", "prompt": "x"})

    assert r.status_code == 200, r.text
    assert manager.arbiter.touches == 2


# ─── image cache route ────────────────────────────────────────────────────────


def test_images_cache_miss_returns_404(client: TestClient, tmp_hal0_home: str) -> None:
    r = client.get("/api/images/cache/deadbeef0000.png")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "image.cache_miss"


def test_images_cache_blocks_path_traversal(client: TestClient, tmp_hal0_home: str) -> None:
    """`..` shouldn't slip past the safe-name regex even via URL encoding."""
    # FastAPI URL-decodes path params; we pass a name that, after decoding,
    # still has characters outside the uuid-hex regex so read_png() returns
    # None and the route surfaces a clean 404.
    r = client.get("/api/images/cache/..%2F..%2Fetc%2Fpasswd")
    # Some routers will 404 at the route layer; either way we never want
    # a 200 + traversed file contents.
    assert r.status_code in (400, 404)
