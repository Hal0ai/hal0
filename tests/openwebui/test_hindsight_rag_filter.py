"""Bundled Open WebUI Hindsight RAG filter.

The shapes asserted here are what Open WebUI v0.12.5 reads, not what looks
tidy: ``utils/filter.py`` runs a ``toggle`` filter only for a chat that enabled
it, ``utils/middleware.py`` rebuilds ``metadata["files"]`` from ``body["files"]``
after the inlet filters, and ``retrieval/utils.py`` takes a text source's
metadata from ``file.meta``.
"""

from __future__ import annotations

import http.client
import urllib.error

import pytest

from hal0.openwebui.functions.hal0_hindsight_rag import Filter

SLOT = {"openai": {"owned_by": "hal0"}}


def _envelope(title="ADR-0043"):
    return {
        "engine": "hindsight",
        "contexts": [
            {
                "id": "hs:doc:chunk",
                "text": "remembered context",
                "title": title,
                "source": title,
                "document_id": "doc",
                "chunk_id": "chunk",
            }
        ],
        "facts": [],
        "truncated": False,
    }


def _patched(monkeypatch, envelope=None):
    function = Filter()
    seen = []
    monkeypatch.setattr(
        function, "_request_sync", lambda query: seen.append(query) or (envelope or _envelope())
    )
    return function, seen


def test_filter_is_not_a_toggle_filter():
    assert getattr(Filter, "toggle", None) is None


@pytest.mark.asyncio
async def test_filter_adds_a_source_open_webui_reads_and_keeps_attachments(monkeypatch):
    function, seen = _patched(monkeypatch)
    original = {"type": "file", "id": "upload-1"}
    body = {
        "model": "hal0/chat",
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "What did we decide?"},
        ],
        "files": [original],
    }

    result = await function.inlet(body, __metadata__={"chat_id": "chat-1"}, __model__=SLOT)

    assert seen == ["What did we decide?"]
    assert "metadata" not in result
    assert result["files"][0] is original
    source = result["files"][1]
    assert source["type"] == "text"
    assert source["context"] == "full"
    assert source["hal0_hindsight_context"] is True
    assert source["file"]["data"]["content"] == "remembered context"
    assert source["file"]["meta"] == {"name": "ADR-0043", "source": "hs:doc:chunk"}


@pytest.mark.asyncio
async def test_filter_reminder_never_adds_a_second_system_message(monkeypatch):
    function, _ = _patched(monkeypatch)
    body = {
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "q"},
        ]
    }
    await function.inlet(body, __model__=SLOT)
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"].startswith("Be concise.\n\n")
    assert "untrusted evidence" in body["messages"][0]["content"]


@pytest.mark.asyncio
async def test_filter_reminder_starts_the_conversation_without_a_system_message(monkeypatch):
    function, _ = _patched(monkeypatch)
    body = {"messages": [{"role": "user", "content": "q"}]}
    await function.inlet(body, __model__=SLOT)
    assert [message["role"] for message in body["messages"]] == ["system", "user"]


@pytest.mark.asyncio
async def test_filter_strips_attribute_breakers_from_the_source_name(monkeypatch):
    function, _ = _patched(monkeypatch, _envelope(title='x" onload="y><source'))
    body = {"messages": [{"role": "user", "content": "q"}]}
    await function.inlet(body, __model__=SLOT)
    name = body["files"][0]["name"]
    assert not set('"<>') & set(name)
    assert body["files"][0]["file"]["meta"]["name"] == name


@pytest.mark.asyncio
async def test_filter_reads_multimodal_user_text(monkeypatch):
    function, seen = _patched(monkeypatch)
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "x"}},
                    {"type": "text", "text": "remember this"},
                ],
            }
        ]
    }
    await function.inlet(body, __model__=SLOT)
    assert seen == ["remember this"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [
        {"openai": {"owned_by": "hermes"}},
        {"openai": {"owned_by": "hermes.discord"}},
        {"openai": {"owned_by": "openrouter"}},
        {"openai": {"owned_by": "minimax"}},
        {"openai": {"owned_by": "ollama"}},
        {"openai": {}},
        {"openai": "hal0"},
        # A workspace model over a slot: top-level owner, no ``openai`` key.
        {"id": "my-preset", "owned_by": "openai", "preset": True},
        {"owned_by": "hal0"},
        {},
        None,
    ],
)
async def test_filter_only_injects_on_a_model_hal0_owns(monkeypatch, model):
    function = Filter()
    monkeypatch.setattr(
        function,
        "_request_sync",
        lambda *args: pytest.fail("context endpoint must not be called"),
    )
    body = {"model": "hal0", "messages": [{"role": "user", "content": "q"}]}
    assert await function.inlet(body, __model__=model) is body
    assert "files" not in body


@pytest.mark.asyncio
async def test_filter_skips_task_calls_on_a_slot_model(monkeypatch):
    function = Filter()
    monkeypatch.setattr(
        function,
        "_request_sync",
        lambda *args: pytest.fail("context endpoint must not be called"),
    )
    body = {"messages": [{"role": "user", "content": "q"}]}
    await function.inlet(body, __metadata__={"task": "title_generation"}, __model__=SLOT)
    assert "files" not in body


@pytest.mark.asyncio
async def test_filter_clips_a_pasted_query(monkeypatch):
    function, seen = _patched(monkeypatch)
    body = {"messages": [{"role": "user", "content": "x" * 5000}]}
    await function.inlet(body, __model__=SLOT)
    assert seen == ["x" * 4000]


@pytest.mark.asyncio
async def test_filter_fail_open_off_re_raises(monkeypatch):
    function = Filter()
    function.valves.fail_open = False

    def fail(query):
        raise OSError("offline")

    monkeypatch.setattr(function, "_request_sync", fail)
    with pytest.raises(OSError):
        await function.inlet({"messages": [{"role": "user", "content": "q"}]}, __model__=SLOT)


@pytest.mark.asyncio
async def test_filter_does_not_skip_a_slot_model(monkeypatch):
    function, seen = _patched(monkeypatch)
    body = {"model": "chat", "messages": [{"role": "user", "content": "q"}]}
    # Open WebUI sets a task_id on every ordinary chat turn; it is not a task call.
    await function.inlet(
        body,
        __metadata__={"task_id": "uuid", "chat_id": "c"},
        __model__=SLOT,
    )
    assert seen == ["q"]


@pytest.mark.asyncio
async def test_filter_is_idempotent(monkeypatch):
    function, seen = _patched(monkeypatch)
    body = {"messages": [{"role": "user", "content": "q"}]}
    await function.inlet(body, __model__=SLOT)
    await function.inlet(body, __model__=SLOT)
    assert seen == ["q"]
    assert len(body["files"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        OSError("offline"),
        TimeoutError(),
        ValueError("bad envelope"),
        http.client.IncompleteRead(b"partial"),
        urllib.error.HTTPError("u", 503, "unavailable", None, None),  # type: ignore[arg-type]
    ],
)
async def test_filter_fails_open_without_touching_the_request(monkeypatch, error):
    function = Filter()

    def fail(query):
        raise error

    monkeypatch.setattr(function, "_request_sync", fail)
    body = {"messages": [{"role": "user", "content": "q"}]}
    result = await function.inlet(body, __model__=SLOT)
    assert result == {"messages": [{"role": "user", "content": "q"}]}


@pytest.mark.asyncio
async def test_filter_survives_an_emitter_that_raises(monkeypatch):
    function = Filter()
    function.valves.show_failure_status = True

    def fail(query):
        raise OSError("offline")

    async def emitter(event):
        raise RuntimeError("socket gone")

    monkeypatch.setattr(function, "_request_sync", fail)
    body = {"messages": [{"role": "user", "content": "q"}]}
    result = await function.inlet(body, __model__=SLOT, __event_emitter__=emitter)
    assert result == {"messages": [{"role": "user", "content": "q"}]}


@pytest.mark.asyncio
async def test_filter_with_no_memory_leaves_the_request_alone(monkeypatch):
    function, _ = _patched(monkeypatch, {"contexts": [], "facts": []})
    body = {"messages": [{"role": "user", "content": "q"}]}
    assert await function.inlet(body, __model__=SLOT) == {
        "messages": [{"role": "user", "content": "q"}]
    }


def test_filter_sends_hal0_its_own_key_and_never_another_providers(monkeypatch):
    monkeypatch.setenv(
        "OPENAI_API_BASE_URLS", "https://api.example.com/v1;http://host.docker.internal:8080/v1"
    )
    monkeypatch.setenv("OPENAI_API_KEYS", "their-key;box-key")
    assert Filter()._api_key() == "box-key"

    monkeypatch.setenv("OPENAI_API_BASE_URLS", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEYS", "their-key")
    assert Filter()._api_key() == "sk-hal0-local"


def test_recall_timeout_outlasts_a_real_recall():
    # Recall took 6 to 8 seconds against a real bank; a cap under that means
    # memory silently never reaches a chat.
    assert Filter.Valves().timeout_seconds >= 10
