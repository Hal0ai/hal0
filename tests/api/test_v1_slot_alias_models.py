"""GET /v1/models per-slot alias entries (hermes-role-slots).

Each LOADED, enabled chat slot surfaces as one OpenAI ``model`` object
addressed by its alias (= slot name), carrying a human display name and
the slot's context length. Unloaded / disabled slots are hidden.

These tests exercise the :func:`hal0.api.hal0_slot_alias_models` helper
directly with a faked lemond health probe so they don't depend on a live
backend.
"""

from __future__ import annotations

from typing import Any

import pytest

import hal0.api as hal0_api
from hal0.api import hal0_slot_alias_models


class _FakeSlotManager:
    def __init__(self, configs: list[dict[str, Any]]):
        self._configs = configs

    async def iter_configs(self) -> list[dict[str, Any]]:
        return list(self._configs)


class _FakeDefaults:
    def __init__(self, context_size: int | None):
        self.context_size = context_size


class _FakeModel:
    def __init__(self, name: str, context_size: int | None = None):
        self.name = name
        self.defaults = _FakeDefaults(context_size) if context_size is not None else None


class _FakeModelRegistry:
    def __init__(self, models: dict[str, tuple[str, int | None]]):
        # model_id -> (display_name, defaults.context_size | None)
        self._models = models

    def get(self, model_id: str) -> _FakeModel:
        if model_id not in self._models:
            raise KeyError(model_id)
        name, ctx = self._models[model_id]
        return _FakeModel(name, ctx)


def _three_chat_slots() -> list[dict[str, Any]]:
    """Mirror the live TOML ctx-key inconsistency:
    primary pins NO ctx (→ registry fallback), agent-hermes uses
    ``ctx_size``, utility uses ``context_size``.
    """
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
        # Non-chat slot — never surfaces as a chat alias.
        {
            "name": "embed",
            "type": "embedding",
            "enabled": True,
            "port": 0,
            "model": {"default": "Qwen3-Embedding-0.6B-GGUF"},
        },
    ]


def _registry() -> _FakeModelRegistry:
    return _FakeModelRegistry(
        {
            # primary's model has a registry defaults.context_size that the
            # alias builder falls back to (the slot TOML pins no ctx key).
            "qwen3-coder-next-reap-40b-a3b-q4kxl": ("Qwen3-Coder-Next", 65536),
            "hermes-4-14b-q5km": ("Hermes 4 14B", None),
            # utility's model intentionally absent → display falls back to id.
        }
    )


def _patch_loaded(monkeypatch: pytest.MonkeyPatch, loaded: set[str] | None) -> None:
    async def _fake(_sm: Any) -> set[str] | None:
        return loaded

    monkeypatch.setattr(hal0_api, "_loaded_model_ids", _fake)


@pytest.mark.asyncio
async def test_all_loaded_chat_slots_emit_alias_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_loaded(
        monkeypatch,
        {
            "qwen3-coder-next-reap-40b-a3b-q4kxl",
            "hermes-4-14b-q5km",
            "qwen3-zero-coder-v2-0.8b-f16",
        },
    )
    entries = await hal0_slot_alias_models(
        _FakeSlotManager(_three_chat_slots()), _registry(), now=1000
    )
    by_id = {e["id"]: e for e in entries}

    # Exactly the three chat slots, addressed by alias; no embed slot.
    assert set(by_id) == {"primary", "agent-hermes", "utility"}

    # Stable id = slot name; owned_by = hal0; OpenAI object shape.
    for e in entries:
        assert e["object"] == "model"
        assert e["owned_by"] == "hal0"
        assert e["created"] == 1000

    # Display name = "<slot> · <model display name>" from the registry.
    assert by_id["primary"]["name"] == "primary · Qwen3-Coder-Next"
    assert by_id["agent-hermes"]["name"] == "agent-hermes · Hermes 4 14B"
    # utility's model isn't in the registry → falls back to the model id.
    assert by_id["utility"]["name"] == "utility · qwen3-zero-coder-v2-0.8b-f16"

    # context_length surfaces for all three: agent-hermes via ``ctx_size``,
    # utility via ``context_size``, primary via the registry fallback
    # (defaults.context_size) since its TOML pins no ctx key.
    assert by_id["agent-hermes"]["context_length"] == 65536
    assert by_id["utility"]["context_length"] == 32768
    assert by_id["primary"]["context_length"] == 65536


@pytest.mark.asyncio
async def test_unloaded_slots_are_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only slots whose model is in lemond's loaded set appear."""
    _patch_loaded(monkeypatch, {"hermes-4-14b-q5km"})
    entries = await hal0_slot_alias_models(
        _FakeSlotManager(_three_chat_slots()), _registry(), now=1000
    )
    assert {e["id"] for e in entries} == {"agent-hermes"}


@pytest.mark.asyncio
async def test_disabled_slots_are_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    cfgs = _three_chat_slots()
    cfgs[0]["enabled"] = False  # disable primary
    _patch_loaded(
        monkeypatch,
        {
            "qwen3-coder-next-reap-40b-a3b-q4kxl",
            "hermes-4-14b-q5km",
            "qwen3-zero-coder-v2-0.8b-f16",
        },
    )
    entries = await hal0_slot_alias_models(_FakeSlotManager(cfgs), _registry(), now=1000)
    assert "primary" not in {e["id"] for e in entries}


@pytest.mark.asyncio
async def test_no_entries_when_health_probe_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If lemond health can't be read at all, no alias entries are emitted
    rather than advertising slots we can't confirm are serving."""
    _patch_loaded(monkeypatch, None)
    entries = await hal0_slot_alias_models(
        _FakeSlotManager(_three_chat_slots()), _registry(), now=1000
    )
    assert entries == []


# ── handler integration: GET /v1/models surfaces the alias entries ──────────


def test_v1_models_handler_includes_slot_alias_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public ``GET /v1/models`` handler folds the per-slot alias
    entries into the OpenAI list response."""
    from fastapi.testclient import TestClient

    from hal0.api import create_app

    async def _fake_alias_models(
        _slot_manager: Any, _model_registry: Any, *, now: int | None = None
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": "agent-hermes",
                "object": "model",
                "created": now or 0,
                "owned_by": "hal0",
                "name": "agent-hermes · Hermes 4 14B",
                "context_length": 65536,
            }
        ]

    monkeypatch.setattr(hal0_api, "hal0_slot_alias_models", _fake_alias_models)

    with TestClient(create_app()) as client:
        r = client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()["data"]
    by_id = {e["id"]: e for e in data}
    assert "agent-hermes" in by_id
    assert by_id["agent-hermes"]["owned_by"] == "hal0"
    assert by_id["agent-hermes"]["name"] == "agent-hermes · Hermes 4 14B"
    assert by_id["agent-hermes"]["context_length"] == 65536
