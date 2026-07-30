"""GET /v1/models per-slot alias entries (hermes-role-slots).

Each LOADED chat slot with a model bound surfaces as one OpenAI ``model``
object addressed by its alias (= slot name), carrying a human display name and
the slot's context length. Unloaded / model-less slots are hidden.

These tests exercise the :func:`hal0.api.hal0_slot_alias_models` helper
directly with a faked loaded-model set (normally derived from dispatchable
container-slot state) so they don't depend on a live backend.
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
            "port": 8001,
            "model": {"default": "qwen3-coder-next-reap-40b-a3b-q4kxl"},
        },
        {
            "name": "agent-hermes",
            "type": "llm",
            "port": 8001,
            "model": {"default": "hermes-4-14b-q5km", "ctx_size": 65536},
        },
        {
            "name": "utility",
            "type": "llm",
            "port": 8081,
            "model": {"default": "qwen3-zero-coder-v2-0.8b-f16", "context_size": 32768},
        },
        # Non-chat slot — never surfaces as a chat alias.
        {
            "name": "embed",
            "type": "embedding",
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


@pytest.mark.asyncio
async def test_all_model_bound_llm_slots_emit_alias_entries() -> None:
    """Every chat slot (type=="llm") with a model appears —
    both warm and cold — because dispatch cold-loads on demand."""
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

    # §21.5: max_context_window aliases context_length (same value, for
    # OpenAI-compat clients that probe the alternate key name).
    for slot_id in ("primary", "agent-hermes", "utility"):
        assert by_id[slot_id]["max_context_window"] == by_id[slot_id]["context_length"]

    # §21.5: a live llm slot's model is always local.
    for e in entries:
        assert e["downloaded"] is True


@pytest.mark.asyncio
async def test_registry_detail_folds_into_alias_entries() -> None:
    """§21.5: labels/checkpoint/recipe surface on an alias entry when the
    slot's model id resolves in the registry, sourced from ``capabilities``/
    ``quant``/the blessed-path recipe bucket."""

    class _FakeDetailModel(_FakeModel):
        def __init__(
            self,
            name: str,
            context_size: int | None = None,
            capabilities: list[str] | None = None,
            quant: str | None = None,
            path: str = "",
        ) -> None:
            super().__init__(name, context_size)
            self.capabilities = capabilities or []
            self.quant = quant
            self.path = path

    class _FakeDetailRegistry(_FakeModelRegistry):
        def get(self, model_id: str) -> _FakeDetailModel:  # type: ignore[override]
            if model_id != "qwen3-coder-next-reap-40b-a3b-q4kxl":
                raise KeyError(model_id)
            return _FakeDetailModel(
                "Qwen3-Coder-Next",
                65536,
                capabilities=["chat", "vision"],
                quant="Q4_K_M",
                path="/var/lib/hal0/models/qwen3-coder-recipe/chat/model.gguf",
            )

    entries = await hal0_slot_alias_models(
        _FakeSlotManager(_three_chat_slots()), _FakeDetailRegistry({}), now=1000
    )
    by_id = {e["id"]: e for e in entries}
    primary = by_id["primary"]
    assert primary["labels"] == ["chat", "vision"]
    assert primary["checkpoint"] == "Q4_K_M"
    assert primary["recipe"] == "qwen3-coder-recipe"
    # utility's model isn't in the fake registry — no registry-detail keys,
    # but it's still marked downloaded (a served slot's model is local).
    assert "labels" not in by_id["utility"]
    assert "checkpoint" not in by_id["utility"]
    assert "recipe" not in by_id["utility"]
    assert by_id["utility"]["downloaded"] is True


@pytest.mark.asyncio
async def test_all_model_bound_llm_slots_appear_regardless_of_load_state() -> None:
    """All model-bound llm slots appear even when only a subset are
    actually loaded — dispatch cold-loads on demand."""
    entries = await hal0_slot_alias_models(
        _FakeSlotManager(_three_chat_slots()), _registry(), now=1000
    )
    assert {e["id"] for e in entries} == {"primary", "agent-hermes", "utility"}


@pytest.mark.asyncio
async def test_model_less_slots_are_hidden() -> None:
    """#1369: clearing the model is what takes a slot off the alias list."""
    cfgs = _three_chat_slots()
    cfgs[0]["model"] = {"default": ""}  # clear primary
    entries = await hal0_slot_alias_models(_FakeSlotManager(cfgs), _registry(), now=1000)
    assert "primary" not in {e["id"] for e in entries}


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
