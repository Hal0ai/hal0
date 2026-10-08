"""#1420 — the retain pipeline needs its own health signal.

``HindsightProvider.degraded`` (#1301) answers exactly one question: *is the
daemon answering?* On lxc105 the daemon answered perfectly — it accepted every
retain, returned ``200`` with an ``operation_id``, and served recalls — while
the LLM fact-extraction step failed asynchronously against an offline
``utility`` slot. 170 failed operations, no durable fact newer than 8 days,
and ``/api/status.memory_degraded: false`` the whole time.

So the fix is a SECOND, distinct signal rather than widening the first: the
issue's own preferred option ("Keep ``memory_degraded`` meaning what it means
today so #1301's contract is unchanged"), and the only one that survives the
observed half-alive state — reads fine, writes silently dropped. Conflating
them would report the read path as broken when it demonstrably is not.

``memory_write_degraded`` is fed by two observations of the *write* path:

  1. a retain that raises (the synchronous half), and
  2. the engine's own failed-operation counter increasing between two samples
     (the asynchronous half — the shape lxc105 actually hit, where every
     retain call succeeds).

Both are held for a window rather than cleared by the next accepted retain: an
accepted retain proves the front door works, which is precisely the evidence
that was already misleading.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from hal0.memory.hindsight_provider import HindsightProvider


class _FakeClient:
    """Hindsight client whose retain outcome and operation counts the test drives."""

    def __init__(self) -> None:
        self.retain_error: Exception | None = None
        #: bank -> {status: total} as the engine's operations endpoint reports it.
        self.operations: dict[str, dict[str, int]] = {}
        self.operation_calls: list[tuple[str, str | None]] = []
        self.request_error: Exception | None = None
        #: bank -> [op_id, ...] failed ids the auto-retry sweep should see/retry.
        self.failed_ids: dict[str, list[str]] = {}
        self.retried: list[str] = []
        #: bank -> {status: [row, ...]} operation rows, NEWEST FIRST like the
        #: engine's ``ORDER BY created_at DESC`` list (#1833). When set for a
        #: status, its length is that status's ``total``.
        self.rows: dict[str, dict[str, list[dict[str, Any]]]] = {}

    async def retain(self, **_kwargs: Any) -> dict[str, str]:
        if self.retain_error is not None:
            raise self.retain_error
        return {"operation_id": "op-1"}

    async def recall(self, **_kwargs: Any) -> dict[str, list[Any]]:
        return {"results": []}

    async def request_json(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, **_kw: Any
    ) -> Any:
        if self.request_error is not None:
            raise self.request_error
        params = params or {}
        if method == "GET" and path.endswith("/operations") and params.get("limit") != 1:
            bank = path.split("/banks/", 1)[1].split("/", 1)[0]
            return {"operations": [{"id": op_id} for op_id in self.failed_ids.get(bank, [])]}
        if method == "POST" and path.endswith("/retry"):
            op_id = path.rsplit("/", 2)[1]
            self.retried.append(op_id)
            for ids in self.failed_ids.values():
                if op_id in ids:
                    ids.remove(op_id)
            return {"success": True}
        bank = path.split("/banks/", 1)[1].split("/", 1)[0]
        status = params.get("status")
        self.operation_calls.append((bank, status))
        rows = self.rows.get(bank, {}).get(str(status))
        if rows is None:
            total = self.operations.get(bank, {}).get(str(status), 0)
            return {"total": total, "operations": []}
        offset = int(params.get("offset") or 0)
        return {"total": len(rows), "operations": rows[offset : offset + 1]}


def _provider(client: _FakeClient) -> HindsightProvider:
    return HindsightProvider(client=client, client_id="hermes", unified_bank=True)


# ── the synchronous half: a retain that raises ───────────────────────────────


@pytest.mark.asyncio
async def test_retain_failure_marks_writes_degraded() -> None:
    client = _FakeClient()
    p = _provider(client)
    assert p.write_degraded is False

    client.retain_error = ConnectionError("connection refused")
    with pytest.raises(ConnectionError):
        await p.add("x", dataset="shared", client_id="hermes")

    assert p.write_degraded is True


@pytest.mark.asyncio
async def test_a_single_accepted_retain_does_not_clear_the_write_signal() -> None:
    """An accepted retain proves the front door works — the exact evidence
    that was already lying. Only the hold window clears it."""
    client = _FakeClient()
    p = _provider(client)
    client.retain_error = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await p.add("x", dataset="shared", client_id="hermes")

    client.retain_error = None
    await p.add("y", dataset="shared", client_id="hermes")

    assert p.write_degraded is True


@pytest.mark.asyncio
async def test_write_signal_clears_after_the_hold_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hal0.memory.hindsight_provider as hp

    fake_now = [1000.0]
    monkeypatch.setattr(hp.time, "monotonic", lambda: fake_now[0])

    client = _FakeClient()
    p = _provider(client)
    client.retain_error = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await p.add("x", dataset="shared", client_id="hermes")
    assert p.write_degraded is True

    fake_now[0] += hp._WRITE_FAILURE_HOLD_S + 1
    assert p.write_degraded is False


# ── the asynchronous half: the shape lxc105 actually hit ─────────────────────


@pytest.mark.asyncio
async def test_growing_failed_operation_count_degrades_writes_despite_200s() -> None:
    """Every retain returns 200 + operation_id; extraction fails behind it."""
    client = _FakeClient()
    p = _provider(client)

    client.operations["shared"] = {"failed": 170, "pending": 2, "processing": 0}
    first = await p.write_health()
    assert first["degraded"] is False, "first sample has no delta yet — no verdict"
    assert first["operations"]["failed"] == 170

    client.operations["shared"] = {"failed": 173, "pending": 5, "processing": 0}
    second = await p.write_health(max_age_s=0)

    assert second["degraded"] is True
    assert second["reason"] == "retain_operations_failing"
    assert p.write_degraded is True
    # ...and the READ path is still reported healthy — #1301's flag is untouched.
    assert p.degraded is False


@pytest.mark.asyncio
async def test_stable_failed_count_is_not_degraded() -> None:
    """A historic backlog on an otherwise healthy box must stay green — the
    counter is cumulative, so an absolute threshold would never recover."""
    client = _FakeClient()
    p = _provider(client)
    client.operations["shared"] = {"failed": 170, "pending": 0, "processing": 0}

    await p.write_health()
    out = await p.write_health(max_age_s=0)

    assert out["degraded"] is False
    assert out["reason"] == "ok"


@pytest.mark.asyncio
async def test_write_health_is_ttl_cached() -> None:
    """/api/status is polled every few seconds — the probe must not be."""
    client = _FakeClient()
    p = _provider(client)
    client.operations["shared"] = {"failed": 0, "pending": 0, "processing": 0}

    await p.write_health()
    calls_after_first = len(client.operation_calls)
    await p.write_health()

    assert len(client.operation_calls) == calls_after_first


@pytest.mark.asyncio
async def test_write_health_is_fail_soft_when_the_probe_errors() -> None:
    """An engine without the operations endpoint (or an outage) must not raise
    into /api/status — it degrades to whatever the retain path observed."""
    client = _FakeClient()
    p = _provider(client)
    client.request_error = RuntimeError("404 not found")

    out = await p.write_health()

    assert out["degraded"] is False
    assert out["reason"] == "unknown"
    assert out["operations"] is None


@pytest.mark.asyncio
async def test_a_raised_retain_wins_over_a_clean_probe() -> None:
    client = _FakeClient()
    p = _provider(client)
    client.operations["shared"] = {"failed": 0, "pending": 0, "processing": 0}
    client.retain_error = ConnectionError("refused")
    with pytest.raises(ConnectionError):
        await p.add("x", dataset="shared", client_id="hermes")

    out = await p.write_health()

    assert out["degraded"] is True
    assert out["reason"] == "retain_failed"
    assert "refused" in (out["last_error"] or "")


# ── stalled in-flight operations (#1833) ─────────────────────────────────────
#
# Retains wedged in ``pending``/``processing`` never touch the ``failed``
# counter, so a delta-only verdict read "landing" on a store that had never
# held a fact (ct152: facts=0, five ops processing, the engine's own worker
# logging ``[STUCK_STACK] age=603s threshold=600s``).


def _row(op_id: str, age_s: float, *, done_s: float | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    row: dict[str, Any] = {
        "id": op_id,
        "task_type": "batch_retain",
        "created_at": (now - timedelta(seconds=age_s)).isoformat(),
    }
    if done_s is not None:
        row["updated_at"] = (now - timedelta(seconds=done_s)).isoformat()
    return row


def _inflight(client: _FakeClient, *, pending: list[float], processing: list[float]) -> None:
    """Seed in-flight rows by age (seconds); stored newest-first like the engine."""
    client.rows["shared"] = {
        status: [_row(f"{status}-{i}", age) for i, age in enumerate(sorted(ages))]
        for status, ages in (("pending", pending), ("processing", processing))
    }
    client.operations["shared"] = {
        "failed": 0,
        "pending": len(pending),
        "processing": len(processing),
    }


@pytest.mark.asyncio
async def test_an_op_stuck_in_processing_degrades_writes_with_no_failures() -> None:
    """The ct152 shape: failed=0, ops in flight past the worker's own stuck
    threshold, nothing ever completed — must not read as landing. And it is
    right on the FIRST sample: no hal0-side history is needed."""
    client = _FakeClient()
    p = _provider(client)
    _inflight(client, pending=[30.0] * 5, processing=[603.0, 400.0, 300.0, 200.0, 100.0])

    out = await p.write_health()

    assert out["degraded"] is True
    assert out["reason"] == "retain_operations_stalled"
    assert "oldest 603s" in (out["last_error"] or "")
    assert out["operations"] == {"failed": 0, "pending": 5, "processing": 5}


@pytest.mark.asyncio
async def test_a_stale_head_is_found_behind_a_page_of_fresh_retains() -> None:
    """The list is newest-first: a wedged queue that keeps accepting retains
    has >50 fresh rows in front of its stale head. The oldest row (offset
    total-1) must still be the one aged."""
    client = _FakeClient()
    p = _provider(client)
    _inflight(client, pending=[10.0] * 60 + [900.0, 950.0], processing=[])

    out = await p.write_health()

    assert out["degraded"] is True
    assert out["reason"] == "retain_operations_stalled"
    assert "oldest 950s" in (out["last_error"] or "")


@pytest.mark.asyncio
async def test_hold_window_expiry_does_not_clear_while_ops_are_still_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The oscillation: failed grows, the retry ladder moves the ops back out
    of ``failed``, and the timed hold expires with nothing recovered."""
    import hal0.memory.hindsight_provider as hp

    fake_now = [1000.0]
    monkeypatch.setattr(hp.time, "monotonic", lambda: fake_now[0])
    client = _FakeClient()
    p = _provider(client)

    client.operations["shared"] = {"failed": 0, "pending": 4, "processing": 4}
    await p.write_health()
    client.operations["shared"] = {"failed": 4, "pending": 0, "processing": 4}
    assert (await p.write_health(max_age_s=0))["reason"] == "retain_operations_failing"

    # Retry ladder requeues them; the hold window then runs out.
    fake_now[0] += hp._WRITE_FAILURE_HOLD_S + 1
    _inflight(client, pending=[], processing=[700.0])
    out = await p.write_health(max_age_s=0)

    assert out["degraded"] is True
    assert out["reason"] == "retain_operations_stalled"


@pytest.mark.asyncio
async def test_young_in_flight_ops_are_not_stalled() -> None:
    client = _FakeClient()
    p = _provider(client)
    _inflight(client, pending=[5.0], processing=[120.0])

    out = await p.write_health()

    assert out["degraded"] is False
    assert out["reason"] == "ok"


@pytest.mark.asyncio
async def test_a_recent_completion_keeps_a_draining_backlog_green() -> None:
    """Evidence-based: an old op in a backlog that IS draining (an op
    completed inside the window) is not a stall — read from the newest
    completed row's ``updated_at``, not a count retention can prune."""
    client = _FakeClient()
    p = _provider(client)
    _inflight(client, pending=[900.0, 30.0], processing=[60.0])
    client.rows["shared"]["completed"] = [_row("done-1", 1200.0, done_s=45.0)]

    out = await p.write_health()

    assert out["degraded"] is False
    assert out["reason"] == "ok"


@pytest.mark.asyncio
async def test_a_completion_older_than_the_window_does_not_clear_a_stall() -> None:
    client = _FakeClient()
    p = _provider(client)
    _inflight(client, pending=[], processing=[900.0])
    client.rows["shared"]["completed"] = [_row("done-1", 5000.0, done_s=4000.0)]

    out = await p.write_health()

    assert out["reason"] == "retain_operations_stalled"


@pytest.mark.asyncio
async def test_the_first_completion_clears_a_stall() -> None:
    client = _FakeClient()
    p = _provider(client)
    _inflight(client, pending=[30.0], processing=[900.0])
    assert (await p.write_health())["reason"] == "retain_operations_stalled"

    client.rows["shared"]["completed"] = [_row("done-1", 900.0, done_s=2.0)]
    out = await p.write_health(max_age_s=0)

    assert out["degraded"] is False
    assert out["reason"] == "ok"


@pytest.mark.asyncio
async def test_unparseable_op_timestamps_are_not_read_as_a_stall() -> None:
    """No evidence of age is no evidence of a stall — fail soft to the
    existing verdict rather than reddening a healthy box."""
    client = _FakeClient()
    p = _provider(client)
    client.operations["shared"] = {"failed": 0, "pending": 1, "processing": 1}
    client.rows["shared"] = {
        "processing": [{"id": "op-a", "created_at": "t1"}],
        "pending": [{"id": "op-b"}],
    }

    out = await p.write_health()

    assert out["degraded"] is False
    assert out["reason"] == "ok"


@pytest.mark.asyncio
async def test_an_unparseable_completion_stamp_is_not_read_as_a_stall() -> None:
    client = _FakeClient()
    p = _provider(client)
    _inflight(client, pending=[], processing=[900.0])
    client.rows["shared"]["completed"] = [{"id": "done-1", "updated_at": None}]

    out = await p.write_health()

    assert out["degraded"] is False


# ── auto-retry of no-chat-model dead letters (#1792) ──────────────────────


@pytest.mark.asyncio
async def test_auto_retry_noop_when_nothing_failed() -> None:
    client = _FakeClient()
    p = _provider(client)
    client.operations["shared"] = {"failed": 0, "pending": 0, "processing": 0}

    assert await p.maybe_auto_retry_dead_letters() is None
    assert client.retried == []


@pytest.mark.asyncio
async def test_auto_retry_requeues_every_failed_op_on_the_tracked_bank() -> None:
    client = _FakeClient()
    p = _provider(client)
    client.failed_ids["shared"] = ["op-1", "op-2"]
    client.operations["shared"] = {"failed": 2, "pending": 0, "processing": 0}

    result = await p.maybe_auto_retry_dead_letters()

    assert result is not None
    assert result["bank"] == "shared"
    assert result["queued"] == 2
    assert result["skipped"] == 0
    assert sorted(client.retried) == ["op-1", "op-2"]


@pytest.mark.asyncio
async def test_auto_retry_respects_the_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.memory.hindsight_provider as hp

    fake_now = [1000.0]
    monkeypatch.setattr(hp.time, "monotonic", lambda: fake_now[0])

    client = _FakeClient()
    p = _provider(client)
    client.failed_ids["shared"] = ["op-1"]
    client.operations["shared"] = {"failed": 1, "pending": 0, "processing": 0}

    first = await p.maybe_auto_retry_dead_letters()
    assert first is not None

    client.failed_ids["shared"] = ["op-2"]
    client.operations["shared"] = {"failed": 1, "pending": 0, "processing": 0}
    fake_now[0] += hp._AUTO_RETRY_COOLDOWN_S - 1
    assert await p.maybe_auto_retry_dead_letters() is None
    assert "op-2" not in client.retried

    fake_now[0] += 2  # past the cooldown now
    second = await p.maybe_auto_retry_dead_letters()
    assert second is not None
    assert "op-2" in client.retried


@pytest.mark.asyncio
async def test_auto_retry_stops_after_the_sweep_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pipeline that keeps failing for a DIFFERENT reason after a model
    loads must stop being auto-retried and go back to reading as FAILING —
    this budget is what makes that happen."""
    import hal0.memory.hindsight_provider as hp

    fake_now = [1000.0]
    monkeypatch.setattr(hp.time, "monotonic", lambda: fake_now[0])

    client = _FakeClient()
    p = _provider(client)

    for i in range(hp._AUTO_RETRY_MAX_SWEEPS):
        client.failed_ids["shared"] = [f"op-{i}"]
        client.operations["shared"] = {"failed": 1, "pending": 0, "processing": 0}
        assert await p.maybe_auto_retry_dead_letters() is not None
        fake_now[0] += hp._AUTO_RETRY_COOLDOWN_S + 1

    client.failed_ids["shared"] = ["op-final"]
    client.operations["shared"] = {"failed": 1, "pending": 0, "processing": 0}
    assert await p.maybe_auto_retry_dead_letters() is None
    assert "op-final" not in client.retried
