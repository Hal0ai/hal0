"""Unit tests for ``hal0.lemonade.idle.IdleDriver`` (ADR-0007 §Related, ADR-0006 §17).

The idle driver's contract (post 2026-05-25 opaque-counter rewrite):

  * Fresh model (first sighting) → NEVER evicted on the same tick,
    regardless of its ``last_use`` value
  * Counter stays unchanged across enough ticks that wall-clock dwell
    exceeds ``idle_timeout_s`` → ``POST /v1/unload`` called
  * Counter changes during the wait window → idle clock resets
  * Model disappears from the loaded list then reappears (even with
    the same ``last_use``) → starts a fresh idle clock
  * Lemond unreachable / 5xx → log + continue, no crash
  * Per-model unload failure → log + continue, sweep proceeds
  * Cancellation propagates cleanly through ``stop()``

We drive the driver via its public ``tick()`` method so tests are
deterministic and don't sleep on the real poll interval. The driver
already takes an injectable ``clock`` callable; we wrap a mutable
container so each tick advances time without monkeypatching.
"""

from __future__ import annotations

import asyncio
import json as _json

import httpx
import pytest

from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.idle import (
    DEFAULT_IDLE_TIMEOUT_S,
    DEFAULT_POLL_INTERVAL_S,
    IdleDriver,
    _coerce_last_use,
    _extract_loaded_models,
)


def _mock_transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


class _Clock:
    """Mutable wall-clock for fast-forwarding through ticks."""

    def __init__(self, start: float = 10_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _HealthScript:
    """Replays a series of ``/v1/health`` responses across ticks.

    Each entry is the list to return under ``all_models_loaded`` on
    the next ``/v1/health`` call. ``/v1/unload`` records the model
    names it received.
    """

    def __init__(self, scripts: list[list[dict[str, object]]]) -> None:
        self._scripts = list(scripts)
        self.unload_calls: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            if not self._scripts:
                return httpx.Response(200, json={"all_models_loaded": []})
            entry = self._scripts.pop(0)
            return httpx.Response(200, json={"all_models_loaded": entry})
        if req.url.path == "/v1/unload":
            self.unload_calls.append(_json.loads(req.content.decode())["model_name"])
            return httpx.Response(200, json={"status": "unloaded"})
        return httpx.Response(404)


# ── tick(): opaque-counter idle semantics ─────────────────────────────


@pytest.mark.asyncio
async def test_fresh_model_not_evicted_on_first_tick() -> None:
    """A model we've never seen before must NEVER be evicted on the same tick.

    Regression guard for the 2026-05-25 bug: the old code computed
    ``age = now - last_use`` against an opaque counter that looked
    like a 2015-era epoch, so a freshly-loaded model was evicted 10s
    after its first /v1/health appearance.
    """
    clock = _Clock()
    # Use a wildly-out-of-band counter value — if the driver still
    # treated this as a unix epoch, age would be ~10000 - 1_420_647_618
    # < 0 (or vice versa) and behaviour would be wrong either way.
    script = _HealthScript(
        [
            [{"model_name": "fresh", "last_use": 1_420_647_618}],
        ]
    )
    async with _mock_transport(script) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client, idle_timeout_s=300.0, clock=clock)
        assert await driver.tick() == 0
        assert script.unload_calls == []


@pytest.mark.asyncio
async def test_unchanged_within_window_is_not_evicted() -> None:
    """Counter stays unchanged across N ticks where N*poll < idle_timeout.

    Wall-clock dwell is below the threshold, so no eviction.
    """
    clock = _Clock()
    # 9 health ticks, all reporting the same last_use counter.
    script = _HealthScript([[{"model_name": "idle", "last_use": 42}]] * 9)
    async with _mock_transport(script) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client, idle_timeout_s=300.0, poll_interval_s=30.0, clock=clock)
        for _ in range(9):
            assert await driver.tick() == 0
            clock.advance(30.0)  # 9 * 30 = 270s < 300s
        assert script.unload_calls == []


@pytest.mark.asyncio
async def test_unchanged_past_window_is_evicted() -> None:
    """Counter unchanged long enough that wall-clock dwell exceeds idle_timeout.

    The driver must eventually evict.
    """
    clock = _Clock()
    # 12 ticks at 30s = 330s dwell, with counter never moving.
    script = _HealthScript([[{"model_name": "idle", "last_use": 42}]] * 12)
    async with _mock_transport(script) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client, idle_timeout_s=300.0, poll_interval_s=30.0, clock=clock)
        unloaded = False
        for _ in range(12):
            evicted = await driver.tick()
            if evicted:
                unloaded = True
                break
            clock.advance(30.0)
        assert unloaded, "model should have been evicted once wall-clock dwell > idle_timeout"
        assert script.unload_calls == ["idle"]


@pytest.mark.asyncio
async def test_counter_change_resets_idle_clock() -> None:
    """A bump in last_use during the wait window resets the idle clock.

    Sequence: model sits idle 270s (under threshold), then gets used
    (counter bumps), then sits idle another 270s. Total wall-clock
    dwell is 540s but neither stretch alone exceeds 300s — so no
    eviction.
    """
    clock = _Clock()
    script = _HealthScript(
        # 9 ticks at last_use=100 (270s dwell), then 9 ticks at
        # last_use=200 (counter moved → reset → another 270s).
        [[{"model_name": "bumpy", "last_use": 100}]] * 9
        + [[{"model_name": "bumpy", "last_use": 200}]] * 9
    )
    async with _mock_transport(script) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client, idle_timeout_s=300.0, poll_interval_s=30.0, clock=clock)
        for _ in range(18):
            assert await driver.tick() == 0
            clock.advance(30.0)
        assert script.unload_calls == []


@pytest.mark.asyncio
async def test_disappear_then_reappear_starts_fresh_clock() -> None:
    """Unload + reload (same last_use even!) must NOT carry stale tracker state.

    The driver should drop tracker entries for models no longer in
    the loaded list, so a reappearance starts the idle window over.
    """
    clock = _Clock()
    script = _HealthScript(
        [
            # Tick 1: model present, first sighting.
            [{"model_name": "blip", "last_use": 42}],
            # Ticks 2-10: model present, counter unchanged → would
            # have dwelt 0..270s on the first run.
            *([[{"model_name": "blip", "last_use": 42}]] * 9),
            # Tick 11: model GONE (someone unloaded it externally).
            [],
            # Tick 12+: model back with the same last_use. This must
            # be treated as a fresh sighting — NOT evicted just
            # because the counter still matches the stale state.
            *([[{"model_name": "blip", "last_use": 42}]] * 12),
        ]
    )
    async with _mock_transport(script) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client, idle_timeout_s=300.0, poll_interval_s=30.0, clock=clock)

        # First sighting + 9 idle ticks (270s wall-clock dwell).
        for _ in range(10):
            assert await driver.tick() == 0
            clock.advance(30.0)

        # Model gone — tracker state must be dropped.
        assert await driver.tick() == 0
        clock.advance(30.0)

        # Model reappears. The reappearance counts as tick #1 for
        # a fresh idle clock. The driver must NOT evict on the same
        # tick — even though if it had kept state, total observed
        # dwell would be 330s > 300s.
        assert await driver.tick() == 0
        clock.advance(30.0)

        # Now run another 9 ticks (still under the fresh 300s window).
        for _ in range(9):
            assert await driver.tick() == 0
            clock.advance(30.0)

        assert script.unload_calls == []


@pytest.mark.asyncio
async def test_entries_without_last_use_are_skipped() -> None:
    """No last_use counter → can't decide → leave the model alone."""
    clock = _Clock()
    script = _HealthScript(
        [
            [
                {"model_name": "no-ts"},  # missing last_use
                {"model_name": "bad-ts", "last_use": "yesterday"},
            ]
        ]
    )
    async with _mock_transport(script) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client, idle_timeout_s=300.0, clock=clock)
        assert await driver.tick() == 0
        assert script.unload_calls == []


# ── tick(): resilience ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_unreachable_does_not_raise() -> None:
    """ConnectError on /v1/health → driver returns 0 evictions, no exception."""

    def h(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client)
        # tick() must NOT raise — the driver is supposed to survive
        # transient lemond outages.
        assert await driver.tick() == 0


@pytest.mark.asyncio
async def test_health_5xx_does_not_raise() -> None:
    """Lemonade 5xx on /v1/health → log + skip, no exception."""

    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "starting"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client)
        assert await driver.tick() == 0


@pytest.mark.asyncio
async def test_unload_failure_does_not_abort_sweep() -> None:
    """If one /v1/unload fails, the next stale model is still attempted."""
    clock = _Clock()
    attempts: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(
                200,
                json={
                    "all_models_loaded": [
                        {"model_name": "first", "last_use": 100},
                        {"model_name": "second", "last_use": 200},
                    ]
                },
            )
        if req.url.path == "/v1/unload":
            name = _json.loads(req.content.decode())["model_name"]
            attempts.append(name)
            if name == "first":
                return httpx.Response(500, json={"detail": "boom"})
            return httpx.Response(200, json={"status": "unloaded"})
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client, idle_timeout_s=300.0, poll_interval_s=30.0, clock=clock)
        # First tick: seed tracker state for both models, no eviction.
        assert await driver.tick() == 0
        # Advance past the idle timeout while both counters stay put.
        clock.advance(301.0)
        evicted = await driver.tick()
        # second one succeeded; first one's failure was swallowed.
        assert evicted == 1
        # Both were attempted (sweep didn't bail out after the first failure).
        assert attempts == ["first", "second"]


# ── lifecycle: start/stop ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_then_stop_is_clean() -> None:
    """Driver must shut down without leaking tasks or raising on cancel."""

    def h(_: httpx.Request) -> httpx.Response:
        # Empty health — nothing to evict, but the loop runs.
        return httpx.Response(200, json={"all_models_loaded": []})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client, poll_interval_s=0.05, idle_timeout_s=300.0)
        await driver.start()
        # Give the loop one tick.
        await asyncio.sleep(0.15)
        await driver.stop()
        # Idempotent — second stop is a no-op.
        await driver.stop()


@pytest.mark.asyncio
async def test_double_start_is_noop() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"all_models_loaded": []})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client, poll_interval_s=10.0)
        await driver.start()
        first_task = driver._task
        await driver.start()  # must not start a second task
        assert driver._task is first_task
        await driver.stop()


# ── shape helpers ─────────────────────────────────────────────────────


def test_extract_loaded_models_accepts_both_field_names() -> None:
    """Lemonade has used both ``loaded`` and ``all_models_loaded``."""
    a = list(_extract_loaded_models({"all_models_loaded": [{"model_name": "x"}]}))
    b = list(_extract_loaded_models({"loaded": [{"model_name": "y"}]}))
    assert a == [{"model_name": "x"}]
    assert b == [{"model_name": "y"}]


def test_extract_loaded_models_tolerates_bad_shapes() -> None:
    """Missing / wrong-type fields yield an empty iterable, not a crash."""
    assert list(_extract_loaded_models({})) == []
    assert list(_extract_loaded_models({"all_models_loaded": "nope"})) == []
    # Non-dict entries inside the list are dropped.
    assert list(_extract_loaded_models({"loaded": [{"ok": 1}, "skip", 42]})) == [{"ok": 1}]


def test_coerce_last_use_rules() -> None:
    assert _coerce_last_use(1234.5) == 1234.5
    assert _coerce_last_use(1234) == 1234.0
    assert _coerce_last_use(None) is None
    assert _coerce_last_use("yesterday") is None
    # Critical: bool must NOT pass as 0.0 / 1.0.
    assert _coerce_last_use(True) is None
    assert _coerce_last_use(False) is None
    # Opaque-counter regime: huge integers (Lemonade 10.6 ships values
    # like 1_420_647_618) are valid counter values, not epoch garbage.
    assert _coerce_last_use(1_420_647_618) == 1_420_647_618.0


# ── constructor validation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_constructor_rejects_nonpositive_intervals() -> None:
    client = LemonadeClient(http_client=_mock_transport(lambda _: httpx.Response(204)))
    try:
        with pytest.raises(ValueError):
            IdleDriver(client, idle_timeout_s=0.0)
        with pytest.raises(ValueError):
            IdleDriver(client, poll_interval_s=0.0)
        with pytest.raises(ValueError):
            IdleDriver(client, idle_timeout_s=-1.0)
    finally:
        await client.aclose()


def test_defaults_match_v0_1_x_policy() -> None:
    """Defaults are documented as matching hal0 v0.1.x (300s / 30s)."""
    assert DEFAULT_IDLE_TIMEOUT_S == 300.0
    assert DEFAULT_POLL_INTERVAL_S == 30.0
