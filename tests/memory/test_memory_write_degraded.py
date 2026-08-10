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
        return {"total": self.operations.get(bank, {}).get(str(status), 0)}


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
