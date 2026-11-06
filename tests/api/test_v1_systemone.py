"""Tests for POST /v1/systemone routing (hal0 router, not the bare slot port).

Covers: default proxy-to-hs-decide path, EXPERIMENTAL slot-decision path,
activity/idle-timer touch on the slot path, and that the old /v1/systemone
405 (unrouted) is gone.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from hal0.api.routes import v1 as v1_routes


def _install_mock_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    mock_transport = httpx.MockTransport(handler)
    real_client_cls = httpx.AsyncClient

    def _client_factory(*args, **kwargs):
        kwargs["transport"] = mock_transport
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(v1_routes.httpx, "AsyncClient", _client_factory, raising=False)
    monkeypatch.setattr("httpx.AsyncClient", _client_factory)


def test_v1_systemone_not_404_or_405(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The route exists now (no more 'no slot manager → 405/404' gap)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "ok"})

    _install_mock_transport(monkeypatch, handler)
    client.app.state.slot_manager = None
    client.app.state.hal0_config = SimpleNamespace(memory=SimpleNamespace(decision=None))

    r = client.post("/v1/systemone", json={"prompt": "hi"})
    assert r.status_code == 200
    assert r.json() == {"decision": "ok"}


def test_v1_systemone_proxies_to_hs_decide_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (no [npu].decision slot configured) hits [memory.decision].base_url."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    _install_mock_transport(monkeypatch, handler)
    client.app.state.slot_manager = None
    client.app.state.hal0_config = SimpleNamespace(
        memory=SimpleNamespace(
            decision=SimpleNamespace(
                base_url="http://127.0.0.1:9178", model="hs-decide-1-flash", timeout_s=30.0
            )
        )
    )

    r = client.post("/v1/systemone", json={"prompt": "should I retain this?"})
    assert r.status_code == 200
    assert seen["url"] == "http://127.0.0.1:9178/v1/systemone"
    assert seen["body"]["model"] == "hs-decide-1-flash"


def test_v1_systemone_routes_to_decision_slot_when_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EXPERIMENTAL path: an [npu].decision slot wins over the hs-decide default."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    _install_mock_transport(monkeypatch, handler)

    slot_manager = SimpleNamespace(
        iter_configs=AsyncMock(
            return_value=[{"name": "flm", "npu": {"decision": "d3:lite"}, "port": 18080}]
        ),
        load=AsyncMock(),
        get_config=AsyncMock(return_value={"port": 18080, "max_inflight": 1}),
    )
    client.app.state.slot_manager = slot_manager
    client.app.state.hal0_config = SimpleNamespace(
        memory=SimpleNamespace(
            decision=SimpleNamespace(
                base_url="http://127.0.0.1:9178", model="hs-decide-1-flash", timeout_s=30.0
            )
        )
    )
    client.app.state.slot_last_used = {}
    client.app.state.slot_request_count = {}

    r = client.post("/v1/systemone", json={"model": "d3:lite", "prompt": "fast gate"})
    assert r.status_code == 200
    assert seen["url"] == "http://127.0.0.1:18080/v1/systemone"
    slot_manager.load.assert_awaited_once_with("flm")
    assert "flm" in client.app.state.slot_last_used  # activity touch resets idle timer
