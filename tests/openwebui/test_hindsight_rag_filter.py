"""Bundled Open WebUI Hindsight RAG filter."""

from __future__ import annotations

import pytest

from hal0.openwebui.functions.hal0_hindsight_rag import Filter


def _envelope():
    return {
        "engine": "hindsight",
        "contexts": [
            {
                "id": "hs:doc:chunk",
                "text": "remembered context",
                "title": "ADR-0043",
                "source": "ADR-0043",
                "document_id": "doc",
                "chunk_id": "chunk",
            }
        ],
        "facts": [],
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_filter_adds_synthetic_text_source_and_preserves_attachments(monkeypatch):
    function = Filter()
    seen = {}

    def fake(query, user, metadata):
        seen.update(query=query, user=user, metadata=metadata)
        return _envelope()

    monkeypatch.setattr(function, "_request_sync", fake)
    original = {"type": "file", "id": "upload-1"}
    body = {
        "model": "hal0/chat",
        "messages": [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "What did we decide?"},
        ],
        "metadata": {"files": [original], "chat_id": "chat-1"},
    }

    result = await function.inlet(body, __user__={"name": "Mint"})

    assert result["metadata"]["files"][0] is original
    source = result["metadata"]["files"][1]
    assert source["type"] == "text"
    assert source["context"] == "full"
    assert source["hal0_hindsight_context"] is True
    assert source["file"]["data"]["meta"]["document_id"] == "doc"
    assert seen["query"] == "What did we decide?"
    assert result["messages"][0]["content"] == "Be concise."
    assert result["messages"][1]["role"] == "system"
    assert "untrusted evidence" in result["messages"][1]["content"]


@pytest.mark.asyncio
async def test_filter_reads_multimodal_user_text(monkeypatch):
    function = Filter()
    seen = []
    monkeypatch.setattr(
        function,
        "_request_sync",
        lambda query, user, metadata: seen.append(query) or _envelope(),
    )
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
    await function.inlet(body)
    assert seen == ["remember this"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,metadata",
    [
        ({"model": "hermes-3", "messages": [{"role": "user", "content": "q"}]}, {}),
        ({"messages": [{"role": "user", "content": "q"}]}, {"task": "title_generation"}),
    ],
)
async def test_filter_skips_agents_and_task_model_calls(monkeypatch, body, metadata):
    function = Filter()
    monkeypatch.setattr(
        function,
        "_request_sync",
        lambda *args: pytest.fail("context endpoint must not be called"),
    )
    assert await function.inlet(body, __metadata__=metadata) is body


@pytest.mark.asyncio
async def test_filter_is_idempotent(monkeypatch):
    function = Filter()
    calls = 0

    def fake(*args):
        nonlocal calls
        calls += 1
        return _envelope()

    monkeypatch.setattr(function, "_request_sync", fake)
    body = {"messages": [{"role": "user", "content": "q"}]}
    await function.inlet(body)
    await function.inlet(body)
    assert calls == 1
    assert len([f for f in body["metadata"]["files"] if f.get("hal0_hindsight_context")]) == 1


@pytest.mark.asyncio
async def test_filter_fails_open_without_touching_messages(monkeypatch):
    function = Filter()
    monkeypatch.setattr(
        function, "_request_sync", lambda *args: (_ for _ in ()).throw(OSError("offline"))
    )
    body = {"messages": [{"role": "user", "content": "q"}]}
    result = await function.inlet(body)
    assert result["messages"] == [{"role": "user", "content": "q"}]
    assert result["metadata"]["files"] == []


def test_filter_forwards_audit_provenance_but_not_private_scope(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEYS", "secret-key;another-key")
    headers = Filter._headers({"name": "Mint"}, {"chat_id": "chat-1"})
    assert headers["Authorization"] == "Bearer secret-key"
    assert headers["X-hal0-Agent"] == "openwebui"
    assert headers["X-OpenWebUI-User-Name"] == "Mint"
    assert headers["X-OpenWebUI-Chat-Id"] == "chat-1"
    assert "X-hal0-Private" not in headers
