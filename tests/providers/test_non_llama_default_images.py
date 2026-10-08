"""``[slots].default_images[family]`` for the non-llama providers (#2234).

The image contract has three tiers — slot ``image_pin`` → operator family
default (``[slots].default_images[<family>]``) → registry default
(:func:`hal0.runners.resolve_runner_image`). The llama/container path
(:func:`hal0.providers.container._resolve_image_ref`) has always honoured the
middle tier; these tests pin the same order for every non-llama provider's
``image_ref`` (the method each ``container_spec`` launches with).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from hal0.providers.comfyui import ComfyUIProvider
from hal0.providers.flm import FLMProvider
from hal0.providers.kokoro import KokoroProvider
from hal0.providers.moonshine import MoonshineProvider
from hal0.providers.qwen3tts import Qwen3TTSProvider
from hal0.runners import get_runner, resolve_runner_image

_PROVIDERS = [
    pytest.param(ComfyUIProvider, "comfyui", id="comfyui"),
    pytest.param(FLMProvider, "flm", id="flm"),
    pytest.param(KokoroProvider, "kokoro", id="kokoro"),
    pytest.param(MoonshineProvider, "moonshine", id="moonshine"),
    pytest.param(Qwen3TTSProvider, "qwen3tts", id="qwen3tts"),
]

_OVERRIDE = "ghcr.io/hal0ai/operator-default:0999"
_PIN = "ghcr.io/foo/debug:pin"


def _set_default_images(monkeypatch: pytest.MonkeyPatch, default_images: dict[str, str]) -> None:
    fake = SimpleNamespace(slots=SimpleNamespace(default_images=default_images))
    monkeypatch.setattr("hal0.config.loader.load_hal0_config", lambda: fake)


@pytest.mark.parametrize(("provider_cls", "family"), _PROVIDERS)
def test_default_images_family_override_beats_registry_default(
    monkeypatch: pytest.MonkeyPatch, provider_cls: Any, family: str
) -> None:
    _set_default_images(monkeypatch, {family: _OVERRIDE})
    assert provider_cls().image_ref({"name": "s"}) == _OVERRIDE


@pytest.mark.parametrize(("provider_cls", "family"), _PROVIDERS)
def test_image_pin_beats_default_images(
    monkeypatch: pytest.MonkeyPatch, provider_cls: Any, family: str
) -> None:
    _set_default_images(monkeypatch, {family: _OVERRIDE})
    assert provider_cls().image_ref({"image_pin": _PIN}) == _PIN
    assert provider_cls().image_ref({"slot": {"image_pin": _PIN}}) == _PIN


@pytest.mark.parametrize(("provider_cls", "family"), _PROVIDERS)
def test_other_family_override_does_not_leak(
    monkeypatch: pytest.MonkeyPatch, provider_cls: Any, family: str
) -> None:
    other = "cpu" if family != "cpu" else "rocmfpx"
    _set_default_images(monkeypatch, {other: _OVERRIDE})
    assert provider_cls().image_ref({}) == resolve_runner_image(get_runner(family))


@pytest.mark.parametrize(("provider_cls", "family"), _PROVIDERS)
def test_default_images_load_failure_fails_soft(
    monkeypatch: pytest.MonkeyPatch, provider_cls: Any, family: str
) -> None:
    def _boom() -> None:
        raise OSError("permission denied")

    monkeypatch.setattr("hal0.config.loader.load_hal0_config", _boom)
    assert provider_cls().image_ref({}) == resolve_runner_image(get_runner(family))


def test_comfyui_legacy_slot_image_still_beats_default_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ComfyUI's pre-pin ``slot.image`` string is a per-slot override, so it
    keeps outranking the box-wide family default."""
    _set_default_images(monkeypatch, {"comfyui": _OVERRIDE})
    assert ComfyUIProvider().image_ref({"image": "ghcr.io/foo/legacy:1"}) == "ghcr.io/foo/legacy:1"
    # The [image] image-gen table (a dict) is not a ref — the override applies.
    assert ComfyUIProvider().image_ref({"image": {"steps": 20}}) == _OVERRIDE


def test_kokoro_container_spec_launches_the_family_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: the launched ContainerSpec carries the operator default."""
    monkeypatch.setenv("HAL0_MODEL_STORE", "/mnt/ai-models")
    _set_default_images(monkeypatch, {"kokoro": _OVERRIDE})
    slot_cfg = {"name": "tts", "port": 8084, "type": "tts", "profile": "kokoro"}
    spec = KokoroProvider().container_spec(slot_cfg, {})
    assert spec.image == _OVERRIDE
