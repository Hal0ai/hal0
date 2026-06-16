"""TDD: curated catalogue includes sdxl-lightning and esrgan-4x entries.

Task 2.5 — ComfyUI image-gen models: SDXL Lightning + ESRGAN 4x.
"""

from __future__ import annotations

from hal0.registry.curated import CURATED_MODELS, get_curated


def _by_id(model_id: str):
    return next((m for m in CURATED_MODELS if m.id == model_id), None)


def test_sdxl_lightning_present():
    """sdxl-lightning must be in the curated catalogue."""
    ids = {m.id for m in CURATED_MODELS}
    assert "sdxl-lightning" in ids


def test_esrgan_4x_present():
    """esrgan-4x must be in the curated catalogue."""
    ids = {m.id for m in CURATED_MODELS}
    assert "esrgan-4x" in ids


def test_sdxl_lightning_model_class_image():
    m = get_curated("sdxl-lightning")
    assert m is not None
    assert m.model_class == "image", f"got {m.model_class!r}"


def test_esrgan_4x_model_class_image():
    m = get_curated("esrgan-4x")
    assert m is not None
    assert m.model_class == "image", f"got {m.model_class!r}"


def test_sdxl_lightning_comfyui_subdir():
    m = get_curated("sdxl-lightning")
    assert m is not None
    assert m.comfyui_subdir == "checkpoints"


def test_esrgan_4x_comfyui_subdir():
    m = get_curated("esrgan-4x")
    assert m is not None
    assert m.comfyui_subdir == "upscale_models"


def test_sdxl_lightning_capability_image():
    m = get_curated("sdxl-lightning")
    assert m is not None
    assert m.capability == "image"


def test_esrgan_4x_capability_image():
    m = get_curated("esrgan-4x")
    assert m is not None
    assert m.capability == "image"
