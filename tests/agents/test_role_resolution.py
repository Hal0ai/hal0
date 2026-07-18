"""Unit tests for hal0.agents.role_resolution — the shared role→slot policy.

Covers the runtime role-slot map (``resolve_role_slots`` + ``generation_of``)
and the provision-time dict shapes (``build_auxiliary_tasks`` /
``build_delegation``) the provisioner delegates to. Same policy, two surfaces
— these tests pin both so the endpoint and the provisioner can never drift.
"""

from __future__ import annotations

from typing import Any

from hal0.agents import role_resolution as rr

_HAL0_V1 = "http://127.0.0.1:8080/v1"


def _slot(name: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"name": name, "type": "llm", "state": "ready"}
    base.update(kw)
    return base


# ── runtime role-slot map ────────────────────────────────────────────────────


def test_main_role_follows_primary_agent_slot_with_capability_basis() -> None:
    slots = [_slot("agent", model_id="m-agent", id=1, labels=["tools", "vision"])]
    roles = {r.role: r for r in rr.resolve_role_slots(slots, base_url=_HAL0_V1)}

    main = roles["main"]
    assert main.provider == "custom"
    assert main.slot == "agent"
    assert main.slot_id == 1
    assert main.model == "m-agent"
    assert main.alias == rr.MAIN_ANCHOR_ALIAS
    assert main.ready is True
    assert main.degraded is False
    assert main.capabilities == ("tools", "vision")  # capability basis surfaced


def test_main_role_degrades_when_no_chat_slot() -> None:
    roles = {r.role: r for r in rr.resolve_role_slots([], base_url=_HAL0_V1)}
    main = roles["main"]
    assert main.ready is False
    assert main.degraded is True
    assert main.alias == rr.MAIN_ANCHOR_ALIAS  # gateway still resolves the virtual
    assert main.fallback is not None


def test_vision_inherits_main_chat_model() -> None:
    slots = [_slot("agent", model_id="m-agent", id=1)]
    roles = {r.role: r for r in rr.resolve_role_slots(slots, base_url=_HAL0_V1)}
    vision = roles["vision"]
    assert vision.provider == "main"  # no dedicated slot; inherits chat model
    assert vision.model is None
    assert vision.degraded is False
    assert vision.ready is True  # main is ready


def test_utility_roles_route_to_utility_slot() -> None:
    slots = [
        _slot("agent", model_id="m-agent", id=1),
        _slot("utility", model_id="m-util", id=2, labels=["tools"]),
    ]
    roles = {r.role: r for r in rr.resolve_role_slots(slots, base_url=_HAL0_V1)}
    for role in rr.UTILITY_ROLES:
        entry = roles[role]
        assert entry.provider == "custom"
        assert entry.slot == "utility"
        assert entry.slot_id == 2
        assert entry.model == "m-util"
        assert entry.alias == "hal0/utility"
        assert entry.ready is True
        assert entry.degraded is False
        assert entry.capabilities == ("tools",)


def test_utility_roles_degrade_to_main_without_utility_slot() -> None:
    slots = [_slot("agent", model_id="m-agent", id=1)]
    roles = {r.role: r for r in rr.resolve_role_slots(slots, base_url=_HAL0_V1)}
    entry = roles["compression"]
    assert entry.provider == "main"  # degrade: inherit the chat model
    assert entry.model is None
    assert entry.degraded is True
    assert entry.fallback is not None


def test_utility_roles_fall_back_to_npu_slot() -> None:
    slots = [
        _slot("agent", model_id="m-agent", id=1),
        _slot("npu", model_id="m-npu", id=3, device="npu"),
    ]
    roles = {r.role: r for r in rr.resolve_role_slots(slots, base_url=_HAL0_V1)}
    entry = roles["mcp"]
    assert entry.provider == "custom"
    assert entry.model == rr.NPU_UTILITY_MODEL
    assert entry.alias == rr.NPU_UTILITY_MODEL
    assert entry.slot == "npu"
    assert entry.slot_id == 3
    assert entry.degraded is True  # served by NPU rather than a dedicated utility slot


def test_unready_slot_is_not_selected() -> None:
    # A utility slot that isn't ready must NOT be selected — the group degrades.
    slots = [
        _slot("agent", model_id="m-agent", id=1),
        _slot("utility", model_id="m-util", id=2, state="warming"),
    ]
    roles = {r.role: r for r in rr.resolve_role_slots(slots, base_url=_HAL0_V1)}
    assert roles["compression"].provider == "main"
    assert roles["compression"].degraded is True


def test_default_roles_cover_the_design_role_set() -> None:
    got = {r.role for r in rr.resolve_role_slots([], base_url=_HAL0_V1)}
    assert got == {
        "main",
        "vision",
        "compression",
        "session_search",
        "skills_hub",
        "mcp",
        "approval",
        "memory_flush",
    }


# ── generation stamping ──────────────────────────────────────────────────────


def test_generation_is_stable_for_identical_inventory() -> None:
    slots = [
        _slot("agent", model_id="m-agent", id=1),
        _slot("utility", model_id="m-util", id=2),
    ]
    g1 = rr.generation_of(rr.resolve_role_slots(slots, base_url=_HAL0_V1))
    g2 = rr.generation_of(rr.resolve_role_slots(list(slots), base_url=_HAL0_V1))
    assert g1 == g2


def test_generation_changes_on_model_swap() -> None:
    before = [_slot("agent", model_id="m-agent", id=1), _slot("utility", model_id="m-a", id=2)]
    after = [_slot("agent", model_id="m-agent", id=1), _slot("utility", model_id="m-b", id=2)]
    g_before = rr.generation_of(rr.resolve_role_slots(before, base_url=_HAL0_V1))
    g_after = rr.generation_of(rr.resolve_role_slots(after, base_url=_HAL0_V1))
    assert g_before != g_after


def test_generation_changes_on_readiness_transition() -> None:
    ready = [_slot("agent", model_id="m-agent", id=1), _slot("utility", model_id="m-u", id=2)]
    warming = [
        _slot("agent", model_id="m-agent", id=1),
        _slot("utility", model_id="m-u", id=2, state="warming"),
    ]
    assert rr.generation_of(rr.resolve_role_slots(ready, base_url=_HAL0_V1)) != rr.generation_of(
        rr.resolve_role_slots(warming, base_url=_HAL0_V1)
    )


# ── provision-time dict shapes (delegation targets) ──────────────────────────

_MAIN_TASKS = ("vision", "web_extract")
_UTILITY_TASKS = ("compression", "session_search", "title_generation", "skills_hub", "mcp")


def test_build_auxiliary_tasks_routes_utility_group_to_utility_slot() -> None:
    slots = [
        _slot("agent", model_id="m-agent", id=1),
        _slot("utility", model_id="m-util", id=2),
    ]
    aux = rr.build_auxiliary_tasks(
        slots, hal0_base_url=_HAL0_V1, main_tasks=_MAIN_TASKS, utility_tasks=_UTILITY_TASKS
    )
    assert aux["vision"] == {"provider": "main", "model": "", "base_url": ""}
    assert aux["compression"] == {
        "provider": "custom",
        "model": "m-util",
        "base_url": _HAL0_V1,
    }


def test_build_auxiliary_tasks_degrades_without_utility_slot() -> None:
    slots = [_slot("agent", model_id="m-agent", id=1)]
    aux = rr.build_auxiliary_tasks(
        slots, hal0_base_url=_HAL0_V1, main_tasks=_MAIN_TASKS, utility_tasks=_UTILITY_TASKS
    )
    assert aux["compression"] == {"provider": "main", "model": "", "base_url": ""}


def test_build_delegation_picks_agent_slot() -> None:
    slots = [_slot("agent", model_id="m-agent", id=1)]
    assert rr.build_delegation(slots, hal0_base_url=_HAL0_V1) == {
        "model": "m-agent",
        "base_url": _HAL0_V1,
        "provider": "custom",
    }


def test_build_delegation_none_when_agent_absent() -> None:
    slots = [_slot("utility", model_id="m-util", id=2)]
    assert rr.build_delegation(slots, hal0_base_url=_HAL0_V1) is None
