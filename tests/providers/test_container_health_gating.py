"""ContainerProvider.health gates on model_loaded when the /health body reports it.

kokoro-server returns 200 with {"status": "loading", "model_loaded": false}
BEFORE weights load — a plain status-200 check flips the slot READY while
/v1/audio/speech still 503s. health() must gate ok on model_loaded when the
key is present; bodies without the key (llama-server) keep the plain-200
behavior, and the /v1/models fallback (FLM, fires only on non-200) is
untouched.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hal0.providers.container import ContainerProvider


def _mock_client(fake_get: Any) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = fake_get
    return client


def _json_resp(status_code: int, body: Any) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body)
    return resp


def _non_json_resp(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(side_effect=ValueError("not JSON"))
    return resp


@pytest.mark.anyio
async def test_health_200_model_loading_not_ok() -> None:
    """200 + {"model_loaded": false} (kokoro loading) → ok False."""
    provider = ContainerProvider()
    health_resp = _json_resp(200, {"status": "loading", "model_loaded": False})

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        assert url.endswith("/health")
        return health_resp

    with patch("hal0.providers.container.httpx.AsyncClient", return_value=_mock_client(fake_get)):
        result = await provider.health(8084)

    assert result["ok"] is False


@pytest.mark.anyio
async def test_health_200_model_loaded_ok() -> None:
    """200 + {"model_loaded": true} (kokoro ready) → ok True."""
    provider = ContainerProvider()
    health_resp = _json_resp(200, {"status": "ok", "model_loaded": True})

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return health_resp

    with patch("hal0.providers.container.httpx.AsyncClient", return_value=_mock_client(fake_get)):
        result = await provider.health(8084)

    assert result["ok"] is True
    assert result["status"] == "healthy"


@pytest.mark.anyio
async def test_health_200_non_json_body_stays_ok() -> None:
    """200 with a non-JSON body (llama-server style) → ok True (pinned)."""
    provider = ContainerProvider()
    health_resp = _non_json_resp(200)

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return health_resp

    with patch("hal0.providers.container.httpx.AsyncClient", return_value=_mock_client(fake_get)):
        result = await provider.health(8095)

    assert result["ok"] is True
    assert result["status"] == "healthy"


@pytest.mark.anyio
async def test_health_200_json_without_model_loaded_stays_ok() -> None:
    """200 JSON body lacking model_loaded key → ok True (llama behavior pinned)."""
    provider = ContainerProvider()
    health_resp = _json_resp(200, {"status": "ok"})

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return health_resp

    with patch("hal0.providers.container.httpx.AsyncClient", return_value=_mock_client(fake_get)):
        result = await provider.health(8095)

    assert result["ok"] is True
    assert result["status"] == "healthy"


@pytest.mark.anyio
async def test_health_404_v1_models_fallback_unchanged() -> None:
    """404 on /health + 200 on /v1/models (FLM) → ok True (fallback regression)."""
    provider = ContainerProvider()
    health_resp = _non_json_resp(404)
    models_resp = _json_resp(200, {"data": []})

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("/health"):
            return health_resp
        if url.endswith("/v1/models"):
            return models_resp
        raise AssertionError(f"unexpected URL: {url}")

    with patch("hal0.providers.container.httpx.AsyncClient", return_value=_mock_client(fake_get)):
        result = await provider.health(8088)

    assert result["ok"] is True
    assert result["status"] == "healthy"
