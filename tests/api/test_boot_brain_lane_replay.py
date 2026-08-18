"""#1897 — boot writes issued during the degraded-provider window get replayed.

hal0-api's terminal ``brain_lane`` boot phase publishes the agent identity
cards + self-report into the ``agents`` dataset. On a fresh install the
hindsight daemon is still cold-starting, so those writes land in the volatile
in-memory ``PgVectorProvider`` fallback. The #1613 self-heal swaps the durable
engine in later, but nothing re-issued the lost writes — the durable ``agents``
bank stayed missing until an unrelated hal0-api restart happened to re-run the
lane against a healthy engine.

These tests pin the replay contract: the publishes whose writes the volatile
fallback actually swallowed (and only those) are armed for replay, the heal
path runs them, and a replay that does not land stays armed.
"""

from __future__ import annotations

import asyncio
import types
from typing import Any

import pytest

import hal0.agents.hermes_provision as hp
import hal0.memory as mem
from hal0.api import BootState, _boot_brain_lane, _BrainLaneReplay, _memory_reprobe_loop

_STEPS = (
    ("namespace_register", "registered"),
    ("brain_profile_seed", "registered"),
    ("self_report", "published"),
)


class _Result:
    def __init__(self, details: dict[str, Any]) -> None:
        self.details = details


class _FakeDegraded:
    """Stand-in for the in-memory fallback, including its write counter."""

    degraded = True

    def __init__(self) -> None:
        self.volatile_writes = 0


class _FakeHealthy:
    degraded = False


def _cfg() -> Any:
    return types.SimpleNamespace(memory=types.SimpleNamespace(engine="hindsight"))


def _app(provider: Any) -> Any:
    return types.SimpleNamespace(state=types.SimpleNamespace(memory_provider=provider))


class _Lane:
    """Fake brain-lane phases that write through whatever provider is live.

    A write made while the live delegate is the volatile fallback bumps that
    fallback's counter — exactly what ``PgVectorProvider.add`` does — which is
    the signal ``_boot_brain_lane`` reads to decide what it owes a replay.
    """

    def __init__(
        self,
        provider: Any,
        monkeypatch: pytest.MonkeyPatch,
        *,
        failing: frozenset[str] = frozenset(),
    ) -> None:
        self.provider = provider
        self.calls: list[str] = []
        self.failing = failing
        for name, key in _STEPS:
            monkeypatch.setattr(hp, f"_phase_{name}", self.phase(name, key))

    def phase(self, name: str, key: str) -> Any:
        def _phase(ctx: Any, *args: Any, **kwargs: Any) -> _Result:
            self.calls.append(name)
            self.write()
            return _Result({key: name not in self.failing})

        return _phase

    def write(self) -> None:
        target = getattr(self.provider, "target", self.provider)
        if getattr(target, "degraded", False):
            target.volatile_writes += 1

    def count(self, name: str) -> int:
        return self.calls.count(name)


@pytest.mark.asyncio
async def test_degraded_boot_lane_replays_after_provider_heals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = mem.SelfHealingMemoryProvider(_FakeDegraded(), _cfg())
    lane = _Lane(shell, monkeypatch)

    assert await _boot_brain_lane(_app(shell), BootState()) == ()
    assert lane.calls == ["namespace_register", "brain_profile_seed", "self_report"]

    # Engine comes up; the heal path must re-issue the lost lane writes.
    monkeypatch.setattr(mem, "provider_from_config", lambda cfg: _FakeHealthy())
    assert shell.try_heal() is True
    assert await shell.run_heal_hooks() is True

    assert lane.calls == ["namespace_register", "brain_profile_seed", "self_report"] * 2
    # One-shot: a successful replay must not arm another.
    assert shell.pending_heal_hooks == 0
    assert await shell.run_heal_hooks() is True
    assert len(lane.calls) == 6


@pytest.mark.asyncio
async def test_healthy_boot_lane_arms_no_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _FakeHealthy()
    lane = _Lane(provider, monkeypatch)

    assert await _boot_brain_lane(_app(provider), BootState()) == ()
    assert lane.calls == ["namespace_register", "brain_profile_seed", "self_report"]


@pytest.mark.asyncio
async def test_only_the_steps_that_hit_the_fallback_are_replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A heal landing mid-lane must not replay the writes that went durable.

    Replaying a publish whose write already reached the engine races that
    engine's asynchronous retain — the dedupe search that makes a replay safe
    may not see it yet — and duplicates the card.
    """
    shell = mem.SelfHealingMemoryProvider(_FakeDegraded(), _cfg())
    lane = _Lane(shell, monkeypatch)
    monkeypatch.setattr(mem, "provider_from_config", lambda cfg: _FakeHealthy())

    def _heal_then_write(ctx: Any, *args: Any, **kwargs: Any) -> _Result:
        lane.calls.append("self_report")
        shell.try_heal()  # engine comes up before this step's write
        lane.write()
        return _Result({"published": True})

    monkeypatch.setattr(hp, "_phase_self_report", _heal_then_write)

    await _boot_brain_lane(_app(shell), BootState())
    assert await shell.run_heal_hooks() is True

    # Only the two publishes the fallback swallowed are re-issued.
    assert lane.count("namespace_register") == 2
    assert lane.count("brain_profile_seed") == 2
    assert lane.count("self_report") == 1


@pytest.mark.asyncio
async def test_replay_is_armed_before_the_publishes_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A heal draining mid-lane must not find an empty, droppable replay."""
    shell = mem.SelfHealingMemoryProvider(_FakeDegraded(), _cfg())
    lane = _Lane(shell, monkeypatch)
    armed: list[int] = []

    def _phase(ctx: Any, *args: Any, **kwargs: Any) -> _Result:
        armed.append(shell.pending_heal_hooks)
        lane.calls.append("namespace_register")
        lane.write()
        return _Result({"registered": True})

    monkeypatch.setattr(hp, "_phase_namespace_register", _phase)

    await _boot_brain_lane(_app(shell), BootState())
    assert armed == [1]


@pytest.mark.asyncio
async def test_replay_hook_reports_not_done_until_the_lane_hands_over() -> None:
    hook = _BrainLaneReplay(_app(None), BootState())

    # Drained while the lane is still running: stays armed, runs nothing.
    assert await hook() is False
    # Lane finished with nothing owed: the drain can drop it.
    hook.finish(())
    assert await hook() is True


@pytest.mark.asyncio
async def test_failed_replay_stays_armed_for_the_next_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy engine can still fail a retain — that must not end the retry."""
    shell = mem.SelfHealingMemoryProvider(_FakeDegraded(), _cfg())
    lane = _Lane(shell, monkeypatch, failing=frozenset({"self_report"}))

    assert await _boot_brain_lane(_app(shell), BootState()) == ("self_report",)

    monkeypatch.setattr(mem, "provider_from_config", lambda cfg: _FakeHealthy())
    assert shell.try_heal() is True
    assert await shell.run_heal_hooks() is False
    assert shell.pending_heal_hooks == 1

    lane.failing = frozenset()
    assert await shell.run_heal_hooks() is True
    assert shell.pending_heal_hooks == 0
    # The failing step is retried; the two that landed are NOT repeated.
    assert lane.count("self_report") == 3
    assert lane.count("namespace_register") == 2


@pytest.mark.asyncio
async def test_reprobe_loop_drains_heal_hooks() -> None:
    events: list[str] = []

    class _Provider:
        def __init__(self) -> None:
            self._attempts = 0

        def try_heal(self) -> bool:
            self._attempts += 1
            events.append(f"probe{self._attempts}")
            return self._attempts >= 2

        async def run_heal_hooks(self) -> bool:
            events.append("replay")
            return True

    await _memory_reprobe_loop(_Provider(), 0.0)
    assert events == ["probe1", "probe2", "replay"]


@pytest.mark.asyncio
async def test_reprobe_loop_retries_a_failed_drain_then_gives_up() -> None:
    drains = 0

    class _Provider:
        def try_heal(self) -> bool:
            return True

        async def run_heal_hooks(self) -> bool:
            nonlocal drains
            drains += 1
            return drains >= 3

    await _memory_reprobe_loop(_Provider(), 0.0)
    assert drains == 3

    drains = 0

    class _NeverLands(_Provider):
        async def run_heal_hooks(self) -> bool:
            nonlocal drains
            drains += 1
            return False

    await _memory_reprobe_loop(_NeverLands(), 0.0, max_replay_attempts=4)
    assert drains == 4


@pytest.mark.asyncio
async def test_not_ready_replay_does_not_spend_the_attempt_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short reprobe interval must not exhaust the cap while the lane runs.

    The hook is armed before the lane's publishes and reports not-ready until
    ``finish()``. Each tick of that state must be free: with a 0s interval the
    loop ticks far more than ``max_replay_attempts`` times here, and it must
    still be alive to drain the replay once the lane hands it over.
    """
    shell = mem.SelfHealingMemoryProvider(_FakeDegraded(), _cfg())
    hook = _BrainLaneReplay(_app(None), BootState())
    assert shell.add_heal_hook(hook) is True
    monkeypatch.setattr(mem, "provider_from_config", lambda cfg: _FakeHealthy())

    task = asyncio.create_task(_memory_reprobe_loop(shell, 0.0, max_replay_attempts=3))
    await asyncio.sleep(0.2)
    assert not task.done()
    assert shell.pending_heal_hooks == 1

    hook.finish(())  # lane finished with nothing owed
    await asyncio.wait_for(task, timeout=5.0)
    assert shell.pending_heal_hooks == 0


def test_add_heal_hook_reports_already_healed() -> None:
    shell = mem.SelfHealingMemoryProvider(_FakeHealthy(), _cfg())
    assert shell.try_heal() is True
    assert shell.add_heal_hook(lambda: None) is False


@pytest.mark.asyncio
async def test_heal_hook_failure_does_not_break_the_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = mem.SelfHealingMemoryProvider(_FakeDegraded(), _cfg())
    ran: list[str] = []

    def _boom() -> None:
        raise RuntimeError("replay exploded")

    assert shell.add_heal_hook(_boom) is True
    assert shell.add_heal_hook(lambda: ran.append("second")) is True

    monkeypatch.setattr(mem, "provider_from_config", lambda cfg: _FakeHealthy())
    assert shell.try_heal() is True
    assert await shell.run_heal_hooks() is False
    assert ran == ["second"]
    # The hook that raised is still owed; the one that landed is done.
    assert shell.pending_heal_hooks == 1


class _LogRecorder:
    """Captures structlog-style event names so tests can pin what fires."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def _record(self, event: str, **kwargs: Any) -> None:
        self.events.append(event)

    info = warning = error = _record


@pytest.fixture
def api_log(monkeypatch: pytest.MonkeyPatch) -> _LogRecorder:
    recorder = _LogRecorder()
    monkeypatch.setattr("hal0.api.log", recorder)
    return recorder


@pytest.mark.asyncio
async def test_memory_disabled_boot_emits_no_volatile_warning(
    monkeypatch: pytest.MonkeyPatch, api_log: _LogRecorder
) -> None:
    """[memory].enabled = false: every publish fails, nothing was volatile.

    Firing ``degraded_writes_volatile`` here would be a WARNING claiming
    durable data loss on EVERY boot of such a box — and #1897's own repro
    greps the journal for exactly that class of key.
    """
    lane = _Lane(None, monkeypatch, failing=frozenset(n for n, _ in _STEPS))

    owed = await _boot_brain_lane(_app(None), BootState())
    assert owed == ("namespace_register", "brain_profile_seed", "self_report")
    assert "brain_lane.degraded_writes_volatile" not in api_log.events
    assert "brain_lane.degraded_replay_inline" not in api_log.events
    # No inline replay fired: each phase ran exactly once.
    assert lane.calls == ["namespace_register", "brain_profile_seed", "self_report"]


@pytest.mark.asyncio
async def test_healthy_provider_publish_failure_is_not_called_volatile(
    monkeypatch: pytest.MonkeyPatch, api_log: _LogRecorder
) -> None:
    """A transient publish failure on a durable provider is not data loss."""
    provider = _FakeHealthy()
    lane = _Lane(provider, monkeypatch, failing=frozenset({"self_report"}))

    assert await _boot_brain_lane(_app(provider), BootState()) == ("self_report",)
    assert "brain_lane.degraded_writes_volatile" not in api_log.events
    assert "brain_lane.degraded_replay_inline" not in api_log.events
    assert lane.count("self_report") == 1


@pytest.mark.asyncio
async def test_deliberate_pgvector_engine_still_warns_volatile(
    monkeypatch: pytest.MonkeyPatch, api_log: _LogRecorder
) -> None:
    """No self-heal shell and swallowed writes: the loss warning must fire."""
    provider = _FakeDegraded()
    _Lane(provider, monkeypatch)

    assert await _boot_brain_lane(_app(provider), BootState()) == ()
    assert "brain_lane.degraded_writes_volatile" in api_log.events


@pytest.mark.asyncio
async def test_volatile_write_counter_moves_on_the_real_fallback() -> None:
    """The counter the lane reads is the real PgVectorProvider's, not a mock."""
    provider = mem.PgVectorProvider()
    before = provider.volatile_writes
    await provider.add("card", dataset="agents")
    assert provider.volatile_writes == before + 1
