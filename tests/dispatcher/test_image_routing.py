"""Routing rules for image-gen requests in the legacy fallback proxy.

These tests cover the new ``/v1/images/*`` path pin + the SDXL/SD/Flux
model-id prefix pin Team K added to ``hal0.dispatcher.proxy.resolve_slot``.
The chat / embed / NPU rules are intentionally not re-tested here — they
have coverage in :mod:`tests.dispatcher.test_router`.
"""

from __future__ import annotations

import pytest

from hal0.dispatcher.proxy import LegacyResolutionFailed, resolve_slot
from hal0.upstreams.registry import Upstream, UpstreamRegistry


def _registry_with_slots(*names: str) -> UpstreamRegistry:
    reg = UpstreamRegistry()
    for n in names:
        reg.upsert(
            Upstream(
                name=n,
                kind="slot",
                url="http://127.0.0.1:8186/v1",
                slot_name=n,
                auth_style="none",
            )
        )
    return reg


def test_images_generations_path_routes_to_img_slot() -> None:
    reg = _registry_with_slots("primary", "img")
    upstream = resolve_slot("/v1/images/generations", {"model": "sdxl-turbo"}, reg)
    assert upstream.name == "img"


def test_sdxl_model_id_routes_to_img_even_without_image_path() -> None:
    """A bare /v1/chat/completions with model='sdxl-turbo' must NOT hit primary."""
    reg = _registry_with_slots("primary", "img")
    upstream = resolve_slot(
        "/v1/chat/completions",
        {"model": "sdxl-turbo", "messages": [{"role": "user", "content": "hi"}]},
        reg,
    )
    assert upstream.name == "img"


def test_sd15_model_prefix_routes_to_img() -> None:
    reg = _registry_with_slots("primary", "img")
    upstream = resolve_slot(
        "/v1/images/generations",
        {"model": "sd-1.5-pruned-emaonly", "prompt": "x"},
        reg,
    )
    assert upstream.name == "img"


def test_flux_model_prefix_routes_to_img() -> None:
    reg = _registry_with_slots("primary", "img")
    upstream = resolve_slot(
        "/v1/images/generations",
        {"model": "Flux-2-Klein-9B-GGUF", "prompt": "x"},
        reg,
    )
    assert upstream.name == "img"


def test_chat_model_id_still_routes_to_primary() -> None:
    """The image rules must not regress chat routing."""
    reg = _registry_with_slots("primary", "img")
    upstream = resolve_slot(
        "/v1/chat/completions",
        {"model": "qwen3-4b", "messages": []},
        reg,
    )
    assert upstream.name == "primary"


def test_image_path_without_img_slot_raises_typed_error() -> None:
    """Path pin selects 'img', missing 'img' upstream → typed legacy error."""
    reg = _registry_with_slots("primary")
    with pytest.raises(LegacyResolutionFailed) as exc:
        resolve_slot("/v1/images/generations", {"model": "sdxl-turbo"}, reg)
    assert exc.value.code == "dispatch.legacy_unresolved"
