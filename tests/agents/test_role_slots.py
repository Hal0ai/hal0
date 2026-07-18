from __future__ import annotations

from typing import Any

from hal0.agents.role_slots import RoleSlotCandidate, resolve_role_slots


def _slot(label: str, **values: Any) -> RoleSlotCandidate:
    defaults: dict[str, Any] = {
        "slot_id": f"slot-{label}",
        "label": label,
        "model": f"model-{label}",
        "ready": True,
        "capabilities": ("llm",),
    }
    defaults.update(values)
    return RoleSlotCandidate(**defaults)


def _entries(*slots: RoleSlotCandidate):
    return {entry.role: entry for entry in resolve_role_slots("hermes", slots).entries}


def test_returns_every_role_in_canonical_order() -> None:
    result = resolve_role_slots("hermes", [_slot("agent")])

    assert [entry.role for entry in result.entries] == [
        "main",
        "compression",
        "vision",
        "approval",
        "session_search",
        "memory_flush",
        "skills_hub",
        "mcp",
    ]


def test_utility_roles_prefer_ready_utility_slot() -> None:
    entries = _entries(_slot("agent"), _slot("utility", model="utility-model"))

    for role in ("compression", "approval", "session_search", "memory_flush", "skills_hub", "mcp"):
        entry = entries[role]
        assert entry.slot_id == "slot-utility"
        assert entry.model == "utility-model"
        assert entry.basis == "utility"


def test_utility_roles_fall_back_to_main() -> None:
    entries = _entries(_slot("agent", model="main-model"))

    assert entries["compression"].slot_id == "slot-agent"
    assert entries["compression"].model == "main-model"
    assert entries["compression"].basis == "main_fallback"
    assert entries["vision"].basis == "main"


def test_ready_npu_uses_virtual_model_address() -> None:
    entries = _entries(_slot("agent"), _slot("npu", model="physical-model", device_class="npu"))

    assert entries["mcp"].slot_id == "slot-npu"
    assert entries["mcp"].model == "hal0/npu"
    assert entries["mcp"].basis == "npu_virtual"


def test_rename_preserves_opaque_identity_and_changes_label() -> None:
    before = resolve_role_slots("hermes", [_slot("utility", slot_id="opaque-7")])
    after = resolve_role_slots(
        "hermes", [_slot("renamed", slot_id="opaque-7", role_hint="utility")]
    )

    before_entry = next(entry for entry in before.entries if entry.role == "compression")
    after_entry = next(entry for entry in after.entries if entry.role == "compression")
    assert before_entry.slot_id == after_entry.slot_id == "opaque-7"
    assert before_entry.label == "utility"
    assert after_entry.label == "renamed"


def test_model_swap_changes_advertised_model_and_generation() -> None:
    before = resolve_role_slots("hermes", [_slot("utility", model="model-a")])
    after = resolve_role_slots("hermes", [_slot("utility", model="model-b")])

    assert next(entry for entry in before.entries if entry.role == "mcp").model == "model-a"
    assert next(entry for entry in after.entries if entry.role == "mcp").model == "model-b"
    assert before.generation != after.generation


def test_generation_is_stable_for_same_normalized_mapping() -> None:
    first = resolve_role_slots("hermes", [_slot("agent"), _slot("utility")])
    second = resolve_role_slots("hermes", [_slot("utility"), _slot("agent")])

    assert first.generation == second.generation
