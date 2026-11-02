"""The fail-watcher and the per-request readiness gate on a halogen slot.

The engine can go silent under host memory pressure, and tearing it down then is
what leaves the driver holding its memory. So a /health that hangs must not read
as a death: only a connection that actually breaks does. These run the real
``ContainerProvider.health`` dispatch and ``HalogenProvider.liveness`` over a
scripted transport, beneath the real watchdog loop.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from hal0.providers import halogen as halogen_module
from hal0.providers.container import ContainerProvider
from hal0.providers.halogen import HalogenProvider
from hal0.slots import watchdog as wd_mod
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotStallCeiling, SlotState
from tests.slots.conftest import FakeContainerProvider


class _RealHealth(FakeContainerProvider):
    """A fake unit with the real health dispatch."""

    async def health(self, port: int, slot_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        return await ContainerProvider().health(port, slot_cfg)


@pytest.fixture
def provider(
    slot_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _RealHealth:
    (slot_root / "chat.toml").write_text(
        'name = "chat"\ntype = "llm"\ndevice = "gpu-rocm"\nport = 8081\n'
        'profile = "halogen"\n[model]\ndefault = "qwen3.8-flash-next-ht43"\n',
        encoding="utf-8",
    )
    fake = _RealHealth()
    fake.active.add("chat")
    monkeypatch.setattr("hal0.providers.container.container_provider", lambda: fake)
    monkeypatch.setattr(wd_mod, "_FAIL_WATCH_INTERVAL_S", 0.02)
    return fake


def _http(handler: Callable[[httpx.Request], Any]) -> Any:
    transport = httpx.MockTransport(handler)
    return patch.object(
        halogen_module, "async_client", lambda **kw: httpx.AsyncClient(transport=transport)
    )


async def _ready(sm: SlotManager) -> None:
    await sm._transition("chat", SlotState.READY, model_id="m", port=8081, force=True)


async def _wait_for(sm: SlotManager, target: SlotState, *, timeout_s: float = 3.0) -> SlotState:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and sm.state("chat") is not target:
        await asyncio.sleep(0.01)
    return sm.state("chat")


def _hangs(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("no answer")


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


# Probes the watcher must have made before a stall is judged. A /health that was
# counted as a strike would stamp ERROR after the second probe and the
# re-verification under the lock (the refused-connection test below: 3 probes,
# then ERROR) and the watcher would stop, so a fourth probe is proof the stall
# was tolerated, not just that it had not been caught yet.
_PROBES_PAST_THE_STRIKES = 4

# Only reached when the watcher stops probing (the failure these tests exist to
# catch), never on the passing path: the wait ends on the probes, not on elapsed
# time, so a loaded host only makes it slower, never wrong.
_PROBE_WAIT_CEILING_S = 15.0


class _Probes:
    """The probes the watcher makes, counted by the transport handler."""

    def __init__(self) -> None:
        self.n = 0
        self._enough = asyncio.Event()

    def hit(self) -> None:
        self.n += 1
        if self.n >= _PROBES_PAST_THE_STRIKES:
            self._enough.set()

    async def past_the_strikes(self, sm: SlotManager) -> None:
        """Wait for the probe that proves the stall was tolerated."""
        try:
            await asyncio.wait_for(self._enough.wait(), timeout=_PROBE_WAIT_CEILING_S)
        except TimeoutError:
            pytest.fail(
                f"the watcher stopped probing after {self.n} probes; the slot is {sm.state('chat')}"
            )
        assert self.n >= _PROBES_PAST_THE_STRIKES, "the watcher must have probed past the strikes"


async def test_a_hanging_health_never_stamps_error_on_a_ready_slot(provider: _RealHealth) -> None:
    probes = _Probes()

    def handler(request: httpx.Request) -> httpx.Response:
        probes.hit()
        return _hangs(request)

    sm = SlotManager()
    with _http(handler):
        await _ready(sm)
        await probes.past_the_strikes(sm)
    assert sm.state("chat") is SlotState.READY
    await sm._transition("chat", SlotState.OFFLINE, force=True)


async def test_a_503_never_stamps_error_on_a_ready_slot(provider: _RealHealth) -> None:
    """Upstream's ``engine_unresponsive``: the front end is up and the engine's PING
    failed, which is the silent engine this probe exists to leave alone."""
    probes = _Probes()

    def handler(request: httpx.Request) -> httpx.Response:
        probes.hit()
        return httpx.Response(503, text="engine_unresponsive")

    sm = SlotManager()
    with _http(handler):
        await _ready(sm)
        await probes.past_the_strikes(sm)
        assert sm.state("chat") is SlotState.READY
        assert await sm.container_readiness_check("chat") == (False, "starting")
        assert await sm._probe_health("chat", strict=True) is False
    await sm._transition("chat", SlotState.OFFLINE, force=True)


async def test_a_500_still_stamps_error_after_two_strikes(provider: _RealHealth) -> None:
    """Only the engine-silent status is a stall."""
    sm = SlotManager()
    with _http(lambda request: httpx.Response(500, text="boom")):
        await _ready(sm)
        assert await _wait_for(sm, SlotState.ERROR) is SlotState.ERROR


async def test_a_refused_connection_still_stamps_error_after_two_strikes(
    provider: _RealHealth,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _refused(request)

    sm = SlotManager()
    with _http(handler):
        await _ready(sm)
        assert await _wait_for(sm, SlotState.ERROR) is SlotState.ERROR
    # Two strikes, then the re-verification under the lock.
    assert calls["n"] == 3
    record = sm._states[sm._key("chat")]
    assert record.message == "model server failed /health probe"
    assert record.extra.get("health_ok") is False


async def test_a_stall_that_breaks_is_struck_from_then_on(provider: _RealHealth) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _hangs(request) if calls["n"] <= 3 else _refused(request)

    sm = SlotManager()
    with _http(handler):
        await _ready(sm)
        assert await _wait_for(sm, SlotState.ERROR) is SlotState.ERROR


async def test_readiness_holds_a_request_only_for_the_short_budget(provider: _RealHealth) -> None:
    """The per-request gate must not sit out the watcher's 35 s read."""

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30.0)
        return httpx.Response(200, json={})

    sm = SlotManager()
    started = time.monotonic()
    with _http(handler), patch.object(HalogenProvider, "readiness_budget_s", 0.05):
        assert await sm.container_readiness_check("chat") == (False, "starting")
    assert time.monotonic() - started < 5.0


async def test_readiness_maps_a_reported_stall_to_starting(provider: _RealHealth) -> None:
    sm = SlotManager()
    with _http(_hangs):
        assert await sm.container_readiness_check("chat") == (False, "starting")


async def test_readiness_passes_a_slot_that_answers(provider: _RealHealth) -> None:
    sm = SlotManager()
    with _http(lambda request: httpx.Response(200, json={})):
        assert await sm.container_readiness_check("chat") == (True, "ready")


async def test_a_stall_is_inconclusive_to_both_probe_modes(provider: _RealHealth) -> None:
    """Nobody demotes on it, and nobody promotes an adopted slot on it either."""
    sm = SlotManager()
    with _http(_hangs):
        assert await sm._probe_health("chat") is True
        assert await sm._probe_health("chat", strict=True) is False


# --- stall ceiling (#3282) ---------------------------------------------------


@pytest.fixture
def ceiling(monkeypatch: pytest.MonkeyPatch) -> int:
    """A tiny ceiling: 5 consecutive stalled probes over 0 s."""
    monkeypatch.setattr(wd_mod, "_STALL_CEILING_STRIKES", 5)
    monkeypatch.setattr(wd_mod, "_STALL_CEILING_S", 0.0)
    return 5


async def test_stalls_below_the_ceiling_stay_ready(provider: _RealHealth, ceiling: int) -> None:
    calls = {"n": 0}
    stall_until = ceiling - 1

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _hangs(request) if calls["n"] <= stall_until else httpx.Response(200, json={})

    sm = SlotManager()
    with _http(handler):
        await _ready(sm)
        deadline = time.monotonic() + 5.0
        while calls["n"] < stall_until + 4 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
    assert calls["n"] >= stall_until + 4
    assert sm.state("chat") is SlotState.READY
    await sm._transition("chat", SlotState.OFFLINE, force=True)


async def test_stalls_at_the_ceiling_stamp_error_naming_it(
    provider: _RealHealth, ceiling: int
) -> None:
    sm = SlotManager()
    with _http(_hangs):
        await _ready(sm)
        assert await _wait_for(sm, SlotState.ERROR) is SlotState.ERROR
    record = sm._states[sm._key("chat")]
    assert "stall ceiling" in (record.message or "")
    assert "5 consecutive probes" in (record.message or "")
    assert record.extra.get("health_ok") is False


async def test_an_answered_probe_resets_the_stall_counter(
    provider: _RealHealth, ceiling: int
) -> None:
    """ceiling-1 stalls, one answer, ceiling-1 more: never N in a row."""
    calls = {"n": 0}
    cycle = ceiling  # (ceiling-1) stalls then one 200, repeated
    total = cycle * 3

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={}) if calls["n"] % cycle == 0 else _hangs(request)

    sm = SlotManager()
    with _http(handler):
        await _ready(sm)
        deadline = time.monotonic() + 10.0
        while calls["n"] < total and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
    assert calls["n"] >= total
    assert sm.state("chat") is SlotState.READY
    await sm._transition("chat", SlotState.OFFLINE, force=True)


# --- the ceiling's ERROR sticks (#3282) --------------------------------------


async def _stalled_to_error(sm: SlotManager) -> None:
    await _ready(sm)
    assert await _wait_for(sm, SlotState.ERROR) is SlotState.ERROR
    assert sm._states[sm._key("chat")].extra.get("stall_ceiling") is True


async def test_the_ceiling_error_persists_across_status_and_list(
    provider: _RealHealth, ceiling: int
) -> None:
    """Adoption's non-strict probe reads a stall as healthy; it must not undo the ERROR."""
    sm = SlotManager()
    with _http(_hangs):
        await _stalled_to_error(sm)
        for _ in range(3):
            assert (await sm.status("chat")).state is SlotState.ERROR
            await sm.list()
            assert sm.state("chat") is SlotState.ERROR


async def test_the_ceiling_error_recovers_when_the_engine_answers(
    provider: _RealHealth, ceiling: int
) -> None:
    sm = SlotManager()
    with _http(_hangs):
        await _stalled_to_error(sm)
    with _http(lambda request: httpx.Response(200, json={})):
        await sm.status("chat")
        assert sm.state("chat") is SlotState.READY
        assert "stall_ceiling" not in sm._states[sm._key("chat")].extra
    await sm._transition("chat", SlotState.OFFLINE, force=True)


async def test_a_raising_probe_keeps_the_ceiling_error(provider: _RealHealth, ceiling: int) -> None:
    """A probe that raises proves nothing, so it must not read as the engine answering."""
    sm = SlotManager()
    with _http(_hangs):
        await _stalled_to_error(sm)

    async def boom(port: int, slot_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        raise RuntimeError("probe transport blew up")

    provider.health = boom  # type: ignore[method-assign]
    assert (await sm.status("chat")).state is SlotState.ERROR
    assert sm._states[sm._key("chat")].extra.get("stall_ceiling") is True


async def test_a_slow_probe_keeps_the_ceiling_error_and_returns_within_the_bound(
    provider: _RealHealth, ceiling: int
) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30.0)
        return httpx.Response(200, json={})

    sm = SlotManager()
    with _http(_hangs):
        await _stalled_to_error(sm)
    started = time.monotonic()
    with _http(slow), patch.object(HalogenProvider, "readiness_budget_s", 0.05):
        assert (await sm.status("chat")).state is SlotState.ERROR
    assert time.monotonic() - started < 5.0
    assert sm._states[sm._key("chat")].extra.get("stall_ceiling") is True


async def test_the_ceiling_error_reprobes_at_most_once_per_interval(
    provider: _RealHealth, ceiling: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """status() and list() must not each pay for a probe of a silent engine."""
    calls = {"n": 0}

    def counted(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _hangs(request)

    sm = SlotManager()
    with _http(_hangs):
        await _stalled_to_error(sm)
    with _http(counted):
        await asyncio.gather(*(sm.status("chat") for _ in range(4)))
        await sm.list()
        await sm.status("chat")
    assert calls["n"] == 1
    # An engine that recovers inside the interval waits for the next probe…
    with _http(lambda request: httpx.Response(200, json={})):
        assert (await sm.status("chat")).state is SlotState.ERROR
        # …and the next one past it clears the ERROR.
        monkeypatch.setattr(wd_mod, "_STALL_REPROBE_INTERVAL_S", 0.0)
        assert (await sm.status("chat")).state is SlotState.READY
    await sm._transition("chat", SlotState.OFFLINE, force=True)


async def test_a_new_ceiling_error_is_probed_at_once(provider: _RealHealth, ceiling: int) -> None:
    """The throttle belongs to one ERROR; a later stamp must not inherit it."""
    sm = SlotManager()
    with _http(_hangs):
        await _stalled_to_error(sm)
        assert (await sm.status("chat")).state is SlotState.ERROR
        await _stalled_to_error(sm)
    with _http(lambda request: httpx.Response(200, json={})):
        assert (await sm.status("chat")).state is SlotState.READY
    await sm._transition("chat", SlotState.OFFLINE, force=True)


async def test_the_ceiling_error_counts_as_holding_gpu_memory(
    provider: _RealHealth, ceiling: int
) -> None:
    from hal0.slots.gtt_guard import slot_may_hold_gpu_memory

    sm = SlotManager()
    with _http(_hangs):
        await _stalled_to_error(sm)
        assert slot_may_hold_gpu_memory(await sm.status("chat"))


async def test_lazy_load_refuses_a_ceiling_error_without_killing_the_engine(
    provider: _RealHealth, ceiling: int
) -> None:
    sm = SlotManager()
    terminated: list[str] = []

    async def spy(name: str, *a: Any, **kw: Any) -> None:
        terminated.append(name)

    sm.terminate = spy  # type: ignore[method-assign]
    with _http(_hangs):
        await _stalled_to_error(sm)
        with pytest.raises(SlotStallCeiling, match="hal0 slot restart chat"):
            await sm.load("chat")
    assert terminated == []
    assert sm.state("chat") is SlotState.ERROR


async def test_lazy_load_reloads_a_ceiling_error_whose_unit_has_died(
    provider: _RealHealth, ceiling: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No engine is left to protect, so this is an ordinary ERROR: never the refusal."""

    async def ready_at_once(self: SlotManager, *a: object, **kw: object) -> SlotState:
        return SlotState.READY

    monkeypatch.setattr(SlotManager, "_await_ready", ready_at_once)
    sm = SlotManager()
    with _http(_hangs):
        await _stalled_to_error(sm)
        provider.active.discard("chat")
        assert (await sm.load("chat")).state is SlotState.READY
    assert "stall_ceiling" not in sm._states[sm._key("chat")].extra
    await sm._transition("chat", SlotState.OFFLINE, force=True)


async def test_restart_still_recycles_a_ceiling_error(provider: _RealHealth, ceiling: int) -> None:
    sm = SlotManager()
    terminated: list[str] = []

    async def spy(name: str, *a: Any, **kw: Any) -> None:
        terminated.append(name)

    loaded: list[str] = []

    async def fake_load(name: str, *a: Any, **kw: Any) -> None:
        loaded.append(name)

    sm.terminate = spy  # type: ignore[method-assign]
    sm.load = fake_load  # type: ignore[method-assign]
    with _http(_hangs):
        await _stalled_to_error(sm)
        await sm.restart("chat")
    assert terminated == ["chat"]
    assert loaded == ["chat"]
    assert sm.state("chat") is SlotState.OFFLINE
    assert "stall_ceiling" not in sm._states[sm._key("chat")].extra


async def test_dispatcher_lazy_load_yields_non_retryable_slot_load_failed(
    provider: _RealHealth, ceiling: int
) -> None:
    from hal0.dispatcher.router import SlotLoadFailed

    sm = SlotManager()
    with _http(_hangs):
        await _stalled_to_error(sm)
        from hal0.dispatcher.router import Dispatcher

        d = Dispatcher.__new__(Dispatcher)
        d._slot_manager = sm  # type: ignore[attr-defined]
        call = type("C", (), {"slot_name": "chat", "upstream_name": "u"})()
        with pytest.raises(SlotLoadFailed):
            await d._ensure_slot_loaded_backend_aware(call)  # type: ignore[attr-defined]


async def test_no_stamp_while_a_request_is_in_flight(provider: _RealHealth, ceiling: int) -> None:
    sm = SlotManager()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _hangs(request)

    with _http(handler):
        await _ready(sm)
        sm._serving_count[sm._key("chat")] = 1
        deadline = time.monotonic() + 5.0
        while calls["n"] < ceiling * 3 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert calls["n"] >= ceiling * 3
        assert sm.state("chat") is SlotState.READY
        # …and the watcher is still alive: once idle, the stall is judged.
        sm._serving_count.pop(sm._key("chat"))
        assert await _wait_for(sm, SlotState.ERROR) is SlotState.ERROR


async def test_a_declined_stamp_keeps_the_slot_watched(provider: _RealHealth, ceiling: int) -> None:
    """The recheck under the lock answers: the watcher resets and keeps going."""
    sm = SlotManager()
    real = sm._watchdog._probe_stalled
    declined = {"n": 0}

    async def decline_once(name: str) -> bool:
        if declined["n"] == 0:
            declined["n"] += 1
            return False
        return await real(name)

    sm._watchdog._probe_stalled = decline_once  # type: ignore[method-assign]
    with _http(_hangs):
        await _ready(sm)
        # First ceiling decline leaves READY + a live watcher; the next lap stamps.
        assert await _wait_for(sm, SlotState.ERROR) is SlotState.ERROR
    assert declined["n"] == 1
