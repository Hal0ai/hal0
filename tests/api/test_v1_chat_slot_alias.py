"""Chat-slot alias → model-id translation (hermes-role-slots).

Co-resident chat slots (``primary`` / ``agent-hermes`` / ``utility``) are
addressable by their ALIAS (= slot name). The ``/v1`` route layer rewrites
a chat-slot alias to that slot's configured model id BEFORE routing, then
the request flows down the normal dispatch path. This is a thin
translation — NOT per-slot upstream routing.

These tests cover the translation map builders + the route-layer rewrite
(including the cached-body overwrite that downstream consumers re-reading
``request.body()`` observe), without depending on a live backend.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import hal0.api as hal0_api
from hal0.api import hal0_chat_slot_alias_map, hal0_chat_slot_model_ids


class _FakeSlotManager:
    def __init__(self, configs: list[dict[str, Any]]):
        self._configs = configs

    async def iter_configs(self) -> list[dict[str, Any]]:
        return list(self._configs)


def _three_chat_slots() -> list[dict[str, Any]]:
    return [
        {
            "name": "primary",
            "type": "llm",
            "enabled": True,
            "port": 8001,
            "model": {"default": "qwen3-coder-next-reap-40b-a3b-q4kxl"},
        },
        {
            "name": "agent-hermes",
            "type": "llm",
            "enabled": True,
            "port": 8001,
            "model": {"default": "hermes-4-14b-q5km", "ctx_size": 65536},
        },
        {
            "name": "utility",
            "type": "llm",
            "enabled": True,
            "port": 8081,
            "model": {"default": "qwen3-zero-coder-v2-0.8b-f16", "context_size": 32768},
        },
        # Non-chat slot — never an alias.
        {
            "name": "embed",
            "type": "embedding",
            "enabled": True,
            "port": 0,
            "model": {"default": "Qwen3-Embedding-0.6B-GGUF"},
        },
        # Disabled chat slot — excluded.
        {
            "name": "spare",
            "type": "llm",
            "enabled": False,
            "port": 8082,
            "model": {"default": "some-spare-model"},
        },
    ]


# ── alias map + model-id set ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alias_map_covers_enabled_chat_slots_only() -> None:
    alias = await hal0_chat_slot_alias_map(_FakeSlotManager(_three_chat_slots()))
    assert alias == {
        "primary": "qwen3-coder-next-reap-40b-a3b-q4kxl",
        "agent-hermes": "hermes-4-14b-q5km",
        "utility": "qwen3-zero-coder-v2-0.8b-f16",
    }
    # No embed (non-chat) and no spare (disabled).
    assert "embed" not in alias
    assert "spare" not in alias


@pytest.mark.asyncio
async def test_chat_slot_model_ids_for_dedup() -> None:
    ids = await hal0_chat_slot_model_ids(_FakeSlotManager(_three_chat_slots()))
    assert ids == {
        "qwen3-coder-next-reap-40b-a3b-q4kxl",
        "hermes-4-14b-q5km",
        "qwen3-zero-coder-v2-0.8b-f16",
    }
    assert "Qwen3-Embedding-0.6B-GGUF" not in ids


# ── route-layer rewrite ─────────────────────────────────────────────────────


class _FakeApp:
    def __init__(self, slot_manager: Any):
        self.state = type("S", (), {"slot_manager": slot_manager})()


class _FakeRequest:
    """Minimal stand-in exposing the surface ``_rewrite_chat_slot_alias``
    touches: ``app.state.slot_manager`` and a settable ``_body``."""

    def __init__(self, slot_manager: Any):
        self.app = _FakeApp(slot_manager)
        self._body = b""


@pytest.mark.asyncio
async def test_rewrite_translates_alias_to_model_id_and_body() -> None:
    from hal0.api.routes.v1 import _rewrite_chat_slot_alias

    req = _FakeRequest(_FakeSlotManager(_three_chat_slots()))
    body = await _rewrite_chat_slot_alias(req, {"model": "primary", "messages": []})

    # Returned dict carries the model id, not the alias.
    assert body["model"] == "qwen3-coder-next-reap-40b-a3b-q4kxl"
    # Cached request body (re-read verbatim by any downstream consumer of
    # request.body()) is overwritten with the rewritten model name.
    assert json.loads(req._body)["model"] == "qwen3-coder-next-reap-40b-a3b-q4kxl"


@pytest.mark.asyncio
async def test_rewrite_each_alias_maps_to_its_distinct_model() -> None:
    from hal0.api.routes.v1 import _rewrite_chat_slot_alias

    sm = _FakeSlotManager(_three_chat_slots())
    for alias, expected in (
        ("primary", "qwen3-coder-next-reap-40b-a3b-q4kxl"),
        ("agent-hermes", "hermes-4-14b-q5km"),
        ("utility", "qwen3-zero-coder-v2-0.8b-f16"),
    ):
        req = _FakeRequest(sm)
        body = await _rewrite_chat_slot_alias(req, {"model": alias, "messages": []})
        assert body["model"] == expected


@pytest.mark.asyncio
async def test_rewrite_is_noop_for_bare_model_id() -> None:
    """A request already keyed on a model id (not an alias) is untouched —
    it dispatches by name unchanged."""
    from hal0.api.routes.v1 import _rewrite_chat_slot_alias

    req = _FakeRequest(_FakeSlotManager(_three_chat_slots()))
    body = await _rewrite_chat_slot_alias(req, {"model": "hermes-4-14b-q5km", "messages": []})
    assert body["model"] == "hermes-4-14b-q5km"
    assert req._body == b""  # not rewritten


@pytest.mark.asyncio
async def test_rewrite_is_noop_without_slot_manager() -> None:
    from hal0.api.routes.v1 import _rewrite_chat_slot_alias

    req = _FakeRequest(None)
    body = await _rewrite_chat_slot_alias(req, {"model": "primary", "messages": []})
    assert body["model"] == "primary"


# ── dispatcher non-regression ───────────────────────────────────────────────


def test_resolve_by_capability_chat_default_raises_typed_legacy_error() -> None:
    """A chat request that reaches the legacy fallback selects the ``chat``
    default and (absent a real chat slot upstream) raises the typed legacy
    error, which the dispatcher converts to NoRouteFound — surfaced to the
    client as the 404 ``dispatch.no_route`` envelope. No per-slot chat
    upstream is matched."""
    from hal0.dispatcher.router import LegacyResolutionFailed, resolve_by_capability
    from hal0.upstreams.registry import Upstream, UpstreamRegistry

    reg = UpstreamRegistry()
    # Only the composite hal0 upstream exists (no per-slot chat upstreams).
    reg.upsert(
        Upstream(
            name="hal0",
            kind="slot",
            url="http://127.0.0.1:8080/v1",
            slot_name=None,
            auth_style="none",
        )
    )
    with pytest.raises(LegacyResolutionFailed):
        # model id form — not an alias, not a registered slot name → falls
        # to the "chat" default which has no slot upstream → legacy error.
        resolve_by_capability(
            "/v1/chat/completions",
            {"model": "hermes-4-14b-q5km", "messages": []},
            reg,
        )


def _patch_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(_sm: Any) -> dict[str, str]:
        return {
            "primary": "qwen3-coder-next-reap-40b-a3b-q4kxl",
            "agent-hermes": "hermes-4-14b-q5km",
            "utility": "qwen3-zero-coder-v2-0.8b-f16",
        }

    monkeypatch.setattr(hal0_api, "hal0_chat_slot_alias_map", _fake)


def test_chat_alias_is_rewritten_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    """End-to-end: POST /v1/chat/completions with model="primary" rewrites
    to the model id BEFORE the dispatcher routes. With nothing serving the
    model the dispatcher raises NoRouteFound, whose details carry the
    REWRITTEN model name (not the bare alias) — proving the rewrite ran
    ahead of routing."""
    from fastapi.testclient import TestClient

    from hal0.api import create_app

    _patch_alias(monkeypatch)

    with TestClient(create_app()) as client:
        r = client.post(
            "/v1/chat/completions",
            json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
        )

    # No upstream serves the model and there is no proxy fall-through —
    # the typed envelope surfaces, keyed on the rewritten model NAME.
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["error"]["code"] == "dispatch.no_route"
    assert body["error"]["details"]["model"] == "qwen3-coder-next-reap-40b-a3b-q4kxl"
