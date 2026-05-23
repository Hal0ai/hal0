"""Filter matrix tests — plan §7.3.

Covers the dynamic-filtering decision tree:

  * Caller without ``tool-calling`` label → empty list.
  * Missing target slot for a tool → tool absent.
  * Missing label on otherwise-eligible slot → tool absent.
  * route_to_chat only appears when a peer chat slot exists.
  * The all-slots-present + all-labels-present matrix → all 8 tools.
"""

from __future__ import annotations

import pytest

from hal0.omni_router.filter import active_tools_for, chat_slot_has_tool_calling
from tests.omni_router.conftest import FakeSlotManager, make_slot


def _caller_no_tools_label() -> dict[str, object]:
    return make_slot("primary", type="llm", model="some-model", labels=())


def _caller_with_tools_label() -> dict[str, object]:
    return make_slot(
        "primary",
        type="llm",
        model="agent-7b",
        labels=("tool-calling",),
    )


@pytest.mark.asyncio
async def test_caller_without_tool_calling_label_gets_empty_list() -> None:
    """LLMs without ``tool-calling`` receive no tools at all."""
    mgr = FakeSlotManager([_caller_no_tools_label()])
    tools = await active_tools_for(mgr, "primary")
    assert tools == []


@pytest.mark.asyncio
async def test_caller_with_tool_calling_but_no_other_slots_returns_empty() -> None:
    """Caller has tool-calling but no peer slots → empty list."""
    mgr = FakeSlotManager([_caller_with_tools_label()])
    tools = await active_tools_for(mgr, "primary")
    # No image / tts / etc. slots exist and there's no peer chat slot
    # for route_to_chat. So: nothing.
    assert tools == []


@pytest.mark.asyncio
async def test_image_slot_present_enables_generate_image() -> None:
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot("img", type="image", model="sdxl", labels=("image",)),
        ]
    )
    tools = await active_tools_for(mgr, "primary")
    names = {t.name for t in tools}
    assert "generate_image" in names


@pytest.mark.asyncio
async def test_image_slot_without_edit_label_skips_edit_image() -> None:
    """An image slot tagged only ``image`` enables generate but not edit."""
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot("img", type="image", model="sdxl", labels=("image",)),
        ]
    )
    tools = await active_tools_for(mgr, "primary")
    names = {t.name for t in tools}
    assert "generate_image" in names
    assert "edit_image" not in names


@pytest.mark.asyncio
async def test_image_slot_with_edit_label_enables_edit_image() -> None:
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot(
                "img",
                type="image",
                model="sdxl-edit",
                labels=("image", "edit"),
            ),
        ]
    )
    tools = await active_tools_for(mgr, "primary")
    names = {t.name for t in tools}
    assert {"generate_image", "edit_image"}.issubset(names)


@pytest.mark.asyncio
async def test_tts_slot_enables_text_to_speech() -> None:
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot("tts", type="tts", model="kokoro", labels=("tts",)),
        ]
    )
    tools = await active_tools_for(mgr, "primary")
    assert "text_to_speech" in {t.name for t in tools}


@pytest.mark.asyncio
async def test_transcription_slot_enables_transcribe_audio() -> None:
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot(
                "stt",
                type="transcription",
                model="whisper",
                labels=("transcription",),
            ),
        ]
    )
    assert "transcribe_audio" in {t.name for t in await active_tools_for(mgr, "primary")}


@pytest.mark.asyncio
async def test_embedding_slot_enables_embed_text() -> None:
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot(
                "embed",
                type="embedding",
                model="bge",
                labels=("embeddings",),
            ),
        ]
    )
    assert "embed_text" in {t.name for t in await active_tools_for(mgr, "primary")}


@pytest.mark.asyncio
async def test_reranking_slot_enables_rerank_documents() -> None:
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot(
                "rerank",
                type="reranking",
                model="bge-rerank",
                labels=("reranking",),
            ),
        ]
    )
    assert "rerank_documents" in {t.name for t in await active_tools_for(mgr, "primary")}


@pytest.mark.asyncio
async def test_vision_capable_llm_enables_analyze_image() -> None:
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot(
                "vision",
                type="llm",
                model="gemma-vision",
                labels=("vision",),
            ),
        ]
    )
    tools = {t.name for t in await active_tools_for(mgr, "primary")}
    assert "analyze_image" in tools


@pytest.mark.asyncio
async def test_llm_without_vision_label_does_not_enable_analyze_image() -> None:
    """Plain LLM peer doesn't enable analyze_image — vision label required."""
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot("coder", type="llm", model="qwen-coder", labels=()),
        ]
    )
    tools = {t.name for t in await active_tools_for(mgr, "primary")}
    assert "analyze_image" not in tools


@pytest.mark.asyncio
async def test_peer_chat_slot_enables_route_to_chat() -> None:
    """``route_to_chat`` requires at least one other enabled llm slot."""
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot("coder", type="llm", model="qwen-coder", labels=()),
        ]
    )
    tools = {t.name for t in await active_tools_for(mgr, "primary")}
    assert "route_to_chat" in tools


@pytest.mark.asyncio
async def test_no_peer_chat_slot_disables_route_to_chat() -> None:
    """A lone chat slot can't delegate to anyone."""
    mgr = FakeSlotManager([_caller_with_tools_label()])
    tools = {t.name for t in await active_tools_for(mgr, "primary")}
    assert "route_to_chat" not in tools


@pytest.mark.asyncio
async def test_disabled_peer_chat_slot_does_not_enable_route_to_chat() -> None:
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot(
                "coder",
                type="llm",
                model="qwen-coder",
                labels=(),
                enabled=False,
            ),
        ]
    )
    tools = {t.name for t in await active_tools_for(mgr, "primary")}
    assert "route_to_chat" not in tools


@pytest.mark.asyncio
async def test_all_slots_present_yields_all_eight_tools() -> None:
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot("coder", type="llm", model="qwen-coder", labels=("vision",)),
            make_slot(
                "img",
                type="image",
                model="sdxl",
                labels=("image", "edit"),
            ),
            make_slot("tts", type="tts", model="kokoro", labels=("tts",)),
            make_slot(
                "stt",
                type="transcription",
                model="whisper",
                labels=("transcription",),
            ),
            make_slot(
                "embed",
                type="embedding",
                model="bge",
                labels=("embeddings",),
            ),
            make_slot(
                "rerank",
                type="reranking",
                model="bge-rerank",
                labels=("reranking",),
            ),
        ]
    )
    tools = {t.name for t in await active_tools_for(mgr, "primary")}
    assert tools == {
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
async def test_missing_caller_slot_returns_empty_list() -> None:
    """Race-safe: caller slot vanished between resolution + filter call."""
    mgr = FakeSlotManager([make_slot("other", type="llm", model="x", labels=("tool-calling",))])
    assert await active_tools_for(mgr, "primary") == []


@pytest.mark.asyncio
async def test_disabled_image_slot_disables_generate_image() -> None:
    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot(
                "img",
                type="image",
                model="sdxl",
                labels=("image",),
                enabled=False,
            ),
        ]
    )
    tools = {t.name for t in await active_tools_for(mgr, "primary")}
    assert "generate_image" not in tools


def test_chat_slot_has_tool_calling_true() -> None:
    cfg = make_slot("p", type="llm", model="x", labels=("tool-calling",))
    assert chat_slot_has_tool_calling(cfg) is True


def test_chat_slot_has_tool_calling_false_no_labels() -> None:
    cfg = make_slot("p", type="llm", model="x", labels=())
    assert chat_slot_has_tool_calling(cfg) is False


def test_chat_slot_has_tool_calling_false_wrong_label() -> None:
    cfg = make_slot("p", type="llm", model="x", labels=("vision",))
    assert chat_slot_has_tool_calling(cfg) is False


@pytest.mark.asyncio
async def test_tool_order_matches_canonical_order() -> None:
    """Active tools are returned in TOOL_DEFINITIONS declaration order
    so the LLM sees a stable list across requests."""
    from hal0.omni_router.tools import TOOL_DEFINITIONS

    mgr = FakeSlotManager(
        [
            _caller_with_tools_label(),
            make_slot("coder", type="llm", model="qwen-coder", labels=("vision",)),
            make_slot("img", type="image", model="sdxl", labels=("image", "edit")),
            make_slot("tts", type="tts", model="kokoro", labels=("tts",)),
            make_slot(
                "stt",
                type="transcription",
                model="whisper",
                labels=("transcription",),
            ),
            make_slot("embed", type="embedding", model="bge", labels=("embeddings",)),
            make_slot(
                "rerank",
                type="reranking",
                model="bge-rerank",
                labels=("reranking",),
            ),
        ]
    )
    active = await active_tools_for(mgr, "primary")
    canonical = [t.name for t in TOOL_DEFINITIONS]
    assert [t.name for t in active] == canonical
