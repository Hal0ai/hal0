"""GET /v1/models does NOT advertise hal0/* virtual names.

PR #1153 removed canonical virtual name advertising from /v1/models.
The per-slot alias entries cover every enabled llm slot, and dispatch
resolves the canonical names on-demand via the LiveSlotResolver.
Keeping virtual names would double-list the same slot (once as its
alias, once as hal0/<alias>).

This test verifies that hal0/agent, hal0/npu, hal0/utility, hal0/chat,
and hal0/primary are NOT present in the /v1/models response, even when a
matching slot exists. The bare slot names (e.g., "agent") ARE advertised
through hal0_slot_alias_models.
"""

from __future__ import annotations

import pytest


class _FakeSlotManager:
    """Returns a single enabled llm slot named ``agent`` with model ``big``."""

    async def iter_configs(self):
        return [
            {
                "name": "agent",
                "type": "llm",
                "enabled": True,
                "model": {"default": "big"},
                "ctx_size": 65536,
                "device": "gpu-vulkan",
            }
        ]


class _FakeModelRegistry:
    """Simple registry that returns a display name."""

    def get(self, model_id):
        return _FakeModelEntry(model_id)


class _FakeModelEntry:
    def __init__(self, model_id):
        self.name = model_id
        self.defaults = type("_", (), {"context_size": 65536})()


@pytest.fixture
def configured_client(isolated_client, monkeypatch):
    """TestClient with a slot_manager and model_registry on app state."""
    sm = _FakeSlotManager()
    mr = _FakeModelRegistry()
    # Attach to the lifespan-created app state
    with isolated_client as client:
        app = client.app
        app.state.slot_manager = sm
        app.state.model_registry = mr
        # Prevent the composite upstream from adding raw model ids
        monkeypatch.setattr("hal0.api.hal0_chat_slot_model_ids", lambda sm: {"big"})
        yield client


def test_virtual_names_not_advertised(client, monkeypatch):
    """hal0/agent, hal0/npu, hal0/utility are not in /v1/models (removed in #1153)."""
    sm = _FakeSlotManager()
    mr = _FakeModelRegistry()
    monkeypatch.setattr(client.app, "state", client.app.state)
    client.app.state.slot_manager = sm
    client.app.state.model_registry = mr
    monkeypatch.setattr("hal0.api.hal0_chat_slot_model_ids", lambda sm: {"big"})

    data = client.get("/v1/models").json()["data"]
    ids = {r["id"] for r in data}
    assert "hal0/agent" not in ids, "virtual hal0/agent must not be advertised"
    assert "hal0/npu" not in ids, "virtual hal0/npu must not be advertised"
    assert "hal0/utility" not in ids, "virtual hal0/utility must not be advertised"


def test_slot_alias_names_advertised(client, monkeypatch):
    """Bare slot names (e.g. 'agent') ARE advertised via hal0_slot_alias_models."""
    sm = _FakeSlotManager()
    mr = _FakeModelRegistry()
    client.app.state.slot_manager = sm
    client.app.state.model_registry = mr
    monkeypatch.setattr("hal0.api.hal0_chat_slot_model_ids", lambda sm: {"big"})

    data = client.get("/v1/models").json()["data"]
    by_id = {r["id"]: r for r in data}
    assert "agent" in by_id, "slot alias 'agent' must be advertised"
    row = by_id["agent"]
    assert row["context_length"] == 65536
    assert row["owned_by"] == "hal0"
    assert "big" in row["name"]


def test_virtual_rows_do_not_duplicate(client, monkeypatch):
    """Slot aliases do not duplicate in /v1/models."""
    sm = _FakeSlotManager()
    mr = _FakeModelRegistry()
    client.app.state.slot_manager = sm
    client.app.state.model_registry = mr
    monkeypatch.setattr("hal0.api.hal0_chat_slot_model_ids", lambda sm: {"big"})

    ids = [r["id"] for r in client.get("/v1/models").json()["data"]]
    assert ids.count("agent") == 1


def test_legacy_primary_virtual_name_is_hidden(client, monkeypatch):
    """#654: hal0/primary was removed — it must NOT be advertised in /v1/models."""
    sm = _FakeSlotManager()
    mr = _FakeModelRegistry()
    client.app.state.slot_manager = sm
    client.app.state.model_registry = mr
    monkeypatch.setattr("hal0.api.hal0_chat_slot_model_ids", lambda sm: {"big"})

    ids = [r["id"] for r in client.get("/v1/models").json()["data"]]
    assert "hal0/primary" not in ids


def test_hal0_chat_not_advertised(client, monkeypatch):
    """hal0/chat was retired — never advertised in /v1/models."""
    sm = _FakeSlotManager()
    mr = _FakeModelRegistry()
    client.app.state.slot_manager = sm
    client.app.state.model_registry = mr
    monkeypatch.setattr("hal0.api.hal0_chat_slot_model_ids", lambda sm: {"big"})

    ids = [r["id"] for r in client.get("/v1/models").json()["data"]]
    assert "hal0/chat" not in ids
