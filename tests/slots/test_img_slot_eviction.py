"""The img slot going down while the GPU arbiter stays in image mode (#3048).

``/v1/images/generations`` calls ``ensure_img()`` before it dispatches. In image
mode that call used to return at once, so an img slot that had stopped (a crash,
a manual unload) stayed down and the request answered 404 ``dispatch.no_route``
until someone loaded it by hand.

These tests use the real ``SlotManager`` and its real slot state, so they pin
the arbiter against what the manager reports and not against a stub. ``load`` is
patched on the instance: the ComfyUI load path is not under test here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from hal0.slots.arbiter import GpuMode
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


async def _img_manager(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    *,
    name: str = "img",
    extra_toml: str = "",
    **manager_kwargs: Any,
) -> SlotManager:
    """A real manager with a ComfyUI slot named ``name`` up and READY.

    ``extra_toml`` lands before the ``[model]`` table, so it can only hold
    top-level keys (``idle_timeout_s = 1``, ``pinned = true``, ...).
    """
    (slot_root / f"{name}.toml").write_text(
        "\n".join(
            [
                f'name = "{name}"',
                "port = 8188",
                'provider = "comfyui"',
                extra_toml,
                "[model]",
                'default = "sdxl-turbo"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    sm = SlotManager(**manager_kwargs)
    container_stub.active.add(name)
    await sm._transition(name, SlotState.READY, force=True)
    return sm


@pytest.mark.parametrize("down_by", ["manual-unload", "crash"])
async def test_img_slot_unloaded_or_crashed_in_image_mode_is_reloaded_by_ensure_img(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
    down_by: str,
) -> None:
    sm = await _img_manager(slot_root, container_stub)
    sm.arbiter.state_path.parent.mkdir(parents=True, exist_ok=True)
    sm.arbiter.state_path.write_text(
        json.dumps(
            {
                "mode": "img",
                "pinned": False,
                "saved_llm_slots": [],
                "last_img_activity": time.time(),
            }
        ),
        encoding="utf-8",
    )
    if down_by == "manual-unload":
        await sm.unload("img")  # terminate() deregisters the upstream; state OFFLINE
    else:
        container_stub.active.discard("img")
        await sm._transition("img", SlotState.ERROR, force=True)
    assert not sm.is_ready_for_dispatch("img")
    assert sm.arbiter.mode is GpuMode.IMG  # ...while the arbiter still says img

    loads: list[tuple[str, str | None]] = []

    async def _load(name: str, model_id: str | None = None) -> None:
        loads.append((name, model_id))
        await sm._transition(name, SlotState.READY, force=True)

    monkeypatch.setattr(sm, "load", _load)
    await sm.arbiter.ensure_img()

    assert loads == [("img", "sdxl-turbo")]
    assert sm.is_ready_for_dispatch("img")
