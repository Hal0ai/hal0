"""#1791 — a crash-looping slot must stop lying about its own state.

Three defects, all observed live on a fresh-install box (CT151, v1.0.0-rc.4)
while a slot's runner crash-looped, and all covered here:

1. **Eternal ``warming``.** ``_await_ready`` times out and parks the slot in
   WARMING as a "successful" load, so ``hal0 status`` / ``/api/slots`` /
   ``state.json`` reported ``warming`` forever while ``systemctl is-active``
   said ``failed``. Nothing ever reconciled it. The follow-up load then blew up
   with ``IllegalSlotTransition: warming → starting`` instead of recovering.
2. **Start-limit exhaustion, permanent and silent.** After ``StartLimitBurst``
   restarts the unit is gone, the slot reads a bare ``offline``, and no alert
   says a previously-loaded slot was lost.
3. **Swap re-applied the OLD model.** During the crash loop, ``swap`` raced an
   interloping load in the unload→load gap; the interloper rendered the quadlet
   from ``model.default`` (still the old, crashing model).

Refs #1424 (the load/restart error-surfacing + reset-failed gap this builds on).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import (
    LEGAL_TRANSITIONS,
    SlotCrashLooping,
    SlotSpawnFailed,
    SlotState,
)
from tests.slots.conftest import FakeContainerProvider

CRASH_LOOP_REASON = (
    "hal0-slot@chat.service is crash-looping: systemd stopped retrying after "
    "5 restarts (start-limit-hit). Fix the cause, then `hal0 slot load chat`"
)


def _time_out_readiness(container_stub: FakeContainerProvider) -> None:
    """Make the health wait give up, which is what parks a slot in WARMING."""

    async def _wait_ready_timeout(port: int, timeout_s: float | None = None) -> None:
        raise TimeoutError("health wait timed out")

    container_stub.wait_ready = _wait_ready_timeout  # type: ignore[method-assign]


# ── facet 1: no more eternal WARMING ────────────────────────────────────────


async def test_readiness_timeout_on_a_failed_unit_lands_error_not_warming(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """Health wait gave up AND systemd gave up → ERROR carrying the reason."""
    sm = SlotManager()
    _time_out_readiness(container_stub)
    container_stub.unit_failure_by_slot["chat"] = CRASH_LOOP_REASON

    with pytest.raises(SlotSpawnFailed) as excinfo:
        await sm.load("chat")

    assert CRASH_LOOP_REASON in str(excinfo.value)
    assert sm._current_state("chat") == SlotState.ERROR
    # A unit systemd has given up on is not active — mirror that on the double
    # so ``status`` doesn't take the inverse-drift adoption branch.
    container_stub.active.discard("chat")
    slot = await sm.status("chat")
    # The operator-facing surfaces (`hal0 status`, /api/slots) read this.
    assert slot.metadata.get("message") == CRASH_LOOP_REASON
    assert slot.metadata.get("load_failures") == 1


async def test_readiness_timeout_on_a_healthy_unit_still_parks_warming(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A big model that is genuinely still loading must NOT be called an error.

    The systemd probe is the only thing that promotes WARMING to ERROR: with no
    terminal verdict the pre-#1791 behaviour is preserved exactly, so the fail
    watcher / next-request reload still governs recovery.
    """
    sm = SlotManager()
    _time_out_readiness(container_stub)
    # No entry in unit_failure_by_slot → probe returns "" (inconclusive).

    await sm.load("chat")

    assert sm._current_state("chat") == SlotState.WARMING


async def test_load_from_stale_warming_recovers_instead_of_illegal_transition(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """warming → starting was illegal, so the recovery load raised (#1791)."""
    # Guard the premise: the fix must not be "make the edge legal".
    assert SlotState.STARTING not in LEGAL_TRANSITIONS[SlotState.WARMING]

    sm = SlotManager()
    _time_out_readiness(container_stub)
    await sm.load("chat")
    assert sm._current_state("chat") == SlotState.WARMING

    # Fault cleared — the next load must tear the stale unit down and re-enter
    # the lifecycle from OFFLINE rather than blowing up on the illegal edge.
    container_stub.wait_ready = FakeContainerProvider.wait_ready.__get__(  # type: ignore[method-assign]
        container_stub, FakeContainerProvider
    )
    container_stub.unload_calls.clear()
    container_stub.load_calls.clear()

    slot = await sm.load("chat")

    assert slot.state == SlotState.READY
    assert container_stub.unload_calls, "stale WARMING must be torn down first"
    assert container_stub.load_calls, "and the unit re-spawned"


@pytest.mark.parametrize("stale", [SlotState.PULLING, SlotState.STARTING, SlotState.WARMING])
async def test_every_stale_mid_lifecycle_state_is_recoverable(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    stale: SlotState,
) -> None:
    """PULLING/STARTING/WARMING all lack an edge to STARTING — all recover."""
    assert SlotState.STARTING not in LEGAL_TRANSITIONS[stale] or stale is SlotState.PULLING
    sm = SlotManager()
    await sm.load("chat")
    await sm._transition("chat", stale, force=True)

    slot = await sm.load("chat")

    assert slot.state == SlotState.READY


async def test_stale_warming_still_honours_the_crash_loop_breaker(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A wedged slot must not respawn at request cadence just because its last
    load timed out (WARMING) instead of raising (ERROR)."""
    sm = SlotManager()
    _time_out_readiness(container_stub)
    await sm.load("chat")
    assert sm._current_state("chat") == SlotState.WARMING

    # Simulate accumulated consecutive failures, as a real crash loop would.
    sm._load_failures[sm._key("chat")] = (3, __import__("time").monotonic())

    with pytest.raises(SlotCrashLooping):
        await sm.load("chat")


# ── facet 2: a lost unit is an ERROR, not a bare OFFLINE ────────────────────


async def test_lost_unit_surfaces_as_error_with_the_recovery_hint(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """READY in state.json + dead unit + systemd verdict → red ERROR."""
    sm = SlotManager()
    await sm.load("chat")
    assert sm._current_state("chat") == SlotState.READY

    # Start-limit exhaustion: the unit is gone and nothing will restart it.
    container_stub.active.discard("chat")
    container_stub.unit_failure_by_slot["chat"] = CRASH_LOOP_REASON

    slot = await sm.status("chat")

    assert slot.state == SlotState.ERROR
    assert slot.metadata.get("message") == CRASH_LOOP_REASON


async def test_a_legitimately_stopped_unit_stays_neutral_offline(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """GPU-arbiter handoff / idle policy / `systemctl stop` are not failures."""
    sm = SlotManager()
    await sm.load("chat")

    container_stub.active.discard("chat")
    # No systemd verdict — the stop was deliberate.

    slot = await sm.status("chat")

    assert slot.state == SlotState.OFFLINE
    assert "auto-reloads" in str(slot.metadata.get("message", ""))


async def test_unit_failure_probe_that_raises_never_manufactures_an_error(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """Absence of evidence is not evidence of failure."""
    sm = SlotManager()
    await sm.load("chat")
    container_stub.active.discard("chat")

    def _boom(slot: object) -> str:
        raise RuntimeError("systemctl show exploded")

    container_stub.unit_failure_reason = _boom  # type: ignore[method-assign]

    slot = await sm.status("chat")

    assert slot.state == SlotState.OFFLINE


async def test_a_non_string_probe_result_is_not_a_failure_verdict(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A provider that doesn't really implement the probe must not forge one.

    A bare ``MagicMock`` provider auto-creates ``unit_failure_reason`` and
    returns a truthy mock; stringifying that would stamp a healthy slot ERROR
    with ``"<MagicMock ...>"`` as the operator-facing message.
    """
    sm = SlotManager()
    await sm.load("chat")
    container_stub.active.discard("chat")

    container_stub.unit_failure_reason = lambda slot: object()  # type: ignore[method-assign, assignment]

    slot = await sm.status("chat")

    assert slot.state == SlotState.OFFLINE


# ── facet 3: swap applies the REQUESTED model ───────────────────────────────


def _model_default_on_disk(slot_root: Path) -> str:
    data = tomllib.loads((slot_root / "chat.toml").read_text(encoding="utf-8"))
    return str(data.get("model", {}).get("default", ""))


async def test_swap_persists_the_requested_model_before_the_teardown(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The TOML must carry the requested model by the time the unload runs.

    That is what closes the unload→load race: whoever renders the quadlet next
    — swap's own load or an interloping reconciler/fail-watcher load that won
    the lock in the gap — reads the REQUESTED model, never the old crashing one.
    """
    sm = SlotManager()
    await sm.load("chat")
    assert _model_default_on_disk(slot_root) == "qwen3-4b-q4_k_m"

    seen: list[str] = []
    orig_unload_sync = container_stub.unload_sync

    def _record_default_at_teardown(cfg: dict) -> None:
        seen.append(_model_default_on_disk(slot_root))
        orig_unload_sync(cfg)

    container_stub.unload_sync = _record_default_at_teardown  # type: ignore[method-assign]

    await sm.swap("chat", "qwen3.5-0.8b")

    assert seen, "swap must tear the old unit down"
    assert seen[0] == "qwen3.5-0.8b", (
        "the requested model must be durable BEFORE the teardown — otherwise an "
        "interloping load in the unload→load gap re-renders the quadlet with the "
        "old, crashing model (#1791 facet 3)"
    )
    assert _model_default_on_disk(slot_root) == "qwen3.5-0.8b"


async def test_swap_during_a_crash_loop_still_applies_the_requested_model(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """End-to-end reproduction of the filed bug, with the race made determinstic.

    An interloper (the fail watcher / reconciler / per-request lazy load, all of
    which fire at the failure cadence) wins the slot lock in swap's unload→load
    gap and runs a full load of its own. It knows nothing about the in-flight
    swap, so it renders from ``model.default``. Pre-fix that was the OLD model
    and the loop continued; the quadlet must now carry the requested one.
    """
    sm = SlotManager()
    await sm.load("chat")

    interloper_models: list[str] = []
    orig_unload = sm.unload

    async def _interloping_unload(slot_name: str):
        result = await orig_unload(slot_name)
        # Runs in the gap, with the lock released — exactly the observed race.
        cfg = await sm._load_slot_config(slot_name)
        interloper_models.append(str(cfg.get("model", {}).get("default", "")))
        return result

    sm.unload = _interloping_unload  # type: ignore[method-assign]

    await sm.swap("chat", "qwen3.5-0.8b")

    assert interloper_models == ["qwen3.5-0.8b"], (
        "a load that wins the lock in the swap gap must render the REQUESTED "
        "model, not the old crashing default"
    )
    # And the load swap itself dispatched carries it too.
    assert container_stub.load_calls[-1][0].get("model", {}).get("default") == "qwen3.5-0.8b"


async def test_swap_on_a_wedged_slot_does_not_abort_in_the_unload_drain(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """``unload()`` re-raises when the stop fails — swap must not ride that path.

    A ``failed`` unit's stop can time out; ``unload`` then stamps ERROR and
    re-raises, so ``swap`` aborted BEFORE its load ever ran and the quadlet kept
    the old, crashing model. Same trap ``restart()`` escaped in #1224.
    """
    sm = SlotManager()
    _time_out_readiness(container_stub)
    container_stub.unit_failure_by_slot["chat"] = CRASH_LOOP_REASON
    with pytest.raises(SlotSpawnFailed):
        await sm.load("chat")
    assert sm._current_state("chat") == SlotState.ERROR

    # The wedged unit's stop blows up, exactly as a failed unit's can.
    def _stop_wedges(cfg: dict) -> None:
        raise TimeoutError("systemctl stop timed out on a failed unit")

    container_stub.unload_sync = _stop_wedges  # type: ignore[method-assign]
    # Fault cleared for the new model — the reload must reach it.
    container_stub.wait_ready = FakeContainerProvider.wait_ready.__get__(  # type: ignore[method-assign]
        container_stub, FakeContainerProvider
    )

    slot = await sm.swap("chat", "qwen3.5-0.8b")

    assert slot.state == SlotState.READY
    assert container_stub.load_calls[-1][0].get("model", {}).get("default") == "qwen3.5-0.8b"
    assert _model_default_on_disk(slot_root) == "qwen3.5-0.8b"


async def test_swap_survives_an_unwritable_toml(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The up-front persist is race protection, not a new failure mode."""
    sm = SlotManager()
    await sm.load("chat")

    calls = {"n": 0}
    orig = sm._persist_model_default

    async def _fail_first(slot_name: str, model_id: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("read-only filesystem")
        await orig(slot_name, model_id)

    sm._persist_model_default = _fail_first  # type: ignore[method-assign]

    slot = await sm.swap("chat", "qwen3.5-0.8b")

    assert slot.state == SlotState.READY
    assert container_stub.load_calls[-1][0].get("model", {}).get("default") == "qwen3.5-0.8b"
