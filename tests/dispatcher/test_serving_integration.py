"""Tests for Dispatcher.forward wiring SlotManager.serving().

Covers the dispatcher half of task #10 SERVING:

  - A slot-kind UpstreamCall flips the slot to SERVING for the duration
    of a non-streaming request, then back to READY.
  - A streaming request keeps the slot in SERVING until the response
    body iterator drains.
  - Remote-kind UpstreamCalls (empty ``slot_name``) leave the slot
    machinery alone.
  - Network errors release the serving counter so the slot doesn't get
    stuck in SERVING forever.
  - The single-flight prefetch path does NOT enter serving() — it only
    fetches /v1/models, never a real inference request.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.dispatcher.router import Dispatcher, SlotLoading, UpstreamCall, UpstreamUnavailable
from hal0.slots.state import SlotState

# ── tiny SlotManager stand-in ────────────────────────────────────────────────


class _RecordingSlotManager:
    """Minimal SlotManager surface — only what Dispatcher.forward touches.

    Records every enter/exit so tests can assert ordering without spinning
    up a real SlotManager (which would need a slot TOML + systemctl stubs).
    """

    def __init__(
        self,
        state: SlotState = SlotState.READY,
        *,
        recover_raises: BaseException | None = None,
    ) -> None:
        self.events: list[tuple[str, str]] = []  # (op, slot_name)
        self._counts: dict[str, int] = {}
        self._state = state
        # Recorded so tests can assert the dispatcher's recovery branch fires
        # exactly once on ConnectError for a slot upstream.
        self.recover_calls: list[str] = []
        self._recover_raises = recover_raises

    def serving(self, slot_name: str) -> _RecordingCtx:
        return _RecordingCtx(self, slot_name)

    def in_flight_count(self, slot_name: str) -> int:
        return self._counts.get(slot_name, 0)

    def _current_state(self, _slot_name: str) -> SlotState:
        # Mirrors SlotManager._current_state — Dispatcher's swap-window
        # gate calls this before forwarding.  Default READY so the
        # existing serving-integration tests pass; tests for the gate
        # construct the mock with the state they want to assert against.
        return self._state

    async def recover_evicted_slot(self, slot_name: str) -> None:
        """Mirrors SlotManager.recover_evicted_slot — dispatcher calls this
        on ConnectError to resync after a silent Lemonade eviction.
        """
        self.recover_calls.append(slot_name)
        if self._recover_raises is not None:
            raise self._recover_raises


class _RecordingCtx:
    def __init__(self, manager: _RecordingSlotManager, slot_name: str) -> None:
        self._manager = manager
        self._slot_name = slot_name

    async def __aenter__(self) -> None:
        self._manager.events.append(("enter", self._slot_name))
        self._manager._counts[self._slot_name] = self._manager._counts.get(self._slot_name, 0) + 1

    async def __aexit__(self, *_: Any) -> None:
        self._manager.events.append(("exit", self._slot_name))
        self._manager._counts[self._slot_name] = self._manager._counts.get(self._slot_name, 1) - 1


def _make_dispatcher(
    transport: httpx.MockTransport,
    sm: _RecordingSlotManager | None = None,
) -> Dispatcher:
    client = httpx.AsyncClient(transport=transport)
    return Dispatcher(http_client=client, slot_manager=sm)  # type: ignore[arg-type]


def _slot_call(streaming: bool = False) -> UpstreamCall:
    return UpstreamCall(
        upstream_name="primary",
        target_url="http://slot/chat/completions",
        headers={"content-type": "application/json"},
        body=b"{}",
        streaming=streaming,
        method="POST",
        slot_name="primary",
    )


def _remote_call() -> UpstreamCall:
    return UpstreamCall(
        upstream_name="openrouter",
        target_url="http://remote/chat/completions",
        headers={"content-type": "application/json"},
        body=b"{}",
        streaming=False,
        method="POST",
        slot_name="",  # remote — no slot
    )


# ── non-streaming ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forward_enters_and_exits_serving_for_slot_call() -> None:
    sm = _RecordingSlotManager()
    dispatcher = _make_dispatcher(
        httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True})),
        sm=sm,
    )
    try:
        resp = await dispatcher.forward(_slot_call())
        assert resp.status_code == 200
        assert sm.events == [("enter", "primary"), ("exit", "primary")]
        assert sm.in_flight_count("primary") == 0
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_releases_serving_on_network_error() -> None:
    """A connect error must still release the serving counter."""
    sm = _RecordingSlotManager()

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=req)

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm=sm)
    try:
        with pytest.raises(UpstreamUnavailable):
            await dispatcher.forward(_slot_call())
        assert sm.in_flight_count("primary") == 0
        # Both enter + exit landed.
        ops = [op for op, _ in sm.events]
        assert ops == ["enter", "exit"]
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_remote_call_does_not_touch_slot_manager() -> None:
    """Remote-kind upstreams leave the slot machinery alone."""
    sm = _RecordingSlotManager()
    dispatcher = _make_dispatcher(
        httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True})),
        sm=sm,
    )
    try:
        resp = await dispatcher.forward(_remote_call())
        assert resp.status_code == 200
        assert sm.events == [], "remote upstream must not enter serving()"
    finally:
        await dispatcher.aclose()


# ── streaming ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forward_streaming_holds_serving_until_drain() -> None:
    """SERVING releases only after the stream is fully consumed."""
    sm = _RecordingSlotManager()
    chunks = [b"data: 1\n\n", b"data: 2\n\n", b"data: [DONE]\n\n"]

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"".join(chunks)),
            headers={"content-type": "text/event-stream"},
        )

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm=sm)
    try:
        resp = await dispatcher.forward(_slot_call(streaming=True))
        # forward() returned but the stream hasn't drained yet — slot
        # must still be marked SERVING.
        assert sm.events == [("enter", "primary")]
        assert sm.in_flight_count("primary") == 1

        collected = b""
        async for c in resp.body_iterator:
            collected += c if isinstance(c, bytes) else c.encode()

        # After draining, exit fires.
        assert collected == b"".join(chunks)
        assert sm.events == [("enter", "primary"), ("exit", "primary")]
        assert sm.in_flight_count("primary") == 0
    finally:
        await dispatcher.aclose()


# ── single-flight prefetch isolation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_flight_prefetch_does_not_enter_serving() -> None:
    """Cold-cache prefetch via SingleFlightGroup never touches slot state.

    Prefetch only calls ``fetch_models`` (a /v1/models GET).  forward()
    is the only path that enters serving() — and prefetch happens inside
    dispatch(), never forward().  This test verifies the boundary holds
    by exercising _cold_prefetch directly while a SlotManager stand-in
    watches for unexpected enter calls.
    """
    sm = _RecordingSlotManager()

    async def fetcher(_u: Any) -> list[str]:
        return ["model-a", "model-b"]

    dispatcher = Dispatcher(
        fetch_models=fetcher,  # type: ignore[arg-type]
        slot_manager=sm,  # type: ignore[arg-type]
    )
    from hal0.upstreams.registry import Upstream

    cold = [Upstream(name="r", kind="remote", url="http://x")]
    await dispatcher._cold_prefetch(cold)
    await dispatcher.aclose()

    assert sm.events == [], "prefetch must never enter serving()"


# ── swap-window gate (SlotLoading) ───────────────────────────────────────────


@pytest.mark.parametrize(
    "state",
    [
        SlotState.OFFLINE,
        SlotState.PULLING,
        SlotState.STARTING,
        SlotState.WARMING,
        SlotState.UNLOADING,
        SlotState.ERROR,
    ],
)
@pytest.mark.asyncio
async def test_forward_gates_slot_in_loading_state(state: SlotState) -> None:
    """Every non-ready slot state must raise SlotLoading before the HTTP forward.

    Without the gate, requests in the swap window hit a dead port (502)
    or a still-loading llama-server (raw 503).  The gate raises a
    structured envelope with retry_after_s instead, which the error
    middleware promotes to a Retry-After header.
    """
    sm = _RecordingSlotManager(state=state)

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("forward must not reach upstream when slot is loading")

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm=sm)
    try:
        with pytest.raises(SlotLoading) as ei:
            await dispatcher.forward(_slot_call())
        exc = ei.value
        assert exc.code == "slot.loading"
        assert exc.status == 503
        assert exc.details["slot"] == "primary"
        assert exc.details["state"] == state.value
        assert exc.details["retry_after_s"] == 15
        progress = exc.details["progress"]
        assert progress["phase"] == state.value
        assert progress["upstream"] == "primary"
        # No serving counter movement — the gate fires before _forward_with_serving.
        assert sm.events == []
        assert sm.in_flight_count("primary") == 0
    finally:
        await dispatcher.aclose()


@pytest.mark.parametrize(
    "state",
    [SlotState.READY, SlotState.SERVING, SlotState.IDLE],
)
@pytest.mark.asyncio
async def test_forward_passes_through_ready_states(state: SlotState) -> None:
    """READY / SERVING / IDLE must all be treated as 'ready to serve'."""
    sm = _RecordingSlotManager(state=state)
    dispatcher = _make_dispatcher(
        httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True})),
        sm=sm,
    )
    try:
        resp = await dispatcher.forward(_slot_call())
        assert resp.status_code == 200
        assert sm.events == [("enter", "primary"), ("exit", "primary")]
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_remote_upstream_skips_gate_even_when_slot_state_lookup_would_fail() -> None:
    """Remote upstreams have no slot_name — the gate must not fire.

    Defends against a regression where the gate is moved before the
    slot_name + slot_manager check, which would crash on remote calls
    that don't carry a slot identity.
    """
    sm = _RecordingSlotManager(state=SlotState.OFFLINE)  # would trip the gate
    dispatcher = _make_dispatcher(
        httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True})),
        sm=sm,
    )
    try:
        resp = await dispatcher.forward(_remote_call())
        assert resp.status_code == 200
        assert sm.events == []
    finally:
        await dispatcher.aclose()


# ── silent-eviction recovery (ConnectError on a READY slot) ──────────────────


@pytest.mark.asyncio
async def test_forward_recovers_from_silent_eviction_and_retries() -> None:
    """ConnectError on a slot upstream triggers recover_evicted_slot + 1 retry.

    Reproduces the v0.3 'lemonade silent eviction' bug: hal0's state.json
    says the slot is READY but the upstream port is dead because lemond
    evicted the child llama-server without notifying hal0.  The dispatcher
    must call SlotManager.recover_evicted_slot() (which drives /v1/load)
    and then retry the forward once before giving up.
    """
    sm = _RecordingSlotManager(state=SlotState.READY)
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused", request=req)
        return httpx.Response(200, json={"ok": True})

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm=sm)
    try:
        resp = await dispatcher.forward(_slot_call())
        assert resp.status_code == 200
        assert calls["n"] == 2, "expected exactly one retry after recovery"
        assert sm.recover_calls == ["primary"]
        # SERVING enter/exit must still balance after a recovered request.
        assert sm.in_flight_count("primary") == 0
        assert sm.events == [("enter", "primary"), ("exit", "primary")]
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_gives_up_when_retry_after_recovery_still_fails() -> None:
    """If the retry forward also gets ConnectError, surface UpstreamUnavailable.

    Prevents an infinite recover-retry loop when lemond is genuinely down
    (not just an idle eviction).  recover_evicted_slot is called exactly
    once; the second ConnectError is fatal.
    """
    sm = _RecordingSlotManager(state=SlotState.READY)

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("still refused", request=req)

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm=sm)
    try:
        with pytest.raises(UpstreamUnavailable):
            await dispatcher.forward(_slot_call())
        assert sm.recover_calls == ["primary"], "exactly one recovery attempt"
        assert sm.in_flight_count("primary") == 0
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_remote_connect_error_does_not_attempt_recovery() -> None:
    """Remote upstreams (no slot_name) skip the slot-recovery branch.

    Recovery only makes sense for slot upstreams whose port is owned by
    lemonade.  Remote providers (OpenRouter, Anthropic) have nothing for
    SlotManager to recover, and calling it would either no-op or crash.
    """
    sm = _RecordingSlotManager()

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=req)

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm=sm)
    try:
        with pytest.raises(UpstreamUnavailable):
            await dispatcher.forward(_remote_call())
        assert sm.recover_calls == []
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_streaming_recovers_from_silent_eviction() -> None:
    """Streaming requests get the same recovery treatment as non-streaming.

    The stream is opened eagerly before the handler returns, so a
    ConnectError on stream-open is recoverable the same way.
    """
    sm = _RecordingSlotManager(state=SlotState.READY)
    chunks = [b"data: 1\n\n", b"data: [DONE]\n\n"]
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("dead port", request=req)
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"".join(chunks)),
            headers={"content-type": "text/event-stream"},
        )

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm=sm)
    try:
        resp = await dispatcher.forward(_slot_call(streaming=True))
        collected = b""
        async for c in resp.body_iterator:
            collected += c if isinstance(c, bytes) else c.encode()
        assert collected == b"".join(chunks)
        assert sm.recover_calls == ["primary"]
        assert sm.in_flight_count("primary") == 0
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_recovers_from_remote_protocol_error() -> None:
    """RemoteProtocolError (peer dropped mid-request) also triggers recovery.

    The race window where lemonade evicts the child while hal0 is dialing
    surfaces as RemoteProtocolError rather than ConnectError — the TCP
    handshake completed but the peer closed before responding.  Recovery
    must cover both transport failure modes.
    """
    sm = _RecordingSlotManager(state=SlotState.READY)
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response.",
                request=req,
            )
        return httpx.Response(200, json={"ok": True})

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm=sm)
    try:
        resp = await dispatcher.forward(_slot_call())
        assert resp.status_code == 200
        assert calls["n"] == 2
        assert sm.recover_calls == ["primary"]
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_gives_up_when_recover_evicted_slot_itself_raises() -> None:
    """If recover_evicted_slot raises, fall back to the original ConnectError.

    Recovery errors are not retried — surfacing UpstreamUnavailable with
    the original connection error is more informative than swallowing it
    behind a recovery-time failure.
    """
    sm = _RecordingSlotManager(
        state=SlotState.READY,
        recover_raises=RuntimeError("lemonade unreachable"),
    )

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    dispatcher = _make_dispatcher(httpx.MockTransport(handler), sm=sm)
    try:
        with pytest.raises(UpstreamUnavailable):
            await dispatcher.forward(_slot_call())
        assert sm.recover_calls == ["primary"]
    finally:
        await dispatcher.aclose()
