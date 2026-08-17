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

import types
from typing import Any

import pytest

import hal0.agents.hermes_provision as hp
import hal0.memory as mem
from hal0.api import BootState, _boot_brain_lane, _memory_reprobe_loop

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

    assert await _boot_brain_lane(_app(shell), BootState()) is True
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

    assert await _boot_brain_lane(_app(provider), BootState()) is True
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

    # try_heal() drained (empty) hooks already, so the replay ran inline —
    # for the two lost steps only.
    assert lane.count("namespace_register") == 2
    assert lane.count("brain_profile_seed") == 2
    assert lane.count("self_report") == 1


@pytest.mark.asyncio
async def test_failed_replay_stays_armed_for_the_next_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy engine can still fail a retain — that must not end the retry."""
    shell = mem.SelfHealingMemoryProvider(_FakeDegraded(), _cfg())
    lane = _Lane(shell, monkeypatch, failing=frozenset({"self_report"}))

    assert await _boot_brain_lane(_app(shell), BootState()) is False

    monkeypatch.setattr(mem, "provider_from_config", lambda cfg: _FakeHealthy())
    assert shell.try_heal() is True
    assert await shell.run_heal_hooks() is False
    assert shell.pending_heal_hooks == 1

    lane.failing = frozenset()
    assert await shell.run_heal_hooks() is True
    assert shell.pending_heal_hooks == 0
    assert lane.count("self_report") == 3


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


@pytest.mark.asyncio
async def test_volatile_write_counter_moves_on_the_real_fallback() -> None:
    """The counter the lane reads is the real PgVectorProvider's, not a mock."""
    provider = mem.PgVectorProvider()
    before = provider.volatile_writes
    await provider.add("card", dataset="agents")
    assert provider.volatile_writes == before + 1
