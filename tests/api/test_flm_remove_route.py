"""DELETE /api/slots/flm/models/{tag}: wiring, error envelope and live-slot read.

The remove itself is covered in tests/slots/test_flm_remove.py; here
:func:`hal0.slots.flm_remove.remove_flm_model` is replaced so the route's own
job (what it passes in, how refusals render) is checked through HTTP.
"""

from __future__ import annotations

import types
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware.error_codes import install as install_error_codes
from hal0.api.routes import slots as slots_routes
from hal0.errors import Conflict
from hal0.slots import flm_remove


class _SlotManager:
    def __init__(self, slots: list[Any]) -> None:
        self._slots = slots

    async def list(self) -> list[Any]:
        return self._slots


class _Events:
    def __init__(self) -> None:
        self.emitted: list[tuple[Any, ...]] = []

    async def emit(self, *args: Any, **kwargs: Any) -> None:
        self.emitted.append((*args, kwargs))


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    install_error_codes(app)
    app.include_router(slots_routes.router, prefix="/api/slots")
    app.state.slot_manager = _SlotManager(
        [
            types.SimpleNamespace(name="npu", state="serving", model_id="qwen3:0.6b"),
            types.SimpleNamespace(name="old", state="offline", model_id="llama3.2:1b"),
        ]
    )
    app.state.model_registry = object()
    app.state.model_pull_jobs = {}
    app.state.events = _Events()
    return app


def test_remove_passes_live_slots_and_returns_the_result(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def _remove(tag: str, **kw: Any) -> dict[str, Any]:
        seen.update(tag=tag, **kw)
        return {
            "tag": tag,
            "removed": True,
            "path": "/s/x",
            "bytes_freed": 42,
            "registry_removed": True,
            "warnings": [],
        }

    monkeypatch.setattr(flm_remove, "remove_flm_model", _remove)
    resp = TestClient(app).delete("/api/slots/flm/models/gemma3:1b")

    assert resp.status_code == 200, resp.text
    assert resp.json()["bytes_freed"] == 42
    assert seen["tag"] == "gemma3:1b"
    assert seen["live"] == [("npu", "qwen3:0.6b")]  # offline slots hold nothing
    assert seen["jobs"] is app.state.model_pull_jobs
    assert seen["registry"] is app.state.model_registry
    (event,) = app.state.events.emitted
    assert event[0] == "model.deleted"


def test_refusal_renders_as_a_409_envelope(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _remove(tag: str, **kw: Any) -> dict[str, Any]:
        raise Conflict(
            f"{tag} is in use",
            code="flm.model_in_use",
            details={"tag": tag, "in_use_by": [{"role": "chat", "slot": "npu"}]},
        )

    monkeypatch.setattr(flm_remove, "remove_flm_model", _remove)
    resp = TestClient(app, raise_server_exceptions=False).delete("/api/slots/flm/models/qwen3:0.6b")

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "flm.model_in_use"
    assert body["error"]["details"]["in_use_by"] == [{"role": "chat", "slot": "npu"}]
    assert app.state.events.emitted == []


def test_real_refuse_rule_through_http(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end with the real service: a live FLM slot's tag is refused, nothing spawned."""
    from hal0.providers import flm_cli

    monkeypatch.setattr(
        flm_cli,
        "_slot_configs",
        lambda: [
            {"name": "npu", "device": "npu", "type": "llm", "model": {"default": "llama3.2:1b"}}
        ],
    )

    async def _never(argv: list[str], tag: str) -> tuple[int, str]:
        raise AssertionError("flm remove must not run")

    monkeypatch.setattr(flm_remove, "_run_remove", _never)
    resp = TestClient(app, raise_server_exceptions=False).delete("/api/slots/flm/models/qwen3:0.6b")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "flm.model_in_use"


def test_catalogue_rows_carry_in_use_by(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """The FLM tab reads which tags are held from the same rule the remove refuses on."""
    from hal0.providers import flm_cli

    monkeypatch.setattr(
        slots_routes._flm_catalog,
        "catalog",
        lambda: [
            {"model": "llama3.2:1b", "installed": True},
            {"model": "qwen3:0.6b", "installed": True},
            {"model": "embed-gemma:300m", "installed": True},
            {"model": "gemma3:1b", "installed": False},
        ],
    )
    monkeypatch.setattr(
        flm_cli,
        "_slot_configs",
        lambda: [
            {
                "name": "npu",
                "device": "npu",
                "type": "llm",
                "model": {"default": "llama3.2:1b"},
                "npu": {"embed": True},
            }
        ],
    )
    resp = TestClient(app).get("/api/slots/flm/models")

    assert resp.status_code == 200
    rows = {r["model"]: r["in_use_by"] for r in resp.json()["models"]}
    assert rows == {
        "llama3.2:1b": [{"role": "chat", "slot": "npu"}],
        # the fixture's live "npu" slot still serves qwen3:0.6b
        "qwen3:0.6b": [{"role": "chat", "slot": "npu"}],
        "embed-gemma:300m": [{"role": "embed", "slot": "npu"}],
        "gemma3:1b": [],
    }
