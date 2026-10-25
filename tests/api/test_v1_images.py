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
  * Fit preflight (ADR-0039): a render that cannot fit the memory budget is refused with
    422 ``image.wont_fit`` before any GPU slot unloads; an unknown budget never refuses.
"""

from __future__ import annotations

import contextlib
import json
import os
import struct
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from hal0.comfyui import paths
from hal0.comfyui.catalog import CATALOG, component_path, weights_gib
from hal0.comfyui.fit import Need, assess, need_for
from hal0.hardware.memory_budget import GpuBudget, MemoryBudget, RamBudget
from hal0.upstreams.registry import Upstream
from tests.fixtures.memory_budget import MemoryBudgetStub


@pytest.fixture(autouse=True)
def comfyui_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The ComfyUI models tree under ``tmp_path``: resolution reads it for install state."""
    root = tmp_path / "comfyui-data"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(root))
    assert paths.models_root().is_relative_to(tmp_path)
    return root


def _budget(
    *,
    render_free_gb: float | None,
    ram_free_gb: float = 100.0,
    ram_limit_gb: float = 128.0,
) -> MemoryBudget:
    """A memory budget with ``render_free_gb`` free for a render; ``None`` is a box with no GPU lane."""
    gpu = (
        None
        if render_free_gb is None
        else GpuBudget(
            kind="shared",
            total_gb=116.0,
            used_gb=0.0,
            this_box=(),
            image_engine_gb=0.0,
            image_engine_slot=None,
            image_engine_estimated=False,
            other_guests_gb=0.0,
            attribution_excess_gb=0.0,
            free_gb=116.0,
        )
    )
    ram = RamBudget(
        limit_gb=ram_limit_gb,
        used_gb=round(ram_limit_gb - ram_free_gb, 1),
        free_gb=ram_free_gb,
        source="container",
    )
    return MemoryBudget(gpu=gpu, ram=ram, render_free_gb=render_free_gb)


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
    assert error["details"]["missing_gb"] == weights_gib(CATALOG["sdxl-turbo"])  # GiB
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
            self.jobs = 0
            self.open_jobs = 0

        async def ensure_img(self) -> None:
            self.ensured += 1

        @contextlib.asynccontextmanager
        async def image_job(self):
            self.jobs += 1
            self.open_jobs += 1
            try:
                yield
            finally:
                self.open_jobs -= 1

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


def test_v1_images_answers_the_body_open_webui_sends(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The body Open WebUI v0.12.0 posts when its ``IMAGE_SIZE`` is empty
    (``routers/images.py:638-655``): a model, a prompt, ``n`` and ``response_format``, no
    ``size`` and no steps. hal0 picks the native size and the template's own step count, which
    is what lets ``openwebui.env`` leave both settings empty."""
    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)

    r = client.post(
        "/v1/images/generations",
        json={
            "model": "sd-1.5-pruned-emaonly",
            "prompt": "a cat",
            "n": 1,
            "response_format": "b64_json",
        },
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"][0]["b64_json"]
    sent = mock_infer.call_args.args[1]
    assert sent["size"] == "512x512"  # hal0 picked the native size; Open WebUI sent none
    assert "steps" not in (sent.get("extra_body") or {})  # the template's own step count


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
        def __init__(self) -> None:
            self.jobs = 0
            self.open_jobs = 0

        @contextlib.asynccontextmanager
        async def image_job(self):
            self.jobs += 1
            self.open_jobs += 1
            try:
                yield
            finally:
                self.open_jobs -= 1

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


def test_v1_images_a_failed_dispatch_leaves_no_image_job_open(client: TestClient) -> None:
    """#3048: a request that never reached the img slot must not hold image mode
    (a retrying client used to pin the box in image mode)."""
    _install("sdxl-turbo")
    manager = _slot_manager_with_image_section({})  # ensure_img is a no-op; no upstream seeded
    client.app.state.slot_manager = manager

    r = client.post("/v1/images/generations", json={"model": "sdxl-turbo", "prompt": "x"})

    assert r.status_code in (404, 502, 503)
    assert r.json()["error"]["code"].startswith("dispatch.")
    assert manager.arbiter.jobs == 1
    assert manager.arbiter.open_jobs == 0


def test_v1_images_a_render_runs_inside_one_image_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The arbiter counts the render as image work until provider.infer returns,
    so it never restores LLM mode under it; the job closes with the render."""
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    payload = _mock_png_infer(monkeypatch).return_value
    manager = _slot_manager_with_image_section({})
    client.app.state.slot_manager = manager
    open_during_render: list[int] = []

    async def _infer(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        open_during_render.append(manager.arbiter.open_jobs)
        return payload

    from hal0.providers import get_provider

    monkeypatch.setattr(get_provider("comfyui"), "infer", _infer)

    r = client.post("/v1/images/generations", json={"model": "sdxl-turbo", "prompt": "x"})

    assert r.status_code == 200, r.text
    assert open_during_render == [1]
    assert manager.arbiter.jobs == 1
    assert manager.arbiter.open_jobs == 0


# ─── fit preflight (ADR-0039): refuse a render that cannot fit, before any GPU switch ──────


class _SwitchingManager:
    """A slot manager whose ``ensure_img`` unloads a GPU slot, as the real arbiter's does."""

    def __init__(self, image: dict[str, Any] | None = None) -> None:
        self.image = image or {}
        self.unload = AsyncMock()
        self.arbiter = _SwitchingArbiter(self)

    async def iter_configs(self) -> list[dict[str, Any]]:
        return [{"name": "img", "provider": "comfyui", "enabled": True, "image": self.image}]


class _SwitchingArbiter:
    def __init__(self, manager: _SwitchingManager) -> None:
        self.jobs = 0
        self.open_jobs = 0

        async def switch(*_args: Any, **_kwargs: Any) -> None:
            await manager.unload("brain")

        self.ensure_img = AsyncMock(side_effect=switch)

    @contextlib.asynccontextmanager
    async def image_job(self):
        self.jobs += 1
        self.open_jobs += 1
        try:
            yield
        finally:
            self.open_jobs -= 1


def _fit_request(model: str, **extra: Any) -> dict[str, Any]:
    return {"model": model, "prompt": "x", **extra}


def test_v1_images_preflight_refuses_before_any_switch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory_budget: MemoryBudgetStub
) -> None:
    """A render that cannot fit is a 422 before the arbiter switches: no slot unloads, no image job."""
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    manager = _SwitchingManager()
    client.app.state.slot_manager = manager
    memory_budget.result = _budget(render_free_gb=4.0)

    r = client.post("/v1/images/generations", json=_fit_request("sdxl-turbo"))

    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "image.wont_fit"
    assert error["message"] == "needs 6.4 GB of GPU memory, 4.0 GB free for a render"
    assert error["details"] == {
        "need": {"gpu_gb": 6.4, "ram_gb": 6.4, "gpu_estimated": True, "ram_estimated": True},
        "render_free_gb": 4.0,
        "ram_free_gb": 100.0,
        "model": "sdxl-turbo",
        "size": "512x512",
    }
    manager.arbiter.ensure_img.assert_not_awaited()
    manager.unload.assert_not_awaited()
    assert manager.arbiter.jobs == 0
    mock_infer.assert_not_called()


def test_v1_images_preflight_message_is_the_fit_reason_verbatim(
    client: TestClient, memory_budget: MemoryBudgetStub
) -> None:
    """The 422 says what ``assess`` says, whichever resource decided it (here RAM)."""
    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    memory_budget.result = _budget(render_free_gb=100.0, ram_free_gb=3.0, ram_limit_gb=48.0)
    expected = assess(need_for(CATALOG["sd-1.5-pruned-emaonly"], "512x512"), memory_budget.result)

    r = client.post("/v1/images/generations", json=_fit_request("sd-1.5-pruned-emaonly"))

    assert r.status_code == 422, r.text
    assert expected is not None
    assert expected.reason == "needs ~4.0 GB of RAM to load, 3.0 GB free (48.0 GB container limit)"
    assert r.json()["error"]["message"] == expected.reason
    assert r.json()["error"]["details"]["ram_free_gb"] == 3.0


@pytest.mark.parametrize(
    ("image_section", "body_extra", "effective"),
    [
        # The request's size, as the translator reads it.
        ({}, {"size": "768x768"}, "768x768"),
        # Rounded down to a multiple of 8 and clamped to 64..2048, like build_workflow.
        ({}, {"size": "1001x1003"}, "1000x1000"),
        ({}, {"size": "5000x20"}, "2048x64"),
        ({}, {"size": "1024X768"}, "1024x768"),
        # Unreadable: the translator falls back to its default size.
        ({}, {"size": "banana"}, "1024x1024"),
        # Omitted: the slot's default_size, else the model's native size.
        ({"default_size": "768x768"}, {}, "768x768"),
        ({"default_size": ""}, {}, "512x512"),
        ({}, {}, "512x512"),
        # The request beats the slot.
        ({"default_size": "768x768"}, {"size": "640x640"}, "640x640"),
    ],
)
def test_v1_images_preflight_assesses_the_size_that_renders(
    client: TestClient,
    memory_budget: MemoryBudgetStub,
    image_section: dict[str, Any],
    body_extra: dict[str, Any],
    effective: str,
) -> None:
    """The size judged is the one the render will use: SD 1.5's need grows with the pixels."""
    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    manager = _SwitchingManager(image_section)
    client.app.state.slot_manager = manager
    memory_budget.result = _budget(render_free_gb=5.0)

    r = client.post(
        "/v1/images/generations", json=_fit_request("sd-1.5-pruned-emaonly", **body_extra)
    )

    assert r.status_code == 422, r.text
    details = r.json()["error"]["details"]
    assert details["size"] == effective
    expected = need_for(CATALOG["sd-1.5-pruned-emaonly"], effective)
    assert details["need"] == {
        "gpu_gb": expected.gpu_gb,
        "ram_gb": expected.ram_gb,
        "gpu_estimated": expected.gpu_estimated,
        "ram_estimated": expected.ram_estimated,
    }
    manager.arbiter.ensure_img.assert_not_awaited()


@pytest.mark.parametrize(
    "size",
    [1024, 1024.5, True, 0, False, ["1024x1024"], [], {"width": 1024}, {}],
    ids=["int", "float", "true", "zero", "false", "list", "empty-list", "object", "empty-object"],
)
def test_v1_images_non_string_size_is_a_client_error_not_a_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory_budget: MemoryBudgetStub, size: Any
) -> None:
    """``size`` is a string or absent. Anything else is a 422 before the model is resolved,
    the budget is read or the GPU is touched (the translator used to raise on it: a 500)."""
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    manager = _SwitchingManager()
    client.app.state.slot_manager = manager
    memory_budget.result = _budget(render_free_gb=1.0)

    r = client.post("/v1/images/generations", json=_fit_request("sdxl-turbo", size=size))

    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "image.invalid_size"
    assert error["details"] == {"size": size}
    assert "1024x1024" in error["message"]
    assert memory_budget.apps == []  # the preflight never saw it
    manager.arbiter.ensure_img.assert_not_awaited()
    assert manager.arbiter.jobs == 0
    mock_infer.assert_not_called()


@pytest.mark.parametrize("size", [None, ""], ids=["null", "empty"])
def test_v1_images_null_or_empty_size_means_the_default_size(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, size: Any
) -> None:
    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)

    r = client.post("/v1/images/generations", json=_fit_request("sd-1.5-pruned-emaonly", size=size))

    assert r.status_code == 200, r.text
    assert mock_infer.call_args.args[1]["size"] == "512x512"


def test_v1_images_preflight_size_matches_what_the_translator_renders(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory_budget: MemoryBudgetStub
) -> None:
    """A fitting render: the size the preflight judged is the size build_workflow renders."""
    from hal0.providers.comfyui_workflows import build_workflow

    _install("sd-1.5-pruned-emaonly")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    memory_budget.result = _budget(render_free_gb=300.0)

    r = client.post(
        "/v1/images/generations", json=_fit_request("sd-1.5-pruned-emaonly", size="5000x1001")
    )

    assert r.status_code == 200, r.text
    sent = mock_infer.call_args.args[1]
    _graph, meta = build_workflow(
        body=sent, template=sent["_hal0_template"], request_tag="t", ckpt_override=None
    )
    assert (meta["width"], meta["height"]) == (2048, 1000)


def test_v1_images_preflight_skip_flag_renders(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory_budget: MemoryBudgetStub
) -> None:
    """``extra_body.hal0_skip_fit_check: true`` renders a request the budget would refuse."""
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    manager = _SwitchingManager()
    client.app.state.slot_manager = manager
    memory_budget.result = _budget(render_free_gb=1.0)

    r = client.post(
        "/v1/images/generations",
        json=_fit_request("sdxl-turbo", extra_body={"hal0_skip_fit_check": True, "steps": 2}),
    )

    assert r.status_code == 200, r.text
    mock_infer.assert_awaited_once()
    assert mock_infer.call_args.args[1]["extra_body"]["steps"] == 2
    manager.arbiter.ensure_img.assert_awaited_once()
    assert memory_budget.apps == []  # a skipped check does not even read the budget


@pytest.mark.parametrize("flag", [False, "true", 1, "yes", None, []])
def test_v1_images_preflight_only_a_true_flag_skips(
    client: TestClient, memory_budget: MemoryBudgetStub, flag: Any
) -> None:
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    memory_budget.result = _budget(render_free_gb=1.0)

    r = client.post(
        "/v1/images/generations",
        json=_fit_request("sdxl-turbo", extra_body={"hal0_skip_fit_check": flag}),
    )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "image.wont_fit"


def test_v1_images_preflight_ignores_an_extra_body_that_is_not_a_mapping(
    client: TestClient, memory_budget: MemoryBudgetStub
) -> None:
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    memory_budget.result = _budget(render_free_gb=1.0)

    r = client.post("/v1/images/generations", json=_fit_request("sdxl-turbo", extra_body="skip"))

    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "image.wont_fit"


@pytest.mark.parametrize(
    "unknown",
    [
        _budget(render_free_gb=None),  # no GPU lane
        _budget(render_free_gb=1.0, ram_free_gb=0.0, ram_limit_gb=0.0),  # RAM unreadable
    ],
    ids=["no-gpu-pool", "ram-unreadable"],
)
def test_v1_images_preflight_skips_on_unknown_budget(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    memory_budget: MemoryBudgetStub,
    unknown: MemoryBudget,
) -> None:
    """Unknown is not "won't fit": a budget that cannot answer lets the render proceed."""
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    memory_budget.result = unknown

    with capture_logs() as logs:
        r = client.post("/v1/images/generations", json=_fit_request("sdxl-turbo"))

    assert r.status_code == 200, r.text
    mock_infer.assert_awaited_once()
    assert memory_budget.apps  # the budget was asked
    assert not [e for e in logs if e["event"] == "images.preflight_budget_unavailable"]


def test_v1_images_preflight_fails_open_when_the_collector_raises(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory_budget: MemoryBudgetStub
) -> None:
    """A collector failure is one warning and the render goes ahead (the collector has no guard)."""
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    memory_budget.result = RuntimeError("nvidia-smi hung")

    with capture_logs() as logs:
        r = client.post("/v1/images/generations", json=_fit_request("sdxl-turbo"))

    assert r.status_code == 200, r.text
    mock_infer.assert_awaited_once()
    warnings = [e for e in logs if e["event"] == "images.preflight_budget_unavailable"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["exc_info"] is True


def test_v1_images_preflight_fails_open_when_the_fit_computation_raises(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory_budget: MemoryBudgetStub
) -> None:
    """Anything in the fit computation, not only the collector, is fail-open."""
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    memory_budget.result = _budget(render_free_gb=1.0)

    def broken(*_args: Any, **_kwargs: Any) -> Need:
        raise ValueError("not a WxH size: 'weird'")

    monkeypatch.setattr("hal0.comfyui.fit.need_for", broken)

    with capture_logs() as logs:
        r = client.post("/v1/images/generations", json=_fit_request("sdxl-turbo"))

    assert r.status_code == 200, r.text
    mock_infer.assert_awaited_once()
    assert len([e for e in logs if e["event"] == "images.preflight_budget_unavailable"]) == 1


@pytest.mark.parametrize("headroom", [1.05, 4.0], ids=["tight", "fits"])
def test_v1_images_preflight_only_wont_fit_refuses(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    memory_budget: MemoryBudgetStub,
    headroom: float,
) -> None:
    """A tight fit (need above 0.9 of the free pool) still renders."""
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    need = need_for(CATALOG["sdxl-turbo"], "512x512")
    memory_budget.result = _budget(render_free_gb=round(need.gpu_gb * headroom, 1))
    fit = assess(need, memory_budget.result)
    assert fit is not None
    assert fit.verdict == ("tight" if headroom < 2 else "fits")

    r = client.post("/v1/images/generations", json=_fit_request("sdxl-turbo"))

    assert r.status_code == 200, r.text
    mock_infer.assert_awaited_once()


def test_v1_images_preflight_runs_for_the_img_selection_model(
    client: TestClient, memory_budget: MemoryBudgetStub
) -> None:
    """A request that names no model is judged as the model the img capability selected."""
    _install("sdxl-turbo")
    _seed_img_upstream(client)
    client.app.state.capability_orchestrator = _FakeOrchestrator("sdxl-turbo")
    memory_budget.result = _budget(render_free_gb=1.0)

    r = client.post("/v1/images/generations", json={"prompt": "x"})

    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "image.wont_fit"
    assert r.json()["error"]["details"]["model"] == "sdxl-turbo"


def test_v1_images_resolution_errors_come_before_the_preflight(
    client: TestClient, memory_budget: MemoryBudgetStub
) -> None:
    """An unknown or uninstalled model is its own error, and no budget is read for it."""
    _seed_img_upstream(client)
    memory_budget.result = _budget(render_free_gb=0.1)

    for model, status in (("nope", 404), ("sdxl-turbo", 409), ("wan22-t2v", 422)):
        r = client.post("/v1/images/generations", json=_fit_request(model))
        assert r.status_code == status, r.text
        assert r.json()["error"]["code"] != "image.wont_fit"

    assert memory_budget.apps == []


def _sized_user_checkpoint(relpath: str, gib: int, key: str = "conditioner.embedders.1.x") -> None:
    """A user checkpoint of ``gib`` GiB on disk (a sparse file: only its header is written)."""
    _user_checkpoint(relpath, key)
    os.truncate(paths.models_root() / "checkpoints" / relpath, gib * 2**30)


def test_v1_images_user_checkpoint_uses_weights_estimate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory_budget: MemoryBudgetStub
) -> None:
    """``ckpt/<stem>`` is judged from its file size in GiB, as an estimate on both sides."""
    _sized_user_checkpoint("anime/waifu.safetensors", 12)
    _seed_img_upstream(client)
    mock_infer = _mock_png_infer(monkeypatch)
    manager = _SwitchingManager()
    client.app.state.slot_manager = manager
    memory_budget.result = _budget(render_free_gb=10.0)

    r = client.post("/v1/images/generations", json=_fit_request("ckpt/anime/waifu"))

    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "image.wont_fit"
    assert error["message"] == "needs 12.0 GB of GPU memory, 10.0 GB free for a render"
    assert error["details"] == {
        "need": {"gpu_gb": 12.0, "ram_gb": 12.0, "gpu_estimated": True, "ram_estimated": True},
        "render_free_gb": 10.0,
        "ram_free_gb": 100.0,
        "model": "ckpt/anime/waifu",
        "size": "1024x1024",
    }
    manager.arbiter.ensure_img.assert_not_awaited()
    manager.unload.assert_not_awaited()
    mock_infer.assert_not_called()

    # The same file fits a bigger pool, and a smaller checkpoint fits this one.
    memory_budget.result = _budget(render_free_gb=40.0)
    assert (
        client.post("/v1/images/generations", json=_fit_request("ckpt/anime/waifu")).status_code
        == 200
    )
    _sized_user_checkpoint("small.safetensors", 4)
    memory_budget.result = _budget(render_free_gb=10.0)
    assert client.post("/v1/images/generations", json=_fit_request("ckpt/small")).status_code == 200


def test_v1_images_user_checkpoint_can_be_refused_by_ram(
    client: TestClient, memory_budget: MemoryBudgetStub
) -> None:
    _sized_user_checkpoint("big.safetensors", 12)
    _seed_img_upstream(client)
    memory_budget.result = _budget(render_free_gb=100.0, ram_free_gb=5.0, ram_limit_gb=48.0)

    r = client.post("/v1/images/generations", json=_fit_request("ckpt/big"))

    assert r.status_code == 422, r.text
    assert (
        r.json()["error"]["message"]
        == "needs ~12.0 GB of RAM to load, 5.0 GB free (48.0 GB container limit)"
    )


def test_v1_images_user_checkpoint_skip_flag_renders(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, memory_budget: MemoryBudgetStub
) -> None:
    _sized_user_checkpoint("big.safetensors", 12)
    _seed_img_upstream(client)
    _mock_png_infer(monkeypatch)
    memory_budget.result = _budget(render_free_gb=1.0)

    r = client.post(
        "/v1/images/generations",
        json=_fit_request("ckpt/big", extra_body={"hal0_skip_fit_check": True}),
    )

    assert r.status_code == 200, r.text


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
