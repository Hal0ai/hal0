"""Belt-and-suspenders test for the modelless-READY guard in ``_transition``.

Even if a future code path tries to ``_transition(slot, READY, ...)``
with no ``model_id`` for a provider that needs one, the guard inside
``_transition`` must coerce the destination state to IDLE before
persisting.  This is the last line of defence — adoption + load() both
have their own checks, but the state machine itself shouldn't trust the
caller to never make this mistake.
"""

from __future__ import annotations

import json
from pathlib import Path

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState


async def test_transition_blocks_modelless_ready_for_llama_server(
    tmp_hal0_home: str,
) -> None:
    """READY + empty model_id + llama-server → coerced to IDLE on disk."""
    sm = SlotManager()
    await sm._transition(
        "primary",
        SlotState.READY,
        model_id=None,
        port=8081,
        extra={"backend": "vulkan", "provider": "llama-server"},
        force=True,
    )
    state_file = Path(tmp_hal0_home) / "var-lib" / "hal0" / "slots" / "primary" / "state.json"
    record = json.loads(state_file.read_text(encoding="utf-8"))
    assert record["state"] == "idle", (
        f"transition guard must coerce modelless READY to IDLE, got {record['state']}"
    )
    assert record["extra"].get("modelless_ready_blocked") is True


async def test_transition_allows_modelless_ready_for_kokoro(
    tmp_hal0_home: str,
) -> None:
    """Self-managed providers may persist READY without a model_id."""
    sm = SlotManager()
    await sm._transition(
        "tts",
        SlotState.READY,
        model_id=None,
        port=8084,
        extra={"backend": "vulkan", "provider": "kokoro"},
        force=True,
    )
    state_file = Path(tmp_hal0_home) / "var-lib" / "hal0" / "slots" / "tts" / "state.json"
    record = json.loads(state_file.read_text(encoding="utf-8"))
    assert record["state"] == "ready"
    assert record["extra"].get("modelless_ready_blocked") is not True


async def test_transition_allows_ready_with_model_id(
    tmp_hal0_home: str,
) -> None:
    """The guard must not interfere with a normal READY transition."""
    sm = SlotManager()
    await sm._transition(
        "primary",
        SlotState.READY,
        model_id="qwen3-4b-q4_k_m",
        port=8081,
        extra={"backend": "vulkan", "provider": "llama-server"},
        force=True,
    )
    state_file = Path(tmp_hal0_home) / "var-lib" / "hal0" / "slots" / "primary" / "state.json"
    record = json.loads(state_file.read_text(encoding="utf-8"))
    assert record["state"] == "ready"
    assert record["model_id"] == "qwen3-4b-q4_k_m"
