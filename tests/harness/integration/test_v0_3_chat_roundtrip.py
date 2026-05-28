"""δ-harness: full v0.3 chat WebSocket round-trip.

Drives the complete shape MASTER-PLAN §4 PR-11 asks for:

    browser → hal0-api WS proxy → mock hermes → message.delta + message.complete

The seam under test is hal0-api's ``/api/agents/hermes/{events,submit}``
chat-proxy bridge from PR-9. The mock hermes (``FakeWsServer``)
implements just enough of the upstream surface that the proxy hop
exercises:

* WS upgrade with Origin allowlist + HMAC session cookie
* Outbound Authorization header injection (PR-9 MUST-FIX #2/#3)
* JSON-RPC frame mirroring + tool.progress coalescing
* Browser-side reconnection (out of scope here; covered by gamma-suite)

No real GGUF download, no real hermes process. A future PR-12 wrinkle
or upstream tools/registry.py drift would change the JSON payload
shape — these tests pin the WS frame envelope so the drift surfaces
in CI before it ships.

FINDINGS row
------------
First green run adds a row to ``tests/harness/FINDINGS.md`` §25
(``v0_3_chat_roundtrip`` — info). Subsequent regressions get logged
inline.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tests.harness.integration.conftest import FakeWsServer


def _wait_for_upstream(fake: FakeWsServer, timeout: float = 3.0) -> None:
    """Block until the proxy has connected to fake hermes' /api/events."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and fake._events_ws is None:
        time.sleep(0.02)
    assert fake._events_ws is not None, "proxy never connected to fake hermes"


def _push(fake: FakeWsServer, frame: dict) -> None:
    """Push a JSON frame through fake hermes' events WS."""
    assert fake._loop is not None
    fut = asyncio.run_coroutine_threadsafe(
        fake.push_event(json.dumps(frame)),
        fake._loop,
    )
    fut.result(timeout=2.0)


def test_chat_roundtrip_emits_delta_and_complete(
    authorised_client: TestClient,
    fake_hermes: FakeWsServer,
) -> None:
    """The browser receives both ``message.delta`` and ``message.complete``.

    Pushes a canned two-frame stream upstream + asserts both arrive
    unchanged downstream. This is the core "did chat work?" δ-row.
    """
    delta = {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {
            "type": "message.delta",
            "session_id": "s1",
            "payload": {"text": "Hello", "index": 0},
        },
    }
    complete = {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {
            "type": "message.complete",
            "session_id": "s1",
            "payload": {"text": "Hello world", "finish_reason": "stop"},
        },
    }

    with authorised_client.websocket_connect(
        "/api/agents/hermes/events",
        headers={"origin": "http://127.0.0.1:8080"},
    ) as ws:
        _wait_for_upstream(fake_hermes)
        _push(fake_hermes, delta)
        _push(fake_hermes, complete)

        # First frame should be message.delta.
        first = json.loads(ws.receive_text())
        assert first["params"]["type"] == "message.delta"
        assert first["params"]["payload"]["text"] == "Hello"

        # Second frame should be message.complete (no coalescing on
        # non-progress events).
        second = json.loads(ws.receive_text())
        assert second["params"]["type"] == "message.complete"
        assert second["params"]["payload"]["finish_reason"] == "stop"


def test_chat_roundtrip_origin_allowlist_rejects_unknown_origin(
    authorised_client: TestClient,
    fake_hermes: FakeWsServer,
) -> None:
    """A WS upgrade from a non-allowlisted Origin gets 403'd.

    The chat-proxy enforces the Origin allowlist BEFORE the WS upgrade
    so a drive-by site cross-window can't bridge into hermes via the
    user's session cookie. This is the DA-sec-ops MUST-FIX #2 contract.
    """
    # The TestClient surfaces 403 as a starlette WebSocketDisconnect.
    with (
        pytest.raises(WebSocketDisconnect),
        authorised_client.websocket_connect(
            "/api/agents/hermes/events",
            headers={"origin": "https://evil.example.com"},
        ),
    ):
        pass


def test_chat_roundtrip_unauthenticated_ws_is_rejected(
    harness_client: TestClient,
    fake_hermes: FakeWsServer,
) -> None:
    """No session cookie + Origin OK = still 403.

    Origin alone isn't enough; the HMAC cookie is the only authn seam.
    Using the unauthenticated ``harness_client`` (not the
    ``authorised_client``) so no cookie is attached.
    """
    with (
        pytest.raises(WebSocketDisconnect),
        harness_client.websocket_connect(
            "/api/agents/hermes/events",
            headers={"origin": "http://127.0.0.1:8080"},
        ),
    ):
        pass


def test_chat_roundtrip_progress_then_complete_ordering_preserved(
    authorised_client: TestClient,
    fake_hermes: FakeWsServer,
) -> None:
    """``tool.progress`` frames are coalesced, but the trailing
    ``tool.complete`` arrives AFTER all progress flushes (ordering
    invariant the chat composer depends on).

    PR-9's ``ProgressCoalescer`` buffers progress for 100ms or until a
    non-progress event arrives, whichever is sooner — and ALWAYS flushes
    pending progress before forwarding the non-progress event. This
    test pins that ordering through the round-trip path.
    """
    base_progress = {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {
            "type": "tool.progress",
            "session_id": "s1",
            "payload": {"tool_id": "search-1", "preview": "step 1"},
        },
    }
    complete = {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {
            "type": "tool.complete",
            "session_id": "s1",
            "payload": {"tool_id": "search-1", "result": "done"},
        },
    }

    with authorised_client.websocket_connect(
        "/api/agents/hermes/events",
        headers={"origin": "http://127.0.0.1:8080"},
    ) as ws:
        _wait_for_upstream(fake_hermes)
        # Three rapid progress frames; complete frame fires immediately
        # after. The proxy MUST flush progress, then the complete.
        for i in range(3):
            payload = json.loads(json.dumps(base_progress))  # deep copy
            payload["params"]["payload"]["preview"] = f"step {i}"
            _push(fake_hermes, payload)
        _push(fake_hermes, complete)

        # First downstream frame = a single coalesced progress (last
        # write wins). Subsequent frame = complete.
        ws.transport_socket_timeout = 2.0  # type: ignore[attr-defined]
        first = json.loads(ws.receive_text())
        second = json.loads(ws.receive_text())

        assert first["params"]["type"] == "tool.progress"
        assert second["params"]["type"] == "tool.complete"
        # The last-progress-wins invariant means we keep step 2's preview.
        assert first["params"]["payload"]["preview"] == "step 2"
