"""Unit tests for the llama-server HTTP-client surface.

Covers health (mocked httpx), infer, and parse_metrics. Health tests
explicitly exercise the Tier 1 fix: non-empty /v1/models PLUS sentinel
/v1/chat/completions both required.

The launch machinery this file used to cover (build_env / start_cmd /
image_ref / container_spec / render_systemd_override) was retired in
WS-15; the equivalent argv-shape guarantees now live in
tests/providers/test_container_assembler.py (and test_container_mmproj.py
for the vision projector) against the container assembler, the single
launch path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from hal0.providers.llama_server import LlamaServerProvider, ProviderInferError

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def provider() -> LlamaServerProvider:
    return LlamaServerProvider()


# ─── health ───────────────────────────────────────────────────────────────────


def _mock_async_response(
    *, status_code: int = 200, json_payload: Any = None, text: str = ""
) -> MagicMock:
    """Construct a MagicMock that mimics httpx.Response.

    NOTE: httpx.Response.raise_for_status() is SYNC, not async — using
    AsyncMock(spec=httpx.Response) wraps it as a coroutine which never
    raises. MagicMock with sync side_effect is the right shape.
    """
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = lambda: json_payload
    resp.text = text
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
async def test_health_ok_requires_both_models_and_sentinel(
    provider: LlamaServerProvider,
) -> None:
    """TIER1: /v1/models non-empty AND /v1/chat/completions both required."""
    models_payload = {"data": [{"id": "qwen3-4b"}]}
    chat_payload = {"choices": [{"message": {"content": "x"}}]}

    async def _fake_get(url: str) -> httpx.Response:
        assert url.endswith("/v1/models")
        return _mock_async_response(status_code=200, json_payload=models_payload)

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        assert url.endswith("/v1/chat/completions")
        # TIER1: assert the sentinel body shape.
        assert json["max_tokens"] == 1
        assert json["model"] == "qwen3-4b"
        return _mock_async_response(status_code=200, json_payload=chat_payload)

    with patch("hal0.providers.llama_server.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        client.post = _fake_post
        result = await provider.health(8081)

    assert result["ok"] is True
    assert result["status"] == "ready"
    assert result["model"] == "qwen3-4b"


@pytest.mark.asyncio
async def test_health_empty_models_endpoint_is_not_ready(
    provider: LlamaServerProvider,
) -> None:
    """TIER1: empty /v1/models must report not-ready (was the haloai bug)."""

    async def _fake_get(url: str) -> httpx.Response:
        return _mock_async_response(status_code=200, json_payload={"data": []})

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        raise AssertionError("must not call /v1/chat/completions when models empty")

    with patch("hal0.providers.llama_server.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        client.post = _fake_post
        result = await provider.health(8081)

    assert result["ok"] is False
    assert result["status"] == "models_endpoint_empty"


@pytest.mark.asyncio
async def test_health_failed_sentinel_completion_is_not_ready(
    provider: LlamaServerProvider,
) -> None:
    """TIER1: sentinel completion failing → not-ready."""
    models_payload = {"data": [{"id": "qwen3-4b"}]}

    async def _fake_get(url: str) -> httpx.Response:
        return _mock_async_response(status_code=200, json_payload=models_payload)

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        return _mock_async_response(status_code=500, text="model not loaded")

    with patch("hal0.providers.llama_server.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        client.post = _fake_post
        result = await provider.health(8081)

    assert result["ok"] is False
    assert "sentinel_completion_http_500" in result["status"]


@pytest.mark.asyncio
async def test_health_transport_error_surfaces_typed_status(
    provider: LlamaServerProvider,
) -> None:
    async def _fake_get(url: str) -> httpx.Response:
        raise httpx.ConnectError("ECONNREFUSED")

    with patch("hal0.providers.llama_server.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        result = await provider.health(8081)

    assert result["ok"] is False
    assert result["status"] == "http_error"
    assert "ECONNREFUSED" in result["detail"]


# ─── infer ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_infer_returns_upstream_json(provider: LlamaServerProvider) -> None:
    expected = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        return _mock_async_response(status_code=200, json_payload=expected)

    with patch("hal0.providers.llama_server.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.post = _fake_post
        out = await provider.infer(8081, {"model": "x", "messages": []})

    assert out == expected


@pytest.mark.asyncio
async def test_infer_raises_typed_error_on_5xx(provider: LlamaServerProvider) -> None:
    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        return _mock_async_response(status_code=503)

    with patch("hal0.providers.llama_server.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.post = _fake_post
        with pytest.raises(ProviderInferError) as exc:
            await provider.infer(8081, {})
    assert exc.value.code == "dispatch.upstream_failed"


# ─── retirement guards ────────────────────────────────────────────────────────


def test_llama_server_is_not_a_launcher() -> None:
    """WS-15: the launch surface must stay deleted; the class is a plain
    HTTP client, not a Provider registered for slot dispatch."""
    from hal0.providers import get_provider
    from hal0.providers.base import Provider

    assert not isinstance(LlamaServerProvider(), Provider)
    for attr in ("build_env", "start_cmd", "container_spec", "image_ref"):
        assert not hasattr(LlamaServerProvider, attr), attr
    with pytest.raises(KeyError, match="llama-server"):
        get_provider("llama-server")


# ─── parse_metrics ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_metrics_whitelists_counters(
    provider: LlamaServerProvider,
) -> None:
    raw = (
        "# HELP llamacpp:n_decode_total decoded tokens\n"
        "# TYPE llamacpp:n_decode_total counter\n"
        "llamacpp:n_decode_total 1234\n"
        "llamacpp:kv_cache_usage_ratio 0.42\n"
        "llamacpp:unknown_metric 7\n"
    )
    out = await provider.parse_metrics(raw)
    assert out["decode_total"] == 1234
    assert out["kv_cache_usage"] == pytest.approx(0.42)
    assert "unknown_metric" not in out
