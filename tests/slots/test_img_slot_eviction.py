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
from hal0.slots.preload_evict import _gather_candidates
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


def _enter_image_mode(sm: SlotManager) -> None:
    """Put the GPU arbiter in image mode, as a crashed-then-restarted api finds it."""
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
    assert sm.arbiter.mode is GpuMode.IMG


@pytest.mark.parametrize("down_by", ["manual-unload", "crash"])
async def test_img_slot_unloaded_or_crashed_in_image_mode_is_reloaded_by_ensure_img(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
    down_by: str,
) -> None:
    sm = await _img_manager(slot_root, container_stub)
    _enter_image_mode(sm)
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


# ── the idle reaper leaves the resident ComfyUI slot alone (#3050) ───────────


async def _ready_chat_and_age_both(
    sm: SlotManager, container_stub: FakeContainerProvider, img_name: str
) -> None:
    """Ready the stock ``chat`` slot and make it and the img slot long idle."""
    container_stub.active.add("chat")
    await sm._transition("chat", SlotState.READY, force=True)
    for name in ("chat", img_name):
        sm._last_used[sm._key(name)] = time.time() - 600


@pytest.mark.parametrize("name", ["img", "comfy"])  # identified by its group, not its name
async def test_ttl_sweep_keeps_the_img_slot_and_evicts_an_ordinary_idle_one(
    slot_root: Path, container_stub: FakeContainerProvider, name: str
) -> None:
    sm = await _img_manager(slot_root, container_stub, name=name, evict_after_s=1.0)
    await _ready_chat_and_age_both(sm, container_stub, name)

    await sm._sweep_idle_once()

    assert sm.is_ready_for_dispatch(name)
    assert sm.state("chat") == SlotState.OFFLINE
    assert [c.get("name") for c in container_stub.unload_calls] == ["chat"]


async def test_an_explicit_idle_timeout_does_not_make_the_img_slot_evictable(
    slot_root: Path, container_stub: FakeContainerProvider
) -> None:
    sm = await _img_manager(
        slot_root, container_stub, extra_toml="idle_timeout_s = 60\n", evict_after_s=1.0
    )
    await _ready_chat_and_age_both(sm, container_stub, "img")

    assert await sm._reaper.evict_timeout_for("img") is None
    await sm._sweep_idle_once()

    assert sm.is_ready_for_dispatch("img")
    assert sm.state("chat") == SlotState.OFFLINE


async def test_pressure_sweep_skips_the_img_slot(
    slot_root: Path, container_stub: FakeContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under pressure the sweep frees an ordinary idle slot and never img."""
    sm = await _img_manager(slot_root, container_stub, evict_pressure_mb=1024)
    await _ready_chat_and_age_both(sm, container_stub, "img")
    monkeypatch.setattr(sm, "_probe_host_free_mb", lambda: 0.0)  # always under the floor

    await sm._pressure_evict_once()

    assert sm.is_ready_for_dispatch("img")
    assert sm.state("chat") == SlotState.OFFLINE
    assert [c.get("name") for c in container_stub.unload_calls] == ["chat"]


@pytest.mark.parametrize("device", ["cpu", "npu"])
async def test_pressure_sweep_in_image_mode_evicts_an_idle_cpu_or_npu_slot_not_img(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
    device: str,
) -> None:
    """In image mode the arbiter has unloaded the GPU llm set, so the idle slots
    the sweep can still reclaim are the CPU/NPU ones. Freeing those and leaving
    img up is the accepted outcome: idle, img holds next to no GTT."""
    (slot_root / "side.toml").write_text(
        "\n".join(
            [
                'name = "side"',
                "port = 8090",
                f'device = "{device}"',
                'provider = "llama-server"',
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    sm = await _img_manager(slot_root, container_stub, evict_pressure_mb=1024)
    _enter_image_mode(sm)
    container_stub.active.add("side")
    await sm._transition("side", SlotState.READY, force=True)
    for name in ("side", "img"):
        sm._last_used[sm._key(name)] = time.time() - 600
    monkeypatch.setattr(sm, "_probe_host_free_mb", lambda: 0.0)  # always under the floor

    await sm._pressure_evict_once()

    assert sm.state("side") == SlotState.OFFLINE
    assert sm.is_ready_for_dispatch("img")
    assert [c.get("name") for c in container_stub.unload_calls] == ["side"]


@pytest.mark.parametrize("name", ["img", "comfy"])
async def test_pre_load_eviction_never_picks_the_img_slot(
    slot_root: Path, container_stub: FakeContainerProvider, name: str
) -> None:
    """The synchronous admission gate uses the reaper's rules, so it spares img too."""
    sm = await _img_manager(slot_root, container_stub, name=name)
    await _ready_chat_and_age_both(sm, container_stub, name)

    candidates = {c.name: c for c in await _gather_candidates(sm, exclude="embed")}

    assert candidates[name].eligible is False
    assert candidates[name].reason == "resident_img"
    assert candidates["chat"].eligible is True  # the ordinary idle slot is still a victim
