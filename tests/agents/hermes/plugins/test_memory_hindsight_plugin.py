"""hal0-memory Hermes plugin (canonical src copy) — hermetic unit coverage.

``src/hal0/agents/hermes/plugins/memory_hindsight/`` is the canonical source
for the shipped hal0-memory plugin (its installer seed at
``installer/agents/hermes/plugins/hal0-memory/`` is pinned byte-identical in
``tests/agents/hermes_plugins/test_seed_parity.py``). The seed's REST-client
contract is already exercised via importlib against the *installer* copy in
``tests/agents/test_hal0_memory_client.py`` — that suite never imports this
package directly, so this file's own statements stay at 0% coverage without
it. This file imports the package the normal way (``hal0.agents.hermes...``)
so coverage attributes to the real source file.

Everything here is pure / mocked:

* ``register(ctx)`` is exercised with a stub ``ctx``.
* ``Hal0MemoryProvider`` is exercised with an injected ``Hal0MemoryClient``
  mock (or ``client=None`` for the not-yet-initialized paths) — no network.
* ``Hal0MemoryClient`` is exercised against a duck-typed fake HTTP client
  (same shape as ``tests/agents/test_hal0_memory_client.py``'s
  ``_FakeHttpClient``) that records calls and never touches a socket.

``provider.py`` falls back to a vendored ``MemoryProvider`` ABC stub when the
real ``agent.memory_provider`` module (only importable inside the Hermes
agent venv) is absent, so no ``importorskip`` is needed here.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from hal0.agents.hermes.plugins.memory_hindsight import Hal0MemoryProvider, register
from hal0.agents.hermes.plugins.memory_hindsight._client import (
    DEFAULT_AGENT_ID,
    DEFAULT_BASE_URL,
    Hal0MemoryClient,
    Hal0MemoryClientError,
    _resolve_agent_id,
    _resolve_base_url,
)
from hal0.agents.hermes.plugins.memory_hindsight.provider import ALL_TOOL_SCHEMAS

# ── register(ctx) / construction ──────────────────────────────────────────


class _FakeCtx:
    """Stub loader context — records ``register_memory_provider`` calls."""

    def __init__(self) -> None:
        self.registered: list[object] = []

    def register_memory_provider(self, provider: object) -> None:
        self.registered.append(provider)


def test_register_registers_a_hal0_memory_provider() -> None:
    ctx = _FakeCtx()
    register(ctx)
    assert len(ctx.registered) == 1
    assert isinstance(ctx.registered[0], Hal0MemoryProvider)


def test_hal0_memory_provider_is_constructable_and_named() -> None:
    provider = Hal0MemoryProvider()
    assert provider.name == "hal0-memory"
    assert provider.is_available() is True


# ── _resolve_base_url / _resolve_agent_id ─────────────────────────────────


def test_resolve_base_url_prefers_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_MEMORY_BASE", "http://env-host:1/")
    assert _resolve_base_url("http://override:9/") == "http://override:9"


def test_resolve_base_url_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_MEMORY_BASE", "http://env-host:1/")
    assert _resolve_base_url(None) == "http://env-host:1"


def test_resolve_base_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_MEMORY_BASE", raising=False)
    assert _resolve_base_url(None) == DEFAULT_BASE_URL


def test_resolve_agent_id_prefers_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_AGENT_ID", "other")
    assert _resolve_agent_id("mine") == "mine"


def test_resolve_agent_id_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_AGENT_ID", "other")
    assert _resolve_agent_id(None) == "other"


def test_resolve_agent_id_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_AGENT_ID", raising=False)
    assert _resolve_agent_id(None) == DEFAULT_AGENT_ID


# ── Hal0MemoryClient: request shaping over a fake HTTP client ─────────────


class _FakeResponse:
    """Duck-typed stand-in for ``httpx.Response`` — no network involved."""

    def __init__(self, *, status_code: int = 200, json_body: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._json_body = json_body
        self.text = text

    def json(self) -> Any:
        if self._json_body is None:
            raise ValueError("no JSON body configured")
        return self._json_body


class _FakeHttpClient:
    """Duck-typed stand-in for ``httpx.Client`` — records calls, no sockets."""

    def __init__(
        self, *, response: _FakeResponse | None = None, raises: Exception | None = None
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = (
            response if response is not None else _FakeResponse(json_body={"status": "ok"})
        )
        self._raises = raises
        self.closed = False

    def request(self, method, path, *, headers=None, json=None, params=None):
        self.calls.append(
            {"method": method, "path": path, "headers": headers, "json": json, "params": params}
        )
        if self._raises is not None:
            raise self._raises
        return self._response

    def close(self) -> None:
        self.closed = True


def test_client_error_carries_message_and_optional_status_code() -> None:
    err = Hal0MemoryClientError("boom")
    assert str(err) == "boom"
    assert err.status_code is None

    err2 = Hal0MemoryClientError("boom2", status_code=500)
    assert err2.status_code == 500


def test_client_defaults_base_url_and_agent_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_MEMORY_BASE", raising=False)
    monkeypatch.delenv("HAL0_AGENT_ID", raising=False)
    client = Hal0MemoryClient()
    try:
        assert client.base_url == DEFAULT_BASE_URL
        assert client.agent_id == DEFAULT_AGENT_ID
    finally:
        client.close()


def test_client_add_posts_expected_payload_and_headers() -> None:
    http = _FakeHttpClient(response=_FakeResponse(json_body={"status": "ok", "id": "m1"}))
    client = Hal0MemoryClient(agent_id="hermes", http_client=http)

    result = client.add("hello", tags=["a"], metadata={"k": "v"}, private=True)

    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/api/memory/add"
    assert call["json"] == {"text": "hello", "tags": ["a"], "metadata": {"k": "v"}}
    assert "dataset" not in call["json"]  # #317: never send a dataset field
    assert call["headers"]["X-hal0-Agent"] == "hermes"
    assert call["headers"]["X-hal0-Private"] == "1"
    assert result == {"status": "ok", "id": "m1"}


def test_client_add_shared_sets_private_header_zero() -> None:
    http = _FakeHttpClient()
    client = Hal0MemoryClient(agent_id="hermes", http_client=http)
    client.add("hello", private=False)
    assert http.calls[0]["headers"]["X-hal0-Private"] == "0"


def test_client_add_omits_tags_and_metadata_when_none() -> None:
    http = _FakeHttpClient()
    client = Hal0MemoryClient(http_client=http)
    client.add("hello")
    assert http.calls[0]["json"] == {"text": "hello"}


def test_client_search_posts_query_and_limit() -> None:
    http = _FakeHttpClient(response=_FakeResponse(json_body={"items": []}))
    client = Hal0MemoryClient(http_client=http)
    client.search("hello", limit=5)
    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/api/memory/search"
    assert call["json"] == {"query": "hello", "limit": 5}
    assert call["headers"]["X-hal0-Private"] == "1"


def test_client_recall_omits_types_by_default() -> None:
    http = _FakeHttpClient(response=_FakeResponse(json_body={"items": []}))
    client = Hal0MemoryClient(http_client=http)
    client.recall("hello", max_tokens=123)
    assert http.calls[0]["json"] == {"query": "hello", "max_tokens": 123}


def test_client_recall_includes_types_when_given() -> None:
    http = _FakeHttpClient(response=_FakeResponse(json_body={"items": []}))
    client = Hal0MemoryClient(http_client=http)
    client.recall("hello", types=["world"], max_tokens=99)
    assert http.calls[0]["json"]["types"] == ["world"]


def test_client_list_items_is_a_get_with_limit_param() -> None:
    http = _FakeHttpClient(response=_FakeResponse(json_body={"items": []}))
    client = Hal0MemoryClient(http_client=http)
    client.list_items(limit=7)
    call = http.calls[0]
    assert call["method"] == "GET"
    assert call["path"] == "/api/memory/list"
    assert call["params"] == {"limit": 7}
    assert call["json"] is None


def test_client_delete_posts_ids_list() -> None:
    http = _FakeHttpClient(response=_FakeResponse(json_body={"status": "ok"}))
    client = Hal0MemoryClient(http_client=http)
    client.delete("mem-1")
    assert http.calls[0]["json"] == {"ids": ["mem-1"]}


def test_client_request_raises_on_4xx_with_status_code() -> None:
    http = _FakeHttpClient(response=_FakeResponse(status_code=404, text="nope"))
    client = Hal0MemoryClient(http_client=http)
    with pytest.raises(Hal0MemoryClientError) as excinfo:
        client.search("q")
    assert excinfo.value.status_code == 404
    assert "nope" in str(excinfo.value)


def test_client_request_wraps_transport_error() -> None:
    http = _FakeHttpClient(raises=httpx.ConnectError("boom"))
    client = Hal0MemoryClient(http_client=http)
    with pytest.raises(Hal0MemoryClientError):
        client.search("q")


def test_client_request_falls_back_to_raw_text_on_non_json_response() -> None:
    http = _FakeHttpClient(response=_FakeResponse(json_body=None, text="not-json"))
    client = Hal0MemoryClient(http_client=http)
    assert client.search("q") == {"status": "ok", "raw": "not-json"}


def test_client_request_wraps_non_dict_json_body() -> None:
    http = _FakeHttpClient(response=_FakeResponse(json_body=[1, 2, 3]))
    client = Hal0MemoryClient(http_client=http)
    assert client.search("q") == {"status": "ok", "raw": [1, 2, 3]}


def test_client_close_closes_owned_client() -> None:
    client = Hal0MemoryClient(base_url="http://testserver")
    client.close()
    assert client._client.is_closed


def test_client_close_does_not_close_injected_client() -> None:
    http = _FakeHttpClient()
    client = Hal0MemoryClient(http_client=http)
    client.close()
    assert http.closed is False


# ── Hal0MemoryProvider: lifecycle ─────────────────────────────────────────


def test_initialize_builds_client_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_MEMORY_BASE", raising=False)
    monkeypatch.delenv("HAL0_AGENT_ID", raising=False)
    provider = Hal0MemoryProvider()
    provider.initialize("session-1")
    try:
        assert provider._session_id == "session-1"
        assert provider._agent_context == "primary"
        assert provider._client is not None
        assert provider._client.base_url == DEFAULT_BASE_URL
        assert provider._client.agent_id == DEFAULT_AGENT_ID
    finally:
        provider.shutdown()


def test_initialize_respects_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_MEMORY_BASE", "http://box:8080/")
    monkeypatch.setenv("HAL0_AGENT_ID", "custom-agent")
    provider = Hal0MemoryProvider()
    provider.initialize()
    try:
        assert provider._client.base_url == "http://box:8080"
        assert provider._client.agent_id == "custom-agent"
    finally:
        provider.shutdown()


def test_initialize_uses_client_override_and_skips_env_lookup() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize("s1", agent_context="flush")
    assert provider._client is fake_client
    assert provider._agent_context == "flush"


def test_initialize_session_id_kwarg_fallback() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize(session_id="from-kwarg")
    assert provider._session_id == "from-kwarg"
    assert provider._agent_context == "primary"


def test_agent_id_defaults_before_initialize() -> None:
    provider = Hal0MemoryProvider()
    assert provider._agent_id() == "hermes"


def test_agent_id_reflects_client_after_initialize() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.agent_id = "hermes-2"
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    assert provider._agent_id() == "hermes-2"


def test_shutdown_closes_client_and_clears_reference() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    provider.shutdown()
    fake_client.close.assert_called_once()
    assert provider._client is None


def test_shutdown_is_a_noop_without_initialize() -> None:
    provider = Hal0MemoryProvider()
    provider.shutdown()  # must not raise
    assert provider._client is None


# ── system_prompt_block ───────────────────────────────────────────────────


def test_system_prompt_block_mentions_private_bank_name() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.agent_id = "hermes"
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    block = provider.system_prompt_block()
    assert "private:hermes" in block
    assert "hal0_memory_search" in block
    assert "hal0_memory_add" in block


# ── prefetch ───────────────────────────────────────────────────────────────


def test_prefetch_returns_empty_for_empty_query() -> None:
    provider = Hal0MemoryProvider(client=Mock(spec=Hal0MemoryClient))
    provider.initialize()
    assert provider.prefetch("") == ""


def test_prefetch_returns_empty_when_client_not_initialized() -> None:
    provider = Hal0MemoryProvider()
    assert provider.prefetch("query") == ""


def test_prefetch_formats_items_as_bullets() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.recall.return_value = {
        "items": [{"text": "fact one"}, {"content": "fact two"}, {"text": ""}]
    }
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    out = provider.prefetch("q")
    fake_client.recall.assert_called_once_with("q", max_tokens=2048)
    assert out == "## hal0-memory recall\n- fact one\n- fact two"


def test_prefetch_returns_empty_when_no_usable_items() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.recall.return_value = {"items": [{}, {"text": ""}]}
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    assert provider.prefetch("q") == ""


def test_prefetch_skips_non_dict_items() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.recall.return_value = {"items": ["not-a-dict", {"text": "kept"}]}
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    assert provider.prefetch("q") == "## hal0-memory recall\n- kept"


def test_prefetch_returns_empty_when_items_missing_or_not_a_list() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.recall.return_value = {"items": None}
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    assert provider.prefetch("q") == ""


def test_prefetch_swallows_client_error() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.recall.side_effect = Hal0MemoryClientError("boom")
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    assert provider.prefetch("q") == ""


# ── sync_turn ────────────────────────────────────────────────────────────


def test_sync_turn_noop_when_no_client() -> None:
    provider = Hal0MemoryProvider()
    provider.sync_turn("hi", "hello")  # must not raise


def test_sync_turn_noop_when_both_contents_empty() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    provider.sync_turn("", "")
    fake_client.add.assert_not_called()


@pytest.mark.parametrize("context", ["cron", "flush", "subagent"])
def test_sync_turn_noop_for_skip_write_contexts(context: str) -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize(agent_context=context)
    provider.sync_turn("hi", "hello")
    fake_client.add.assert_not_called()


def test_sync_turn_writes_formatted_transcript() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    provider.sync_turn("hi", "hello")
    fake_client.add.assert_called_once_with(
        "User: hi\nAssistant: hello", tags=["chat", "agent:hermes"], private=True
    )


def test_sync_turn_swallows_client_error() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.add.side_effect = Hal0MemoryClientError("boom")
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    provider.sync_turn("hi", "hello")  # must not raise


# ── tool schemas + dispatch ────────────────────────────────────────────────


def test_get_tool_schemas_returns_a_copy_of_all_three() -> None:
    provider = Hal0MemoryProvider()
    schemas = provider.get_tool_schemas()
    assert schemas == ALL_TOOL_SCHEMAS
    assert schemas is not ALL_TOOL_SCHEMAS
    assert {s["name"] for s in schemas} == {
        "hal0_memory_search",
        "hal0_memory_recall",
        "hal0_memory_add",
    }


def test_handle_tool_call_errors_without_client() -> None:
    provider = Hal0MemoryProvider()
    out = json.loads(provider.handle_tool_call("hal0_memory_search", {"query": "q"}))
    assert out["status"] == "error"


def test_handle_tool_call_search_missing_query() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    out = json.loads(provider.handle_tool_call("hal0_memory_search", {"query": "  "}))
    assert out["status"] == "error"
    fake_client.search.assert_not_called()


def test_handle_tool_call_search_dispatches_with_limit() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.search.return_value = {"items": []}
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    out = json.loads(provider.handle_tool_call("hal0_memory_search", {"query": "q", "limit": 3}))
    fake_client.search.assert_called_once_with("q", limit=3)
    assert out == {"items": []}


def test_handle_tool_call_search_default_limit_is_ten() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.search.return_value = {"items": []}
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    provider.handle_tool_call("hal0_memory_search", {"query": "q"})
    fake_client.search.assert_called_once_with("q", limit=10)


def test_handle_tool_call_recall_missing_query() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    out = json.loads(provider.handle_tool_call("hal0_memory_recall", {"query": ""}))
    assert out["status"] == "error"


def test_handle_tool_call_recall_dispatches_with_max_tokens() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.recall.return_value = {"items": []}
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    provider.handle_tool_call("hal0_memory_recall", {"query": "q", "max_tokens": 512})
    fake_client.recall.assert_called_once_with("q", max_tokens=512)


def test_handle_tool_call_add_missing_text() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    out = json.loads(provider.handle_tool_call("hal0_memory_add", {"text": "   "}))
    assert out["status"] == "error"
    fake_client.add.assert_not_called()


def test_handle_tool_call_add_private_default_sets_bank() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.agent_id = "hermes"
    fake_client.add.return_value = {"status": "ok", "id": "m1"}
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    out = json.loads(provider.handle_tool_call("hal0_memory_add", {"text": "fact"}))
    fake_client.add.assert_called_once_with("fact", tags=["agent:hermes"], private=True)
    assert out["bank"] == "private:hermes"


def test_handle_tool_call_add_shared_sets_bank_and_private_false() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.add.return_value = {"status": "ok"}
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    out = json.loads(provider.handle_tool_call("hal0_memory_add", {"text": "fact", "shared": True}))
    fake_client.add.assert_called_once_with("fact", tags=["agent:hermes"], private=False)
    assert out["bank"] == "shared"


def test_handle_tool_call_add_custom_tags() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.add.return_value = {"status": "ok"}
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    provider.handle_tool_call("hal0_memory_add", {"text": "fact", "tags": ["a", "b"]})
    fake_client.add.assert_called_once_with("fact", tags=["a", "b"], private=True)


def test_handle_tool_call_add_does_not_overwrite_existing_error_result() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.add.return_value = {"error": "boom"}
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    out = json.loads(provider.handle_tool_call("hal0_memory_add", {"text": "fact"}))
    assert "bank" not in out


def test_handle_tool_call_unknown_tool() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    out = json.loads(provider.handle_tool_call("nope", {}))
    assert out["status"] == "error"
    assert "nope" in out["error"]


def test_handle_tool_call_swallows_client_error() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.search.side_effect = Hal0MemoryClientError("boom")
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    out = json.loads(provider.handle_tool_call("hal0_memory_search", {"query": "q"}))
    assert out == {"status": "error", "error": "boom"}


# ── on_memory_write ─────────────────────────────────────────────────────


def test_on_memory_write_noop_for_non_add_action() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    provider.on_memory_write("delete", "t", "content")
    fake_client.add.assert_not_called()


def test_on_memory_write_noop_without_client() -> None:
    provider = Hal0MemoryProvider()
    provider.on_memory_write("add", "t", "content")  # must not raise


def test_on_memory_write_noop_for_empty_content() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    provider.on_memory_write("add", "t", "")
    fake_client.add.assert_not_called()


@pytest.mark.parametrize("context", ["cron", "flush", "subagent"])
def test_on_memory_write_noop_for_skip_write_contexts(context: str) -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize(agent_context=context)
    provider.on_memory_write("add", "t", "content")
    fake_client.add.assert_not_called()


def test_on_memory_write_adds_with_target_tag() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    provider.on_memory_write("add", "note", "content", metadata={"k": "v"})
    fake_client.add.assert_called_once_with(
        "content",
        tags=["builtin-memory", "agent:hermes", "note"],
        metadata={"k": "v"},
        private=True,
    )


def test_on_memory_write_without_target_uses_base_tags() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    provider.on_memory_write("add", "", "content")
    fake_client.add.assert_called_once_with(
        "content", tags=["builtin-memory", "agent:hermes"], metadata=None, private=True
    )


def test_on_memory_write_swallows_client_error() -> None:
    fake_client = Mock(spec=Hal0MemoryClient)
    fake_client.add.side_effect = Hal0MemoryClientError("boom")
    provider = Hal0MemoryProvider(client=fake_client)
    provider.initialize()
    provider.on_memory_write("add", "t", "content")  # must not raise
