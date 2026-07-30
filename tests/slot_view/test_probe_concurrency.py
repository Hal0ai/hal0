"""``GET /api/slots`` stays bounded with N slots and a slow upstream (#1427).

Measured on lxc105: 17–41 s for 19–29 slots, ~1 s per slot, because
``container_enrichment`` walked the slot set serially and each slot paid a
full connect timeout against a port with nothing listening. These tests pin
the two properties that fix rests on:

  * the per-slot probes overlap (wall-clock ≈ the slowest slot, not the sum);
  * one wedged slot cannot hold the whole list past ``_PROBE_TIMEOUT_S``.

Timings are asserted with generous headroom — the failure they guard against
is an order of magnitude, not a few ms.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import hal0.slot_view as sv_mod
from hal0.slot_view import SlotViewAggregator, container_enrichment
from hal0.slots.manager import Slot
from hal0.slots.state import SlotState

PROBE_S = 0.25


class SlowProvider:
    """Every probe sleeps ``delay`` — the "port with nothing listening" shape."""

    def __init__(self, delay: float = PROBE_S, wedged: set[str] | None = None) -> None:
        self.delay = delay
        self.wedged = wedged or set()
        self.max_in_flight = 0
        self._in_flight = 0

    def _enter(self) -> None:
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)

    def is_active(self, slot_name: str) -> bool:
        # Synchronous, exactly like the real provider — the aggregator is
        # responsible for pushing it off the event loop.
        import time

        time.sleep(self.delay)
        return True

    async def health(self, port: int) -> dict[str, Any]:
        self._enter()
        try:
            await asyncio.sleep(self.delay)
            return {"ok": True}
        finally:
            self._in_flight -= 1

    def running_image(self, slot_name: str) -> str | None:
        if slot_name in self.wedged:
            import time

            # Long relative to the probe deadline, short enough that the
            # abandoned executor thread doesn't stall the test session.
            time.sleep(3)
        return None

    def image_present(self, image: str) -> bool:
        return True


def _configs(n: int, prefix: str = "s") -> list[dict[str, Any]]:
    return [{"name": f"{prefix}{i}", "port": 8100 + i, "profile": ""} for i in range(n)]


async def test_probes_overlap_instead_of_summing() -> None:
    n = 10
    provider = SlowProvider(delay=PROBE_S)
    loop = asyncio.get_running_loop()
    started = loop.time()
    out = await container_enrichment(_configs(n), provider=provider)
    elapsed = loop.time() - started

    assert len(out) == n
    assert all(e["container_status"] == "running" for e in out.values())
    # Serial would be >= n * 2 * PROBE_S (is_active + health per slot) = 5 s.
    assert elapsed < n * PROBE_S, f"probes serialised: {elapsed:.2f}s for {n} slots"
    assert provider.max_in_flight > 1, "no probe overlap at all"


async def test_probe_fanout_is_bounded() -> None:
    """Concurrency is capped so 40 slots don't spawn 120 podman children."""
    provider = SlowProvider(delay=0.05)
    await container_enrichment(_configs(40), provider=provider)
    assert provider.max_in_flight <= sv_mod._PROBE_CONCURRENCY


async def test_one_wedged_slot_cannot_hold_the_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unreachable-upstream case: a probe that never returns is dropped.

    This is where the tens-of-seconds behaviour came from — nothing bounded
    the SUM of a slot's four independent IO waits.
    """
    monkeypatch.setattr(sv_mod, "_PROBE_TIMEOUT_S", 0.4)
    provider = SlowProvider(delay=0.01, wedged={"s3"})

    loop = asyncio.get_running_loop()
    started = loop.time()
    out = await container_enrichment(_configs(6), provider=provider)
    elapsed = loop.time() - started

    assert elapsed < 3.0, f"a wedged slot held the list for {elapsed:.2f}s"
    assert set(out) == {f"s{i}" for i in range(6)}
    # The wedged slot degrades rather than disappearing or 500ing.
    assert out["s3"]["container_status"] == "stopped"
    assert out["s3"]["container_health"] is False
    assert out["s2"]["container_status"] == "running"


# ── the whole GET /api/slots path ────────────────────────────────────────────


class _SM:
    def __init__(self, configs: list[dict[str, Any]]) -> None:
        self._configs = configs

    async def list(self) -> list[Slot]:
        return [
            Slot(c["name"], state=SlotState.READY, port=c["port"], model_id="m")
            for c in self._configs
        ]

    async def iter_configs(self) -> list[dict[str, Any]]:
        return self._configs


async def test_slot_list_snapshot_bounded_with_a_slow_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N slots + an upstream metrics fetch that never returns.

    ``_safe_metrics`` is the leg that reaches upstreams; it used to be
    awaited *after* the container probe and the capacity probe, so its
    latency added to theirs. It now overlaps them, and the aggregator still
    returns a full row set.
    """
    monkeypatch.setattr(sv_mod, "_PROBE_TIMEOUT_S", 1.0)

    async def fake_build(slots: Any, registry: Any = None, **kw: Any) -> dict[str, Any]:
        await asyncio.sleep(PROBE_S)
        return {}

    monkeypatch.setattr("hal0.slots.capacity.build_per_slot", fake_build)

    async def slow_metrics() -> dict[str, Any]:
        # Stands in for a remote fan-out that times out.
        await asyncio.sleep(PROBE_S)
        raise TimeoutError("upstream unreachable")

    configs = _configs(8)
    agg = SlotViewAggregator(
        _SM(configs),
        metrics=slow_metrics,
        container_provider=SlowProvider(delay=PROBE_S / 2),
        model_cache={},
        upstreams=SimpleNamespace(list=lambda: []),
        last_used_model={},
        slot_pull_jobs={},
    )

    loop = asyncio.get_running_loop()
    started = loop.time()
    views = await agg.snapshot()
    elapsed = loop.time() - started

    assert len(views) == 8
    assert elapsed < 2.0, f"GET /api/slots aggregation took {elapsed:.2f}s"
