"""#430 — backend-aware load for slot-backed models, path-independent.

A model requested **by name** through the ``:8080`` gateway must be loaded
under its owning slot's DECLARED backend (e.g. ``device=gpu-vulkan``)
before any routing decision is taken.

There are several routing paths a by-name request can take depending on
cache/registry state:
  * container-slot preemption / passthrough on a warm cache,
  * registry / real-slot upstream → ``forward()`` (B1),
  * no route → the dispatcher's typed NoRouteFound 404 envelope (the
    catch-all proxy fall-through was removed in epic #687).

To be correct on ALL of them, the backend-aware load runs at the route
layer **before** ``dispatcher.dispatch()`` — so the model is already loaded
under the right backend whichever path dispatch then takes. These tests
assert the load fires before dispatch (and only for slot-backed models),
without a live backend.
"""

from __future__ import annotations

from typing import Any

import pytest

import hal0.api as hal0_api


class _RecordingSlotManager:
    """Records ``load`` calls; ``iter_configs`` unused (alias map is patched)."""

    def __init__(self, raises: bool = False) -> None:
        self.loaded: list[str] = []
        self._raises = raises

    async def load(self, slot_name: str, model_id: str | None = None) -> None:
        self.loaded.append(slot_name)
        if self._raises:
            raise RuntimeError("boom")

    async def iter_configs(self) -> list[dict[str, Any]]:
        return []


def _patch_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(_sm: Any) -> dict[str, str]:
        return {
            "agent-hermes": "hermes-4-14b-q5km",
            "utility": "qwen3-zero-coder-v2-0.8b-f16",
        }

    monkeypatch.setattr(hal0_api, "hal0_chat_slot_alias_map", _fake)


def _run_chat(
    monkeypatch: pytest.MonkeyPatch,
    slot_manager: Any,
    model: str,
) -> tuple[Any, list[Any]]:
    """POST /v1/chat/completions for ``model``; return (response, order).

    ``order`` interleaves the backend-aware load and the dispatch entry so
    tests can assert load happens BEFORE dispatch.
    """
    from fastapi.testclient import TestClient

    from hal0.api import create_app
    from hal0.dispatcher.router import Dispatcher

    _patch_alias(monkeypatch)

    order: list[Any] = []

    # Record load ordering.
    orig_load = slot_manager.load

    async def _load(slot_name: str, model_id: str | None = None) -> None:
        order.append(("load", slot_name))
        await orig_load(slot_name, model_id)

    slot_manager.load = _load  # type: ignore[assignment]

    # Record dispatch entry (it raises NoRouteFound here — no upstreams
    # serve the model, and there is no proxy fall-through — but the point
    # is WHEN it is entered relative to the load).
    orig_dispatch = Dispatcher.dispatch

    async def _spy_dispatch(self: Any, request: Any, body: Any = None) -> Any:
        order.append("dispatch")
        return await orig_dispatch(self, request, body=body)

    monkeypatch.setattr(Dispatcher, "dispatch", _spy_dispatch)

    app = create_app()
    with TestClient(app) as client:
        app.state.slot_manager = slot_manager
        r = client.post(
            "/v1/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        )
    return r, order


def test_slot_backed_model_loads_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    """A by-name request for a model whose owning slot declares a device
    backend drives ``SlotManager.load(owning_slot)`` BEFORE dispatch — so
    whatever path dispatch then takes (container preemption, real slot,
    or the typed no-route 404), the model is already loaded under the
    right backend."""
    sm = _RecordingSlotManager()
    r, order = _run_chat(monkeypatch, sm, "qwen3-zero-coder-v2-0.8b-f16")

    # No upstream serves the model → typed NoRouteFound envelope (the
    # routing outcome is irrelevant to the load-ordering contract).
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "dispatch.no_route"
    assert sm.loaded == ["utility"]
    # The load precedes dispatch (and therefore every routing outcome).
    assert order[0] == ("load", "utility")
    assert order.index(("load", "utility")) < order.index("dispatch")


def test_unbacked_model_does_not_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    """A by-name request for a model with NO backing slot kicks no
    backend-aware load — it dispatches as-is."""
    sm = _RecordingSlotManager()
    r, order = _run_chat(monkeypatch, sm, "some-bare-pulled-model")

    assert r.status_code == 404, r.text  # nothing serves it; no fall-through
    assert sm.loaded == []
    assert "dispatch" in order  # routing still ran


def test_dispatch_proceeds_even_if_backend_aware_load_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    """A failing backend-aware load is swallowed: dispatch still runs and
    decides the client-facing outcome, rather than 500ing on the new code
    path."""
    sm = _RecordingSlotManager(raises=True)
    r, order = _run_chat(monkeypatch, sm, "hermes-4-14b-q5km")

    assert r.status_code == 404, r.text  # dispatch ran and found no route
    assert r.json()["error"]["code"] == "dispatch.no_route"
    assert sm.loaded == ["agent-hermes"]  # load was attempted
    assert order.index(("load", "agent-hermes")) < order.index("dispatch")
