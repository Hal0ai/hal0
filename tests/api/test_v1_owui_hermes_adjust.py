"""``_owui_hermes_adjust`` / ``_apply_owui_hermes_adjust``: the Open WebUI → Hermes relay edits."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.api.routes import v1
from hal0.config.schema import Hal0Config

_TOOLS = [{"type": "function", "function": {"name": "t"}}]


def _body(**extra: Any) -> bytes:
    return json.dumps({"model": "coder", "messages": [], **extra}).encode()


def test_adjust_derives_session_and_strips_tools() -> None:
    headers, body = v1._owui_hermes_adjust(
        "abc-123", {"content-type": "application/json"}, _body(tools=_TOOLS, tool_choice="auto")
    )
    assert headers["x-hermes-session-id"] == "owui-abc-123"
    assert headers["content-type"] == "application/json"
    assert json.loads(body) == {"model": "coder", "messages": []}


def test_adjust_keeps_client_session_header() -> None:
    headers, _ = v1._owui_hermes_adjust("abc", {"x-hermes-session-id": "mine"}, _body())
    assert headers["x-hermes-session-id"] == "mine"


@pytest.mark.parametrize("chat_id", ["", "a b", "a" * 65, "a/b", "a.b"])
def test_adjust_drops_unsafe_chat_id(chat_id: str) -> None:
    headers, _ = v1._owui_hermes_adjust(chat_id, {}, _body())
    assert "x-hermes-session-id" not in headers


def test_adjust_leaves_toolless_body_byte_identical() -> None:
    raw = b'{"model":"coder",  "messages":[]}'
    assert v1._owui_hermes_adjust("abc", {}, raw)[1] is raw


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", b""])
def test_adjust_passes_non_object_bodies_through(raw: bytes) -> None:
    assert v1._owui_hermes_adjust("abc", {}, raw)[1] is raw


def _request(
    upstream_managed_by: str | None, *, headers: dict[str, str], path: str = "/v1/chat/completions"
) -> Any:
    registry = {"u": SimpleNamespace(managed_by=upstream_managed_by)}
    state = SimpleNamespace(upstreams=SimpleNamespace(get=registry.get), hal0_config=Hal0Config())
    return SimpleNamespace(
        headers=headers,
        url=SimpleNamespace(path=path),
        app=SimpleNamespace(state=state),
    )


def _call() -> Any:
    return SimpleNamespace(upstream_name="u", headers={}, body=_body(tools=_TOOLS))


def test_slot_upstream_body_is_untouched() -> None:
    call = _call()
    v1._apply_owui_hermes_adjust(_request(None, headers={"x-openwebui-chat-id": "abc"}), call)
    assert call.headers == {} and json.loads(call.body)["tools"] == _TOOLS


def test_operator_remote_upstream_is_untouched() -> None:
    call = _call()
    v1._apply_owui_hermes_adjust(_request("operator", headers={"x-openwebui-chat-id": "abc"}), call)
    assert call.headers == {} and "tools" in json.loads(call.body)


def test_hermes_upstream_without_owui_header_is_untouched() -> None:
    call = _call()
    v1._apply_owui_hermes_adjust(_request("agent:hermes", headers={}), call)
    assert call.headers == {} and "tools" in json.loads(call.body)


def test_non_chat_path_is_untouched() -> None:
    call = _call()
    req = _request("agent:hermes", headers={"x-openwebui-chat-id": "abc"}, path="/v1/embeddings")
    v1._apply_owui_hermes_adjust(req, call)
    assert call.headers == {} and "tools" in json.loads(call.body)


def test_hermes_upstream_with_owui_header_is_adjusted() -> None:
    call = _call()
    v1._apply_owui_hermes_adjust(
        _request("agent:hermes", headers={"x-openwebui-chat-id": "abc"}), call
    )
    assert call.headers == {"x-hermes-session-id": "owui-abc"}
    assert "tools" not in json.loads(call.body)
