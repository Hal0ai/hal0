"""Auth on the WS upgrade: CLIENT tier, KB-1 enforcement (spec §4b auth)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hal0.api import create_app
from tests.realtime.conftest import default_backends


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    os.makedirs(str(tmp_path) + "/etc/hal0", exist_ok=True)
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_CLIENT_KEY", "test-client-key")
    monkeypatch.setenv("HAL0_ADMIN_KEY", "test-admin-key")
    app = create_app()
    app.state.realtime_backends = default_backends()
    return app


def test_upgrade_denied_without_credentials(monkeypatch, tmp_path) -> None:
    app = _app(monkeypatch, tmp_path)
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/v1/realtime?model=gpt-test") as ws,
    ):
        ws.receive_json()


def test_upgrade_allowed_with_api_key(monkeypatch, tmp_path) -> None:
    app = _app(monkeypatch, tmp_path)
    with (
        TestClient(app) as client,
        client.websocket_connect("/v1/realtime?model=gpt-test&api_key=test-client-key") as ws,
    ):
        created = ws.receive_json()
        assert created["type"] == "session.created"
