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

from hal0.mcp.approval_queue import MAX_RESOLVED_ENTRIES, ApprovalQueue


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


# ── target_args (#3052) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entry_names_the_caller_supplied_target_args_present_in_args() -> None:
    """The entry lists the args that pick the resource, so the card shows them first."""
    q = ApprovalQueue()
    await q.enqueue(
        tool="slot_rename",
        args={"new_name": "x" * 200, "name": "agent"},
        client_id="pi",
        executor=_noop_executor,
        target_args=("name", "absent"),
    )
    assert q.list_pending()[0]["target_args"] == ["name"]


@pytest.mark.asyncio
async def test_entry_target_args_fall_back_to_the_primary_target_arg() -> None:
    q = ApprovalQueue()
    await q.enqueue(
        tool="memory_delete",
        args={"reason": "y" * 200, "ids": ["a", "b"]},
        client_id="pi",
        executor=_noop_executor,
    )
    assert q.list_pending()[0]["target_args"] == ["ids"]


@pytest.mark.asyncio
async def test_entry_target_args_empty_for_an_unmapped_tool() -> None:
    q = ApprovalQueue()
    await q.enqueue(
        tool="stack_load",
        args={"stack": "s"},
        client_id="pi",
        executor=_noop_executor,
    )
    assert q.list_pending()[0]["target_args"] == []


# ── deny hook and withdraw (agent bridge, ADR-0043 decision 9) ─────────────


@pytest.mark.asyncio
async def test_deny_runs_the_deny_hook_not_the_executor() -> None:
    """A mirrored run approval tells Hermes about a denial through ``on_deny``."""
    ran: list[str] = []

    async def _exec(args: dict[str, Any]) -> str:
        ran.append("executor")
        return "approved"

    async def _on_deny(args: dict[str, Any]) -> str:
        ran.append(f"deny:{args['run_id']}")
        return "denied in hermes"

    q = ApprovalQueue()
    aid = await q.enqueue(
        tool="agent_run_approval",
        args={"run_id": "run_1", "request_id": "req1"},
        client_id="bridge:cc",
        executor=_exec,
        on_deny=_on_deny,
    )
    result = await q.deny(aid)
    assert ran == ["deny:run_1"]
    assert result["state"] == "denied" and result["result"] == "denied in hermes"


@pytest.mark.asyncio
async def test_deny_hook_failure_is_recorded_and_the_entry_stays_denied() -> None:
    async def _on_deny(args: dict[str, Any]) -> None:
        raise RuntimeError("hermes is down")

    q = ApprovalQueue()
    aid = await q.enqueue(
        tool="agent_run_approval",
        args={"run_id": "run_1"},
        client_id="bridge:cc",
        executor=_noop_executor,
        on_deny=_on_deny,
    )
    result = await q.deny(aid)
    assert result["state"] == "denied"
    assert result["error"] == "RuntimeError: hermes is down"


@pytest.mark.asyncio
async def test_agent_run_approval_dedups_on_run_id() -> None:
    q = ApprovalQueue()
    first = await q.enqueue(
        tool="agent_run_approval",
        args={"run_id": "run_1", "request_id": "req1", "command": "rm a"},
        client_id="bridge:cc",
        executor=_noop_executor,
        target_args=("run_id",),
    )
    again = await q.enqueue(  # a replayed frame: incidental fields may differ
        tool="agent_run_approval",
        args={"run_id": "run_1", "request_id": "req1", "command": "rm a", "pattern_key": "rm"},
        client_id="bridge:cc",
        executor=_noop_executor,
        target_args=("run_id",),
    )
    other = await q.enqueue(
        tool="agent_run_approval",
        args={"run_id": "run_2", "request_id": "req1", "command": "rm a"},
        client_id="bridge:cc",
        executor=_noop_executor,
        target_args=("run_id",),
    )
    assert first == again != other
    entry = q.get(first)
    assert entry is not None and entry.hit_count == 2 and entry.target_args == ["run_id"]


@pytest.mark.asyncio
async def test_withdraw_retires_a_pending_entry_without_running_anything() -> None:
    """A request settled elsewhere leaves the inbox and frees its dedup slot."""
    ran: list[str] = []

    async def _hook(args: dict[str, Any]) -> None:
        ran.append("hook")

    q = ApprovalQueue()
    events: list[Any] = []
    async with q.subscribe() as sub:
        aid = await q.enqueue(
            tool="agent_run_approval",
            args={"run_id": "run_1", "request_id": "req1"},
            client_id="bridge:cc",
            executor=_hook,
            on_deny=_hook,
        )
        withdrawn = await q.withdraw(aid, "settled in Hermes")
        while not sub.empty():
            events.append(sub.get_nowait())
    assert withdrawn is not None
    assert withdrawn["state"] == "failed" and withdrawn["error"] == "settled in Hermes"
    assert ran == [] and q.list_pending() == []
    assert [e.kind for e in events] == ["enqueued", "failed"]
    fresh = await q.enqueue(
        tool="agent_run_approval",
        args={"run_id": "run_1", "request_id": "req2"},
        client_id="bridge:cc",
        executor=_hook,
    )
    assert fresh != aid


@pytest.mark.asyncio
async def test_withdraw_leaves_resolved_and_unknown_entries_alone() -> None:
    q = ApprovalQueue()
    aid = await q.enqueue(
        tool="slot_restart", args={"name": "primary"}, client_id="pi", executor=_noop_executor
    )
    await q.approve(aid)
    assert await q.withdraw(aid, "late") is None
    entry = q.get(aid)
    assert entry is not None and entry.state == "executed"
    assert await q.withdraw("nope", "late") is None


# -- a resolved entry lets go of its callables (ADR-0043: a skill push's executor pins its content) --


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["approve", "approve_failing", "deny", "withdraw"])
async def test_a_resolved_entry_holds_no_executor_or_deny_hook(how: str) -> None:
    async def failing(args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")

    q = ApprovalQueue()
    aid = await q.enqueue(
        tool="agent_skill_put",
        args={"name": "forum-style"},
        client_id="bridge:cc",
        executor=failing if how == "approve_failing" else _noop_executor,
        on_deny=_noop_executor,
    )
    entry = q.get(aid)
    assert entry is not None and entry._executor is not None and entry._on_deny is not None
    if how.startswith("approve"):
        await q.approve(aid)
    elif how == "deny":
        await q.deny(aid)
    else:
        await q.withdraw(aid, "superseded")
    assert entry.state != "pending"
    assert entry._executor is None and entry._on_deny is None


@pytest.mark.asyncio
async def test_a_pending_entry_keeps_its_executor_until_decided() -> None:
    q = ApprovalQueue()
    aid = await q.enqueue(
        tool="slot_restart", args={"name": "primary"}, client_id="pi", executor=_noop_executor
    )
    entry = q.get(aid)
    assert entry is not None and entry._executor is _noop_executor
    assert (await q.approve(aid))["state"] == "executed"  # and it still ran once


@pytest.mark.asyncio
async def test_resolved_entries_are_pruned_to_the_newest_two_hundred() -> None:
    """A skill push carries up to 48 KiB in its args, so the resolved history is bounded."""
    assert MAX_RESOLVED_ENTRIES == 200
    q = ApprovalQueue()
    pending = await q.enqueue(
        tool="model_pull", args={"model_id": "keep-me"}, client_id="c", executor=_noop_executor
    )
    ids: list[str] = []
    for n in range(MAX_RESOLVED_ENTRIES + 25):
        aid = await q.enqueue(
            tool="model_delete", args={"model_id": f"m{n}"}, client_id="c", executor=_noop_executor
        )
        ids.append(aid)
        how = n % 3
        if how == 0:
            await q.approve(aid)
        elif how == 1:
            await q.deny(aid)
        else:
            await q.withdraw(aid, "settled elsewhere")
    resolved = [e for e in q.list_all() if e["state"] != "pending"]
    assert len(resolved) == MAX_RESOLVED_ENTRIES
    # the oldest decisions went, the newest stayed
    assert all(q.get(aid) is None for aid in ids[:25])
    assert all(q.get(aid) is not None for aid in ids[25:])
    # a pending entry is never pruned
    assert [e["id"] for e in q.list_pending()] == [pending]
