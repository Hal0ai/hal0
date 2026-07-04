"""Drift guard: the dispatchable ready-set has a single source of truth (DR-8).

Finding DR-8 unified four copies of the ``{READY, SERVING, IDLE}`` ready-set
onto ``hal0.slots.state.DISPATCHABLE_STATES``. These tests fail the moment any
copy diverges — by identity for the enum-set consumers, by value for the
string-set consumers, and by exhaustive membership so a newly-added
non-dispatchable SlotState is auto-caught.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hal0.slots.state import (
    DISPATCHABLE_STATES,
    SlotState,
    is_dispatchable_state,
)

# The three states that dispatch, expressed independently of the constant under
# test so a mistaken edit to the constant does not silently pass.
_EXPECTED_ENUM = {SlotState.READY, SlotState.SERVING, SlotState.IDLE}
_EXPECTED_STR = {"ready", "serving", "idle"}


# 1. Identity/equality — enum-set consumers alias the canonical frozenset.
def test_manager_aliases_canonical_set() -> None:
    from hal0.slots.manager import SlotManager

    assert SlotManager._DISPATCHABLE_STATES is DISPATCHABLE_STATES


def test_arbiter_aliases_canonical_set() -> None:
    import hal0.slots.arbiter as arbiter

    assert arbiter._DISPATCHABLE is DISPATCHABLE_STATES


def test_stacks_apply_aliases_canonical_set() -> None:
    import hal0.stacks.apply as apply

    assert apply._DISPATCHABLE is DISPATCHABLE_STATES


# 2. String-set alignment — the value-based consumers agree with the constant.
def test_canonical_set_values() -> None:
    assert {s.value for s in DISPATCHABLE_STATES} == _EXPECTED_STR
    assert DISPATCHABLE_STATES == _EXPECTED_ENUM


def test_slot_view_uses_shared_membership() -> None:
    """slot_view derives loaded models via the shared dispatchable set.

    Dispatchable states contribute their model; non-dispatchable ones don't.
    """
    from hal0.slot_view import loaded_model_names_from_slots

    dispatchable = [SimpleNamespace(state=s, model_id=f"m-{s.value}") for s in _EXPECTED_ENUM]
    non_dispatchable = [
        SimpleNamespace(state=SlotState.OFFLINE, model_id="m-offline"),
        SimpleNamespace(state=SlotState.WARMING, model_id="m-warming"),
    ]
    got = loaded_model_names_from_slots(dispatchable + non_dispatchable)
    assert got == {f"m-{s.value}" for s in _EXPECTED_ENUM}


def test_metrics_ready_set_matches() -> None:
    import hal0.slots.metrics as metrics

    assert set(metrics._READY_STATES) == _EXPECTED_STR


# 3. Exhaustive membership guard — every SlotState classified consistently.
@pytest.mark.parametrize("state", list(SlotState), ids=lambda s: s.value)
def test_membership_exhaustive(state: SlotState) -> None:
    expected = state in _EXPECTED_ENUM
    assert (state in DISPATCHABLE_STATES) is expected
    assert is_dispatchable_state(state) is expected
    # StrEnum membership also works for the lowercase wire string.
    assert (state.value in DISPATCHABLE_STATES) is expected
    assert is_dispatchable_state(state.value) is expected


# 4. Behaviour preservation for the orthogonal nuances (#792, #791).
def test_slot_view_serving_empty_cache_downgrades_to_idle() -> None:
    """#792/#31: a model-requiring serving slot with empty cache surfaces idle."""
    from hal0.slot_view import serialize_slot
    from hal0.slots.manager import Slot

    slot = Slot(
        "chat",
        state=SlotState.SERVING,
        port=8081,
        model_id="m",
        metadata={"provider": "llamacpp"},
    )
    view = serialize_slot(slot, model_cache={"chat": []})
    assert view["status"] == "idle"


def test_metrics_health_ok_false_forces_down() -> None:
    """#791: a serving slot with health_ok False reports up=0."""
    from hal0.slots.metrics import render_slot_metrics

    slot = SimpleNamespace(name="chat", state=SlotState.SERVING, metadata={"health_ok": False})
    body = render_slot_metrics([slot])
    assert 'hal0_slot_up{slot="chat"} 0' in body


# 5. Cross-file grep lint — no other module hardcodes the literal ready-set.
def test_no_duplicate_literals_outside_state_module() -> None:
    src_root = Path(__file__).resolve().parents[2] / "src" / "hal0"
    needles = (
        "{SlotState.READY, SlotState.SERVING, SlotState.IDLE}",
        '{"ready", "serving", "idle"}',
    )
    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        if path.name == "state.py" and path.parent.name == "slots":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                offenders.append(f"{path}: {needle}")
    assert not offenders, f"duplicate ready-set literals: {offenders}"
