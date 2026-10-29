"""In-memory approval queue for gated MCP tool calls.

Phase 8 (Agents v0.2) MCP servers split tool catalogs into autonomous
(executes immediately) and gated (requires owner approval) buckets.
Gated invocations land here; the owner then approves or
denies via ``POST /api/agent/approvals/{id}/{approve|deny}``.

Design notes
------------

* **In-memory only.** The queue lives in the FastAPI process for the
  same reason ``app.state.model_pull_jobs`` does — durability across
  restarts adds complexity we don't yet need. A future ADR can promote
  this to a persisted table.

* **Dedup rule.** At most one pending entry per ``(tool_name,
  primary_target)`` tuple, where ``primary_target`` is the first
  semantically-meaningful arg (e.g. ``model_id`` for ``model_pull``,
  ``slot_name`` for ``slot_*``). Without dedup, an agent that retries
  ``model_pull qwen3:0.6b`` five times floods the inbox with five
  approval prompts for the same operation; dedup collapses them to one
  with ``hit_count`` reflecting how often it was re-requested.

* **SSE subscribers.** The approvals UI subscribes via
  ``GET /api/agent/approvals/events`` (server-sent events) so the inbox
  updates live as the agent enqueues new requests and the owner
  approves / denies in another tab. ``subscribe()`` is an async
  generator exposed on the queue; the route adapts it to SSE frames.

* **Approve runs the tool.** ``approve(id)`` invokes the bound
  ``executor`` callable that the MCP server stashed at enqueue time.
  ``deny(id)`` transitions state and emits an event; the executor never
  runs. An entry enqueued with ``on_deny`` runs that hook instead, for a
  request whose owner must hear about a denial (a delegated Hermes run's
  approval, ADR-0043 decision 9, posts ``deny`` back to Hermes).

* **Withdraw.** ``withdraw(id, reason)`` retires a pending entry whose
  request was settled somewhere else (the run's approval was answered in
  Hermes, or the run ended), so the inbox stops offering a decision that
  can no longer apply and the dedup slot is free for the next request.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

log = structlog.get_logger("hal0.mcp.approvals")


ApprovalState = Literal["pending", "approved", "denied", "executed", "failed"]


# Map of tool_name → arg name that uniquely identifies the operation.
# Used for dedup so retries of the same destructive call collapse into
# one pending approval row instead of stacking.
_PRIMARY_TARGET_ARG: dict[str, str] = {
    "model_pull": "model_id",
    "model_delete": "model_id",
    "slot_create": "name",
    "slot_delete": "name",
    "slot_restart": "name",
    "capability_set": "slot",
    "config_write": "path",
    "provider_credential_write": "name",
    "memory_delete": "ids",  # list — dedup key joins them
    # A delegated Hermes run has at most one approval pending at a time, and a
    # reconnect can replay its frame: one inbox row per run (ADR-0043).
    "agent_run_approval": "run_id",
    # A bridge client's skill push or removal (ADR-0043 decision 10): one inbox row
    # per skill name, so repeated pushes of one skill are one decision.
    "agent_skill_put": "name",
    "agent_skill_delete": "name",
}


def _hash_args(args: dict[str, Any]) -> str:
    """Stable short digest of a full arg dict, for dedup fallback.

    Canonicalises with ``sort_keys`` so key order can't split a genuine
    retry into two rows, and ``default=str`` so any non-JSON value
    (rare in tool args) still hashes rather than raising. Truncated —
    collision risk across a per-process inbox is negligible.
    """
    canonical = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha1(canonical.encode()).hexdigest()[:16]


def _primary_target(tool: str, args: dict[str, Any]) -> str:
    """Return the dedup key suffix for a tool invocation.

    For a tool with a registered primary arg, the dedup key is that
    arg's value so retries of the same operation (``model_pull qwen3``)
    collapse to one pending row even when incidental body fields differ.

    For a tool WITHOUT a registered primary arg we fall back to a digest
    of the *full* arg set — NOT ``""``. The empty-string fallback was a
    collapse-all footgun: every distinct call to an unmapped gated tool
    (``profile_delete{name=A}`` vs ``profile_delete{name=B}``, every
    ``stack_*`` / ``upstream_*`` / ``profile_*`` op, …) shared the dedup
    key ``(tool, "")`` and so the SAME approval_id, silently discarding
    all but the first call's args. Approving then ran the wrong (first)
    target. Hashing the args keeps true retries deduped while giving
    distinct calls distinct approvals, and it self-maintains for any new
    gated tool added without a primary-arg entry.
    """
    key = _PRIMARY_TARGET_ARG.get(tool)
    if key is None:
        return _hash_args(args)
    value = args.get(key)
    if isinstance(value, list | tuple):
        return ",".join(sorted(str(v) for v in value))
    return str(value) if value is not None else ""


def _target_args(tool: str, args: dict[str, Any], named: Iterable[str]) -> list[str]:
    """The arg names that pick the resource a call acts on, in display order.

    ``named`` comes from the caller (the admin server passes the tool's URL
    path args); the tool's :data:`_PRIMARY_TARGET_ARG` is added after them.
    Only names present in ``args`` are kept. The approval cards render these
    first and in full, because the agent controls the order of ``args`` and a
    long body field placed first would otherwise push the target off a
    truncated preview (#3052).
    """
    primary = _PRIMARY_TARGET_ARG.get(tool)
    candidates = [*named, *([primary] if primary else [])]
    return [k for k in dict.fromkeys(candidates) if k in args]


@dataclass
class ApprovalEntry:
    """A single gated tool invocation awaiting owner action."""

    id: str
    tool: str
    args: dict[str, Any]
    client_id: str
    enqueued_at: float
    state: ApprovalState = "pending"
    hit_count: int = 1
    target_args: list[str] = field(default_factory=list)
    decided_at: float | None = None
    result: Any = None
    error: str | None = None
    # Stored separately from the dataclass-serialised payload — callers
    # must not see the bound executor through public API surface.
    _executor: Callable[[dict[str, Any]], Awaitable[Any]] | None = field(
        default=None, repr=False, compare=False
    )
    _on_deny: Callable[[dict[str, Any]], Awaitable[Any]] | None = field(
        default=None, repr=False, compare=False
    )

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe projection for REST / SSE consumers."""
        return {
            "id": self.id,
            "tool": self.tool,
            "args": self.args,
            "target_args": list(self.target_args),
            "client_id": self.client_id,
            "enqueued_at": self.enqueued_at,
            "state": self.state,
            "hit_count": self.hit_count,
            "decided_at": self.decided_at,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class _Event:
    """A queue event emitted to SSE subscribers."""

    kind: Literal["enqueued", "approved", "denied", "executed", "failed"]
    entry: dict[str, Any]


class ApprovalQueue:
    """Async-safe pending-approval queue with dedup + SSE fan-out.

    One instance per process — wired onto ``app.state.approval_queue``
    by the API factory (other team owns that hand-off; this module just
    provides the class).
    """

    def __init__(self) -> None:
        self._entries: dict[str, ApprovalEntry] = {}
        # Reverse index: (tool, primary_target) → approval_id
        # Used by enqueue() to find a pending dup before creating one.
        self._dedup: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()
        # Subscribers each get their own asyncio.Queue; emit() fans out
        # without holding the main lock so a slow consumer can't stall
        # an enqueue.
        self._subscribers: list[asyncio.Queue[_Event]] = []
        self._sub_lock = asyncio.Lock()

    # ── Enqueue ────────────────────────────────────────────────────────

    async def enqueue(
        self,
        tool: str,
        args: dict[str, Any],
        client_id: str,
        executor: Callable[[dict[str, Any]], Awaitable[Any]],
        target_args: Iterable[str] = (),
        on_deny: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> str:
        """Queue a gated tool call. Returns the approval id.

        ``target_args`` names the args that select the resource (for an admin
        tool, its URL path args); the entry carries them, plus the tool's
        primary target arg, as ``target_args`` for the approval cards.

        ``on_deny`` is awaited with the args when the owner denies the entry;
        most tools pass nothing, because a denied call simply never runs.

        If an existing pending entry shares the same dedup key, its
        ``hit_count`` bumps and the existing id is returned — no new
        row, no new event.
        """
        target = _primary_target(tool, args)
        dedup_key = (tool, target)
        async with self._lock:
            existing_id = self._dedup.get(dedup_key)
            if existing_id is not None:
                entry = self._entries.get(existing_id)
                if entry is not None and entry.state == "pending":
                    entry.hit_count += 1
                    log.info(
                        "mcp.approval.deduped",
                        approval_id=entry.id,
                        tool=tool,
                        hit_count=entry.hit_count,
                    )
                    return entry.id
                # Stale dedup pointer (entry resolved already); fall
                # through and create a fresh one.
                self._dedup.pop(dedup_key, None)

            approval_id = uuid.uuid4().hex
            entry = ApprovalEntry(
                id=approval_id,
                tool=tool,
                args=dict(args),
                target_args=_target_args(tool, args, target_args),
                client_id=client_id,
                enqueued_at=time.time(),
                _executor=executor,
                _on_deny=on_deny,
            )
            self._entries[approval_id] = entry
            self._dedup[dedup_key] = approval_id

        log.info(
            "mcp.approval.enqueued",
            approval_id=approval_id,
            tool=tool,
            client_id=client_id,
        )
        await self._emit(_Event(kind="enqueued", entry=entry.as_dict()))
        return approval_id

    # ── Inspect ────────────────────────────────────────────────────────

    def list_pending(self) -> list[dict[str, Any]]:
        """Snapshot of every entry still in the ``pending`` state."""
        return [e.as_dict() for e in self._entries.values() if e.state == "pending"]

    def list_all(self) -> list[dict[str, Any]]:
        """Snapshot of every entry (pending + resolved). Used by tests."""
        return [e.as_dict() for e in self._entries.values()]

    def get(self, approval_id: str) -> ApprovalEntry | None:
        return self._entries.get(approval_id)

    # ── Resolve ────────────────────────────────────────────────────────

    async def approve(self, approval_id: str) -> dict[str, Any]:
        """Owner approves the call; execute the bound executor.

        Two-phase: state goes pending → approved → (executed|failed).
        SSE consumers see all three transitions so the UI can show a
        spinner during the executor run.
        """
        async with self._lock:
            entry = self._entries.get(approval_id)
            if entry is None:
                raise KeyError(approval_id)
            if entry.state != "pending":
                raise ValueError(f"approval {approval_id} already resolved (state={entry.state})")
            entry.state = "approved"
            entry.decided_at = time.time()
            target = _primary_target(entry.tool, entry.args)
            self._dedup.pop((entry.tool, target), None)
            executor = entry._executor

        log.info(
            "mcp.approval.approved",
            approval_id=approval_id,
            tool=entry.tool,
        )
        await self._emit(_Event(kind="approved", entry=entry.as_dict()))

        if executor is None:
            # Defensive: queue invariant says executor exists at enqueue
            # time. Treat missing executor as a failure rather than a
            # silent no-op.
            entry.state = "failed"
            entry.error = "no executor bound to approval"
            await self._emit(_Event(kind="failed", entry=entry.as_dict()))
            return entry.as_dict()

        try:
            result = await executor(entry.args)
            entry.state = "executed"
            entry.result = result
            log.info(
                "mcp.approval.executed",
                approval_id=approval_id,
                tool=entry.tool,
            )
            await self._emit(_Event(kind="executed", entry=entry.as_dict()))
        except Exception as exc:  # pragma: no cover — surfaced to caller
            entry.state = "failed"
            entry.error = f"{type(exc).__name__}: {exc}"
            log.warning(
                "mcp.approval.failed",
                approval_id=approval_id,
                tool=entry.tool,
                error=entry.error,
            )
            await self._emit(_Event(kind="failed", entry=entry.as_dict()))
        return entry.as_dict()

    async def deny(self, approval_id: str) -> dict[str, Any]:
        """Owner denies the call; no executor runs.

        The entry's ``on_deny`` hook, when it has one, runs after the state
        change. Its return value lands in ``result``; a hook that raises leaves
        the entry ``denied`` with the failure in ``error``.
        """
        async with self._lock:
            entry = self._entries.get(approval_id)
            if entry is None:
                raise KeyError(approval_id)
            if entry.state != "pending":
                raise ValueError(f"approval {approval_id} already resolved (state={entry.state})")
            entry.state = "denied"
            entry.decided_at = time.time()
            target = _primary_target(entry.tool, entry.args)
            self._dedup.pop((entry.tool, target), None)
            on_deny = entry._on_deny

        log.info("mcp.approval.denied", approval_id=approval_id, tool=entry.tool)
        await self._emit(_Event(kind="denied", entry=entry.as_dict()))
        if on_deny is not None:
            try:
                entry.result = await on_deny(entry.args)
            except Exception as exc:
                entry.error = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "mcp.approval.deny_hook_failed",
                    approval_id=approval_id,
                    tool=entry.tool,
                    error=entry.error,
                )
        return entry.as_dict()

    async def withdraw(self, approval_id: str, reason: str) -> dict[str, Any] | None:
        """Retire a pending entry whose request was settled elsewhere.

        The entry becomes ``failed`` with ``reason`` as its error and leaves
        the inbox; neither the executor nor ``on_deny`` runs. Returns None,
        changing nothing, for an unknown id or an entry already resolved.
        """
        async with self._lock:
            entry = self._entries.get(approval_id)
            if entry is None or entry.state != "pending":
                return None
            entry.state = "failed"
            entry.error = reason
            entry.decided_at = time.time()
            target = _primary_target(entry.tool, entry.args)
            self._dedup.pop((entry.tool, target), None)

        log.info("mcp.approval.withdrawn", approval_id=approval_id, tool=entry.tool)
        await self._emit(_Event(kind="failed", entry=entry.as_dict()))
        return entry.as_dict()

    # ── SSE fan-out ────────────────────────────────────────────────────

    async def _emit(self, event: _Event) -> None:
        """Push ``event`` to every live subscriber (drop slow consumers).

        We snapshot the subscriber list under ``_sub_lock`` so a
        concurrent ``subscribe()`` / unsubscribe doesn't mutate the
        list we're iterating. Bounded subscriber queues (maxlen=256)
        protect against runaway memory if a client stops reading.
        """
        async with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer — drop the event for them. They'll
                # need to refresh from REST list_pending on reconnect.
                log.warning("mcp.approval.subscriber_dropped")

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[_Event]]:
        """Context-managed subscriber registration.

        Routes use this like::

            async with queue.subscribe() as q:
                while True:
                    event = await q.get()
                    yield f"data: {json.dumps(event.entry)}\\n\\n"

        The context manager guarantees the subscriber is unregistered
        on client disconnect / generator close, even on exception.
        """
        q: asyncio.Queue[_Event] = asyncio.Queue(maxsize=256)
        async with self._sub_lock:
            self._subscribers.append(q)
        try:
            yield q
        finally:
            async with self._sub_lock:
                with contextlib.suppress(ValueError):
                    self._subscribers.remove(q)


__all__ = [
    "ApprovalEntry",
    "ApprovalQueue",
    "ApprovalState",
]
