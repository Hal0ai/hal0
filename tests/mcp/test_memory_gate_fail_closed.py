"""#1302 ratchet — an unconfigured memory gate must refuse, not execute.

``make_dispatcher(approval_queue=None)`` used to mean two incompatible things
at once:

1. "an admin dispatcher already classified and gated this call; run it" — the
   in-process executor path, correct and load-bearing; and
2. "nobody wired an approval queue to this server" — a misconfiguration.

Both produced the identical object, so the dispatcher could not tell them
apart and resolved the ambiguity in the dangerous direction: it executed. A
bulk ``memory_delete`` therefore ran with no operator in the loop whenever a
mount was built without a queue, and the only thing standing between that and
a live incident was that the single production call site happened to pass one.

That is a ratchet, not a live bug — and a ratchet is exactly what should be
nailed down before someone adds a second mount.

The fix names the two safe cases (``GATED_UPSTREAM``, ``TRUSTED_IN_PROCESS``)
so ``None`` keeps only its honest meaning — nobody decided — and fails closed.
``build_server`` is stricter still: it is by definition an outermost mount, so
neither bypass can be true there and it refuses at construction.

The gate itself still lives in ``make_dispatcher`` and classification still
lives in ``admin.is_gated``; nothing here moves either.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.mcp import memory
from hal0.mcp.approval_queue import ApprovalQueue


class _RecordingWrapper:
    """Records deletes so a test can prove one did NOT happen."""

    def __init__(self) -> None:
        self.delete_calls: list[dict[str, Any]] = []
        self.add_calls: list[dict[str, Any]] = []

    async def add(self, **kwargs: Any) -> dict[str, Any]:
        self.add_calls.append(kwargs)
        return {"id": "id-1", "timestamp": "2026-07-29T00:00:00Z"}

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def list_items(self, **kwargs: Any) -> dict[str, Any]:
        return {"items": [], "next_cursor": None}

    async def delete(self, **kwargs: Any) -> dict[str, Any]:
        self.delete_calls.append(kwargs)
        return {"deleted": len(kwargs.get("ids") or [])}


@pytest.fixture
def wrapper() -> _RecordingWrapper:
    return _RecordingWrapper()


# ── the hazard: no queue, no declared reason ────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_delete_refuses_when_no_queue_and_no_reason(
    wrapper: _RecordingWrapper,
) -> None:
    """THE regression. Before the fix this returned ok and deleted both ids."""
    disp = memory.make_dispatcher(
        wrapper,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: False,
        approval_queue=None,
    )
    out = await disp("memory_delete", {"ids": ["a", "b"]})

    assert out["status"] == "error"
    assert out["error"]["code"] == "mcp.memory_gate_unconfigured"
    assert wrapper.delete_calls == [], (
        "an unconfigured gate executed a bulk delete — this is the #1302 hole"
    )


@pytest.mark.asyncio
async def test_unconfigured_gate_does_not_break_non_destructive_tools(
    wrapper: _RecordingWrapper,
) -> None:
    """Failing closed is scoped to the gated subset.

    A delete-gate misconfiguration must not escalate into a total memory
    outage — the refusal above is the signal, and reads keep working so an
    operator can still see what is stored while they fix it.
    """
    disp = memory.make_dispatcher(
        wrapper,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: False,
        approval_queue=None,
    )
    assert (await disp("memory_add", {"text": "x"}))["status"] == "ok"
    assert (await disp("memory_search", {"query": "x"}))["status"] == "ok"
    assert (await disp("memory_list", {}))["status"] == "ok"
    # A SINGLE-id delete is not classified as bulk, so it is not gated and
    # must still run (admin.is_gated owns that threshold).
    assert (await disp("memory_delete", {"ids": ["only-one"]}))["status"] == "ok"


# ── the two declared bypasses still work ────────────────────────────────────


@pytest.mark.parametrize(
    "reason",
    [memory.GATED_UPSTREAM, memory.TRUSTED_IN_PROCESS],
    ids=["gated_upstream", "trusted_in_process"],
)
@pytest.mark.asyncio
async def test_declared_bypass_executes(
    wrapper: _RecordingWrapper, reason: memory.UngatedReason
) -> None:
    disp = memory.make_dispatcher(
        wrapper,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: False,
        approval_queue=reason,
    )
    out = await disp("memory_delete", {"ids": ["a", "b"]})
    assert out["status"] == "ok"
    assert wrapper.delete_calls[0]["ids"] == ["a", "b"]


def test_the_two_reasons_are_distinguishable() -> None:
    """They must not collapse back into one undifferentiated 'ungated'."""
    assert memory.GATED_UPSTREAM != memory.TRUSTED_IN_PROCESS
    assert memory.GATED_UPSTREAM.label == "gated_upstream"
    assert memory.TRUSTED_IN_PROCESS.label == "trusted_in_process"
    assert isinstance(memory.GATED_UPSTREAM, memory.UngatedReason)


# ── a real queue still gates ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_queue_still_enqueues(wrapper: _RecordingWrapper) -> None:
    queue = ApprovalQueue()
    disp = memory.make_dispatcher(
        wrapper,
        client_id_resolver=lambda: "pi-coder",
        private_resolver=lambda: False,
        approval_queue=queue,
    )
    out = await disp("memory_delete", {"ids": ["a", "b"]})
    assert out["status"] == "pending_approval"
    assert wrapper.delete_calls == []


# ── the argument cannot be omitted, or faked ────────────────────────────────


def test_make_dispatcher_requires_an_explicit_decision(wrapper: _RecordingWrapper) -> None:
    """Omitting the argument is no longer possible: there is no default.

    A default — any default — is what let a caller skip the decision without
    noticing. The TypeError is the point.
    """
    with pytest.raises(TypeError):
        memory.make_dispatcher(wrapper)  # type: ignore[call-arg]


def test_truthy_non_queue_is_rejected(wrapper: _RecordingWrapper) -> None:
    """A stale handle must not silently disarm the gate.

    Without this check ``approval_queue=True`` passes ``is not None``, the
    caller believes the call is gated, and the AttributeError only surfaces
    mid-delete — after the classification said "gate this".
    """
    with pytest.raises(TypeError, match="enqueue"):
        memory.make_dispatcher(
            wrapper,
            client_id_resolver=lambda: "pi-coder",
            private_resolver=lambda: False,
            approval_queue=True,
        )


# ── build_server: outermost, so no bypass is admissible ─────────────────────


def test_build_server_requires_a_queue(wrapper: _RecordingWrapper) -> None:
    with pytest.raises(TypeError):
        memory.build_server(wrapper=wrapper)  # type: ignore[call-arg]


def test_build_server_refuses_none(wrapper: _RecordingWrapper) -> None:
    """The original hazard, at the mount level: /mcp/memory with no queue was
    a bypass around the admin surface's bulk-delete gate."""
    with pytest.raises(ValueError, match="outermost mount"):
        memory.build_server(wrapper=wrapper, approval_queue=None)


@pytest.mark.parametrize(
    "reason",
    [memory.GATED_UPSTREAM, memory.TRUSTED_IN_PROCESS],
    ids=["gated_upstream", "trusted_in_process"],
)
def test_build_server_refuses_a_bypass_reason(
    wrapper: _RecordingWrapper, reason: memory.UngatedReason
) -> None:
    """Neither bypass can be truthfully claimed by an outermost transport mount:
    nothing gates in front of it, and its callers are by definition remote."""
    with pytest.raises(ValueError, match="outermost mount"):
        memory.build_server(wrapper=wrapper, approval_queue=reason)


def test_build_server_accepts_a_real_queue(wrapper: _RecordingWrapper) -> None:
    server = memory.build_server(wrapper=wrapper, approval_queue=ApprovalQueue())
    assert server is not None


# ── the production mount still passes a real queue ──────────────────────────


def test_production_mount_passes_a_real_queue() -> None:
    """mcp_mount is the one production build_server call site; if it ever
    stops passing a queue, build_server now raises at boot — but assert the
    wiring directly so the failure names the cause."""
    import inspect

    from hal0.api import mcp_mount

    source = inspect.getsource(mcp_mount.mount_mcp_servers)
    assert "approval_queue=approval_queue" in source
