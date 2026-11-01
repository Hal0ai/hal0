"""#2944 — /v1 ends a request whose client left during a lazy load, before dispatch."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

import hal0.api as hal0_api
from hal0.api import create_app
from hal0.api.routes import v1
from hal0.dispatcher.router import Dispatcher
from hal0.slots.abandon import abandon_probe


class _SlotManager:
    def __init__(self) -> None:
        self.loaded: list[str] = []
        self.probe_seen: bool = False

    async def load(self, slot_name: str, model_id: str | None = None) -> None:
        self.loaded.append(slot_name)
        self.probe_seen = abandon_probe.get() is not None

    async def iter_configs(self) -> list[dict[str, Any]]:
        return []


@pytest.mark.parametrize("gone", [True, False])
def test_route_stops_before_dispatch_only_when_client_gone(
    monkeypatch: pytest.MonkeyPatch, gone: bool
) -> None:
    async def _alias(_sm: Any) -> dict[str, str]:
        return {"agent": "big-model"}

    monkeypatch.setattr(hal0_api, "hal0_chat_slot_alias_map", _alias)
    monkeypatch.setattr(v1, "_SLOW_LOAD_S", 0.0)

    async def _probe(self: Any) -> bool:
        return gone

    monkeypatch.setattr(v1.ClientGoneProbe, "__call__", _probe)
    dispatched: list[Any] = []

    async def _dispatch(self: Any, request: Any, body: Any = None) -> Any:
        dispatched.append(body)
        raise RuntimeError("stop here")

    monkeypatch.setattr(Dispatcher, "dispatch", _dispatch)
    sm = _SlotManager()
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.slot_manager = sm
        resp: Response = client.post(
            "/v1/chat/completions", json={"model": "big-model", "messages": []}
        )

    assert sm.loaded == ["agent"]
    assert sm.probe_seen
    if gone:
        assert resp.status_code == 499
        assert not dispatched
    else:
        assert dispatched
