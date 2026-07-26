"""Model-owned slot-key partition guard (spec-hw-slot-ownership §1).

Symmetric to ``tests/slots/test_argv.py``'s SLOT_HARDWARE_FLAGS coverage, but
for the OTHER direction of the partition: a slot config write may no longer
set ``mtp`` / ``enable_thinking`` / ``vision`` — those are model-owned typed
capabilities now (``ModelDefaults.mtp`` / ``.enable_thinking`` / ``.vision``).
``reconcile_slot_updates`` is the single write-side chokepoint shared by
``SlotManager.update_config`` and the stacks apply engine
(``reconcile_and_guard_slot_config``), so testing it here covers both callers.
"""

from __future__ import annotations

import pytest

from hal0.slots.config_write import (
    MODEL_OWNED_SLOT_KEYS,
    _deny_model_owned_keys,
    reconcile_and_guard_slot_config,
    reconcile_slot_updates,
)
from hal0.slots.state import SlotConfigError

_BASE_CFG: dict[str, object] = {
    "name": "chat",
    "port": 8081,
    "device": "gpu-rocm",
    "model": {"default": "qwen3"},
}


def test_model_owned_slot_keys_covers_the_three_typed_capabilities() -> None:
    assert frozenset({"mtp", "enable_thinking", "vision"}) == MODEL_OWNED_SLOT_KEYS


@pytest.mark.parametrize(
    "key,value", [("mtp", True), ("enable_thinking", False), ("vision", False)]
)
def test_deny_model_owned_keys_rejects_each_key(key: str, value: object) -> None:
    with pytest.raises(SlotConfigError) as exc_info:
        _deny_model_owned_keys({key: value})
    assert exc_info.value.code == "slot.model_owned_key_denied"
    assert key in exc_info.value.message
    assert "model" in exc_info.value.message.lower()
    assert exc_info.value.details["keys"] == [key]


def test_deny_model_owned_keys_reports_every_offender() -> None:
    with pytest.raises(SlotConfigError) as exc_info:
        _deny_model_owned_keys({"mtp": True, "vision": False, "device": "cpu"})
    # "device" is not model-owned — only the two typed-capability keys offend,
    # reported sorted for a deterministic message.
    assert exc_info.value.details["keys"] == ["mtp", "vision"]


def test_deny_model_owned_keys_allows_clean_update() -> None:
    _deny_model_owned_keys({"device": "cpu", "threads": 8})  # must not raise


@pytest.mark.parametrize(
    "key,value", [("mtp", True), ("enable_thinking", False), ("vision", False)]
)
def test_reconcile_slot_updates_rejects_model_owned_keys(key: str, value: object) -> None:
    """The shared write chokepoint (SlotManager.update_config's entrypoint)
    hard-rejects before the merge ever runs."""
    with pytest.raises(SlotConfigError) as exc_info:
        reconcile_slot_updates(dict(_BASE_CFG), {key: value})
    assert exc_info.value.code == "slot.model_owned_key_denied"


def test_reconcile_slot_updates_allows_a_normal_update() -> None:
    merged = reconcile_slot_updates(dict(_BASE_CFG), {"threads": 8})
    assert merged["threads"] == 8
    assert merged["name"] == "chat"


def test_reconcile_and_guard_slot_config_also_rejects(tmp_path) -> None:
    """The stacks apply engine's shared entrypoint gets the same guard — a
    stack row can no longer smuggle a model-owned key onto a slot either."""
    with pytest.raises(SlotConfigError) as exc_info:
        reconcile_and_guard_slot_config(
            "chat", dict(_BASE_CFG), {"vision": True}, slots_dir=tmp_path
        )
    assert exc_info.value.code == "slot.model_owned_key_denied"


def test_reconcile_and_guard_slot_config_allows_a_normal_update(tmp_path) -> None:
    merged = reconcile_and_guard_slot_config(
        "chat", dict(_BASE_CFG), {"threads": 4}, slots_dir=tmp_path
    )
    assert merged["threads"] == 4
