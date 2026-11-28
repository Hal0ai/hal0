"""``SlotManager.loaded_model_id``: what a slot serves, from its own state (#4017)."""

from __future__ import annotations

from pathlib import Path

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState

from .conftest import FakeContainerProvider


async def test_a_ready_slot_reports_the_model_it_loaded(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    sm = SlotManager()
    slot = await sm.load("chat")
    assert slot.state is SlotState.READY
    assert sm.loaded_model_id("chat") == slot.model_id != ""


async def test_a_slot_that_is_not_serving_reports_nothing(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    sm = SlotManager()
    assert sm.loaded_model_id("chat") == ""  # never loaded
    await sm.load("chat")
    await sm.unload("chat")
    assert sm.loaded_model_id("chat") == ""  # offline keeps no claim
    await sm.load("chat")
    await sm._transition("chat", SlotState.ERROR, message="x", force=True)
    assert sm.loaded_model_id("chat") == ""  # an errored slot answers nothing


def test_an_unknown_slot_reports_nothing_and_does_not_raise(slot_root: Path) -> None:
    assert SlotManager().loaded_model_id("no-such-slot") == ""
