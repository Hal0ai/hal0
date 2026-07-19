"""Fresh-box UX: an unrouteable brain model surfaces guidance, not a raw 404.

FINDING (docs/rework/r4-stage-validation.md "steward config note", live): on
a fresh box with ``[brain_chat] model=""`` the chat still drives the
``hal0/brain`` -> ``agent`` resolver chain (:mod:`hal0.normalize.resolver`),
but if NEITHER slot is loaded the self ``/v1/chat/completions`` call 404s
with ``dispatch.no_route`` and the operator got the raw transport failure
text with no indication of what to do.

These tests pin the fix in :func:`hal0.brain.chat._primary_completion` /
:func:`hal0.brain.chat._unrouteable_model_error`:

  * a 404 from the self ``/v1`` call is rewritten into an actionable message
    naming the tried model id and "load a slot" guidance, both directly and
    through the full SSE ``_chat_stream`` round trip;
  * an empty/missing model short-circuits to the same guidance WITHOUT
    spending a self-HTTP round trip;
  * the happy path (2xx) is byte-for-byte unchanged;
  * a genuine transport failure (connection error) stays a DISTINCT message
    that never carries the "load a slot" guidance and always says
    "transport failure".

No live network — httpx.AsyncClient is monkeypatched to a scripted stub, same
pattern as tests/brain/test_brain_self_auth.py.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from hal0.brain import chat as bc
from hal0.config.schema import BrainChatConfig, Hal0Config


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _request(headers: dict[str, str] | None = None, **state: Any) -> Any:
    base = {
        "self_api_base_url": "http://testserver",
        "hal0_config": Hal0Config(brain_chat=BrainChatConfig()),
        "brain_persona_root": Path("/nonexistent-personas-root"),
    }
    base.update(state)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(**base)), headers=headers or {}
    )


def _client_class(*, response: httpx.Response | None = None, raises: Exception | None = None):
    """A stand-in for ``httpx.AsyncClient`` whose ``.post()`` is scripted.

    Records every call on the returned class's ``.calls`` list so a test can
    assert whether the self-call happened at all (the empty-model
    short-circuit case must never instantiate the client).
    """
    calls: list[dict[str, Any]] = []

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

        async def post(self, url: str, json: Any = None, headers: Any = None) -> httpx.Response:
            calls.append({"url": url, "json": json, "headers": headers})
            if raises is not None:
                raise raises
            assert response is not None
            return response

    return _Client, calls


class _ExplodingClient:
    """Fails the test if httpx.AsyncClient is ever instantiated."""

    def __init__(self, *a: Any, **k: Any) -> None:
        raise AssertionError("must not open a self-HTTP client for an empty model")


# ── _primary_completion: unrouteable (404) → actionable message ────────────


def test_primary_completion_unrouteable_model_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    no_route_body = {
        "error": {
            "code": "dispatch.no_route",
            "message": "model 'hal0/brain' not found in registry, no upstream advertised it",
        }
    }
    client_cls, calls = _client_class(response=httpx.Response(404, json=no_route_body))
    monkeypatch.setattr(bc.httpx, "AsyncClient", client_cls)

    req = _request()
    llm = bc._resolve_llm(req)
    result = _run(llm({"model": "hal0/brain", "messages": []}))

    assert len(calls) == 1  # the self-call DID happen — 404 only known after trying
    assert "error" in result
    msg = result["error"]
    assert "hal0/brain" in msg  # the tried model id
    assert "load" in msg.lower()  # actionable "load a slot" guidance
    assert "[brain_chat] model" in msg  # the config escape hatch
    assert "transport failure" not in msg  # not confused with a genuine transport error


def test_chat_stream_unrouteable_model_emits_actionable_error_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through _chat_stream: the SSE ``error`` frame carries the
    actionable text, then the stream ends cleanly with ``done`` — no crash,
    no raw dispatch envelope."""
    no_route_body = {"error": {"code": "dispatch.no_route", "message": "model not found"}}
    client_cls, _calls = _client_class(response=httpx.Response(404, json=no_route_body))
    monkeypatch.setattr(bc.httpx, "AsyncClient", client_cls)

    req = _request(
        hermes_kanban=object(),
        board_chat_llm=None,  # force the production _primary_completion closure
    )
    payload = {"messages": [{"role": "user", "content": "hi"}]}
    frames = [_parse(f) for f in _run(_collect_frames(req, payload))]
    err = next(f for f in frames if f["type"] == "error")
    assert "hal0/brain" in err["message"]
    assert "load" in err["message"].lower()
    assert frames[-1] == {"type": "done"}


def _parse(frame: str) -> dict[str, Any]:
    assert frame.startswith("data: ")
    return json.loads(frame[len("data: ") :].strip())


async def _collect_frames(req: Any, payload: dict[str, Any]) -> list[str]:
    return [f async for f in bc._chat_stream(req, payload)]


# ── empty/missing model: no wasted self-call ────────────────────────────────


def test_primary_completion_empty_model_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bc.httpx, "AsyncClient", _ExplodingClient)
    req = _request()
    llm = bc._resolve_llm(req)

    result = _run(llm({"model": "", "messages": []}))

    assert "error" in result
    assert "load" in result["error"].lower()


def test_primary_completion_missing_model_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bc.httpx, "AsyncClient", _ExplodingClient)
    req = _request()
    llm = bc._resolve_llm(req)

    result = _run(llm({"messages": []}))  # no "model" key at all

    assert "error" in result
    assert "load" in result["error"].lower()


# ── happy path: unchanged ────────────────────────────────────────────────────


def test_primary_completion_happy_path_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    happy = {"choices": [{"message": {"role": "assistant", "content": "hi there"}}]}
    client_cls, calls = _client_class(response=httpx.Response(200, json=happy))
    monkeypatch.setattr(bc.httpx, "AsyncClient", client_cls)

    req = _request()
    llm = bc._resolve_llm(req)
    result = _run(llm({"model": "hal0/brain", "messages": []}))

    assert result == happy
    assert len(calls) == 1


# ── genuine transport failure: still distinct ───────────────────────────────


def test_primary_completion_transport_failure_stays_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_cls, _calls = _client_class(raises=httpx.ConnectError("connection refused"))
    monkeypatch.setattr(bc.httpx, "AsyncClient", client_cls)

    req = _request()
    llm = bc._resolve_llm(req)
    result = _run(llm({"model": "hal0/brain", "messages": []}))

    assert "error" in result
    assert "transport failure" in result["error"]
    assert "load" not in result["error"].lower()


def test_primary_completion_non_404_http_error_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-404 HTTP failure (e.g. a 503 from an overloaded slot) keeps the
    pre-existing raw-status text — only 404/no-slot gets the actionable
    rewrite."""
    client_cls, _calls = _client_class(response=httpx.Response(503, text="busy, try again"))
    monkeypatch.setattr(bc.httpx, "AsyncClient", client_cls)

    req = _request()
    llm = bc._resolve_llm(req)
    result = _run(llm({"model": "hal0/brain", "messages": []}))

    assert "error" in result
    assert "primary slot HTTP 503" in result["error"]
    assert "load" not in result["error"].lower()


# ── _unrouteable_model_error directly ───────────────────────────────────────


def test_unrouteable_model_error_names_the_model_and_the_fix() -> None:
    msg = bc._unrouteable_model_error("hal0/brain")
    assert "hal0/brain" in msg
    assert "load" in msg.lower()
    assert "[brain_chat] model" in msg
    assert "8k" in msg  # context floor called out
