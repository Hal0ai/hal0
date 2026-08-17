"""#1897 — boot writes issued during the degraded-provider window get replayed.

hal0-api's terminal ``brain_lane`` boot phase publishes the agent identity
cards + self-report into the ``agents`` dataset. On a fresh install the
hindsight daemon is still cold-starting, so those writes land in the volatile
in-memory ``PgVectorProvider`` fallback. The #1613 self-heal swaps the durable
engine in later, but nothing re-issued the lost writes — the durable ``agents``
bank stayed missing until an unrelated hal0-api restart happened to re-run the
lane against a healthy engine.

These tests pin the replay contract: a lane that ran against a degraded
provider arms a heal hook, and the heal path runs it.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

import hal0.agents.hermes_provision as hp
import hal0.memory as mem
from hal0.api import BootState, _boot_brain_lane, _memory_reprobe_loop


class _Result:
    def __init__(self, details: dict[str, Any]) -> None:
        self.details = details


class _FakeDegraded:
    degraded = True


class _FakeHealthy:
    degraded = False


def _cfg() -> Any:
    return types.SimpleNamespace(memory=types.SimpleNamespace(engine="hindsight"))


@pytest.fixture
def phase_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def _mk(name: str, key: str) -> Any:
        def _phase(ctx: Any, *args: Any, **kwargs: Any) -> _Result:
            seen.append(name)
            return _Result({key: True})

        return _phase

    monkeypatch.setattr(hp, "_phase_namespace_register", _mk("namespace_register", "registered"))
    monkeypatch.setattr(hp, "_phase_brain_profile_seed", _mk("brain_profile_seed", "registered"))
    monkeypatch.setattr(hp, "_phase_self_report", _mk("self_report", "published"))
    return seen


def _app(provider: Any) -> Any:
    return types.SimpleNamespace(state=types.SimpleNamespace(memory_provider=provider))


@pytest.mark.asyncio
async def test_degraded_boot_lane_replays_after_provider_heals(
    phase_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    shell = mem.SelfHealingMemoryProvider(_FakeDegraded(), _cfg())
    app = _app(shell)

    await _boot_brain_lane(app, BootState())
    assert phase_calls == ["namespace_register", "brain_profile_seed", "self_report"]

    # Engine comes up; the heal path must re-issue the lost lane writes.
    monkeypatch.setattr(mem, "provider_from_config", lambda cfg: _FakeHealthy())
    assert shell.try_heal() is True
    await shell.run_heal_hooks()

    assert (
        phase_calls
        == [
            "namespace_register",
            "brain_profile_seed",
            "self_report",
        ]
        * 2
    )
    # One-shot: a replay must not arm another replay.
    await shell.run_heal_hooks()
    assert len(phase_calls) == 6


@pytest.mark.asyncio
async def test_healthy_boot_lane_arms_no_replay(phase_calls: list[str]) -> None:
    app = _app(_FakeHealthy())
    await _boot_brain_lane(app, BootState())
    assert phase_calls == ["namespace_register", "brain_profile_seed", "self_report"]


@pytest.mark.asyncio
async def test_lane_replays_inline_when_provider_healed_mid_lane(
    phase_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heal landing between the lane's writes and its arming still replays."""
    shell = mem.SelfHealingMemoryProvider(_FakeDegraded(), _cfg())
    app = _app(shell)

    monkeypatch.setattr(mem, "provider_from_config", lambda cfg: _FakeHealthy())

    def _heal_midway(ctx: Any, *args: Any, **kwargs: Any) -> _Result:
        phase_calls.append("self_report")
        shell.try_heal()
        return _Result({"published": True})

    monkeypatch.setattr(hp, "_phase_self_report", _heal_midway)

    await _boot_brain_lane(app, BootState())

    # Ran once for the (lost) degraded pass, once for the inline replay.
    assert phase_calls.count("namespace_register") == 2
    assert phase_calls.count("self_report") == 2


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

        async def run_heal_hooks(self) -> None:
            events.append("replay")

    await _memory_reprobe_loop(_Provider(), 0.0)
    assert events == ["probe1", "probe2", "replay"]


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
    await shell.run_heal_hooks()
    assert ran == ["second"]
