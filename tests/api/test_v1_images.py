"""Wiring tests for ``POST /v1/images/generations`` + the image cache.

The provider's ``infer()`` is mocked so we don't need a live ComfyUI;
this exercises:

  * dispatcher routing (image-gen path lands on the ``img`` slot legacy
    fallback when no registry binding exists).
  * curated-model gating (random model id 404s).
  * URL response_format → cached PNG + ``/api/images/cache/...`` URL.
  * b64_json response_format → inline base64 PNG.
  * GET /api/images/cache/{name}.png serves the cached bytes.
  * Cache-miss + path-traversal attempt 404 cleanly.
  * Cold-slot path (issue #725): ensure_img() runs before dispatch so the
    img upstream is registered before resolve_by_capability runs.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from hal0.upstreams.registry import Upstream


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
    # Could route to a model_not_curated 404 or the dispatcher's no_route 404.
    assert r.status_code == 404
    body = r.json()
    # Both shapes pass the test — what matters is a typed envelope.
    assert body["error"]["code"] in ("image.model_not_curated", "dispatch.no_route")


def test_v1_images_url_response_format_writes_cache(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
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
    """
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
