"""Unit tests for ``hal0.lemonade.idle.IdleDriver`` (ADR-0007 §Related, ADR-0006 §17).

The idle driver's contract:

  * Stale loaded model → ``POST /v1/unload`` called
  * Fresh loaded model → left alone
  * Lemond unreachable / 5xx → log + continue, no crash
  * Per-model unload failure → log + continue, sweep proceeds
  * Cancellation propagates cleanly through ``stop()``

We drive the driver via its public ``tick()`` method so tests are
deterministic and don't sleep on the real poll interval.
"""

from __future__ import annotations

import asyncio

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


# ── tick(): stale → unload ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_model_gets_unloaded() -> None:
    """A model whose last_use is older than idle_timeout_s must be unloaded."""
    now = 10_000.0
    calls: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(
                200,
                json={
                    "all_models_loaded": [
                        {"model_name": "stale", "last_use": now - 500.0},
                        {"model_name": "fresh", "last_use": now - 5.0},
                    ]
                },
            )
        if req.url.path == "/v1/unload":
            import json as _json

            body = _json.loads(req.content.decode())
            calls.append(body["model_name"])
            return httpx.Response(200, json={"status": "unloaded"})
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(
            client,
            idle_timeout_s=300.0,
            poll_interval_s=30.0,
            clock=lambda: now,
        )
        evicted = await driver.tick()
        assert evicted == 1
        # Only the stale model was unloaded.
        assert calls == ["stale"]


@pytest.mark.asyncio
async def test_fresh_model_not_unloaded() -> None:
    """Right on the boundary stays loaded — we use strict `>`, not `>=`."""
    now = 10_000.0
    calls: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(
                200,
                json={
                    # last_use exactly idle_timeout_s ago — boundary
                    # case, must NOT evict.
                    "all_models_loaded": [
                        {"model_name": "boundary", "last_use": now - 300.0},
                    ]
                },
            )
        if req.url.path == "/v1/unload":
            import json as _json

            calls.append(_json.loads(req.content.decode())["model_name"])
            return httpx.Response(200, json={"status": "unloaded"})
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client, idle_timeout_s=300.0, poll_interval_s=30.0, clock=lambda: now)
        assert await driver.tick() == 0
        assert calls == []


@pytest.mark.asyncio
async def test_entries_without_last_use_are_skipped() -> None:
    """No last_use timestamp → can't decide → leave the model alone."""
    now = 10_000.0
    calls: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(
                200,
                json={
                    "all_models_loaded": [
                        {"model_name": "no-ts"},  # missing last_use
                        {"model_name": "bad-ts", "last_use": "yesterday"},
                    ]
                },
            )
        if req.url.path == "/v1/unload":
            import json as _json

            calls.append(_json.loads(req.content.decode())["model_name"])
            return httpx.Response(200)
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client, idle_timeout_s=300.0, clock=lambda: now)
        assert await driver.tick() == 0
        assert calls == []


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
    now = 10_000.0
    attempts: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(
                200,
                json={
                    "all_models_loaded": [
                        {"model_name": "first", "last_use": now - 1000.0},
                        {"model_name": "second", "last_use": now - 1000.0},
                    ]
                },
            )
        if req.url.path == "/v1/unload":
            import json as _json

            name = _json.loads(req.content.decode())["model_name"]
            attempts.append(name)
            if name == "first":
                return httpx.Response(500, json={"detail": "boom"})
            return httpx.Response(200, json={"status": "unloaded"})
        return httpx.Response(404)

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        driver = IdleDriver(client, idle_timeout_s=300.0, clock=lambda: now)
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
