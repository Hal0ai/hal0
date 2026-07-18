from __future__ import annotations

from typing import Any

from hal0.agents.role_slots import (
    RoleSlotCandidate,
    candidate_from_slot_mapping,
    resolve_role_slots,
)


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


def test_agent_anchor_wins_over_legacy_main_labels() -> None:
    entries = _entries(_slot("primary"), _slot("chat"), _slot("agent"))

    assert entries["main"].label == "agent"


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


def test_ready_non_llm_utility_is_rejected() -> None:
    entries = _entries(_slot("agent"), _slot("utility", capabilities=("embedding",)))

    assert entries["compression"].basis == "main_fallback"


def test_ready_non_llm_npu_is_rejected() -> None:
    entries = _entries(
        _slot("agent"),
        _slot("npu", capabilities=("embedding",), device_class="npu"),
    )

    assert entries["mcp"].basis == "main_fallback"


def test_rename_preserves_opaque_identity_and_changes_label() -> None:
    before_candidate = candidate_from_slot_mapping(
        {
            "id": "opaque-7",
            "name": "utility",
            "role": "utility",
            "model_id": "model-u",
            "state": "ready",
            "type": "llm",
        }
    )
    after_candidate = candidate_from_slot_mapping(
        {
            "id": "opaque-7",
            "name": "renamed",
            "role": "utility",
            "model_id": "model-u",
            "state": "ready",
            "type": "llm",
        }
    )
    before = resolve_role_slots("hermes", [before_candidate])
    after = resolve_role_slots("hermes", [after_candidate])

    before_entry = next(entry for entry in before.entries if entry.role == "compression")
    after_entry = next(entry for entry in after.entries if entry.role == "compression")
    assert before_entry.slot_id == after_entry.slot_id == "opaque-7"
    assert before_entry.label == "utility"
    assert after_entry.label == "renamed"


def test_mapping_adapter_represents_missing_stable_id_as_none() -> None:
    candidate = candidate_from_slot_mapping(
        {"name": "utility", "model_id": "model-u", "state": "ready", "type": "llm"}
    )

    assert candidate.slot_id is None


def test_mapping_adapter_normalizes_scalar_capability() -> None:
    candidate = candidate_from_slot_mapping(
        {
            "id": 7,
            "name": "utility",
            "model": "model-u",
            "status": "online",
            "capabilities": "tools",
            "type": "llm",
        }
    )

    assert candidate.slot_id == "7"
    assert candidate.capabilities == ("llm", "tools")


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


def test_generation_is_agent_scoped() -> None:
    slots = [_slot("agent")]

    assert (
        resolve_role_slots("agent-a", slots).generation
        != resolve_role_slots("agent-b", slots).generation
    )


def test_generation_changes_for_each_advertised_mapping_input() -> None:
    baseline = resolve_role_slots("hermes", [_slot("agent"), _slot("utility", role_hint="utility")])
    changes = (
        _slot("utility", role_hint="utility", ready=False),
        _slot("utility", role_hint="utility", capabilities=("llm", "tools")),
        _slot("renamed", role_hint="utility", slot_id="slot-utility"),
        _slot("utility", role_hint="utility", slot_id="different-id"),
    )

    for changed in changes:
        assert (
            resolve_role_slots("hermes", [_slot("agent"), changed]).generation
            != baseline.generation
        )
