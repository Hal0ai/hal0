"""SC-5: SlotManager.create() must not clobber an existing slot.

Before this guard, a second create() for an existing name overwrote the
on-disk TOML and force-reset state.json to OFFLINE — orphaning any
running container the previous config pointed at. create() now rejects a
duplicate with a typed SlotConfigError; the operator uses update to
modify an existing slot.

Internal reconcile callers (install/orchestrate, stack apply) pre-check
``cfg_path.exists()`` before calling create(), so they never reach the
new guard — the idempotency test below pins that contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotConfigError, SlotState


def _slot_toml(home: str, name: str) -> Path:
    return Path(home) / "etc" / "hal0" / "slots" / f"{name}.toml"


async def test_create_rejects_duplicate_and_preserves_config_and_state(
    tmp_hal0_home: str,
) -> None:
    """A second create() for the same name raises and touches nothing.

    Asserts three things the pre-guard code got wrong:
      1. the duplicate create() raises SlotConfigError (typed conflict),
      2. the on-disk TOML is UNCHANGED (port stays 8081, not 8082),
      3. state.json is NOT force-reset (a pre-set RUNNING-ish state
         survives instead of being stomped back to OFFLINE).
    """
    sm = SlotManager()
    await sm.create(
        "foo",
        {
            "name": "foo",
            "port": 8081,
            "device": "gpu-rocm",
            "type": "llm",
            "model": {"default": "qwen3-4b-q4_k_m"},
        },
    )

    # Simulate the slot having been loaded: drive it to a non-OFFLINE state
    # so we can prove the rejected create() does not force-reset it.
    await sm._transition("foo", SlotState.STARTING, force=True)
    assert sm.state("foo") == SlotState.STARTING

    with pytest.raises(SlotConfigError) as exc:
        await sm.create(
            "foo",
            {
                "name": "foo",
                "port": 8082,
                "device": "gpu-rocm",
                "type": "llm",
                "model": {"default": "qwen3-4b-q4_k_m"},
            },
        )
    assert exc.value.details["slot"] == "foo"

    # (2) TOML unchanged — the clobbering write never happened.
    cfg = await sm.get_config("foo")
    assert cfg["port"] == 8081

    # (3) state.json survived — no force transition back to OFFLINE.
    assert sm.state("foo") == SlotState.STARTING


async def test_reconcile_precheck_pattern_is_idempotent_noop(
    tmp_hal0_home: str,
) -> None:
    """Internal reconcile callers pre-check cfg_path.exists() → never reject.

    install/orchestrate + stack apply guard create() with an existence
    check. Mirroring that pattern, a second reconcile pass is a no-op: it
    skips create() entirely (so the SC-5 guard is never reached) and the
    original config is preserved.
    """
    sm = SlotManager()

    async def _ensure_slot(name: str, port: int) -> bool:
        # The pre-check every internal reconcile path performs before create.
        if sm._config_file(name).exists():
            return False
        await sm.create(
            name,
            {
                "name": name,
                "port": port,
                "device": "gpu-rocm",
                "type": "llm",
                "model": {"default": "qwen3-4b-q4_k_m"},
            },
        )
        return True

    assert await _ensure_slot("bar", 8083) is True
    # Second pass: pre-check short-circuits, create() (and its guard) is
    # never entered — no raise, config untouched.
    assert await _ensure_slot("bar", 8099) is False

    cfg = await sm.get_config("bar")
    assert cfg["port"] == 8083
    assert _slot_toml(tmp_hal0_home, "bar").exists()
