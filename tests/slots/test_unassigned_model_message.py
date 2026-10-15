"""#2694: a running slot whose model binding was emptied says so on /api/slots.

The ADR-0028 runner-system conversion unassigns a fork-only-quant model by
writing ``[model].default = ""``. The container keeps serving the old model
until the next restart, so ``status()`` still reports that ``model_id`` — and,
before this fix, ``message=None``: nothing user-facing said the binding was
gone.
"""

from __future__ import annotations

from pathlib import Path

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


def _empty_the_binding(slot_root: Path) -> None:
    path = slot_root / "chat.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('default = "qwen3-4b-q4_k_m"', 'default = ""'),
        encoding="utf-8",
    )


async def test_running_slot_with_an_emptied_binding_carries_a_message(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    sm = SlotManager()
    slot = await sm.load("chat")
    assert slot.state == SlotState.READY
    assert slot.model_id

    _empty_the_binding(slot_root)
    slot = await sm.status("chat")

    message = slot.metadata.get("message") or ""
    assert "no model assigned" in message
    assert slot.model_id in message
    assert "hal0 slot edit chat --model <id>" in message


async def test_a_bound_slot_gets_no_unassigned_message(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    sm = SlotManager()
    await sm.load("chat")
    slot = await sm.status("chat")
    assert "no model assigned" not in (slot.metadata.get("message") or "")


async def test_offline_slot_with_stale_model_id_is_not_said_to_be_serving(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """#2715: a stopped slot whose state record keeps an old model_id."""
    sm = SlotManager()
    await sm.load("chat")
    container_stub.active.discard("chat")
    # An offline record with a stale model_id and no message of its own.
    await sm._transition("chat", SlotState.OFFLINE, message="", force=True)
    _empty_the_binding(slot_root)

    slot = await sm.status("chat")

    assert slot.state == SlotState.OFFLINE
    message = slot.metadata.get("message") or ""
    assert "no model assigned" in message
    assert "still serving" not in message
