"""Routing rules for image-gen requests in capability/path routing (Step 4).

These tests cover the ``/v1/images/*`` path pin in
``hal0.dispatcher.router.resolve_by_capability``. The ``img`` slot is reached by
that path, never by what a model id looks like: the image catalog owns image
model ids (``hal0.comfyui``), so the dispatcher has no model-name rule for them.
The chat / embed / NPU rules are intentionally not re-tested here — they
have coverage in :mod:`tests.dispatcher.test_router`.
"""

from __future__ import annotations

import pytest

from hal0.dispatcher.router import Dispatcher, LegacyResolutionFailed, resolve_by_capability
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


def _container_remote_img() -> Upstream:
    """Container-backed img upstream — how SlotManager._register_container_upstream
    registers a podman slot (kind='remote' with slot_name set, #656)."""
    return Upstream(
        name="img",
        kind="remote",
        url="http://127.0.0.1:8188/v1",
        slot_name="img",
        auth_style="none",
    )


class _FakeModelRegistry:
    def route_for(self, model_id: str) -> str | None:
        return None


def test_images_generations_path_routes_to_img_slot() -> None:
    reg = _registry_with_slots("chat", "img")
    upstream = resolve_by_capability("/v1/images/generations", {"model": "sdxl-turbo"}, reg)
    assert upstream.name == "img"


@pytest.mark.parametrize(
    "model", ["sdxl-turbo", "sd-1.5-pruned-emaonly", "qwen-image", "ckpt/my-merge", "", None]
)
def test_image_path_routes_to_img_whatever_the_model_id(model: str | None) -> None:
    """``img`` routes by the request path (the slot's type), not by the model id."""
    reg = _registry_with_slots("agent", "img")
    body = {"prompt": "x"} if model is None else {"model": model, "prompt": "x"}
    upstream = resolve_by_capability("/v1/images/generations", body, reg)
    assert upstream.name == "img"


@pytest.mark.parametrize(
    "model", ["sdxl-turbo", "sd15-anything", "flux-dev", "Flux-2-Klein-9B-GGUF"]
)
def test_image_looking_model_id_on_a_chat_path_is_not_pinned_to_img(model: str) -> None:
    """A model id that merely looks like an image model no longer pins ``img``.

    The old id-prefix rule routed ``sdxl``/``sd-1.5``/``sd15``/``flux`` names to the
    image slot from a chat path. The catalog owns those ids now, so the request
    falls through to the default ``agent`` slot like any other unknown model.
    """
    reg = _registry_with_slots("agent", "img")
    upstream = resolve_by_capability(
        "/v1/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": "hi"}]},
        reg,
    )
    assert upstream.name == "agent"


def test_chat_model_id_still_routes_to_primary() -> None:
    """The image rules must not regress chat routing.

    ADR-0023: the fallback anchor is the `agent` slot (was `chat`).
    """
    reg = _registry_with_slots("agent", "img")
    upstream = resolve_by_capability(
        "/v1/chat/completions",
        {"model": "qwen3-4b", "messages": []},
        reg,
    )
    assert upstream.name == "agent"


def test_image_path_without_img_slot_raises_typed_error() -> None:
    """Path pin selects 'img', missing 'img' upstream → typed legacy error."""
    reg = _registry_with_slots("chat")
    with pytest.raises(LegacyResolutionFailed) as exc:
        resolve_by_capability("/v1/images/generations", {"model": "sdxl-turbo"}, reg)
    assert exc.value.code == "dispatch.legacy_unresolved"


# ── container-remote acceptance (Phase D — img is a podman slot, #656) ────────


def test_image_path_accepts_container_remote() -> None:
    """/v1/images/* path pin must accept a container-backed kind='remote' img
    upstream — same acceptance as the embed/tts/rerank path pins."""
    reg = _registry_with_slots("chat")
    reg.upsert(_container_remote_img())
    upstream = resolve_by_capability("/v1/images/generations", {"model": "sdxl-turbo"}, reg)
    assert upstream.name == "img"
    assert upstream.kind == "remote"


def test_genuine_external_remote_still_rejected() -> None:
    """A genuine external remote (kind='remote', slot_name=None) named 'img'
    must still be rejected — only container remotes (slot_name set) qualify."""
    reg = _registry_with_slots("chat")
    reg.upsert(
        Upstream(
            name="img",
            kind="remote",
            url="http://example.com/v1",
            slot_name=None,
            auth_style="none",
        )
    )
    with pytest.raises(LegacyResolutionFailed) as exc:
        resolve_by_capability("/v1/images/generations", {"model": "sdxl-turbo"}, reg)
    assert exc.value.code == "dispatch.legacy_unresolved"


# ── router._default_for_path image default ────────────────────────────────────


def test_default_for_path_images() -> None:
    """Model-less /v1/images/* request defaults to the 'img' slot, not 'chat'."""
    reg = _registry_with_slots("img")
    dispatcher = Dispatcher(upstream_registry=reg, model_registry=_FakeModelRegistry())
    assert dispatcher._default_for_path("/v1/images/generations") == "img"
