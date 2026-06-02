"""#430 — backend-aware load on the lemonade-proxy catch-all fall-through.

A model requested **by name** through the ``:8080`` gateway misses the
registry / passthrough, fails legacy ``resolve_slot`` (only the composite
``hal0`` upstream is registered, no per-slot upstreams), and falls through
to the raw lemonade-proxy catch-all — which reverse-proxies verbatim to
lemond, where lemond auto-loads the model under its GLOBAL ``config.json``
default backend (``rocm``), ignoring the owning slot's declared
``device=gpu-vulkan``.

B1 (``dispatcher.forward``) closes this gap, but ``forward()`` is never
reached on the catch-all path. So the fix loads the model under its slot's
declared backend (via ``SlotManager.load``, which sends the device-derived
``llamacpp_backend``) **before** the proxy hands off to lemond.

These tests exercise the no-route → proxy fall-through with a slot-backed
model and assert the backend-aware load fires (and only for slot-backed
models), without a live backend.
"""

from __future__ import annotations

import json
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
    """``utility`` declares device=gpu-vulkan in real config; here we only
    need the alias→model_id map so the route can resolve model_id → slot."""

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
) -> tuple[Any, list[Any], dict[str, Any]]:
    """POST /v1/chat/completions for ``model``; return (response, order, captured).

    ``order`` records the interleaving of the backend-aware load and the
    proxy forward so we can assert load happens BEFORE proxy.
    """
    from fastapi.testclient import TestClient

    from hal0.api import create_app

    _patch_alias(monkeypatch)

    order: list[Any] = []
    captured: dict[str, Any] = {}

    # Wrap the recording manager's load so it also stamps `order`.
    orig_load = slot_manager.load

    async def _load(slot_name: str, model_id: str | None = None) -> None:
        order.append(("load", slot_name))
        await orig_load(slot_name, model_id)

    slot_manager.load = _load  # type: ignore[assignment]

    async def _fake_proxy(request: Any, path: str) -> Any:
        from fastapi.responses import Response

        order.append("proxy")
        body = await request.body()
        captured["path"] = path
        captured["body"] = json.loads(body) if body else {}
        return Response(content=b'{"ok": true}', media_type="application/json")

    import hal0.api.routes.lemonade_proxy as lp

    monkeypatch.setattr(lp, "_proxy", _fake_proxy)

    app = create_app()
    with TestClient(app) as client:
        app.state.slot_manager = slot_manager
        r = client.post(
            "/v1/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        )
    return r, order, captured


def test_proxy_loads_slot_backed_model_under_declared_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A by-name request for a model whose owning slot declares a device
    backend drives ``SlotManager.load(owning_slot)`` BEFORE the proxy
    forwards — so the model loads under the slot's backend, not lemond's
    global default."""
    sm = _RecordingSlotManager()
    r, order, captured = _run_chat(monkeypatch, sm, "qwen3-zero-coder-v2-0.8b-f16")

    assert r.status_code == 200, r.text
    # Backend-aware load fired for the owning slot...
    assert sm.loaded == ["utility"]
    # ...and it happened BEFORE the proxy handed off to lemond.
    assert order == [("load", "utility"), "proxy"]
    # The proxy still forwards the model by name.
    assert captured["body"]["model"] == "qwen3-zero-coder-v2-0.8b-f16"


def test_proxy_does_not_load_for_unbacked_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A by-name request for a model with NO backing slot is left to
    lemond's global default — no backend-aware load is kicked."""
    sm = _RecordingSlotManager()
    r, order, _ = _run_chat(monkeypatch, sm, "some-bare-pulled-model")

    assert r.status_code == 200, r.text
    assert sm.loaded == []
    assert order == ["proxy"]


def test_proxy_runs_even_if_backend_aware_load_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing backend-aware load is swallowed: the proxy still forwards
    (preserving today's behavior — lemond auto-loads), rather than 500ing
    on the new code path."""
    sm = _RecordingSlotManager(raises=True)
    r, order, _ = _run_chat(monkeypatch, sm, "hermes-4-14b-q5km")

    assert r.status_code == 200, r.text
    assert sm.loaded == ["agent-hermes"]  # load was attempted
    assert order == [("load", "agent-hermes"), "proxy"]  # proxy still ran after
