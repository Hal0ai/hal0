"""Unit tests for :class:`hal0.mcp.approval_queue.ApprovalQueue`.

Covers:

* Enqueue + dedup by ``(tool, primary_target)``.
* List / approve / deny state transitions and dedup-pointer cleanup.
* SSE subscriber receives ``enqueued / approved / denied / executed``
  events in order.
* Approve runs the bound executor; failures land as ``failed``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hal0.mcp.approval_queue import ApprovalQueue


async def _noop_executor(args: dict[str, Any]) -> dict[str, Any]:
    return {"echo": args}


@pytest.mark.asyncio
async def test_enqueue_returns_new_id() -> None:
    q = ApprovalQueue()
    aid = await q.enqueue(
        tool="model_pull",
        args={"model_id": "qwen3:0.6b"},
        client_id="pi-coder",
        executor=_noop_executor,
    )
    assert isinstance(aid, str)
    pending = q.list_pending()
    assert len(pending) == 1
    assert pending[0]["tool"] == "model_pull"
    assert pending[0]["state"] == "pending"


@pytest.mark.asyncio
async def test_enqueue_dedups_same_tool_and_target() -> None:
    q = ApprovalQueue()
    first = await q.enqueue(
        tool="model_pull",
        args={"model_id": "qwen3:0.6b"},
        client_id="pi-coder",
        executor=_noop_executor,
    )
    second = await q.enqueue(
        tool="model_pull",
        args={"model_id": "qwen3:0.6b"},
        client_id="pi-coder",
        executor=_noop_executor,
    )
    assert first == second
    pending = q.list_pending()
    assert len(pending) == 1
    assert pending[0]["hit_count"] == 2


@pytest.mark.asyncio
async def test_enqueue_distinct_targets_not_deduped() -> None:
    q = ApprovalQueue()
    a = await q.enqueue(
        tool="model_pull",
        args={"model_id": "qwen3:0.6b"},
        client_id="pi",
        executor=_noop_executor,
    )
    b = await q.enqueue(
        tool="model_pull",
        args={"model_id": "qwen3:4b"},
        client_id="pi",
        executor=_noop_executor,
    )
    assert a != b
    assert len(q.list_pending()) == 2


@pytest.mark.asyncio
async def test_unmapped_tool_distinct_args_get_distinct_ids() -> None:
    """Regression: gated tools with no registered primary-target arg
    (``profile_delete``, ``stack_*``, ``upstream_*``, …) used to fall
    back to a ``""`` dedup key, collapsing EVERY distinct call onto one
    approval_id and silently discarding all but the first call's args.

    Distinct targets must now mint distinct approvals so approving one
    runs the operator's actual target, not the first-ever call's.
    """
    q = ApprovalQueue()
    a = await q.enqueue(
        tool="profile_delete",
        args={"name": "profile-a"},
        client_id="claude-code",
        executor=_noop_executor,
    )
    b = await q.enqueue(
        tool="profile_delete",
        args={"name": "profile-b"},
        client_id="claude-code",
        executor=_noop_executor,
    )
    assert a != b, "distinct profile_delete targets collapsed onto one approval_id"
    pending = q.list_pending()
    assert len(pending) == 2
    names = sorted(e["args"]["name"] for e in pending)
    assert names == ["profile-a", "profile-b"]
    assert all(e["hit_count"] == 1 for e in pending)


@pytest.mark.asyncio
async def test_unmapped_tool_identical_args_still_dedup() -> None:
    """A genuine retry of an unmapped gated tool (identical args) should
    still collapse to one row — the hash-of-args fallback keeps
    retry-dedup working, only distinct args diverge."""
    q = ApprovalQueue()
    first = await q.enqueue(
        tool="stack_delete",
        args={"slug": "prod"},
        client_id="claude-code",
        executor=_noop_executor,
    )
    second = await q.enqueue(
        tool="stack_delete",
        args={"slug": "prod"},
        client_id="claude-code",
        executor=_noop_executor,
    )
    assert first == second
    pending = q.list_pending()
    assert len(pending) == 1
    assert pending[0]["hit_count"] == 2


@pytest.mark.asyncio
async def test_unmapped_tool_approve_runs_correct_target() -> None:
    """After the collapse fix, approving the second-target entry must run
    the executor with THAT target's args — not the first call's."""
    captured: list[dict[str, Any]] = []

    async def _exec(args: dict[str, Any]) -> dict[str, Any]:
        captured.append(dict(args))
        return {"deleted": args.get("name")}

    q = ApprovalQueue()
    await q.enqueue(
        tool="profile_delete",
        args={"name": "keep-a"},
        client_id="claude-code",
        executor=_exec,
    )
    bid = await q.enqueue(
        tool="profile_delete",
        args={"name": "delete-b"},
        client_id="claude-code",
        executor=_exec,
    )
    result = await q.approve(bid)
    assert result["state"] == "executed"
    assert result["result"] == {"deleted": "delete-b"}
    assert captured == [{"name": "delete-b"}]
    # The other target is untouched and still pending.
    still = q.list_pending()
    assert len(still) == 1
    assert still[0]["args"] == {"name": "keep-a"}


@pytest.mark.asyncio
async def test_approve_runs_executor_and_records_result() -> None:
    captured: dict[str, Any] = {}

    async def _exec(args: dict[str, Any]) -> dict[str, Any]:
        captured.update(args)
        return {"ran": True}

    q = ApprovalQueue()
    aid = await q.enqueue(
        tool="slot_delete",
        args={"name": "primary"},
        client_id="pi",
        executor=_exec,
    )
    result = await q.approve(aid)
    assert result["state"] == "executed"
    assert result["result"] == {"ran": True}
    assert captured == {"name": "primary"}
    assert q.list_pending() == []


@pytest.mark.asyncio
async def test_approve_failure_lands_failed_state() -> None:
    async def _boom(args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("nope")

    q = ApprovalQueue()
    aid = await q.enqueue(
        tool="slot_delete",
        args={"name": "primary"},
        client_id="pi",
        executor=_boom,
    )
    result = await q.approve(aid)
    assert result["state"] == "failed"
    assert "nope" in (result["error"] or "")


@pytest.mark.asyncio
async def test_deny_does_not_run_executor() -> None:
    calls: list[dict[str, Any]] = []

    async def _exec(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {"ran": True}

    q = ApprovalQueue()
    aid = await q.enqueue(
        tool="model_delete",
        args={"model_id": "x"},
        client_id="pi",
        executor=_exec,
    )
    result = await q.deny(aid)
    assert result["state"] == "denied"
    assert calls == []


@pytest.mark.asyncio
async def test_double_resolve_raises_value_error() -> None:
    q = ApprovalQueue()
    aid = await q.enqueue(
        tool="slot_restart",
        args={"name": "primary"},
        client_id="pi",
        executor=_noop_executor,
    )
    await q.deny(aid)
    with pytest.raises(ValueError):
        await q.deny(aid)
    with pytest.raises(ValueError):
        await q.approve(aid)


@pytest.mark.asyncio
async def test_unknown_id_raises_key_error() -> None:
    q = ApprovalQueue()
    with pytest.raises(KeyError):
        await q.approve("nope")
    with pytest.raises(KeyError):
        await q.deny("nope")


@pytest.mark.asyncio
async def test_subscriber_receives_lifecycle_events() -> None:
    q = ApprovalQueue()
    events: list[Any] = []

    async def _reader(ready: asyncio.Event) -> None:
        async with q.subscribe() as sub:
            ready.set()
            for _ in range(3):
                ev = await asyncio.wait_for(sub.get(), timeout=2.0)
                events.append(ev)

    ready = asyncio.Event()
    task = asyncio.create_task(_reader(ready))
    await ready.wait()
    aid = await q.enqueue(
        tool="slot_create",
        args={"name": "scratch"},
        client_id="pi",
        executor=_noop_executor,
    )
    await q.approve(aid)
    await asyncio.wait_for(task, timeout=2.0)

    kinds = [e.kind for e in events]
    assert kinds == ["enqueued", "approved", "executed"]


@pytest.mark.asyncio
async def test_dedup_pointer_cleared_after_resolution() -> None:
    """Approving an entry should free the dedup slot so a new enqueue for
    the same (tool, target) doesn't collide with the stale row."""
    q = ApprovalQueue()
    first = await q.enqueue(
        tool="slot_restart",
        args={"name": "primary"},
        client_id="pi",
        executor=_noop_executor,
    )
    await q.deny(first)
    second = await q.enqueue(
        tool="slot_restart",
        args={"name": "primary"},
        client_id="pi",
        executor=_noop_executor,
    )
    assert first != second
    assert q.list_pending()[0]["id"] == second
