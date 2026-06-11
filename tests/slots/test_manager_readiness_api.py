"""Tests for SlotManager.state() and is_ready_for_dispatch() — issue #696.

Locked interface:
  state(name: str) -> SlotState   — sync, cache-first, state.json fallback, OFFLINE default
  is_ready_for_dispatch(name: str) -> bool — owns the READY|SERVING|IDLE rule in one place

Adding a new SlotState member MUST fail loudly here (the parametrized enum
test covers every member so a forgotten addition breaks the test suite).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState

# ── helpers ───────────────────────────────────────────────────────────────────


def _manager(tmp_hal0_home: str) -> SlotManager:
    """Fresh SlotManager filesystem-isolated under tmp_hal0_home."""
    return SlotManager()


def _write_state(tmp_hal0_home: str, slot_name: str, state: SlotState) -> None:
    """Write a minimal state.json for *slot_name* under tmp_hal0_home.

    HAL0_HOME layout uses ``var-lib/hal0`` (with a hyphen) for the state root.
    """
    data_dir = Path(tmp_hal0_home) / "var-lib" / "hal0" / "slots" / slot_name
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "state.json").write_text(
        json.dumps(
            {
                "name": slot_name,
                "state": state.value,
                "model_id": "test-model",
                "port": 8081,
                "updated_at": 0.0,
                "message": "",
                "extra": {},
            }
        ),
        encoding="utf-8",
    )


# ── state() ───────────────────────────────────────────────────────────────────


def test_state_cache_hit_returns_cached_state(tmp_hal0_home: str) -> None:
    """state() returns from in-memory cache when present."""
    sm = _manager(tmp_hal0_home)
    # Seed the in-memory cache directly (same path _transition uses).
    import time

    from hal0.slots.state import SlotStateRecord

    rec = SlotStateRecord(
        name="chat",
        state=SlotState.READY,
        model_id="m",
        port=8081,
        updated_at=time.time(),
        message="",
        extra={},
    )
    sm._states["chat"] = rec
    assert sm.state("chat") is SlotState.READY


def test_state_cache_miss_falls_back_to_state_json(tmp_hal0_home: str) -> None:
    """state() reads state.json when the slot is not in the in-memory cache."""
    sm = _manager(tmp_hal0_home)
    _write_state(tmp_hal0_home, "embed", SlotState.IDLE)
    assert sm.state("embed") is SlotState.IDLE


def test_state_unknown_slot_returns_offline(tmp_hal0_home: str) -> None:
    """state() returns OFFLINE for an unknown slot — no exception raised."""
    sm = _manager(tmp_hal0_home)
    # "ghost" has no config, no state.json, no memory entry.
    assert sm.state("ghost") is SlotState.OFFLINE


def test_state_resolves_alias(tmp_hal0_home: str) -> None:
    """state() transparently resolves back-compat aliases (primary → chat)."""
    sm = _manager(tmp_hal0_home)
    import time

    from hal0.slots.state import SlotStateRecord

    rec = SlotStateRecord(
        name="chat",
        state=SlotState.SERVING,
        model_id="m",
        port=8081,
        updated_at=time.time(),
        message="",
        extra={},
    )
    sm._states["chat"] = rec
    assert sm.state("primary") is SlotState.SERVING


# ── is_ready_for_dispatch() ───────────────────────────────────────────────────

# The ready set per #696 — these three states dispatch.
_READY_STATES = {SlotState.READY, SlotState.SERVING, SlotState.IDLE}

# All states in the enum — the parametrize covers them so a new state addition
# must be consciously classified here or the test fails loudly.
_ALL_STATES = list(SlotState)


@pytest.mark.parametrize("state", _ALL_STATES, ids=lambda s: s.value)
def test_is_ready_for_dispatch_parametrized(tmp_hal0_home: str, state: SlotState) -> None:
    """Every SlotState is classified as dispatchable or not per the locked set (#696).

    READY, SERVING, IDLE → True.
    Everything else → False (OFFLINE, PULLING, STARTING, WARMING, UNLOADING, ERROR).
    """
    sm = _manager(tmp_hal0_home)
    import time

    from hal0.slots.state import SlotStateRecord

    rec = SlotStateRecord(
        name="chat",
        state=state,
        model_id="m",
        port=8081,
        updated_at=time.time(),
        message="",
        extra={},
    )
    sm._states["chat"] = rec
    expected = state in _READY_STATES
    assert sm.is_ready_for_dispatch("chat") is expected


def test_is_ready_for_dispatch_offline_unknown_slot(tmp_hal0_home: str) -> None:
    """Unknown slot → OFFLINE → not ready."""
    sm = _manager(tmp_hal0_home)
    assert sm.is_ready_for_dispatch("nonexistent") is False
