"""Per-tool dispatch handler tests — plan §7.

Each handler validates args, routes via SlotManager, calls an HTTP
endpoint, and shapes the tool_result. We mock httpx via
``httpx.MockTransport`` and the SlotManager via the FakeSlotManager
from conftest.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.omni_router.dispatch import (
    HANDLERS,
    DispatchContext,
    dispatch_tool,
)
from tests.omni_router.conftest import FakeSlotManager, make_http_client, make_slot


def _ctx(
    handler_resp: dict[str, Any] | None = None,
    *,
    status: int = 200,
    slots: list[dict[str, Any]] | None = None,
    caller_slot_name: str = "primary",
) -> DispatchContext:
    """Build a DispatchContext wired against a single-response mock."""

    def handler(_request: httpx.Request) -> httpx.Response:
        if handler_resp is None:
            return httpx.Response(status, json={"ok": True})
        return httpx.Response(status, json=handler_resp)

    return DispatchContext(
        slot_manager=FakeSlotManager(slots or []),
        http_client=make_http_client(handler),
        api_base_url="http://test",
        caller_slot_name=caller_slot_name,
    )


# ── handler table sanity ─────────────────────────────────────────────


def test_handlers_table_covers_all_eight_tools() -> None:
    assert set(HANDLERS.keys()) == {
        "generate_image",
        "edit_image",
        "text_to_speech",
        "transcribe_audio",
        "analyze_image",
        "embed_text",
        "rerank_documents",
        "route_to_chat",
    }


@pytest.mark.asyncio
async def test_dispatch_tool_unknown_returns_error() -> None:
    ctx = _ctx()
    result = await dispatch_tool(ctx, "no_such_tool", {})
    assert "error" in result
    assert "no_such_tool" in result["error"]


# ── generate_image ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_image_happy_path() -> None:
    slots = [
        make_slot("img", type="image", model="sdxl", labels=("image",)),
    ]
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = req.read()
        return httpx.Response(200, json={"data": [{"url": "x"}]})

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(handler),
        api_base_url="http://test",
        caller_slot_name="primary",
    )
    result = await dispatch_tool(ctx, "generate_image", {"prompt": "a cat"})
    assert result == {"data": [{"url": "x"}]}
    assert seen["path"] == "/v1/images/generations"
    import json as _j

    body = _j.loads(seen["body"])
    assert body["model"] == "sdxl"
    assert body["prompt"] == "a cat"


@pytest.mark.asyncio
async def test_generate_image_missing_prompt() -> None:
    ctx = _ctx(slots=[make_slot("img", type="image", model="sdxl", labels=("image",))])
    result = await dispatch_tool(ctx, "generate_image", {})
    assert "error" in result
    assert "prompt" in result["error"]


@pytest.mark.asyncio
async def test_generate_image_no_image_slot() -> None:
    ctx = _ctx(slots=[])
    result = await dispatch_tool(ctx, "generate_image", {"prompt": "x"})
    assert "error" in result
    assert "image" in result["error"]


@pytest.mark.asyncio
async def test_generate_image_passes_optional_size_and_n() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = req.read()
        return httpx.Response(200, json={})

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(
            [make_slot("img", type="image", model="sdxl", labels=("image",))]
        ),
        http_client=make_http_client(handler),
        api_base_url="http://test",
        caller_slot_name="primary",
    )
    await dispatch_tool(ctx, "generate_image", {"prompt": "x", "size": "512x512", "n": 2})
    import json as _j

    body = _j.loads(seen["body"])
    assert body["size"] == "512x512"
    assert body["n"] == 2


# ── edit_image ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_image_happy_path() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        return httpx.Response(200, json={"ok": True})

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(
            [make_slot("img", type="image", model="sdxl-edit", labels=("image", "edit"))]
        ),
        http_client=make_http_client(handler),
        api_base_url="http://test",
        caller_slot_name="primary",
    )
    result = await dispatch_tool(
        ctx, "edit_image", {"image": "data:base64...", "prompt": "make sky red"}
    )
    assert seen["path"] == "/v1/images/edits"
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_edit_image_missing_image() -> None:
    ctx = _ctx(slots=[make_slot("img", type="image", model="sdxl-edit", labels=("image", "edit"))])
    result = await dispatch_tool(ctx, "edit_image", {"prompt": "x"})
    assert "error" in result
    assert "image" in result["error"]


@pytest.mark.asyncio
async def test_edit_image_image_slot_lacking_edit_label() -> None:
    """Image slot without ``edit`` label is rejected by route_for_request."""
    ctx = _ctx(slots=[make_slot("img", type="image", model="sdxl", labels=("image",))])
    result = await dispatch_tool(ctx, "edit_image", {"image": "x", "prompt": "y"})
    assert "error" in result


# ── text_to_speech ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_text_to_speech_happy_path() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        return httpx.Response(200, json={"audio": "..."})

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(
            [make_slot("tts", type="tts", model="kokoro", labels=("tts",))]
        ),
        http_client=make_http_client(handler),
        api_base_url="http://test",
        caller_slot_name="primary",
    )
    result = await dispatch_tool(ctx, "text_to_speech", {"input": "hello"})
    assert seen["path"] == "/v1/audio/speech"
    assert result == {"audio": "..."}


@pytest.mark.asyncio
async def test_text_to_speech_binary_response_returns_metadata() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        # /v1/audio/speech returns audio/wav. Our handler returns
        # metadata so the LLM context doesn't get a megabyte blob.
        return httpx.Response(
            200,
            content=b"RIFF....WAVE...",
            headers={"content-type": "audio/wav"},
        )

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(
            [make_slot("tts", type="tts", model="kokoro", labels=("tts",))]
        ),
        http_client=make_http_client(handler),
        api_base_url="http://test",
        caller_slot_name="primary",
    )
    result = await dispatch_tool(ctx, "text_to_speech", {"input": "hello"})
    assert result["content_type"] == "audio/wav"
    assert result["byte_length"] == len(b"RIFF....WAVE...")


@pytest.mark.asyncio
async def test_text_to_speech_missing_input() -> None:
    ctx = _ctx(slots=[make_slot("tts", type="tts", model="kokoro", labels=("tts",))])
    result = await dispatch_tool(ctx, "text_to_speech", {})
    assert "error" in result


# ── transcribe_audio ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_audio_happy_path() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        return httpx.Response(200, json={"text": "hello world"})

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(
            [
                make_slot(
                    "stt",
                    type="transcription",
                    model="whisper",
                    labels=("transcription",),
                )
            ]
        ),
        http_client=make_http_client(handler),
        api_base_url="http://test",
        caller_slot_name="primary",
    )
    result = await dispatch_tool(ctx, "transcribe_audio", {"audio": "data:base64..."})
    assert seen["path"] == "/v1/audio/transcriptions"
    assert result == {"text": "hello world"}


@pytest.mark.asyncio
async def test_transcribe_audio_missing_audio() -> None:
    ctx = _ctx(
        slots=[
            make_slot(
                "stt",
                type="transcription",
                model="whisper",
                labels=("transcription",),
            )
        ]
    )
    result = await dispatch_tool(ctx, "transcribe_audio", {})
    assert "error" in result


# ── analyze_image ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_image_happy_path() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = req.read()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "I see a cat."}}]},
        )

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(
            [
                make_slot(
                    "vision",
                    type="llm",
                    model="gemma-vision",
                    labels=("vision",),
                )
            ]
        ),
        http_client=make_http_client(handler),
        api_base_url="http://test",
        caller_slot_name="primary",
    )
    result = await dispatch_tool(
        ctx,
        "analyze_image",
        {"image": "data:base64...", "question": "what is in it?"},
    )
    assert seen["path"] == "/v1/chat/completions"
    import json as _j

    body = _j.loads(seen["body"])
    assert body["model"] == "gemma-vision"
    assert body["messages"][0]["role"] == "user"
    # multimodal content array shape
    content = body["messages"][0]["content"]
    assert any(part.get("type") == "image_url" for part in content)
    assert any(part.get("type") == "text" for part in content)
    assert result["choices"][0]["message"]["content"] == "I see a cat."


@pytest.mark.asyncio
async def test_analyze_image_no_vision_llm() -> None:
    ctx = _ctx(slots=[make_slot("primary", type="llm", model="chat", labels=("tool-calling",))])
    result = await dispatch_tool(ctx, "analyze_image", {"image": "x", "question": "y"})
    assert "error" in result


# ── embed_text ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_embed_text_happy_path() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = req.read()
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(
            [make_slot("embed", type="embedding", model="bge", labels=("embeddings",))]
        ),
        http_client=make_http_client(handler),
        api_base_url="http://test",
        caller_slot_name="primary",
    )
    result = await dispatch_tool(ctx, "embed_text", {"input": ["hello", "world"]})
    assert seen["path"] == "/v1/embeddings"
    import json as _j

    body = _j.loads(seen["body"])
    assert body["model"] == "bge"
    assert body["input"] == ["hello", "world"]
    assert result["data"][0]["embedding"] == [0.1, 0.2]


@pytest.mark.asyncio
async def test_embed_text_empty_input_rejected() -> None:
    ctx = _ctx(slots=[make_slot("embed", type="embedding", model="bge", labels=("embeddings",))])
    result = await dispatch_tool(ctx, "embed_text", {"input": []})
    assert "error" in result


# ── rerank_documents ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_documents_happy_path() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = req.read()
        return httpx.Response(200, json={"results": [{"index": 1, "score": 0.9}]})

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(
            [
                make_slot(
                    "rerank",
                    type="reranking",
                    model="bge-rerank",
                    labels=("reranking",),
                )
            ]
        ),
        http_client=make_http_client(handler),
        api_base_url="http://test",
        caller_slot_name="primary",
    )
    result = await dispatch_tool(
        ctx,
        "rerank_documents",
        {"query": "q", "documents": ["a", "b"], "top_n": 1},
    )
    assert seen["path"] == "/v1/rerank"
    import json as _j

    body = _j.loads(seen["body"])
    assert body["query"] == "q"
    assert body["documents"] == ["a", "b"]
    assert body["top_n"] == 1
    assert result["results"][0]["score"] == 0.9


@pytest.mark.asyncio
async def test_rerank_documents_missing_query() -> None:
    ctx = _ctx(
        slots=[
            make_slot(
                "rerank",
                type="reranking",
                model="bge-rerank",
                labels=("reranking",),
            )
        ]
    )
    result = await dispatch_tool(ctx, "rerank_documents", {"documents": ["a"]})
    assert "error" in result


@pytest.mark.asyncio
async def test_rerank_documents_empty_documents() -> None:
    ctx = _ctx(
        slots=[
            make_slot(
                "rerank",
                type="reranking",
                model="bge-rerank",
                labels=("reranking",),
            )
        ]
    )
    result = await dispatch_tool(ctx, "rerank_documents", {"query": "q", "documents": []})
    assert "error" in result


# ── transport failures ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transport_failure_returns_error_envelope() -> None:
    """httpx ConnectError → tool_result error, not raise."""

    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("kaboom")

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(
            [make_slot("embed", type="embedding", model="bge", labels=("embeddings",))]
        ),
        http_client=make_http_client(handler),
        api_base_url="http://test",
        caller_slot_name="primary",
    )
    result = await dispatch_tool(ctx, "embed_text", {"input": ["x"]})
    assert "error" in result
    assert "transport" in result["error"].lower()


@pytest.mark.asyncio
async def test_upstream_5xx_returns_error_envelope() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"err": "kaboom"})

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(
            [make_slot("embed", type="embedding", model="bge", labels=("embeddings",))]
        ),
        http_client=make_http_client(handler),
        api_base_url="http://test",
        caller_slot_name="primary",
    )
    result = await dispatch_tool(ctx, "embed_text", {"input": ["x"]})
    assert "error" in result
    assert "500" in result["error"]


@pytest.mark.asyncio
async def test_handler_internal_exception_returns_error_envelope() -> None:
    """A truly unexpected exception inside a handler is caught at the
    dispatch_tool boundary and returned as a tool_result envelope."""

    async def crashing_handler(_ctx: Any, _args: Any) -> dict[str, Any]:
        raise RuntimeError("oh no")

    # Monkey-patch the handler table for one call.
    original = HANDLERS["embed_text"]
    HANDLERS["embed_text"] = crashing_handler
    try:
        ctx = _ctx(slots=[])
        result = await dispatch_tool(ctx, "embed_text", {"input": ["x"]})
        assert "error" in result
        assert "RuntimeError" in result["error"]
    finally:
        HANDLERS["embed_text"] = original
