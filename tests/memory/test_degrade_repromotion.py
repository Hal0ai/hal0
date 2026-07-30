"""The boot degrade ladder must have a rung going back UP.

#1301 made ``HindsightProvider.degraded`` a live property, so an outage
*after* boot self-heals. A degrade *at* boot did not, and it is the case where
nothing recovers on its own: hal0-api and hindsight-api race on reboot,
hal0-api wins, probes a daemon that is still starting, falls back to the
volatile ``PgVectorProvider`` — whose ``degraded`` is a hard-coded ``True`` —
and then never re-reads that decision. The daemon comes up seconds later and
the box runs on storage that loses everything on restart, indefinitely, with a
stuck warning as its only signal.

These tests pin the fix and, more importantly, the constraints on it. A naive
re-promotion is worse than none:

* it must not LOSE the rows written while degraded (promote and they are
  orphaned in a store nothing reads any more — a silent partial loss);
* it must not SPLIT them (half in Hindsight, half in the fallback, with reads
  going to only one);
* it must not DUPLICATE them on a retried drain;
* it must not FLAP between engines.

Each of those has a test below, because each is a way a "fix" could ship
looking correct.

No live daemon, no network, no root: the promote factory is a plain callable
and the clock is injected.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.memory.degrade import DegradedMemoryProvider
from hal0.memory.pgvector_provider import PgVectorProvider


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeHindsight:
    """Stands in for a promoted HindsightProvider. Durable, records adds."""

    degraded = False

    def __init__(self, *, fail_adds: int = 0) -> None:
        self.adds: list[dict[str, Any]] = []
        self.fail_adds = fail_adds
        self.graph_calls: list[tuple[bool, str | None]] = []
        self.rerank_calls: list[bool] = []

    async def add(self, text: str, **kwargs: Any) -> dict[str, str]:
        if self.fail_adds > 0:
            self.fail_adds -= 1
            raise RuntimeError("hindsight rejected the write")
        self.adds.append({"text": text, **kwargs})
        return {"id": kwargs.get("document_id") or "hs-1", "timestamp": "2026-07-29T00:00:00Z"}

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"id": a.get("document_id"), "text": a["text"]} for a in self.adds]

    async def list_items(self, **kwargs: Any) -> dict[str, Any]:
        return {"items": list(self.adds), "next_cursor": None}

    async def delete(self, ids: list[str], **kwargs: Any) -> dict[str, int]:
        return {"deleted": len(ids)}

    async def recall(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def graph_status(self) -> dict[str, Any]:
        return {"enabled": False, "extraction_slot": "utility"}

    def set_graph_enabled(self, enabled: bool, extraction_slot: str | None = None) -> None:
        self.graph_calls.append((enabled, extraction_slot))

    def set_rerank_enabled(self, enabled: bool) -> None:
        self.rerank_calls.append(enabled)


def _dead_daemon() -> Any:
    raise ConnectionRefusedError("hindsight-api is not up yet")


def _provider(promote: Any, *, clock: _FakeClock | None = None, interval_s: float = 30.0):
    return DegradedMemoryProvider(
        promote=promote,
        fallback=PgVectorProvider(),
        interval_s=interval_s,
        clock=clock or _FakeClock(),
    )


# ── THE regression: a boot degrade must not be permanent ────────────────────


@pytest.mark.asyncio
async def test_promotes_once_the_daemon_comes_up() -> None:
    """The whole point. Before this, a box that lost the boot race stayed on
    volatile storage for the life of the process."""
    clock = _FakeClock()
    engine = _FakeHindsight()
    state = {"up": False}

    def promote() -> Any:
        if not state["up"]:
            raise ConnectionRefusedError("still starting")
        return engine

    provider = _provider(promote, clock=clock)

    # Boot: daemon down. Degraded, serving from volatile storage.
    assert provider.degraded is True
    await provider.add("written while degraded")
    assert provider.promoted is False

    # hindsight-api finishes starting.
    state["up"] = True
    clock.advance(31)

    # The next ordinary call notices.
    await provider.search("anything")
    assert provider.promoted is True, "boot degrade never re-promoted — the whole bug"
    assert provider.degraded is False


@pytest.mark.asyncio
async def test_stays_degraded_while_the_daemon_stays_down() -> None:
    clock = _FakeClock()
    provider = _provider(_dead_daemon, clock=clock)
    for _ in range(5):
        clock.advance(31)
        await provider.search("x")
    assert provider.promoted is False
    assert provider.degraded is True


# ── data safety: the reason a naive promotion would be wrong ────────────────


@pytest.mark.asyncio
async def test_rows_written_while_degraded_are_replayed_on_promotion() -> None:
    """Promotion without a drain orphans these rows: they still exist, but
    nothing ever reads them again. That is a silent partial loss."""
    clock = _FakeClock()
    engine = _FakeHindsight()
    provider = _provider(lambda: engine, clock=clock, interval_s=30.0)
    provider._interval_s = -1  # freeze auto-promotion so we control the moment

    await provider.add("first", tags=["a"], metadata={"k": "v"})
    await provider.add("second")
    assert provider.volatile_rows == 2

    provider._interval_s = 30.0
    clock.advance(31)
    await provider.search("trigger")

    assert provider.promoted is True
    assert [a["text"] for a in engine.adds] == ["first", "second"]
    # Metadata/tags survive the replay — a drain that dropped them would be a
    # quieter version of the same loss.
    assert engine.adds[0]["tags"] == ["a"]
    assert engine.adds[0]["metadata"] == {"k": "v"}
    # And the volatile copies are released only after the drain succeeded.
    assert provider.volatile_rows == 0


@pytest.mark.asyncio
async def test_failed_drain_aborts_promotion_and_keeps_every_row_readable() -> None:
    """A daemon that answers /health but rejects writes must NOT promote.

    Promoting anyway would strand the un-drained rows. Staying degraded keeps
    the corpus whole and readable — no split.
    """
    clock = _FakeClock()
    engine = _FakeHindsight(fail_adds=1)  # first replayed row is rejected
    provider = _provider(lambda: engine, clock=clock)
    provider._interval_s = -1
    await provider.add("row-one")
    await provider.add("row-two")
    provider._interval_s = 30.0

    clock.advance(31)
    await provider.search("trigger")

    assert provider.promoted is False, "promoted despite an incomplete drain"
    assert provider.degraded is True
    assert provider.volatile_rows == 2, "a row was dropped by a failed drain"
    # Reads stay complete while degraded.
    page = await provider.list_items()
    assert len(page["items"]) == 2


@pytest.mark.asyncio
async def test_retried_drain_does_not_duplicate_already_replayed_rows() -> None:
    """The retry after a partial drain must be idempotent."""
    clock = _FakeClock()
    engine = _FakeHindsight(fail_adds=0)
    provider = _provider(lambda: engine, clock=clock)
    provider._interval_s = -1
    await provider.add("alpha")
    await provider.add("beta")

    # Fail on the SECOND row so the first is already accepted upstream.
    engine.fail_adds = 0
    original_add = engine.add
    calls = {"n": 0}

    async def flaky_add(text: str, **kwargs: Any) -> dict[str, str]:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("transient")
        return await original_add(text, **kwargs)

    engine.add = flaky_add  # type: ignore[method-assign]
    provider._interval_s = 30.0
    clock.advance(31)
    await provider.search("attempt-1")
    assert provider.promoted is False
    assert [a["text"] for a in engine.adds] == ["alpha"]

    # Retry: alpha must not be sent twice.
    clock.advance(31)
    await provider.search("attempt-2")
    assert provider.promoted is True
    assert [a["text"] for a in engine.adds] == ["alpha", "beta"], (
        "the retried drain duplicated a row that was already accepted"
    )


@pytest.mark.asyncio
async def test_replay_pins_the_original_id_as_document_id() -> None:
    """Belt and braces on idempotency: even a drain-tracking bug cannot
    duplicate, because the engine upserts on document_id."""
    clock = _FakeClock()
    engine = _FakeHindsight()
    provider = _provider(lambda: engine, clock=clock)
    provider._interval_s = -1
    result = await provider.add("pinned")
    volatile_id = result["id"]
    provider._interval_s = 30.0

    clock.advance(31)
    await provider.search("trigger")
    assert engine.adds[0]["document_id"] == volatile_id


# ── no flapping ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_never_demotes_after_promotion() -> None:
    """The ratchet turns once. A daemon that dies later is handled by
    HindsightProvider's own live flag, not by swapping engines back."""
    clock = _FakeClock()
    engine = _FakeHindsight()
    provider = _provider(lambda: engine, clock=clock)
    clock.advance(31)
    await provider.search("x")
    assert provider.promoted is True

    # The engine now reports itself unreachable (post-boot outage).
    engine.degraded = True
    clock.advance(10_000)
    await provider.search("x")

    assert provider.promoted is True, "demoted back to volatile storage — flap"
    assert provider._active is engine
    # ...but the degraded bit tells the truth, delegated to the live provider.
    assert provider.degraded is True


@pytest.mark.asyncio
async def test_probe_is_throttled_to_one_attempt_per_interval() -> None:
    """A dead daemon must not cost a blocking probe on every single call."""
    clock = _FakeClock()
    attempts = {"n": 0}

    def promote() -> Any:
        attempts["n"] += 1
        raise ConnectionRefusedError("down")

    provider = _provider(promote, clock=clock, interval_s=30.0)
    for _ in range(20):
        await provider.search("x")
    assert attempts["n"] == 1, f"probed {attempts['n']}x within one interval"

    clock.advance(31)
    await provider.search("x")
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_concurrent_calls_promote_exactly_once() -> None:
    import asyncio

    clock = _FakeClock()
    engine = _FakeHindsight()
    attempts = {"n": 0}

    def promote() -> Any:
        attempts["n"] += 1
        return engine

    provider = _provider(promote, clock=clock)
    clock.advance(31)
    await asyncio.gather(*(provider.search("x") for _ in range(10)))
    assert attempts["n"] == 1
    assert provider.promoted is True


# ── operator surface ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_promotion_can_be_disabled() -> None:
    clock = _FakeClock()
    engine = _FakeHindsight()
    provider = _provider(lambda: engine, clock=clock, interval_s=0)
    clock.advance(100_000)
    await provider.search("x")
    assert provider.promoted is False
    assert provider.degrade_state()["auto_promote"] is False


@pytest.mark.asyncio
async def test_promote_now_works_even_with_auto_promotion_disabled() -> None:
    """The explicit operator path (POST /api/memory/promote) must not be
    gated on the timer — that is the whole reason it exists."""
    engine = _FakeHindsight()
    provider = _provider(lambda: engine, interval_s=0)
    assert await provider.promote_now() is True
    assert provider.promoted is True


@pytest.mark.asyncio
async def test_promote_now_is_idempotent_and_reports_failure() -> None:
    provider = _provider(_dead_daemon, interval_s=0)
    assert await provider.promote_now() is False
    assert await provider.promote_now() is False
    assert provider.degraded is True


@pytest.mark.asyncio
async def test_degrade_state_exposes_what_is_at_risk() -> None:
    """`memory_degraded: true` says something is wrong; an operator also needs
    to know how much data is at stake and whether it is self-healing."""
    provider = _provider(_dead_daemon, interval_s=0)
    await provider.add("at risk")
    state = provider.degrade_state()
    assert state["degraded"] is True
    assert state["promoted"] is False
    assert state["volatile_rows"] == 1
    assert state["auto_promote"] is False


@pytest.mark.asyncio
async def test_runtime_toggles_survive_promotion() -> None:
    """An operator who enables graph extraction while degraded must not have
    it silently revert to the config value when the daemon shows up."""
    clock = _FakeClock()
    engine = _FakeHindsight()
    provider = _provider(lambda: engine, clock=clock)
    provider._interval_s = -1
    provider.set_graph_enabled(True, "utility")
    provider.set_rerank_enabled(True)
    provider._interval_s = 30.0

    clock.advance(31)
    await provider.search("trigger")
    assert provider.promoted is True
    assert (True, "utility") in engine.graph_calls
    assert True in engine.rerank_calls


# ── the factory actually wires it ───────────────────────────────────────────


def _cfg(engine: str = "hindsight") -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        memory=SimpleNamespace(
            engine=engine,
            unified_bank=True,
            embedding=SimpleNamespace(
                rerank_gateway_url="http://127.0.0.1:8080",
                rerank_model="builtin.jina-reranker-v1-tiny-en-q8",
                rerank_connect_timeout_s=1.0,
                rerank_read_timeout_s=8.0,
            ),
            graph=SimpleNamespace(enabled=False, extraction_slot="utility"),
        )
    )


def test_boot_degrade_returns_a_promoting_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare PgVectorProvider here IS the bug: it can never come back."""
    import hal0.memory as memory_pkg

    def dead(_cfg_arg: Any) -> Any:
        raise ConnectionRefusedError("hindsight-api is not up yet")

    monkeypatch.setattr(memory_pkg, "_build_hindsight_client", dead)
    provider = memory_pkg.provider_from_config(_cfg())

    assert isinstance(provider, DegradedMemoryProvider), (
        f"boot degrade returned {type(provider).__name__}, which has no path "
        "back to Hindsight — the box stays on volatile storage forever"
    )
    assert provider.degraded is True
    assert callable(provider.promote_now)


def test_explicitly_configured_pgvector_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """engine = "pgvector" is an operator's choice, not a failure. Promoting
    away from it would override the config rather than recover from an outage.
    """
    import hal0.memory as memory_pkg

    provider = memory_pkg.provider_from_config(_cfg(engine="pgvector"))
    assert isinstance(provider, PgVectorProvider)
    assert not isinstance(provider, DegradedMemoryProvider)


@pytest.mark.asyncio
async def test_engine_specific_attributes_reach_through_after_promotion() -> None:
    """memory_admin routes read ``provider.hindsight_client``; that must work
    once promoted, and behave as before (AttributeError) while degraded."""
    clock = _FakeClock()
    engine = _FakeHindsight()
    engine.hindsight_client = object()  # type: ignore[attr-defined]
    provider = _provider(lambda: engine, clock=clock, interval_s=0)

    with pytest.raises(AttributeError):
        _ = provider.hindsight_client

    assert await provider.promote_now() is True
    assert provider.hindsight_client is engine.hindsight_client
