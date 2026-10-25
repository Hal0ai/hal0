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
    ArbiterQueueBusy,
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
        # DR-2 — committed dispatch tickets, summed into in_flight_count so the
        # drain sees requests that passed the guard but aren't yet serving().
        self.tickets: dict[str, int] = {}
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
        count += self.tickets.get(name, 0)
        self.poll_log.append((name, count))
        return count

    def enter_dispatch(self, name: str) -> None:
        self.tickets[name] = self.tickets.get(name, 0) + 1

    def exit_dispatch(self, name: str) -> None:
        remaining = self.tickets.get(name, 1) - 1
        if remaining > 0:
            self.tickets[name] = remaining
        else:
            self.tickets.pop(name, None)

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
def _no_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """No editor hand-off after a switch unless a test sets its own: most tests
    switch and restore back to back, and 3 s of real waiting each adds up."""
    monkeypatch.setattr(arbiter_mod, "_GATE_HANDOFF_S", 0.0)


@pytest.fixture(autouse=True)
def comfyui_http(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the arbiter's ComfyUI HTTP seams (resident-container design).

    ``free`` counts /free calls (restore_llm frees models instead of
    unloading the container). ``queue`` scripts the strict ``GET /queue``
    read, ``_comfyui_queue_state``:

      - ``None`` means the connection was refused, which counts as idle (default);
      - a ``(running, pending)`` tuple is returned as is;
      - a list is consumed one entry per read, and the last entry sticks;
      - ``"unknown"`` raises ``ComfyUIQueueUnknown`` (a failed read counts as busy).
    """
    rec: dict[str, Any] = {"free": 0, "queue": None}

    async def _fake_free() -> bool:
        rec["free"] += 1
        return True

    async def _fake_queue() -> tuple[int, int] | None:
        val = rec["queue"]
        if isinstance(val, list):
            val = val.pop(0) if len(val) > 1 else val[0]
        if val == "unknown":
            raise arbiter_mod.ComfyUIQueueUnknown("scripted")
        return val

    monkeypatch.setattr(arbiter_mod, "_comfyui_free", _fake_free, raising=False)
    monkeypatch.setattr(arbiter_mod, "_comfyui_queue_state", _fake_queue)
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
    assert "last_img_activity" not in final


async def test_ensure_img_drain_blocks_on_committed_dispatch_ticket(
    fake_mgr: FakeManager, state_path: Path
) -> None:
    """DR-2: a committed dispatch ticket keeps the drain alive until released.

    ``serving()`` reports 0 (the request passed the guard but hasn't entered
    the serving context yet), yet ``in_flight_count`` must still be >= 1 so the
    drain does not prematurely reach 0 and unload the slot under the request.
    """
    fake_mgr.in_flight = {"chat": 0}  # serving() count is 0…
    fake_mgr.enter_dispatch("chat")  # …but a request is committed past the guard
    assert fake_mgr.in_flight_count("chat") == 1

    arb = GpuArbiter(fake_mgr, state_path=state_path)
    task = asyncio.create_task(arb.ensure_img())
    await asyncio.sleep(0.05)
    assert not task.done(), "drain must block while a dispatch ticket is held"
    assert ("unload", "chat") not in fake_mgr.calls

    fake_mgr.exit_dispatch("chat")  # request finished → drain can proceed
    assert fake_mgr.in_flight_count("chat") == 0
    await asyncio.wait_for(task, timeout=2.0)
    assert ("unload", "chat") in fake_mgr.calls
    assert arb.mode is GpuMode.IMG


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
    assert "last_img_activity" not in _read_state(state_path)


async def test_ensure_img_starts_the_quiet_clock_when_the_switch_finishes(
    fake_mgr: FakeManager, state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The restore grace runs from the end of the switch, not its start: a slow
    container start must not eat it, and a prompt the gate node lets through
    right after the switch lands inside it."""
    fake_mgr.slot_states = {"img": ["warming", "warming", "ready"]}
    monkeypatch.setattr(arbiter_mod, "_READY_POLL_S", 0.05)
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    seen: list[float | None] = []
    unloaded_at: list[float] = []

    def _on_unload(_name: str) -> None:
        seen.append(arb._quiet_since)
        unloaded_at.append(time.monotonic())

    fake_mgr.on_unload = _on_unload

    await arb.ensure_img()

    assert seen == [None, None]  # the clock is not running while the switch is
    assert arb._quiet_since is not None
    assert arb._quiet_since >= unloaded_at[-1]


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


def _write_img_state(path: Path, *, pinned: bool = False) -> None:
    path.write_text(
        json.dumps(
            {
                "mode": "img",
                "pinned": pinned,
                "saved_llm_slots": ["chat", "agent"],
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


def test_guard_retry_after_is_the_floor(
    fake_mgr: FakeManager, state_path: Path, tmp_hal0_home: str
) -> None:
    # No idle window to count down: image mode ends one grace after the last
    # render, or at once for an LLM request, so the hint is the fixed floor.
    _write_img_state(state_path)
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


# ── restore after each render ────────────────────────────────────────────────
#
# Image mode lasts only while image work runs: a /v1 image request (image_job),
# a request forwarded to the img slot (in_flight_count), or a render in
# ComfyUI's queue. The restore loop hands the GPU back one grace after the last
# render; an LLM request does it at once (make_room_for_llm). A pin holds.


async def _cancel_loop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_restore_tick_restores_once_the_grace_has_passed_with_nothing_rendering(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any]
) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path, restore_grace_s=10.0)
    async with arb.image_job():
        await arb.ensure_img()
    fake_mgr.calls.clear()

    await arb._restore_tick()  # inside the grace
    assert fake_mgr.calls == [] and comfyui_http["free"] == 0
    assert arb.mode is GpuMode.IMG

    arb._quiet_since -= 11  # the grace has passed
    await arb._restore_tick()
    assert fake_mgr.calls == [("load", "chat", None), ("load", "agent", None)]
    assert comfyui_http["free"] == 1
    assert arb.mode is GpuMode.LLM


async def test_back_to_back_renders_inside_the_grace_do_not_reload_llms(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any]
) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path, restore_grace_s=10.0)
    for _ in range(3):
        async with arb.image_job():
            await arb.ensure_img()
        await arb._restore_tick()

    assert [c for c in fake_mgr.calls if c[0] == "load"] == [("load", "img", "sdxl-turbo")]
    assert comfyui_http["free"] == 0
    arb._quiet_since -= 11
    await arb._restore_tick()
    assert fake_mgr.calls.count(("load", "chat", None)) == 1
    assert comfyui_http["free"] == 1


async def test_restore_tick_waits_out_an_editor_render_then_restores(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any]
) -> None:
    """A render queued in ComfyUI's editor never passes through hal0; only /queue shows it."""
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    comfyui_http["queue"] = [(1, 0), (0, 0)]
    arb = GpuArbiter(fake_mgr, state_path=state_path, restore_grace_s=0.0)

    await arb._restore_tick()  # first tick in this process: the quiet clock starts
    await arb._restore_tick()  # the editor render is running: no restore, clock restarts
    assert fake_mgr.calls == [] and comfyui_http["free"] == 0

    await arb._restore_tick()  # queue empty
    assert fake_mgr.calls == [("load", "chat", None), ("load", "agent", None)]
    assert arb.mode is GpuMode.LLM


async def test_a_render_in_flight_is_never_restored_under(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any]
) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path, restore_grace_s=0.0)
    async with arb.image_job():
        await arb.ensure_img()
        fake_mgr.calls.clear()
        arb._quiet_since = time.monotonic() - 3600
        await arb._restore_tick()
        await arb.make_room_for_llm("chat", max_wait_s=0.05)  # waits, then gives up
        with pytest.raises(ArbiterQueueBusy):
            await arb.restore_llm()
        with pytest.raises(GpuImageMode):
            arb.guard_dispatch("chat")
        assert fake_mgr.calls == [] and comfyui_http["free"] == 0
        assert arb.status()["image_jobs"] == 1
    assert arb.status()["image_jobs"] == 0


async def test_a_request_arriving_during_the_quiet_check_cancels_the_restore(
    fake_mgr: FakeManager, state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    arb = GpuArbiter(fake_mgr, state_path=state_path, restore_grace_s=0.0)
    job = arb.image_job()

    async def _queue_read_while_a_request_arrives() -> tuple[int, int]:
        await job.__aenter__()  # a /v1 request enters during the /queue read
        return (0, 0)

    monkeypatch.setattr(arbiter_mod, "_comfyui_queue_state", _queue_read_while_a_request_arrives)
    assert await arb._restore_if_quiet(honour_grace=False) is False
    assert fake_mgr.calls == []
    assert arb.mode is GpuMode.IMG
    await job.__aexit__(None, None, None)


async def test_a_restore_queued_behind_an_img_revive_waits_a_full_grace(
    fake_mgr: FakeManager, state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tick passes its grace check, then waits for the switch lock while
    ensure_img revives a down img slot. The revive restarts the quiet clock,
    so the queued restore must re-check the grace under the lock and stand
    down instead of freeing ComfyUI right after it came up."""
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}  # image mode; the img slot is down too
    arb = GpuArbiter(fake_mgr, state_path=state_path, restore_grace_s=10.0)
    arb._quiet_since = time.monotonic() - 3600

    release = asyncio.Event()
    real_load = fake_mgr.load

    async def _gated_load(name: str, model_id: str | None = None) -> None:
        if name == "img":
            await release.wait()
        await real_load(name, model_id)

    monkeypatch.setattr(fake_mgr, "load", _gated_load)
    revive = asyncio.create_task(arb.ensure_img())
    for _ in range(50):
        if arb.status()["switching"]:
            break
        await asyncio.sleep(0)
    assert arb.status()["switching"] is True

    tick = asyncio.create_task(arb._restore_tick())  # passes the grace check, waits on the lock
    await asyncio.sleep(0)
    assert not tick.done()

    release.set()
    await asyncio.wait_for(revive, timeout=2.0)
    await asyncio.wait_for(tick, timeout=2.0)

    assert fake_mgr.calls == [("load", "img", "sdxl-turbo")]
    assert arb.mode is GpuMode.IMG


async def test_pinned_image_mode_is_held_against_the_tick_and_llm_demand(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any], tmp_hal0_home: str
) -> None:
    _write_img_state(state_path, pinned=True)
    fake_mgr.ready -= {"chat", "agent"}
    arb = GpuArbiter(fake_mgr, state_path=state_path, restore_grace_s=0.0)
    arb._quiet_since = time.monotonic() - 3600

    await arb._restore_tick()
    await asyncio.wait_for(arb.make_room_for_llm("chat"), timeout=1.0)  # at once: no wait

    assert fake_mgr.calls == [] and comfyui_http["free"] == 0
    with pytest.raises(GpuImageMode):
        arb.guard_dispatch("chat")
    with pytest.raises(ArbiterPinned):
        await arb.restore_llm()


async def test_llm_demand_ends_the_grace_at_once(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any]
) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path, restore_grace_s=600.0)
    async with arb.image_job():
        await arb.ensure_img()
    fake_mgr.calls.clear()

    await arb.make_room_for_llm("chat")

    assert fake_mgr.calls == [("load", "chat", None), ("load", "agent", None)]
    assert arb.mode is GpuMode.LLM
    arb.guard_dispatch("chat")  # no longer refused


async def test_llm_demand_answers_at_once_for_an_unreadable_queue_and_non_llm_slots(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any], tmp_hal0_home: str
) -> None:
    """A failed /queue read is not evidence the render stopped, and is no
    render to wait for either: the guard answers 503 at once."""
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    comfyui_http["queue"] = "unknown"
    await asyncio.wait_for(arb.make_room_for_llm("chat"), timeout=1.0)
    comfyui_http["queue"] = None
    await asyncio.wait_for(arb.make_room_for_llm("npu"), timeout=1.0)
    await asyncio.wait_for(arb.make_room_for_llm("img"), timeout=1.0)

    assert fake_mgr.calls == [] and comfyui_http["free"] == 0
    assert arb.mode is GpuMode.IMG


async def test_a_chat_during_a_v1_render_waits_without_holding_up_the_render(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chat arrives while a /v1 render is switching the GPU. It waits instead
    of a 503; the switch and the render go ahead meanwhile, and the chat is
    served once the render has ended and the LLM set is back."""
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    unload_gate = asyncio.Event()
    render_done = asyncio.Event()
    events: list[str] = []
    real_unload = fake_mgr.unload

    async def _slow_unload(name: str) -> None:
        await unload_gate.wait()
        await real_unload(name)

    monkeypatch.setattr(fake_mgr, "unload", _slow_unload)

    async def _render() -> None:
        async with arb.image_job():
            await arb.ensure_img()
            events.append("render running")
            await render_done.wait()

    render = asyncio.create_task(_render())
    for _ in range(50):
        if arb.status()["switching"]:
            break
        await asyncio.sleep(0)
    assert arb.status()["switching"] is True

    chat = asyncio.create_task(_llm_request(arb, "chat"))  # arrives mid-switch
    await asyncio.sleep(0.05)
    assert not chat.done()  # it waits rather than taking a 503

    unload_gate.set()
    for _ in range(100):
        if events:
            break
        await asyncio.sleep(0.01)
    assert events == ["render running"]  # the waiting chat did not hold the switch up
    await asyncio.sleep(0.1)
    assert not chat.done()  # still waiting: the render is running
    assert ("load", "chat", None) not in fake_mgr.calls

    render_done.set()
    assert await asyncio.wait_for(chat, timeout=2.0) == "dispatched"
    await render
    assert fake_mgr.calls[-2:] == [("load", "chat", None), ("load", "agent", None)]
    assert arb.mode is GpuMode.LLM


async def test_a_chat_during_an_editor_render_waits_for_the_queue_then_is_served(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    """A render queued in ComfyUI's editor shows only in /queue: the chat waits
    for the queue to drain, then the LLM set comes back and it is served."""
    monkeypatch.setattr(arbiter_mod, "_DEMAND_RECHECK_S", 0.02, raising=False)
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    comfyui_http["queue"] = (1, 0)
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    chat = asyncio.create_task(_llm_request(arb, "chat"))
    await asyncio.sleep(0.15)
    assert not chat.done()  # waiting on the editor render, not a 503
    assert fake_mgr.calls == [] and comfyui_http["free"] == 0

    comfyui_http["queue"] = (0, 0)  # the render finished
    assert await asyncio.wait_for(chat, timeout=2.0) == "dispatched"
    assert fake_mgr.calls == [("load", "chat", None), ("load", "agent", None)]
    assert comfyui_http["free"] == 1


async def test_a_chat_waiting_on_a_render_gives_up_at_its_cap(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    tmp_hal0_home: str,
) -> None:
    """The cap still ends the wait with the guard's 503, and once nobody waits
    the restore it started stands down: the render's end is the restore loop's
    business again, one grace later."""
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    async with arb.image_job():  # a render that outlasts the chat's patience
        started = time.monotonic()
        await arb.make_room_for_llm("chat", max_wait_s=0.1)
        assert time.monotonic() - started >= 0.09  # it waited
        with pytest.raises(GpuImageMode):
            arb.guard_dispatch("chat")
    await asyncio.sleep(0.2)  # the render ended after the chat gave up

    assert fake_mgr.calls == [] and comfyui_http["free"] == 0
    assert arb.mode is GpuMode.IMG


async def test_llm_demand_stops_waiting_at_its_budget_but_the_restore_finishes(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    """The request stops waiting after max_wait_s and the guard answers; the
    restore itself is shielded and still ends image mode, so a cancelled or
    timed-out request never leaves the LLM set half reloaded."""
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    release = asyncio.Event()
    real_load = fake_mgr.load

    async def _slow_load(name: str, model_id: str | None = None) -> None:
        await release.wait()
        await real_load(name, model_id)

    monkeypatch.setattr(fake_mgr, "load", _slow_load)

    await asyncio.wait_for(arb.make_room_for_llm("chat", max_wait_s=0.05), timeout=1.0)
    assert arb.mode is GpuMode.IMG
    with pytest.raises(GpuImageMode):
        arb.guard_dispatch("chat")

    release.set()
    restore = arb._demand_restore
    assert restore is not None
    assert await asyncio.wait_for(restore, timeout=2.0) is True
    assert fake_mgr.calls == [("load", "chat", None), ("load", "agent", None)]
    assert arb.mode is GpuMode.LLM


async def test_concurrent_llm_demands_share_one_restore(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    real_restore = arb._restore_on_demand
    restores = 0

    async def _counting_restore() -> bool:
        nonlocal restores
        restores += 1
        await asyncio.sleep(0.01)  # both requests arrive while it runs
        return await real_restore()

    monkeypatch.setattr(arb, "_restore_on_demand", _counting_restore)

    await asyncio.gather(arb.make_room_for_llm("chat"), arb.make_room_for_llm("agent"))

    assert restores == 1
    assert fake_mgr.calls == [("load", "chat", None), ("load", "agent", None)]
    assert arb.mode is GpuMode.LLM


async def test_restore_with_a_failing_slot_still_ends_image_mode(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any]
) -> None:
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    fake_mgr.fail_loads = {"chat"}
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    await arb.restore_llm()

    assert ("load_failed", "chat", None) in fake_mgr.calls
    assert ("load", "agent", None) in fake_mgr.calls
    assert arb.mode is GpuMode.LLM
    assert _read_state(state_path)["saved_llm_slots"] == []


async def test_a_pin_set_in_llm_mode_does_not_pin_the_next_image_mode(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any]
) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    arb.set_pin(True)
    await arb.ensure_img()
    assert arb.pinned is False
    await arb.ensure_img(pin=True)  # already in image mode: an explicit pin still pins
    assert arb.pinned is True


async def test_restore_llm_when_idle_waits_for_in_flight_image_requests(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arbiter_mod, "_QUEUE_IDLE_POLL_S", 0.01)
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    job = arb.image_job()
    await job.__aenter__()
    await arb.ensure_img()

    with pytest.raises(ArbiterQueueBusy):
        await arb.restore_llm_when_idle(max_wait_s=0.05)
    assert arb.mode is GpuMode.IMG and comfyui_http["free"] == 0

    await job.__aexit__(None, None, None)
    await arb.restore_llm_when_idle(max_wait_s=1.0)
    assert arb.mode is GpuMode.LLM


async def test_restore_llm_when_idle_restores_once_the_image_request_ends(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The omni loop's restore waits for an open /v1 render instead of
    refusing at once, then restores when it ends."""
    monkeypatch.setattr(arbiter_mod, "_QUEUE_IDLE_POLL_S", 0.01)
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    job = arb.image_job()
    await job.__aenter__()
    await arb.ensure_img()

    restore = asyncio.create_task(arb.restore_llm_when_idle(max_wait_s=2.0))
    await asyncio.sleep(0.05)  # a few polls while the render runs
    assert not restore.done()
    assert comfyui_http["free"] == 0

    await job.__aexit__(None, None, None)
    await asyncio.wait_for(restore, timeout=2.0)
    assert arb.mode is GpuMode.LLM
    assert comfyui_http["free"] == 1


async def test_restore_tick_after_a_restart_in_image_mode_restores_after_one_grace(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any]
) -> None:
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    arb = GpuArbiter(fake_mgr, state_path=state_path, restore_grace_s=10.0)

    await arb._restore_tick()  # a fresh process: the first tick only starts the quiet clock
    assert fake_mgr.calls == [] and arb.mode is GpuMode.IMG

    arb._quiet_since -= 11
    await arb._restore_tick()
    assert fake_mgr.calls == [("load", "chat", None), ("load", "agent", None)]
    assert arb.mode is GpuMode.LLM


async def test_restore_tick_defers_while_the_img_slot_has_a_forwarded_request(
    state_path: Path, comfyui_http: dict[str, Any]
) -> None:
    fake_mgr = FakeManager(in_flight={"img": 1})
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    arb = GpuArbiter(fake_mgr, state_path=state_path, restore_grace_s=0.0)
    arb._quiet_since = time.monotonic() - 3600

    await arb._restore_tick()
    await arb._restore_tick()

    assert fake_mgr.calls == [] and comfyui_http["free"] == 0
    assert arb.mode is GpuMode.IMG


async def test_status_reports_switching_and_image_jobs(
    fake_mgr: FakeManager, state_path: Path
) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    assert arb.status() == {
        "mode": "llm",
        "pinned": False,
        "saved_llm_slots": [],
        "switching": False,
        "image_jobs": 0,
    }
    async with arb.image_job():
        assert arb.status()["image_jobs"] == 1
        async with arb.image_job():
            assert arb.status()["image_jobs"] == 2
    assert arb.status()["image_jobs"] == 0


async def test_restore_loop_survives_a_tick_exception(
    fake_mgr: FakeManager,
    state_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    ticks = 0

    async def _tick() -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            raise RuntimeError("tick failed")

    monkeypatch.setattr(arb, "_restore_tick", _tick)

    with caplog.at_level(logging.WARNING, logger="hal0.slots.arbiter"):
        task = asyncio.create_task(arb.run_restore_loop(interval_s=0.01))
        try:
            for _ in range(200):
                if ticks >= 3:
                    break
                await asyncio.sleep(0.01)
            assert ticks >= 3
            assert not task.done(), "the loop must survive a tick exception"
            assert any("restore_loop_error" in rec.message for rec in caplog.records)
        finally:
            await _cancel_loop(task)


# ── the editor hand-off and the gate node (real gate logic) ──────────────────

_GATE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "hal0" / "comfyui" / "assets" / "hal0_gpu_gate.py"
)


def _load_gate() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("hal0_gpu_gate_under_test", _GATE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _EditorPrompt:
    """One editor prompt through the real ``hal0_gpu_gate`` hook, wired to ``arb``.

    The hook runs in a worker thread, as it does inside ComfyUI. Its status
    fetch reports ``arb.status()`` the way ``GET /api/comfyui/status`` does
    (mode, the arbiter block, and the switchover tracker), and its switchover
    post starts ``ensure_img`` in the background, like the route's 202. When
    the hook lets the prompt through, ComfyUI queues it.
    """

    def __init__(self, arb: GpuArbiter, comfyui_http: dict[str, Any]) -> None:
        self.gate = _load_gate()
        self.gate._SWITCH_POLL_S = 0.05
        self.gate._SWITCH_TIMEOUT_S = 2.0
        self.gate._fetch_status = self._fetch_status
        self.gate._post_switchover = self._post_switchover
        self.arb = arb
        self.comfyui_http = comfyui_http
        self.loop = asyncio.get_running_loop()
        self.posts = 0
        self.switch_active = False
        self.mode_when_queued: GpuMode | None = None
        self.refused: Exception | None = None

    def _on_loop(self, fn: Any) -> Any:
        async def _call() -> Any:
            return fn()

        return asyncio.run_coroutine_threadsafe(_call(), self.loop).result(timeout=5)

    def _fetch_status(self) -> dict[str, Any]:
        st = self._on_loop(self.arb.status)
        return {
            "mode": {"img": "generation", "llm": "inference"}[st["mode"]],
            "arbiter": st,
            "switchover": {"active": self.switch_active, "error": None},
        }

    def _post_switchover(self) -> bool:
        self.posts += 1
        self.switch_active = True

        async def _switch() -> None:
            try:
                await self.arb.ensure_img()
            finally:
                self.switch_active = False

        asyncio.run_coroutine_threadsafe(_switch(), self.loop)
        return True

    def _submit(self) -> None:
        try:
            self.gate.prepare_image_mode()
        except Exception as exc:  # the hook refused the prompt
            self.refused = exc
            return
        self.mode_when_queued = self._on_loop(lambda: self.arb.mode)
        self.comfyui_http["queue"] = (0, 1)  # ComfyUI queued the prompt

    def submit(self) -> asyncio.Future[None]:
        return self.loop.run_in_executor(None, self._submit)

    async def wait_for_the_switch(self) -> None:
        for _ in range(1000):
            if self.posts and self.arb.mode is GpuMode.IMG and not self.arb.status()["switching"]:
                return
            await asyncio.sleep(0.005)
        raise AssertionError("the gate's switch never finished")


async def _llm_request(arb: GpuArbiter, slot: str) -> str:
    """What the dispatcher does for an LLM request: the demand step, then the
    guard, then (in the same step as its ticket) the lease release."""
    await arb.make_room_for_llm(slot)
    try:
        arb.guard_dispatch(slot)
    except GpuImageMode:
        return "503 gpu.image_mode"
    finally:
        getattr(arb, "release_demand_lease", lambda: None)()
    return "dispatched"


@pytest.fixture
def slow_unload(fake_mgr: FakeManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real unload takes seconds; the gate polls meanwhile."""
    real_unload = fake_mgr.unload

    async def _slow_unload(name: str) -> None:
        await asyncio.sleep(0.05)
        await real_unload(name)

    monkeypatch.setattr(fake_mgr, "unload", _slow_unload)


async def test_llm_demand_right_after_an_editor_switch_leaves_the_gpu_to_the_prompt(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    slow_unload: None,
) -> None:
    """An editor prompt asks for image mode through the gate. An LLM request
    that lands in the gate's poll gap, after the switch and before the prompt
    reaches ComfyUI's queue, must not restore the LLM set under the prompt:
    it waits out the hand-off, sees the queued prompt, and waits for the render
    to finish before the LLM set comes back."""
    # Longer than this test gate's 0.05 s poll, as 3 s is longer than the real
    # gate's 0.5 s poll plus its 2 s status fetch.
    monkeypatch.setattr(arbiter_mod, "_GATE_HANDOFF_S", 1.0, raising=False)
    fake_mgr.ready.add("img")  # resident ComfyUI
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    prompt = _EditorPrompt(arb, comfyui_http)

    queued = prompt.submit()
    await prompt.wait_for_the_switch()
    # e.g. an agent's chat request, in the gate's poll gap
    chat = asyncio.create_task(_llm_request(arb, "chat"))
    await asyncio.wait_for(queued, timeout=5)

    assert prompt.refused is None
    assert prompt.mode_when_queued is GpuMode.IMG
    assert prompt.posts == 1
    await asyncio.sleep(1.2)  # past the hand-off: the chat sees the queued prompt
    assert not chat.done()  # and waits for it, rather than restoring under it
    assert comfyui_http["free"] == 0
    assert ("load", "chat", None) not in fake_mgr.calls
    assert arb.mode is GpuMode.IMG

    comfyui_http["queue"] = (0, 0)  # the editor render finished
    assert await asyncio.wait_for(chat, timeout=3) == "dispatched"
    assert arb.mode is GpuMode.LLM


async def test_llm_demand_after_a_switch_whose_prompt_never_comes_restores_after_the_handoff(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manual switch (or a prompt the gate let through that never queued):
    the LLM request waits out the hand-off instead of a 503, then restores."""
    monkeypatch.setattr(arbiter_mod, "_GATE_HANDOFF_S", 0.2, raising=False)
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.ensure_img()  # no image job: a gate or manual switch
    fake_mgr.calls.clear()

    await arb._restore_tick()
    arb._quiet_since = time.monotonic() - 3600  # even past the grace ...
    await arb._restore_tick()  # ... the tick leaves the hand-off alone
    assert fake_mgr.calls == []

    started = time.monotonic()
    assert await _llm_request(arb, "chat") == "dispatched"
    assert time.monotonic() - started >= 0.1  # it waited for the hand-off
    assert fake_mgr.calls == [("load", "chat", None), ("load", "agent", None)]
    assert arb.mode is GpuMode.LLM


async def test_the_handoff_opens_after_a_v1_switch_too(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A /v1 render that fails right after its switch closes its job inside the
    gate's poll gap. An editor prompt that asked for the same switch may still
    be on its way, so the hand-off holds after every switch."""
    monkeypatch.setattr(arbiter_mod, "_GATE_HANDOFF_S", 0.3)
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    async with arb.image_job():
        await arb.ensure_img()
    # the render failed at once; its job is closed
    fake_mgr.calls.clear()

    started = time.monotonic()
    assert await _llm_request(arb, "chat") == "dispatched"
    assert time.monotonic() - started >= 0.25  # it waited out the hand-off
    assert fake_mgr.calls == [("load", "chat", None), ("load", "agent", None)]


async def test_restore_llm_when_idle_waits_out_the_handoff(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omni's restore runs on its own, so it waits for the hand-off too instead
    of relying on the gate to ask again."""
    monkeypatch.setattr(arbiter_mod, "_GATE_HANDOFF_S", 0.3)
    monkeypatch.setattr(arbiter_mod, "_QUEUE_IDLE_POLL_S", 0.01)
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.ensure_img()  # a gate or manual switch

    with pytest.raises(ArbiterQueueBusy, match="ComfyUI"):
        await arb.restore_llm_when_idle(max_wait_s=0.1)
    assert arb.mode is GpuMode.IMG and comfyui_http["free"] == 0

    await arb.restore_llm_when_idle(max_wait_s=1.0)
    assert arb.mode is GpuMode.LLM


async def test_the_gate_asks_again_when_a_restore_beats_its_prompt(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    slow_unload: None,
) -> None:
    """Defence in depth: when the LLM set is restored before the prompt lands
    (here with no hand-off, as an explicit restore would), the gate sees
    inference after its switch and asks again instead of letting the prompt
    render under the LLM weights."""
    monkeypatch.setattr(arbiter_mod, "_GATE_HANDOFF_S", 0.0, raising=False)
    fake_mgr.ready.add("img")
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    prompt = _EditorPrompt(arb, comfyui_http)

    queued = prompt.submit()
    await prompt.wait_for_the_switch()
    assert await _llm_request(arb, "chat") == "dispatched"  # the restore beat the prompt
    await asyncio.wait_for(queued, timeout=5)

    assert prompt.refused is None
    assert prompt.posts == 2
    assert prompt.mode_when_queued is GpuMode.IMG


# ── a chat served by a demand restore against a render queued behind it ──────


async def test_a_chat_served_by_a_demand_restore_dispatches_before_a_queued_render_switches(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    """The chat waited for the reload it asked for. A /v1 render queued on the
    switch lock behind that restore must not persist image mode before the
    chat reaches its dispatch ticket; the render switches once the chat ran."""
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    fake_mgr.ready.add("img")
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    release = asyncio.Event()
    real_load = fake_mgr.load
    events: list[str] = []

    async def _gated_load(name: str, model_id: str | None = None) -> None:
        await release.wait()
        await real_load(name, model_id)

    monkeypatch.setattr(fake_mgr, "load", _gated_load)
    fake_mgr.on_unload = lambda name: events.append(f"unload {name}")

    async def _chat() -> str:
        # What Dispatcher.forward does: the demand step, the guard, the ticket.
        await arb.make_room_for_llm("chat")
        try:
            arb.guard_dispatch("chat")
        except GpuImageMode:
            return "503 gpu.image_mode"
        fake_mgr.enter_dispatch("chat")
        getattr(arb, "release_demand_lease", lambda: None)()
        try:
            await asyncio.sleep(0.05)  # the request runs on the slot
            events.append("chat served")
        finally:
            fake_mgr.exit_dispatch("chat")
        return "served"

    async def _render() -> None:
        async with arb.image_job():
            await arb.ensure_img()
        events.append("render switched")

    chat = asyncio.create_task(_chat())
    await asyncio.sleep(0.01)  # the demand restore holds the lock, reloading
    render = asyncio.create_task(_render())
    await asyncio.sleep(0.01)  # the render waits on the lock behind it
    release.set()

    assert await asyncio.wait_for(chat, timeout=2.0) == "served"
    await asyncio.wait_for(render, timeout=2.0)
    assert events == ["chat served", "unload chat", "unload agent", "render switched"]
    assert arb.mode is GpuMode.IMG


async def test_a_switch_ahead_of_a_demand_restore_does_not_wait_for_its_waiters(
    fake_mgr: FakeManager,
    state_path: Path,
    comfyui_http: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_hal0_home: str,
) -> None:
    """A switch that reaches the lock before a demand restore must not wait for
    that restore's waiters: the restore cannot run until the switch ends. The
    switch goes ahead; the demand restore then runs after it (here no prompt
    follows the manual switch, so it hands the GPU back)."""
    monkeypatch.setattr(arbiter_mod, "_GATE_HANDOFF_S", 0.0, raising=False)
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    fake_mgr.ready.add("img")
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    release = asyncio.Event()
    real_load = fake_mgr.load

    async def _gated_load(name: str, model_id: str | None = None) -> None:
        await release.wait()
        await real_load(name, model_id)

    monkeypatch.setattr(fake_mgr, "load", _gated_load)

    restore = asyncio.create_task(arb.restore_llm())  # an explicit restore holds the lock
    await asyncio.sleep(0.01)
    switch = asyncio.create_task(arb.ensure_img())  # a manual switch queues first ...
    await asyncio.sleep(0.01)
    chat = asyncio.create_task(_llm_request(arb, "chat"))  # ... a demand restore second
    await asyncio.sleep(0.01)
    release.set()

    await asyncio.wait_for(restore, timeout=1.0)
    await asyncio.wait_for(switch, timeout=1.0)  # not held up by the chat's wait
    assert await asyncio.wait_for(chat, timeout=1.0) == "dispatched"
    assert fake_mgr.calls == [
        ("load", "chat", None),  # the explicit restore
        ("load", "agent", None),
        ("unload", "chat"),  # the manual switch, ahead of the demand restore
        ("unload", "agent"),
        ("load", "chat", None),  # the demand restore, after the switch
        ("load", "agent", None),
    ]


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


def test_manager_arbiter_defaults_without_img_slot(tmp_hal0_home: str) -> None:
    from hal0.slots.manager import SlotManager

    sm = SlotManager()
    assert sm.arbiter.mode is GpuMode.LLM
    assert sm.arbiter.restore_grace_s == arbiter_mod._RESTORE_GRACE_S


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


# ── restore_llm_when_idle (#2191) ────────────────────────────────────────────
#
# restore_llm() frees ComfyUI's models and reloads the LLM set. Called while a
# render is still running or queued — another request's, or one a cancelled
# caller abandoned — it would drop that render. This variant waits for the
# ComfyUI queue to drain first, bounded, and is what the omni loop uses.


async def test_restore_llm_when_idle_waits_for_the_queue_then_restores(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any], monkeypatch
) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.ensure_img()
    assert arb.mode is GpuMode.IMG

    monkeypatch.setattr(arbiter_mod, "_QUEUE_IDLE_POLL_S", 0.01)
    # Scripted queue: a render running, then one pending, then idle.
    polls = iter([(1, 0), (0, 1), (0, 0)])

    async def _queue() -> tuple[int, int] | None:
        return next(polls, (0, 0))

    monkeypatch.setattr(arbiter_mod, "_comfyui_queue_state", _queue)

    await arb.restore_llm_when_idle()

    assert arb.mode is GpuMode.LLM
    assert comfyui_http["free"] == 1  # freed exactly once, after the queue drained


async def test_restore_llm_when_idle_treats_a_failed_queue_read_as_busy(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any], monkeypatch
) -> None:
    """A 500 or a garbled /queue body is not evidence the render stopped —
    only a refused connection (nothing listening, nothing on the GPU) is."""
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.ensure_img()
    monkeypatch.setattr(arbiter_mod, "_QUEUE_IDLE_POLL_S", 0.01)

    async def _queue() -> tuple[int, int] | None:
        raise arbiter_mod.ComfyUIQueueUnknown("HTTP 500")

    monkeypatch.setattr(arbiter_mod, "_comfyui_queue_state", _queue)

    with pytest.raises(arbiter_mod.ArbiterQueueBusy):
        await arb.restore_llm_when_idle(max_wait_s=0.05)
    assert arb.mode is GpuMode.IMG
    assert comfyui_http["free"] == 0


async def test_restore_llm_when_idle_gives_up_after_the_wait_budget(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any], monkeypatch
) -> None:
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.ensure_img()
    monkeypatch.setattr(arbiter_mod, "_QUEUE_IDLE_POLL_S", 0.01)

    async def _queue() -> tuple[int, int] | None:
        return (1, 0)  # never drains

    monkeypatch.setattr(arbiter_mod, "_comfyui_queue_state", _queue)

    with pytest.raises(arbiter_mod.ArbiterQueueBusy):
        await arb.restore_llm_when_idle(max_wait_s=0.05)

    assert arb.mode is GpuMode.IMG  # nothing was freed or reloaded
    assert comfyui_http["free"] == 0


async def test_restore_llm_when_idle_treats_an_unreachable_queue_as_idle(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any], monkeypatch
) -> None:
    """A refused connection means nothing is listening and nothing is on the
    GPU — same stance as restore_llm's /free."""
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.ensure_img()

    async def _queue() -> tuple[int, int] | None:
        return None  # unreachable

    monkeypatch.setattr(arbiter_mod, "_comfyui_queue_state", _queue)

    await arb.restore_llm_when_idle()

    assert arb.mode is GpuMode.LLM


async def test_restore_llm_when_idle_refuses_a_pinned_mode_before_polling_the_queue(
    fake_mgr: FakeManager, state_path: Path, comfyui_http: dict[str, Any], monkeypatch
) -> None:
    """Pinned image mode with a busy ComfyUI is the normal pinned case (someone
    rendering in the web UI): report "pinned" at once instead of waiting out
    the queue and then reporting queue_busy (#2639)."""
    arb = GpuArbiter(fake_mgr, state_path=state_path)
    await arb.ensure_img(pin=True)
    polled = 0

    async def _queue() -> tuple[int, int] | None:
        nonlocal polled
        polled += 1
        return (1, 0)

    monkeypatch.setattr(arbiter_mod, "_comfyui_queue_state", _queue)

    with pytest.raises(arbiter_mod.ArbiterPinned):
        await arb.restore_llm_when_idle()

    assert polled == 0
    assert arb.mode is GpuMode.IMG
    assert comfyui_http["free"] == 0


# ── ensure_img in image mode: the img slot is down (#3048) ───────────────────


async def test_ensure_img_cold_starts_an_img_slot_that_is_down_in_img_mode(
    fake_mgr: FakeManager, state_path: Path
) -> None:
    """#3048: the persisted mode says img but the slot is down (a crash, a manual
    unload). ensure_img must reload it instead of returning early."""
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}  # the original switch unloaded them
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    await arb.ensure_img()

    assert fake_mgr.calls == [("load", "img", "sdxl-turbo")]
    assert arb.mode is GpuMode.IMG
    assert arb.saved_llm_slots == ("chat", "agent")
    assert arb._quiet_since is not None


async def test_ensure_img_in_img_mode_rolls_back_when_the_reload_fails(
    fake_mgr: FakeManager, state_path: Path
) -> None:
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    fake_mgr.fail_loads.add("img")
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    with pytest.raises(RuntimeError, match="load failed: img"):
        await arb.ensure_img()

    assert arb.mode is GpuMode.LLM
    assert ("load", "chat", None) in fake_mgr.calls
    assert ("load", "agent", None) in fake_mgr.calls
    assert _read_state(state_path)["saved_llm_slots"] == []


async def test_concurrent_requests_cold_start_a_down_img_slot_once(
    fake_mgr: FakeManager, state_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_img_state(state_path)
    fake_mgr.ready -= {"chat", "agent"}
    arb = GpuArbiter(fake_mgr, state_path=state_path)

    # FakeManager.load never suspends, so without a yield the first ensure_img
    # runs to completion before the second one starts and the switch lock is
    # never contended. Yield inside load (before the slot turns ready) so the
    # second request reaches the down-slot check while the first is mid-load:
    # only the lock keeps it from loading the slot a second time.
    real_load = fake_mgr.load

    async def _yielding_load(name: str, model_id: str | None = None) -> None:
        await asyncio.sleep(0)
        await real_load(name, model_id)

    monkeypatch.setattr(fake_mgr, "load", _yielding_load)

    await asyncio.gather(arb.ensure_img(), arb.ensure_img())

    assert fake_mgr.calls.count(("load", "img", "sdxl-turbo")) == 1
