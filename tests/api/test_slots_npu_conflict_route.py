"""The ``unload_npu_holder`` opt-in on slot load/restart and the 409 envelope (#3666)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hal0.api as hal0_api
from hal0.api import create_app
from hal0.api.routes import slots as slots_routes
from hal0.api.routes import v1
from hal0.dispatcher.router import Dispatcher
from hal0.slots.state import SlotNpuConflict, SlotState

_DETAILS_KEYS = {"slot", "holders", "holder_engine", "unloads", "pinned", "pending", "reason"}


def _conflict(*, pending: bool = False) -> SlotNpuConflict:
    return SlotNpuConflict(
        "px needs the whole NPU",
        details={
            "slot": "px",
            "holders": ["flm"],
            "holder_engine": "flm",
            "unloads": ["flm", "flm-stt"],
            "pinned": False,
            "pending": pending,
            "reason": "px needs the whole NPU",
        },
    )


class _Snap:
    state = SlotState.READY
    model_id = "m"


@pytest.fixture
def calls(
    isolated_app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> dict[str, list[dict[str, Any]]]:
    app, _client = isolated_app_client
    seen: dict[str, list[dict[str, Any]]] = {"load": [], "restart": []}
    sm = app.state.slot_manager

    async def load(slot_name: str, model_id: str | None = None, **kw: Any) -> Any:
        seen["load"].append({"slot": slot_name, "model_id": model_id, **kw})
        return _Snap()

    async def restart(slot_name: str, **kw: Any) -> Any:
        seen["restart"].append({"slot": slot_name, **kw})
        return _Snap()

    monkeypatch.setattr(sm, "load", load)
    monkeypatch.setattr(sm, "restart", restart)
    monkeypatch.setattr(sm, "reset_load_failures", lambda *_a, **_k: None)
    monkeypatch.setattr(slots_routes, "_slot_to_dict", lambda snap, _req: {"state": "ready"})
    return seen


def test_load_conflict_returns_409_envelope(
    isolated_app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = isolated_app_client

    async def load(slot_name: str, model_id: str | None = None, **kw: Any) -> Any:
        raise _conflict()

    monkeypatch.setattr(app.state.slot_manager, "load", load)
    monkeypatch.setattr(app.state.slot_manager, "reset_load_failures", lambda *_a, **_k: None)
    r = client.post("/api/slots/px/load", json={})
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "slot.npu_conflict"
    assert set(err["details"]) == _DETAILS_KEYS


def test_load_with_unload_npu_holder_true_passes_flag(
    isolated_app_client: tuple[FastAPI, TestClient], calls: dict[str, list[dict[str, Any]]]
) -> None:
    _app, client = isolated_app_client
    r = client.post("/api/slots/px/load", json={"unload_npu_holder": True})
    assert r.status_code == 200, r.text
    assert calls["load"][0]["unload_npu_holder"] is True


def test_load_without_flag_passes_false(
    isolated_app_client: tuple[FastAPI, TestClient], calls: dict[str, list[dict[str, Any]]]
) -> None:
    _app, client = isolated_app_client
    assert client.post("/api/slots/px/load", json={}).status_code == 200
    assert calls["load"][0]["unload_npu_holder"] is False


def test_restart_body_flag_passes(
    isolated_app_client: tuple[FastAPI, TestClient], calls: dict[str, list[dict[str, Any]]]
) -> None:
    _app, client = isolated_app_client
    r = client.post("/api/slots/px/restart", json={"unload_npu_holder": True})
    assert r.status_code == 200, r.text
    assert calls["restart"][0]["unload_npu_holder"] is True
    # No body at all is fine and means false.
    assert client.post("/api/slots/px/restart").status_code == 200
    assert calls["restart"][1]["unload_npu_holder"] is False


@pytest.mark.parametrize("value", ["true", 1, "yes", None])
def test_unload_npu_holder_string_true_is_ignored(
    isolated_app_client: tuple[FastAPI, TestClient],
    calls: dict[str, list[dict[str, Any]]],
    value: Any,
) -> None:
    _app, client = isolated_app_client
    assert client.post("/api/slots/px/load", json={"unload_npu_holder": value}).status_code == 200
    assert (
        client.post("/api/slots/px/restart", json={"unload_npu_holder": value}).status_code == 200
    )
    assert calls["load"][0]["unload_npu_holder"] is False
    assert calls["restart"][0]["unload_npu_holder"] is False


# ── traffic paths surface the 409 instead of a retryable 503 ────────────────


def test_v1_path_returns_409_without_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _alias(_sm: Any) -> dict[str, str]:
        return {"px": "big-model"}

    monkeypatch.setattr(hal0_api, "hal0_chat_slot_alias_map", _alias)

    class _SM:
        async def load(self, slot_name: str, model_id: str | None = None) -> None:
            raise _conflict()

        async def iter_configs(self) -> list[dict[str, Any]]:
            return []

    async def _dispatch(self: Any, request: Any, body: Any = None) -> Any:
        raise AssertionError("dispatch must not run after an NPU refusal")

    monkeypatch.setattr(Dispatcher, "dispatch", _dispatch)
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.slot_manager = _SM()
        r = client.post("/v1/chat/completions", json={"model": "big-model", "messages": []})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "slot.npu_conflict"
    assert "retry-after" not in {k.lower() for k in r.headers}
    assert v1  # module imported for patch targets


@pytest.mark.asyncio
async def test_dispatcher_path_reraises_npu_conflict() -> None:
    from tests.dispatcher.test_serving_integration import (
        _make_dispatcher,
        _RecordingSlotManager,
        _slot_call,
    )

    class _SM(_RecordingSlotManager):
        def __init__(self) -> None:
            super().__init__(state=SlotState.OFFLINE)

        async def load(self, slot_name: str) -> None:
            raise _conflict()

    dispatcher = _make_dispatcher(
        httpx.MockTransport(lambda req: httpx.Response(200, json={})), sm=_SM()
    )
    try:
        with pytest.raises(SlotNpuConflict) as ei:
            await dispatcher.forward(_slot_call())
        assert ei.value.status == 409
        assert "retry_after_s" not in ei.value.details
    finally:
        await dispatcher.aclose()
