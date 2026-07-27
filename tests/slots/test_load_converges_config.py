"""#1224 part 2 — a load must converge the unit onto the slot's current config.

``load()`` short-circuits on a slot already in READY/SERVING/IDLE and returns
the current snapshot without re-rendering the unit. The sequence that bites:

    PUT /api/slots/ops/config {"port": 8091}   # TOML now says 8091
    hal0 slot load ops                         # returns the OLD snapshot
    # ...unit still running --port 8089; nothing listening on either port
    # after the next implicit reload cycles warming → error.

Recovery required ``systemctl reset-failed`` + a second load *from the error
state* — the only path that regenerates.

The fix reuses the drift comparator that already knows how to compare the
running argv against what a restart would render: an explicit load on a live
slot converges when they disagree, and stays a no-op when they don't.

Also covered here: ``terminate`` must be bounded. The stop runs as a blocking
call in an executor thread; without a timeout a wedged ``systemctl stop``
never returns and the caller (``restart``'s best-effort cleanup, the HTTP
request behind it) hangs forever — the original #1224 symptom. A bounded stop
cannot kill the thread, but it must hand control back so the caller can make
forward progress.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


async def _load_to_ready(sm: SlotManager, stub: FakeContainerProvider) -> None:
    await sm.load("chat")
    assert sm._current_state("chat") == SlotState.READY
    stub.load_calls.clear()
    stub.unload_calls.clear()


# ── load() convergence ───────────────────────────────────────────────────────


async def test_load_on_live_slot_converges_when_argv_drifted(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The headline bug: an edited config must reach the running unit."""
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    # The unit is running the old port; the TOML now renders a new one.
    container_stub.running_argv_by_slot["chat"] = ["--port", "8089", "--ctx-size", "4096"]
    container_stub.expected_argv_by_slot["chat"] = ["--port", "8091", "--ctx-size", "4096"]

    await sm.load("chat")

    assert container_stub.unload_calls, "drifted slot must be torn down"
    assert container_stub.load_calls, "drifted slot must be re-spawned on the new config"
    assert sm._current_state("chat") == SlotState.READY


async def test_load_on_live_slot_is_a_no_op_without_drift(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """Idempotency must not regress — an unchanged slot is not restarted."""
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    argv = ["--port", "8089", "--ctx-size", "4096"]
    container_stub.running_argv_by_slot["chat"] = list(argv)
    container_stub.expected_argv_by_slot["chat"] = list(argv)

    await sm.load("chat")

    assert container_stub.unload_calls == [], "unchanged slot must not be torn down"
    assert container_stub.load_calls == [], "unchanged slot must not be re-spawned"
    assert sm._current_state("chat") == SlotState.READY


async def test_load_on_live_slot_no_ops_when_drift_is_unknowable(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """No argv on either side → the comparator returns None. Absence of
    evidence is not drift: keep the existing no-op rather than bouncing a
    healthy container on every load."""
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    container_stub.running_argv_by_slot["chat"] = None
    container_stub.expected_argv_by_slot["chat"] = None

    await sm.load("chat")

    assert container_stub.unload_calls == []
    assert container_stub.load_calls == []
    assert sm._current_state("chat") == SlotState.READY


async def test_converging_load_survives_a_failing_terminate(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The teardown half is best-effort; the re-spawn must still run."""
    sm = SlotManager()
    await _load_to_ready(sm, container_stub)

    container_stub.running_argv_by_slot["chat"] = ["--port", "8089"]
    container_stub.expected_argv_by_slot["chat"] = ["--port", "8091"]
    monkeypatch.setattr(
        container_stub,
        "unload_sync",
        lambda cfg: (_ for _ in ()).throw(RuntimeError("systemctl stop wedged")),
    )

    await sm.load("chat")

    assert container_stub.load_calls, "re-spawn must run even if teardown failed"
    assert sm._current_state("chat") == SlotState.READY


# ── terminate() must be bounded ──────────────────────────────────────────────


async def test_terminate_is_bounded_when_stop_blocks(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocking stop must hand control back inside the timeout."""
    from hal0.slots.state import SlotTerminateTimeout

    release = threading.Event()
    monkeypatch.setattr(container_stub, "unload_sync", lambda cfg: release.wait(30))

    sm = SlotManager()
    try:
        with pytest.raises(SlotTerminateTimeout):
            await asyncio.wait_for(sm.terminate("chat", timeout_s=0.05), timeout=5.0)
    finally:
        release.set()  # let the executor thread retire


async def test_restart_from_error_survives_a_blocking_terminate(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original #1224 symptom, end to end: an errored slot whose stop
    wedges must still relaunch instead of hanging the caller forever."""
    sm = SlotManager()
    container_stub.fail_load = RuntimeError("spawn boom")
    with pytest.raises(RuntimeError):
        await sm.load("chat")
    assert sm._current_state("chat") == SlotState.ERROR

    release = threading.Event()
    real_unload = container_stub.unload_sync
    calls = {"n": 0}

    def _block_once(cfg: dict) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            release.wait(30)
            return
        real_unload(cfg)

    monkeypatch.setattr(container_stub, "unload_sync", _block_once)
    monkeypatch.setattr(SlotManager, "_terminate_timeout_s", 0.05, raising=False)
    container_stub.fail_load = None

    try:
        await asyncio.wait_for(sm.restart("chat"), timeout=10.0)
    finally:
        release.set()

    assert sm._current_state("chat") == SlotState.READY
