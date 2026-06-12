"""NpuTrioRouter: container-only static-port dispatch (Phase E).

Covers:
  - ready/serving/idle container npu slot → static-port URL
  - transitional/offline container npu slot → resolve_npu_url() is None
  - no slot_manager → None
  - disabled container npu slot → None
  - non-container npu slot (no profile, no runtime=container) → None
  - missing port → None
  - slot_manager accessor raising → None (never crash dispatch)
  - dispatch_stt_npu / dispatch_embed_npu POST to the static port
  - both raise NpuTrioNotAvailable when the container isn't dispatchable
  - param/body forwarding is verbatim; content-type can't be clobbered
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from hal0.dispatcher.npu_trio import NpuTrioNotAvailable, NpuTrioRouter

# ── Helpers ────────────────────────────────────────────────────────────


def _slot_manager_with_container_npu(
    state: str = "ready",
    *,
    enabled: bool = True,
    profile: str = "flm-npu",
    runtime: str | None = None,
    port: int | None = 8088,
) -> MagicMock:
    """SlotManager mock for a container npu slot.

    Mocks ``get_config`` plus the #696 public ``is_ready_for_dispatch``
    method. The ready-set (READY | SERVING | IDLE) is re-derived here from
    the ``state`` string so the mock is always in sync with the locked
    definition.
    """

    sm = MagicMock()
    cfg: dict[str, Any] = {
        "name": "npu",
        "device": "npu",
        "enabled": enabled,
    }
    if port is not None:
        cfg["port"] = port
    if profile:
        cfg["profile"] = profile
    if runtime is not None:
        cfg["runtime"] = runtime
    sm.get_config = AsyncMock(return_value=cfg)
    _dispatchable = frozenset({"ready", "serving", "idle"})
    sm.is_ready_for_dispatch = MagicMock(return_value=state in _dispatchable)
    return sm


def _slot_manager_with_noncontainer_npu() -> MagicMock:
    """SlotManager mock for an npu slot that is NOT containerized."""
    sm = MagicMock()
    sm.get_config = AsyncMock(
        return_value={
            "name": "npu",
            "port": 8099,
            "device": "npu",
            "enabled": True,
            # no profile, no runtime=container
        }
    )
    sm.is_ready_for_dispatch = MagicMock(return_value=True)
    return sm


def _mock_transport(handler: Any) -> httpx.AsyncClient:
    """httpx ``MockTransport`` convenience wrapper."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


# ── Static-port resolution ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_container_npu_resolves_static_port() -> None:
    """Ready container npu slot → static URL from the slot config port."""
    router = NpuTrioRouter(_slot_manager_with_container_npu())
    assert await router.resolve_npu_url() == "http://127.0.0.1:8088"


@pytest.mark.asyncio
async def test_container_npu_via_runtime_field_resolves_static_port() -> None:
    """runtime='container' with no profile also qualifies as container slot."""
    sm = _slot_manager_with_container_npu(profile="", runtime="container", port=9090)
    router = NpuTrioRouter(sm)
    assert await router.resolve_npu_url() == "http://127.0.0.1:9090"


@pytest.mark.asyncio
async def test_serving_container_npu_resolves_static_port() -> None:
    """SERVING (inference in flight) still resolves — a concurrent STT/embed
    request mid-inference is dispatchable."""
    router = NpuTrioRouter(_slot_manager_with_container_npu(state="serving"))
    assert await router.resolve_npu_url() == "http://127.0.0.1:8088"


@pytest.mark.asyncio
async def test_idle_container_npu_resolves_static_port() -> None:
    """IDLE npu container → static port.

    IDLE = "warm but quiet" (no in-flight inference). Under the locked
    #696 ready-set (READY | SERVING | IDLE) an IDLE container is
    dispatchable.
    """
    router = NpuTrioRouter(_slot_manager_with_container_npu(state="idle"))
    assert await router.resolve_npu_url() == "http://127.0.0.1:8088"


# ── Not available: non-ready state ─────────────────────────────────────


@pytest.mark.asyncio
async def test_non_ready_container_resolves_none() -> None:
    """Container npu slot still starting up → None (trio not available).

    Uses STARTING — the closest real lifecycle state to the "container
    launched but not yet ready" window. There is no fallback path.
    """
    router = NpuTrioRouter(_slot_manager_with_container_npu(state="starting"))
    assert await router.resolve_npu_url() is None


@pytest.mark.asyncio
async def test_offline_container_resolves_none() -> None:
    """offline → None."""
    router = NpuTrioRouter(_slot_manager_with_container_npu(state="offline"))
    assert await router.resolve_npu_url() is None


# ── Not available: no slot_manager / disabled / non-container ──────────


@pytest.mark.asyncio
async def test_no_slot_manager_resolves_none() -> None:
    """No slot_manager wired → None (the trio has nothing to observe)."""
    router = NpuTrioRouter(None)
    assert await router.resolve_npu_url() is None


@pytest.mark.asyncio
async def test_disabled_container_resolves_none() -> None:
    """enabled=False → not a live container target → None."""
    router = NpuTrioRouter(_slot_manager_with_container_npu(enabled=False))
    assert await router.resolve_npu_url() is None


@pytest.mark.asyncio
async def test_noncontainer_npu_resolves_none() -> None:
    """npu slot without profile + without runtime=container → None."""
    router = NpuTrioRouter(_slot_manager_with_noncontainer_npu())
    assert await router.resolve_npu_url() is None


@pytest.mark.asyncio
async def test_missing_port_resolves_none() -> None:
    """Container npu config without a port → None (nothing to dial)."""
    router = NpuTrioRouter(_slot_manager_with_container_npu(port=None))
    assert await router.resolve_npu_url() is None


# ── Not available: accessor errors degrade to None ─────────────────────


@pytest.mark.asyncio
async def test_get_config_raises_resolves_none() -> None:
    """get_config raising → swallowed, resolve degrades to None."""
    sm = MagicMock()
    sm.get_config = AsyncMock(side_effect=RuntimeError("TOML missing"))
    router = NpuTrioRouter(sm)
    assert await router.resolve_npu_url() is None


@pytest.mark.asyncio
async def test_is_ready_for_dispatch_raises_resolves_none() -> None:
    """is_ready_for_dispatch() raising → swallowed, resolve degrades to None.

    Same resilience contract as get_config: an accessor bug must never
    crash dispatch — the caller sees "trio not available" instead.
    """
    sm = MagicMock()
    sm.get_config = AsyncMock(
        return_value={
            "name": "npu",
            "port": 8088,
            "device": "npu",
            "profile": "flm-npu",
            "enabled": True,
        }
    )
    sm.is_ready_for_dispatch = MagicMock(side_effect=RuntimeError("state file corrupt"))
    router = NpuTrioRouter(sm)
    assert await router.resolve_npu_url() is None


# ── dispatch_stt_npu ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_stt_posts_multipart_to_static_port() -> None:
    """Verify the URL + content-type + body bytes the container sees."""
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["content_type"] = req.headers.get("content-type", "")
        captured["body"] = req.content
        return httpx.Response(200, json={"text": "transcribed"})

    async with _mock_transport(h) as transport:
        router = NpuTrioRouter(_slot_manager_with_container_npu(), http_client=transport)
        multipart_body = (
            b"--boundary\r\n"
            b'Content-Disposition: form-data; name="model"\r\n\r\n'
            b"whisper-v3\r\n"
            b"--boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="x.wav"\r\n'
            b"Content-Type: audio/wav\r\n\r\n"
            b"RIFF\x00\x00\x00\x00WAVE\r\n"
            b"--boundary--\r\n"
        )
        resp = await router.dispatch_stt_npu(
            body=multipart_body,
            content_type="multipart/form-data; boundary=boundary",
        )

    assert resp.status_code == 200
    # The target is the container's static port, not any discovery hop.
    assert captured["url"] == "http://127.0.0.1:8088/v1/audio/transcriptions"
    assert captured["method"] == "POST"
    # Multipart boundary preserved — without this, the container-side
    # parser fails and the request 415s.
    assert captured["content_type"].startswith("multipart/form-data")
    assert "boundary=boundary" in captured["content_type"]
    # Body bytes round-trip verbatim — the wav payload must not be
    # re-encoded along the way.
    assert captured["body"] == multipart_body


@pytest.mark.asyncio
async def test_dispatch_stt_raises_trio_unavailable_when_not_dispatchable() -> None:
    """The surfaced error has to call out the user action."""
    router = NpuTrioRouter(_slot_manager_with_container_npu(state="offline"))
    with pytest.raises(NpuTrioNotAvailable) as exc:
        await router.dispatch_stt_npu(
            body=b"--x--\r\n",
            content_type="multipart/form-data; boundary=x",
        )
    assert "load an NPU chat slot first" in str(exc.value)
    # Carries the route in details so the dashboard can show context.
    assert exc.value.details.get("endpoint") == "/v1/audio/transcriptions"
    # Stable code so frontends can pattern-match.
    assert exc.value.code == "npu.trio_unavailable"
    assert exc.value.status == 503


@pytest.mark.asyncio
async def test_dispatch_stt_raises_when_no_container_npu_slot() -> None:
    """A non-container npu slot is NOT a trio backend — dispatch refuses."""
    router = NpuTrioRouter(_slot_manager_with_noncontainer_npu())
    with pytest.raises(NpuTrioNotAvailable):
        await router.dispatch_stt_npu(
            body=b"--x--\r\n",
            content_type="multipart/form-data; boundary=x",
        )


# ── dispatch_embed_npu ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_embed_posts_json_to_static_port() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["content_type"] = req.headers.get("content-type", "")
        captured["body"] = req.content
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2, 0.3]}], "model": "embed-gemma"},
        )

    async with _mock_transport(h) as transport:
        router = NpuTrioRouter(_slot_manager_with_container_npu(), http_client=transport)
        resp = await router.dispatch_embed_npu(
            body={"model": "embed-gemma", "input": "hello world"},
        )

    assert resp.status_code == 200
    # URL points at the container's static port.
    assert captured["url"] == "http://127.0.0.1:8088/v1/embeddings"
    assert captured["method"] == "POST"
    assert captured["content_type"].startswith("application/json")
    import json as _json

    decoded = _json.loads(captured["body"])
    assert decoded == {"model": "embed-gemma", "input": "hello world"}


@pytest.mark.asyncio
async def test_dispatch_embed_preserves_extra_params() -> None:
    """Caller-supplied params (encoding_format, dimensions, ...) round-trip
    untouched — the router is pure forward, no shape opinions."""
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content
        return httpx.Response(200, json={"data": []})

    async with _mock_transport(h) as transport:
        router = NpuTrioRouter(_slot_manager_with_container_npu(), http_client=transport)
        body = {
            "model": "embed-gemma",
            "input": ["one", "two"],
            "encoding_format": "float",
            "dimensions": 768,
            "user": "test-user-123",
        }
        await router.dispatch_embed_npu(body=body)

    import json as _json

    decoded = _json.loads(captured["body"])
    assert decoded == body


@pytest.mark.asyncio
async def test_dispatch_embed_raises_trio_unavailable() -> None:
    router = NpuTrioRouter(_slot_manager_with_container_npu(state="offline"))
    with pytest.raises(NpuTrioNotAvailable) as exc:
        await router.dispatch_embed_npu(body={"model": "embed-gemma", "input": "x"})
    assert "load an NPU chat slot first" in str(exc.value)
    assert exc.value.details.get("endpoint") == "/v1/embeddings"


@pytest.mark.asyncio
async def test_dispatch_embed_propagates_upstream_status_codes() -> None:
    """A container-side 4xx (e.g. validation) reaches the caller verbatim."""

    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "empty input"})

    async with _mock_transport(h) as transport:
        router = NpuTrioRouter(_slot_manager_with_container_npu(), http_client=transport)
        resp = await router.dispatch_embed_npu(body={"model": "embed-gemma", "input": ""})

    assert resp.status_code == 422
    assert resp.json() == {"detail": "empty input"}


# ── content-type isolation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_stt_extra_headers_dont_clobber_content_type() -> None:
    """If the caller passes an ``extra_headers`` dict that itself contains
    a content-type, the multipart boundary that the route handler computed
    MUST win — otherwise the container-side multipart parser breaks."""
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["content_type"] = req.headers.get("content-type", "")
        return httpx.Response(200, json={"text": "ok"})

    async with _mock_transport(h) as transport:
        router = NpuTrioRouter(_slot_manager_with_container_npu(), http_client=transport)
        await router.dispatch_stt_npu(
            body=b"--x--\r\n",
            content_type="multipart/form-data; boundary=correct",
            extra_headers={"Content-Type": "application/json"},  # adversarial
        )

    assert "boundary=correct" in captured["content_type"]
