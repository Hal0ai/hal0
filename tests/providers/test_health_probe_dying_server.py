"""A server that dies while a probe connects must read as "not healthy" (#3937).

httpcore reads the socket's peer address after the socket was closed and the
``AttributeError: 'NoneType' object has no attribute 'getpeername'`` escapes
``httpx`` untyped. It used to abort the whole slot load as a 500 traceback
through ``wait_ready``. The probes now see an ``httpx.TransportError`` instead.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from hal0.http_client import probe_get
from hal0.providers import container as container_module
from hal0.providers.container import ContainerProvider
from hal0.providers.halogen import HalogenProvider

_RAW = "'NoneType' object has no attribute 'getpeername'"


def _client_raising(exc: Exception) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    async def fake_get(url: str, **kwargs: Any) -> Any:
        raise exc

    client.get = fake_get
    return client


async def test_probe_get_turns_the_raw_attribute_error_into_a_transport_error() -> None:
    client = _client_raising(AttributeError(_RAW))
    with pytest.raises(httpx.TransportError) as info:
        await probe_get(client, "http://127.0.0.1:1/health")
    assert "getpeername" in str(info.value)
    assert isinstance(info.value.__cause__, AttributeError)


async def test_probe_get_turns_an_os_error_into_a_transport_error() -> None:
    client = _client_raising(OSError("socket closed"))
    with pytest.raises(httpx.TransportError):
        await probe_get(client, "http://127.0.0.1:1/health")


async def test_probe_get_leaves_other_errors_alone() -> None:
    client = _client_raising(ValueError("not ours"))
    with pytest.raises(ValueError):
        await probe_get(client, "http://127.0.0.1:1/health")


async def test_container_health_survives_the_raw_error() -> None:
    client = _client_raising(AttributeError(_RAW))
    with patch("hal0.providers.container.httpx.AsyncClient", return_value=client):
        result = await ContainerProvider().health(8081)
    assert result["ok"] is False
    assert "getpeername" in result["status"]


async def test_halogen_liveness_survives_the_raw_error() -> None:
    client = _client_raising(AttributeError(_RAW))
    with patch("hal0.providers.halogen.async_client", MagicMock(return_value=client)):
        result = await HalogenProvider().liveness(8099)
    assert result["ok"] is False
    assert result["status"] != "stalled"


async def test_wait_ready_keeps_polling_across_the_raw_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first probes hit the dying server; the loop polls on and returns."""
    provider = ContainerProvider()
    monkeypatch.setattr(container_module, "_HEALTH_POLL_INTERVAL_S", 0.0)
    calls = {"n": 0}
    good = MagicMock()
    good.status_code = 200
    good.json = MagicMock(return_value=None)

    def make_client(*_a: Any, **_k: Any) -> AsyncMock:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        async def fake_get(url: str, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] <= 2:
                raise AttributeError(_RAW)
            return good

        client.get = fake_get
        return client

    with patch("hal0.providers.container.httpx.AsyncClient", side_effect=make_client):
        await provider.wait_ready(8081)

    assert calls["n"] == 3
