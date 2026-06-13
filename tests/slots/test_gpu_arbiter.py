"""GpuArbiter — exclusive llm/img GPU group arbitration (Phase D, Task D4).

Spec §7 (locked): exclusive groups are DERIVED from slot configs, never
declared. The arbiter drains in-flight LLM requests, persists its target
state BEFORE unloading anything (crash-recovery ordering), flips the GPU to
the ComfyUI img slot, and can restore the saved LLM set later — including
after an api restart, via /var/lib/hal0/gpu_arbiter.json.

The fake SlotManager here is dict-driven, mirroring how the rest of
tests/slots stubs the manager boundary: state()/is_ready_for_dispatch()/
in_flight_count() read from dicts, load()/unload() record calls.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import pytest

import hal0.slots.arbiter as arbiter_mod
from hal0.errors import Hal0Error
from hal0.slots.arbiter import (
    ArbiterPinned,
    GpuArbiter,
    GpuImageMode,
    GpuInferenceMode,
    GpuMode,
    gpu_exclusive_group,
)

# ── fake SlotManager ─────────────────────────────────────────────────────────


def _base_configs() -> list[dict[str, Any]]:
    """Raw slot-config dicts shaped like SlotManager._load_slot_config output."""
    return [
        {
            "name": "chat",
            "device": "gpu-rocm",
            "runtime": "container",
            "provider": "llama-server",
            "type": "llm",
            "model": {"default": "qwen3-4b-q4_k_m"},
        },
        {
            "name": "agent",
            "device": "gpu-vulkan",
            "runtime": "container",
            "provider": "llama-server",
            "type": "llm",
            "model": {"default": "qwen3-4b-q4_k_m"},
        },
        {
            "name": "npu",
            "device": "npu",
            "runtime": "container",
            "provider": "flm",
            "type": "llm",
            "model": {"default": "gemma3:4b"},
        },
        {
            "name": "tts",
            "device": "cpu",
            "runtime": "container",
            "provider": "kokoro",
            "type": "tts",
            "model": {"default": "kokoro-v1"},
        },
        {
            "name": "img",
            "device": "gpu-rocm",
            "runtime": "container",
            "provider": "comfyui",
            "type": "image",
            "model": {"default": "sdxl-turbo"},
            "image": {"idle_restore_minutes": 5},
        },
    ]


class FakeManager:
    """Dict-driven SlotManager stub.

    ``in_flight`` values may be an int (constant) or a list consumed one
    entry per ``in_flight_count()`` poll (last entry sticks) — lets tests
    script a drain curve like ``[2, 1, 0]``.
    """

    def __init__(
        self,
        configs: list[dict[str, Any]] | None = None,
        *,
        ready: set[str] | None = None,
        in_flight: dict[str, Any] | None = None,
        slot_states: dict[str, Any] | None = None,
    ) -> None:
        self.configs = configs if configs is not None else _base_configs()
        self.ready: set[str] = set(ready or {"chat", "agent", "npu", "tts"})
        self.in_flight: dict[str, Any] = dict(in_flight or {})
        self.calls: list[tuple[Any, ...]] = []
        self.poll_log: list[tuple[str, int]] = []
        self.on_unload = None  # optional hook(name) fired before recording
        self.fail_loads: set[str] = set()  # load(name) raises for these slots
        # slot_states: name → str or list[str] of states consumed per state()
        # poll (last entry sticks). When set for a slot, load() does NOT
        # auto-add to ready — the caller drives state via the sequence.
        self.slot_states: dict[str, Any] = dict(slot_states or {})

    async def iter_configs(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self.configs]

    def is_ready_for_dispatch(self, name: str) -> bool:
        return name in self.ready

    def state(self, name: str) -> str:
        if name in self.slot_states:
            val = self.slot_states[name]
            if isinstance(val, list):
                s = str(val.pop(0)) if len(val) > 1 else str(val[0])
            else:
                s = str(val)
            return s
        return "ready" if name in self.ready else "offline"

    def in_flight_count(self, name: str) -> int:
        val = self.in_flight.get(name, 0)
        if isinstance(val, list):
            count = int(val.pop(0)) if len(val) > 1 else int(val[0])
        else:
            count = int(val)
        self.poll_log.append((name, count))
        return count

    async def load(self, name: str, model_id: str | None = None) -> None:
        if name in self.fail_loads:
            self.calls.append(("load_failed", name, model_id))
            raise RuntimeError(f"load failed: {name}")
        self.calls.append(("load", name, model_id))
        # Only auto-add to ready when not scripted via slot_states (the
        # slot_states dict drives the readiness sequence for that slot).
        if name not in self.slot_states:
            self.ready.add(name)

    async def unload(self, name: str) -> None:
        if self.on_unload is not None:
            self.on_unload(name)
        self.calls.append(("unload", name))
        self.ready.discard(name)


@pytest.fixture
def fake_mgr() -> FakeManager:
    return FakeManager()


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "gpu_arbiter.json"


@pytest.fixture(autouse=True)
def _fast_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Speed the drain poll up for tests; timeout stays generous."""
    monkeypatch.setattr(arbiter_mod, "_DRAIN_POLL_S", 0.01)


@pytest.fixture(autouse=True)
def comfyui_http(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the arbiter's ComfyUI HTTP seams (resident-container design).

    ``free`` counts /free calls (restore_llm frees models instead of
    unloading the container); ``queue`` scripts the (running, pending)
    counts the idle tick reads from /queue — ``None`` = unreachable.
    """
    rec: dict[str, Any] = {"free": 0, "queue": None}

    async def _fake_free() -> bool:
        rec["free"] += 1
        return True

    async def _fake_queue() -> tuple[int, int] | None:
        return rec["queue"]

    monkeypatch.setattr(arbiter_mod, "_comfyui_free", _fake_free, raising=False)
    monkeypatch.setattr(arbiter_mod, "_comfyui_queue_counts", _fake_queue, raising=False)
    return rec


def _read_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ── group derivation ─────────────────────────────────────────────────────────


def test_group_derivation() -> None:
    assert (
        gpu_exclusive_group({"device": "gpu-rocm", "runtime": "container", "provider": "comfyui"})
        == "img"
    )
    assert gpu_exclusive_group({"device": "gpu-vulkan", "runtime": "container"}) == "llm"
    assert gpu_exclusive_group({"device": "npu", "runtime": "container"}) is None
    # cpu container slots (tts) are never arbitrated
    assert gpu_exclusive_group({"device": "cpu", "runtime": "container"}) is None
    # img can be signalled by profile or type too
    assert (
        gpu_exclusive_group({"device": "gpu-rocm", "runtime": "container", "profile": "comfyui"})
        == "img"
    )
    assert (
        gpu_exclusive_group({"device": "gpu-rocm", "runtime": "container", "type": "image"})
        == "img"
    )
    # missing runtime defaults to container → GPU slots are arbitrated
    assert gpu_exclusive_group({"device": "gpu-rocm"}) == "llm"
    # profile-only GPU slots ARE containers (profile is the primary
    # signal, wins regardless of runtime) → arbitrated
    assert gpu_exclusive_group({"device": "gpu-vulkan", "profile": "vulkan"}) == "llm"
    assert gpu_exclusive_group({"device": "gpu-rocm", "profile": "comfyui"}) == "img"
    # profile-only on a non-GPU device still never arbitrated
    assert gpu_exclusive_group({"device": "npu", "profile": "flm"}) is None


# ── ensure_img ───────────────────────────────────────────────────────────────


async def test_ensure_img_drains_then_stops_then_starts(
    fake_mgr: FakeManager, state_path: Path
) -> None:
    fake_mgr.in_flight = {"chat": [2, 1, 0]}
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    seen_at_unload: list[dict[str, Any]] = []

    def _on_unload(name: str) -> None:
        # state file written BEFORE the first unload (crash-recovery ordering)
        assert state_path.exists(), "state must be persisted before unloads begin"
        seen_at_unload.append(_read_state(state_path))
        # drain completed before any unload
        assert fake_mgr.in_flight["chat"] == [0]

    fake_mgr.on_unload = _on_unload
    await arb.ensure_img()

    # drain polled chat down 2 → 1 → 0 before any unload
    chat_polls = [c for (n, c) in fake_mgr.poll_log if n == "chat"]
    assert chat_polls[:3] == [2, 1, 0]
    # order: unload llm slots (config order), then load img with its default
    assert fake_mgr.calls == [
        ("unload", "chat"),
        ("unload", "agent"),
        ("load", "img", "sdxl-turbo"),
    ]
    # npu/tts (non-llm groups) untouched — only ever arbitrate GPU slots
    assert all(c[1] not in {"npu", "tts"} for c in fake_mgr.calls)

    persisted = seen_at_unload[0]
    assert persisted["mode"] == "img"
    assert set(persisted["saved_llm_slots"]) == {"chat", "agent"}

    assert arb.mode is GpuMode.IMG
    assert set(arb.saved_llm_slots) == {"chat", "agent"}
    final = _read_state(state_path)
    assert final["mode"] == "img"
    assert isinstance(final["last_img_activity"], float)


async def test_ensure_img_noop_when_already_img(fake_mgr: FakeManager, state_path: Path) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.ensure_img()
    calls_after_first = list(fake_mgr.calls)

    await arb.ensure_img()
    assert fake_mgr.calls == calls_after_first  # no second flip
    assert arb.pinned is False

    # pin is still applied on the no-op path
    await arb.ensure_img(pin=True)
    assert fake_mgr.calls == calls_after_first
    assert arb.pinned is True
    assert _read_state(state_path)["pinned"] is True


async def test_ensure_img_skips_load_when_img_already_running(
    fake_mgr: FakeManager, state_path: Path
) -> None:
    """Resident-container design: the ComfyUI container stays up across
    modes, so when the img slot is already dispatchable the switch only
    unloads the LLM set — no container load, no readiness poll."""
    fake_mgr.ready.add("img")
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    await arb.ensure_img()

    assert fake_mgr.calls == [("unload", "chat"), ("unload", "agent")]
    assert arb.mode is GpuMode.IMG
    assert isinstance(_read_state(state_path)["last_img_activity"], float)


async def test_ensure_img_restamps_activity_at_completion(
    fake_mgr: FakeManager, state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The idle window must start when the switch COMPLETES, not when it
    begins — a slow container start used to eat most of the window."""
    fake_mgr.slot_states = {"img": ["warming", "warming", "ready"]}
    monkeypatch.setattr(arbiter_mod, "_READY_POLL_S", 0.05)
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    stamps: list[float] = []
    fake_mgr.on_unload = lambda _name: stamps.append(_read_state(state_path)["last_img_activity"])

    await arb.ensure_img()

    final = _read_state(state_path)["last_img_activity"]
    assert final > stamps[0], "activity must be re-stamped after readiness"


async def test_drain_timeout_proceeds(
    fake_mgr: FakeManager,
    state_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_mgr.in_flight = {"chat": 1}  # stuck forever
    monkeypatch.setattr(arbiter_mod, "_DRAIN_TIMEOUT_S", 0.05)
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    with caplog.at_level(logging.WARNING, logger="hal0.slots.arbiter"):
        await arb.ensure_img()

    assert any("drain_timeout" in rec.message for rec in caplog.records)
    # proceeded anyway: llm slots unloaded, img loaded
    assert ("unload", "chat") in fake_mgr.calls
    assert ("unload", "agent") in fake_mgr.calls
    assert fake_mgr.calls[-1] == ("load", "img", "sdxl-turbo")
    assert arb.mode is GpuMode.IMG


# ── img-load failure rollback (D5 quality gate I1) ──────────────────────────


async def test_img_load_failure_rolls_back_llm_set(
    fake_mgr: FakeManager, state_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """img load raises AFTER llm unloads → saved set reloaded, mode=llm
    persisted, original exception propagates, retry re-attempts the switch."""
    fake_mgr.fail_loads = {"img"}
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    with (
        caplog.at_level(logging.WARNING, logger="hal0.slots.arbiter"),
        pytest.raises(RuntimeError, match="load failed: img"),
    ):
        await arb.ensure_img()

    assert fake_mgr.calls == [
        ("unload", "chat"),
        ("unload", "agent"),
        ("load_failed", "img", "sdxl-turbo"),
        ("load", "chat", None),
        ("load", "agent", None),
    ]
    assert any("img_load_failed_rolled_back" in rec.message for rec in caplog.records)
    assert arb.mode is GpuMode.LLM
    final = _read_state(state_path)
    assert final["mode"] == "llm"
    assert final["saved_llm_slots"] == []
    # guard must NOT be wedged
    arb.guard_dispatch("chat")

    # a retried image request re-attempts the FULL switch (no IMG no-op
    # short-circuit against a dead img port)
    fake_mgr.fail_loads = set()
    fake_mgr.calls.clear()
    await arb.ensure_img()
    assert fake_mgr.calls[-1] == ("load", "img", "sdxl-turbo")
    assert arb.mode is GpuMode.IMG


async def test_img_load_failure_rollback_load_also_fails(
    fake_mgr: FakeManager, state_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """img load raises AND one rollback load raises → mode=llm STILL
    persisted (guard never wedges), original img exception propagates."""
    fake_mgr.fail_loads = {"img", "chat"}
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    with (
        caplog.at_level(logging.WARNING, logger="hal0.slots.arbiter"),
        pytest.raises(RuntimeError, match="load failed: img"),
    ):
        await arb.ensure_img()

    # rollback continued past the failed chat load and restored agent
    assert ("load_failed", "chat", None) in fake_mgr.calls
    assert ("load", "agent", None) in fake_mgr.calls
    assert any("rollback_load_failed" in rec.message for rec in caplog.records)
    assert arb.mode is GpuMode.LLM
    assert _read_state(state_path)["mode"] == "llm"
    arb.guard_dispatch("chat")  # not wedged


# ── restore_llm ──────────────────────────────────────────────────────────────


async def test_restore_llm_frees_comfyui_and_reloads_saved_set(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any]
) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.ensure_img()
    fake_mgr.calls.clear()

    await arb.restore_llm()

    # ComfyUI models freed via POST /free FIRST — the container itself is
    # NEVER unloaded (resident design: the web UI stays up in llm mode) —
    # then the saved set reloaded (default models).
    assert comfyui_http["free"] == 1
    assert ("unload", "img") not in fake_mgr.calls
    assert fake_mgr.calls == [("load", "chat", None), ("load", "agent", None)]
    assert arb.mode is GpuMode.LLM
    final = _read_state(state_path)
    assert final["mode"] == "llm"
    assert final["saved_llm_slots"] == []


async def test_restore_llm_noop_in_llm_mode(fake_mgr: FakeManager, state_path: Path) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.restore_llm()
    assert fake_mgr.calls == []
    assert arb.mode is GpuMode.LLM


async def test_restore_blocked_when_pinned_unless_force(
    fake_mgr: FakeManager, state_path: Path
) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.ensure_img(pin=True)
    fake_mgr.calls.clear()

    with pytest.raises(ArbiterPinned) as ei:
        await arb.restore_llm()
    assert ei.value.code == "gpu.pinned"
    assert ei.value.status == 409
    assert fake_mgr.calls == []  # nothing touched
    assert arb.mode is GpuMode.IMG

    await arb.restore_llm(force=True)
    assert arb.mode is GpuMode.LLM
    assert arb.pinned is False  # force-restore clears the pin
    assert fake_mgr.calls[0] == ("load", "chat", None)  # container never unloaded


# ── concurrency: one lock, no interleaved flips ──────────────────────────────


async def test_concurrent_ensure_img_and_restore_serialize(
    fake_mgr: FakeManager, state_path: Path
) -> None:
    """ensure_img + restore_llm fired concurrently: the switch lock
    serializes them — full img switch first, then full restore, with zero
    interleaved load/unload recorder entries."""
    # drain curve forces awaits INSIDE the locked ensure_img section so the
    # restore task genuinely contends for the lock
    fake_mgr.in_flight = {"chat": [1, 1, 0]}
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    await asyncio.gather(arb.ensure_img(), arb.restore_llm())

    assert fake_mgr.calls == [
        ("unload", "chat"),
        ("unload", "agent"),
        ("load", "img", "sdxl-turbo"),
        ("load", "chat", None),
        ("load", "agent", None),
    ]
    assert arb.mode is GpuMode.LLM
    assert arb.saved_llm_slots == ()
    assert _read_state(state_path)["mode"] == "llm"


# ── persistence across restarts ──────────────────────────────────────────────


async def test_state_survives_restart(fake_mgr: FakeManager, state_path: Path) -> None:
    arb1 = GpuArbiter(fake_mgr, state_path=state_path)
    await arb1.ensure_img()

    # new process: fresh arbiter over the same state file
    fake2 = FakeManager()
    fake2.ready = {"img"}
    arb2 = GpuArbiter(fake2, state_path=state_path)
    assert arb2.mode is GpuMode.IMG
    assert set(arb2.saved_llm_slots) == {"chat", "agent"}

    await arb2.restore_llm()
    assert ("unload", "img") not in fake2.calls  # resident container stays up
    assert ("load", "chat", None) in fake2.calls
    assert ("load", "agent", None) in fake2.calls
    assert arb2.mode is GpuMode.LLM


async def test_partial_crash_recovery_via_restore(state_path: Path) -> None:
    """Crash mid-switch: mode=img + saved set persisted (pre-unload ordering),
    llm slots unloaded, img never loaded. A fresh arbiter over that state
    file restores the saved set cleanly."""
    state_path.write_text(
        json.dumps(
            {
                "mode": "img",
                "pinned": False,
                "saved_llm_slots": ["chat", "agent"],
                "last_img_activity": time.time(),
            }
        ),
        encoding="utf-8",
    )
    mgr = FakeManager()
    mgr.ready = set()  # nothing survived the crash
    arb = GpuArbiter(mgr, state_path=state_path)
    assert arb.mode is GpuMode.IMG
    assert set(arb.saved_llm_slots) == {"chat", "agent"}

    await arb.restore_llm()

    assert mgr.calls == [
        ("load", "chat", None),
        ("load", "agent", None),
    ]
    assert arb.mode is GpuMode.LLM
    assert _read_state(state_path)["mode"] == "llm"


def test_corrupt_state_file_falls_back_to_llm(fake_mgr: FakeManager, state_path: Path) -> None:
    state_path.write_text("{not json", encoding="utf-8")
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    assert arb.mode is GpuMode.LLM
    assert arb.saved_llm_slots == ()
    arb.guard_dispatch("chat")  # must not raise


# ── guard_dispatch ───────────────────────────────────────────────────────


def _write_img_state(path: Path, *, last_activity: float | None = None) -> None:
    path.write_text(
        json.dumps(
            {
                "mode": "img",
                "pinned": False,
                "saved_llm_slots": ["chat", "agent"],
                "last_img_activity": time.time() if last_activity is None else last_activity,
            }
        ),
        encoding="utf-8",
    )


def test_guard_dispatch_raises_in_img_mode(
    fake_mgr: FakeManager, state_path: Path, tmp_hal0_home: str
) -> None:
    _write_img_state(state_path)
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    with pytest.raises(GpuImageMode) as ei:
        arb.guard_dispatch("chat")
    assert ei.value.code == "gpu.image_mode"
    assert ei.value.status == 503
    assert ei.value.details["retry_after_s"] >= 15
    assert ei.value.details["slot"] == "chat"

    # non-llm-group slots always pass
    arb.guard_dispatch("npu")
    arb.guard_dispatch("tts")

    # mode LLM → everything passes
    llm_path = state_path.parent / "llm_state.json"
    arb_llm = GpuArbiter(fake_mgr, state_path=llm_path)
    arb_llm.guard_dispatch("chat")
    arb_llm.guard_dispatch("npu")


def test_guard_retry_after_floor(
    fake_mgr: FakeManager, state_path: Path, tmp_hal0_home: str
) -> None:
    # idle window long past → floor of 15s
    _write_img_state(state_path, last_activity=time.time() - 10_000)
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    with pytest.raises(GpuImageMode) as ei:
        arb.guard_dispatch("agent")
    assert ei.value.details["retry_after_s"] == 15


def test_guard_uses_derived_group_from_slot_toml(
    fake_mgr: FakeManager, state_path: Path, tmp_hal0_home: str
) -> None:
    """Restart case: a GPU-container slot NOT in the saved set is still guarded
    when its on-disk TOML derives to the llm group."""
    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "utility.toml").write_text(
        "\n".join(
            [
                'name = "utility"',
                'type = "llm"',
                'device = "gpu-vulkan"',
                'runtime = "container"',
                "port = 8081",
                "[model]",
                'default = "gemma-4-12b-it"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_img_state(state_path)
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    with pytest.raises(GpuImageMode):
        arb.guard_dispatch("utility")


def test_guard_dispatch_blocks_img_slot_in_llm_mode(
    fake_mgr: FakeManager, state_path: Path, tmp_hal0_home: str
) -> None:
    """Resident img container: the slot stays READY in llm mode, so the
    dispatch guard is the only thing stopping an image generation from
    running while the LLM set holds the GPU — refuse with a typed 503
    telling the caller to flip the switch first."""
    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "img.toml").write_text(
        "\n".join(
            [
                'name = "img"',
                'type = "image"',
                'provider = "comfyui"',
                'device = "gpu-rocm"',
                'runtime = "container"',
                "port = 8188",
                "",
            ]
        ),
        encoding="utf-8",
    )
    arb = GpuArbiter(fake_mgr, state_path=state_path)  # default llm mode

    with pytest.raises(GpuInferenceMode) as ei:
        arb.guard_dispatch("img")
    assert ei.value.code == "gpu.inference_mode"
    assert ei.value.status == 503
    assert ei.value.details["slot"] == "img"

    # llm-group + non-arbitrated slots pass untouched in llm mode
    arb.guard_dispatch("npu")
    arb.guard_dispatch("tts")


# ── activity / pin / status ──────────────────────────────────────────────────


async def test_touch_and_status(fake_mgr: FakeManager, state_path: Path) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path, idle_restore_minutes=7)
    st = arb.status()
    assert st == {
        "mode": "llm",
        "pinned": False,
        "saved_llm_slots": [],
        "idle_restore_at": None,
    }

    await arb.ensure_img()
    before = time.time()
    arb.touch_img_activity()
    st = arb.status()
    assert st["mode"] == "img"
    assert st["idle_restore_at"] is not None
    assert st["idle_restore_at"] >= before + 7 * 60 - 1
    assert _read_state(state_path)["last_img_activity"] >= before

    # pinned → no auto-restore window advertised
    arb.set_pin(True)
    assert arb.status()["pinned"] is True
    assert arb.status()["idle_restore_at"] is None
    arb.set_pin(False)
    assert arb.status()["idle_restore_at"] is not None


# ── idle-restore loop (D6) ───────────────────────────────────────────────────

#: ~0.05s idle window expressed in minutes (idle_restore_minutes * 60 = secs).
_TINY_WINDOW_MIN = 0.05 / 60


async def _img_mode_arbiter(fake_mgr: FakeManager, state_path: Path) -> GpuArbiter:
    """Arbiter already flipped to IMG with a tiny idle window, calls cleared."""
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.ensure_img()
    arb.idle_restore_minutes = _TINY_WINDOW_MIN
    fake_mgr.calls.clear()
    return arb


async def _cancel_loop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_idle_restore_fires_after_window(fake_mgr: FakeManager, state_path: Path) -> None:
    """Past the idle window the loop restores the LLM set EXACTLY once and
    keeps ticking (mode flips to LLM so later ticks are no-ops)."""
    arb = await _img_mode_arbiter(fake_mgr, state_path)

    task = asyncio.create_task(arb.run_idle_loop(interval_s=0.01))
    try:
        await asyncio.sleep(0.3)
        assert ("unload", "img") not in fake_mgr.calls  # container stays up
        assert fake_mgr.calls.count(("load", "chat", None)) == 1  # restored ONCE
        assert ("load", "agent", None) in fake_mgr.calls
        assert arb.mode is GpuMode.LLM
        assert not task.done(), "loop must stay alive after restoring"
    finally:
        await _cancel_loop(task)


async def test_idle_restore_skipped_when_pinned(fake_mgr: FakeManager, state_path: Path) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.ensure_img(pin=True)
    arb.idle_restore_minutes = _TINY_WINDOW_MIN
    fake_mgr.calls.clear()

    task = asyncio.create_task(arb.run_idle_loop(interval_s=0.01))
    try:
        await asyncio.sleep(0.2)
        assert fake_mgr.calls == []  # nothing unloaded/loaded
        assert arb.mode is GpuMode.IMG
        assert not task.done()
    finally:
        await _cancel_loop(task)


async def test_idle_restore_skipped_when_window_zero(
    fake_mgr: FakeManager, state_path: Path
) -> None:
    """idle_restore_minutes=0 → manual-only restore (#599 schema), never auto."""
    arb = GpuArbiter(fake_mgr, state_path=state_path, idle_restore_minutes=0)
    await arb.ensure_img()
    # backdate activity far past ANY window so a buggy 0-window would fire
    arb._load_state()["last_img_activity"] = time.time() - 10_000
    arb._persist()
    fake_mgr.calls.clear()

    task = asyncio.create_task(arb.run_idle_loop(interval_s=0.01))
    try:
        await asyncio.sleep(0.2)
        assert fake_mgr.calls == []
        assert arb.mode is GpuMode.IMG
        assert not task.done()
    finally:
        await _cancel_loop(task)


async def test_in_flight_img_job_defers_restore(fake_mgr: FakeManager, state_path: Path) -> None:
    """An in-flight img job (long Wan video render) defers the restore even
    past the window; it fires once the job count drops to zero."""
    arb = await _img_mode_arbiter(fake_mgr, state_path)
    fake_mgr.in_flight = {"img": 1}

    task = asyncio.create_task(arb.run_idle_loop(interval_s=0.01))
    try:
        await asyncio.sleep(0.2)
        assert fake_mgr.calls == []  # restore deferred
        assert arb.mode is GpuMode.IMG

        fake_mgr.in_flight = {"img": 0}  # render finished
        await asyncio.sleep(0.2)
        assert fake_mgr.calls.count(("load", "chat", None)) == 1
        assert arb.mode is GpuMode.LLM
    finally:
        await _cancel_loop(task)


async def test_idle_tick_defers_and_restamps_when_comfyui_queue_busy(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any]
) -> None:
    """Renders queued straight into ComfyUI's web UI never pass through the
    dispatcher (in_flight_count can't see them), so the idle tick must also
    consult ComfyUI's /queue: busy → defer the restore AND restart the idle
    window; drained → restore on the next eligible tick."""
    arb = await _img_mode_arbiter(fake_mgr, state_path)

    comfyui_http["queue"] = (1, 0)
    arb._load_state()["last_img_activity"] = time.time() - 10_000
    arb._persist()
    await arb._idle_tick()
    assert arb.mode is GpuMode.IMG
    assert fake_mgr.calls == []
    # busy queue re-stamped the activity → idle window restarted
    assert _read_state(state_path)["last_img_activity"] > time.time() - 5

    comfyui_http["queue"] = (0, 0)
    arb._load_state()["last_img_activity"] = time.time() - 10_000
    arb._persist()
    await arb._idle_tick()
    assert arb.mode is GpuMode.LLM
    assert comfyui_http["free"] == 1


async def test_idle_loop_survives_restore_exception(
    fake_mgr: FakeManager, state_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """restore_llm raising must not kill the loop: the error is logged and
    the restore succeeds on the next eligible tick."""
    arb = await _img_mode_arbiter(fake_mgr, state_path)
    fake_mgr.fail_loads = {"chat"}  # restore_llm raises mid-reload

    with caplog.at_level(logging.WARNING, logger="hal0.slots.arbiter"):
        task = asyncio.create_task(arb.run_idle_loop(interval_s=0.01))
        try:
            await asyncio.sleep(0.2)
            assert any("idle_loop_error" in rec.message for rec in caplog.records)
            assert arb.mode is GpuMode.IMG  # failed restore left mode persisted
            assert not task.done(), "loop must survive tick exceptions"

            fake_mgr.fail_loads = set()
            await asyncio.sleep(0.2)
            assert arb.mode is GpuMode.LLM
            assert ("load", "chat", None) in fake_mgr.calls
            assert ("load", "agent", None) in fake_mgr.calls
        finally:
            await _cancel_loop(task)


# ── manager wiring ───────────────────────────────────────────────────────────


def test_manager_owns_lazy_arbiter(tmp_hal0_home: str) -> None:
    from hal0.slots.manager import SlotManager

    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "img.toml").write_text(
        "\n".join(
            [
                'name = "img"',
                'type = "image"',
                'provider = "comfyui"',
                'device = "gpu-rocm"',
                'runtime = "container"',
                "port = 8188",
                "[model]",
                'default = "sdxl-turbo"',
                "[image]",
                "idle_restore_minutes = 7",
                "",
            ]
        ),
        encoding="utf-8",
    )
    sm = SlotManager()
    arb = sm.arbiter
    assert isinstance(arb, GpuArbiter)
    assert sm.arbiter is arb  # constructed once, cached
    # state path lives under the manager's var-lib root (HAL0_HOME-redirected)
    assert arb.state_path == Path(tmp_hal0_home) / "var-lib" / "hal0" / "gpu_arbiter.json"
    # idle_restore_minutes read from the img slot's [image] section
    assert arb.idle_restore_minutes == 7


def test_manager_arbiter_defaults_without_img_slot(tmp_hal0_home: str) -> None:
    from hal0.slots.manager import SlotManager

    sm = SlotManager()
    assert sm.arbiter.idle_restore_minutes == 60


def test_arbiter_default_idle_window_is_60(fake_mgr: FakeManager, state_path: Path) -> None:
    """Resident design default: an hour of UI time before auto-restore."""
    assert GpuArbiter(fake_mgr, state_path=state_path).idle_restore_minutes == 60


def _write_img_toml(tmp_hal0_home: str, idle_restore_minutes: str) -> None:
    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "img.toml").write_text(
        "\n".join(
            [
                'name = "img"',
                'type = "image"',
                'provider = "comfyui"',
                'device = "gpu-rocm"',
                'runtime = "container"',
                "port = 8188",
                "[model]",
                'default = "sdxl-turbo"',
                "[image]",
                f"idle_restore_minutes = {idle_restore_minutes}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_manager_arbiter_accepts_zero_window_from_toml(tmp_hal0_home: str) -> None:
    """TOML ``[image].idle_restore_minutes = 0`` reaches the arbiter as 0
    (manual-only restore, #599 schema) — NOT coerced to the default 60.
    Negatives/garbage still fall back to 60."""
    from hal0.slots.manager import SlotManager

    _write_img_toml(tmp_hal0_home, "0")
    assert SlotManager().arbiter.idle_restore_minutes == 0

    # invalid values still fall back to 60 (fresh managers: arbiter is cached)
    _write_img_toml(tmp_hal0_home, "-3")
    assert SlotManager().arbiter.idle_restore_minutes == 60
    _write_img_toml(tmp_hal0_home, "true")
    assert SlotManager().arbiter.idle_restore_minutes == 60
    _write_img_toml(tmp_hal0_home, '"soon"')
    assert SlotManager().arbiter.idle_restore_minutes == 60


async def test_zero_window_from_toml_never_auto_restores(tmp_hal0_home: str) -> None:
    """End-to-end #599 pin: the TOML-sourced 0 window feeds an arbiter whose
    idle loop never auto-restores (manual restore only)."""
    from hal0.slots.manager import SlotManager

    _write_img_toml(tmp_hal0_home, "0")
    fake_mgr = FakeManager()
    arb = GpuArbiter(
        fake_mgr,
        state_path=Path(tmp_hal0_home) / "gpu_arbiter.json",
        idle_restore_minutes=SlotManager()._img_idle_restore_minutes(),
    )
    assert arb.idle_restore_minutes == 0
    await arb.ensure_img()
    # backdate activity far past ANY window so a buggy coercion-to-5 (or a
    # 0-window treated as "always expired") would fire
    arb._load_state()["last_img_activity"] = time.time() - 10_000
    arb._persist()
    fake_mgr.calls.clear()

    task = asyncio.create_task(arb.run_idle_loop(interval_s=0.01))
    try:
        await asyncio.sleep(0.1)
        assert fake_mgr.calls == []
        assert arb.mode is GpuMode.IMG
        assert not task.done()
    finally:
        await _cancel_loop(task)


# ── img readiness poll after load (#714) ────────────────────────────────────


async def test_img_never_ready_rolls_back_on_timeout(
    fake_mgr: FakeManager,
    state_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """load() succeeds but img slot never reaches READY within the timeout →
    rollback fires: saved llm slots reloaded, mode=llm persisted, img
    best-effort unloaded, new Hal0Error with code gpu.img_not_ready raised."""
    # img state stays "warming" forever — simulate crash-at-start that never
    # transitions away from WARMING (or a stuck container).
    fake_mgr.slot_states = {"img": "warming"}
    monkeypatch.setattr(arbiter_mod, "_IMG_READY_TIMEOUT_S", 0.05)
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    with (
        caplog.at_level(logging.WARNING, logger="hal0.slots.arbiter"),
        pytest.raises(Hal0Error) as ei,
    ):
        await arb.ensure_img()

    # New error, correct code
    assert ei.value.code == "gpu.img_not_ready"

    # img unloaded (best-effort) then llm set reloaded
    assert ("unload", "img") in fake_mgr.calls
    assert ("load", "chat", None) in fake_mgr.calls
    assert ("load", "agent", None) in fake_mgr.calls

    # mode rolled back to llm
    assert arb.mode is GpuMode.LLM
    final = _read_state(state_path)
    assert final["mode"] == "llm"
    assert final["saved_llm_slots"] == []
    assert final["pinned"] is False

    # guard is not wedged
    arb.guard_dispatch("chat")

    # retry after rollback re-attempts the full switch
    fake_mgr.slot_states = {}  # next time img goes ready immediately via load()
    fake_mgr.calls.clear()
    await arb.ensure_img()
    assert arb.mode is GpuMode.IMG


async def test_img_eventually_ready_succeeds(
    fake_mgr: FakeManager,
    state_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """img is WARMING for first N polls then READY → ensure_img succeeds,
    no rollback, mode stays img."""
    fake_mgr.slot_states = {"img": ["warming", "warming", "ready"]}
    monkeypatch.setattr(arbiter_mod, "_IMG_READY_TIMEOUT_S", 5.0)
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    await arb.ensure_img()

    assert arb.mode is GpuMode.IMG
    # img loaded exactly once, no rollback loads
    assert fake_mgr.calls[-1] == ("load", "img", "sdxl-turbo")
    rollback_calls = [c for c in fake_mgr.calls if c[0] == "load" and c[1] in {"chat", "agent"}]
    assert rollback_calls == []
    final = _read_state(state_path)
    assert final["mode"] == "img"


async def test_img_terminal_error_rolls_back_immediately(
    fake_mgr: FakeManager,
    state_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """img reaches ERROR state → rollback fires without burning the full
    timeout; saved llm set reloaded, gpu.img_not_ready raised."""
    # ERROR is terminal — arbiter must abort the poll immediately.
    fake_mgr.slot_states = {"img": ["warming", "error"]}
    # Set a generous timeout; the test must not need to wait for it.
    monkeypatch.setattr(arbiter_mod, "_IMG_READY_TIMEOUT_S", 30.0)
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    import time as _time

    start = _time.monotonic()
    with (
        caplog.at_level(logging.WARNING, logger="hal0.slots.arbiter"),
        pytest.raises(Hal0Error) as ei,
    ):
        await arb.ensure_img()
    elapsed = _time.monotonic() - start

    # Must finish well under the 30s timeout (should be near-instant).
    assert elapsed < 2.0, f"terminal-error abort took too long: {elapsed:.2f}s"
    assert ei.value.code == "gpu.img_not_ready"

    assert arb.mode is GpuMode.LLM
    assert _read_state(state_path)["mode"] == "llm"
    assert ("unload", "img") in fake_mgr.calls
    assert ("load", "chat", None) in fake_mgr.calls
    assert ("load", "agent", None) in fake_mgr.calls
    arb.guard_dispatch("chat")  # not wedged


async def test_existing_load_raises_rollback_still_works(
    fake_mgr: FakeManager,
    state_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Existing D5 path: load() RAISES → original RuntimeError propagates.
    The rollback helper extraction must not break the pre-existing behavior."""
    fake_mgr.fail_loads = {"img"}
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    with (
        caplog.at_level(logging.WARNING, logger="hal0.slots.arbiter"),
        pytest.raises(RuntimeError, match="load failed: img"),
    ):
        await arb.ensure_img()

    # rollback executed
    assert any("img_load_failed_rolled_back" in rec.message for rec in caplog.records)
    assert arb.mode is GpuMode.LLM
    assert _read_state(state_path)["mode"] == "llm"
    assert _read_state(state_path)["saved_llm_slots"] == []
    arb.guard_dispatch("chat")
