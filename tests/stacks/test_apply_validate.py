"""PS-5: StackApplyEngine.validate flags unresolved profile/model refs.

A stack can reference a profile absent from the local catalog or a model id
that isn't registered on this host. Before this guard, plan() reported such a
stack as applying "cleanly" and the slot then failed at start — silent
divergence. validate() surfaces one advisory warning per unresolved reference
so the dry-run preview (and a real apply) can show it. Advisory only: apply
still proceeds; converge records its own per-slot lifecycle errors separately.
"""

from __future__ import annotations

from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.stacks.apply import StackApplyEngine


def _engine() -> StackApplyEngine:
    return StackApplyEngine()


def test_validate_flags_unknown_profile() -> None:
    stack = StackConfig(
        name="Forge",
        slots=[StackSlotEntry(slot="agent", model="known-model", profile="ghost-profile")],
    )
    warnings = _engine().validate(stack, known_profiles=set(), known_models={"known-model"})
    assert any("profile 'ghost-profile' not found" in w for w in warnings)
    assert all("model" not in w for w in warnings)


def test_validate_flags_unknown_model() -> None:
    stack = StackConfig(
        name="Forge",
        slots=[StackSlotEntry(slot="agent", model="ghost-model", profile="rocm")],
    )
    warnings = _engine().validate(stack, known_profiles={"rocm"}, known_models=set())
    assert any("model 'ghost-model' not in registry" in w for w in warnings)


def test_validate_clean_when_all_refs_resolve() -> None:
    stack = StackConfig(
        name="Forge",
        slots=[StackSlotEntry(slot="agent", model="m1", profile="p1")],
    )
    warnings = _engine().validate(stack, known_profiles={"p1"}, known_models={"m1"})
    assert warnings == []


def test_validate_ignores_entries_without_refs() -> None:
    """A slot entry carrying no profile/model ref can't diverge — no warning."""
    stack = StackConfig(name="Forge", slots=[StackSlotEntry(slot="agent")])
    warnings = _engine().validate(stack, known_profiles=set(), known_models=set())
    assert warnings == []
