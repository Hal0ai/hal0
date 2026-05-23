"""route_to_chat handler + guardrail tests — plan §7.4."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.omni_router.dispatch import DispatchContext, dispatch_tool
from hal0.omni_router.route_to_chat import (
    DELEGATION_DEPTH,
    MAX_DELEGATION_DEPTH,
    build_delegation_messages,
    validate_delegation,
)
from tests.omni_router.conftest import FakeSlotManager, make_http_client, make_slot

# ── validate_delegation (pure-sync matrix) ──────────────────────────


def test_validate_target_missing() -> None:
    configs = [make_slot("primary", type="llm", model="x", labels=("tool-calling",))]
    err = validate_delegation(configs, caller_slot_name="primary", target="nope", current_depth=0)
    assert err is not None
    assert "not enabled" in err


def test_validate_target_disabled_is_rejected() -> None:
    configs = [
        make_slot("primary", type="llm", model="x", labels=("tool-calling",)),
        make_slot("coder", type="llm", model="qwen", labels=(), enabled=False),
    ]
    err = validate_delegation(configs, caller_slot_name="primary", target="coder", current_depth=0)
    assert err is not None
    assert "not enabled" in err


def test_validate_self_delegation_rejected() -> None:
    configs = [make_slot("primary", type="llm", model="x", labels=("tool-calling",))]
    err = validate_delegation(
        configs, caller_slot_name="primary", target="primary", current_depth=0
    )
    assert err is not None
    assert "self" in err.lower()


def test_validate_npu_npu_rejected() -> None:
    """Plan §7.4 guardrail 3 — both ends on NPU forces FLM swap."""
    configs = [
        make_slot(
            "agent",
            type="llm",
            model="flm-agent",
            labels=("tool-calling",),
            device="npu",
        ),
        make_slot("npu-peer", type="llm", model="flm-peer", labels=(), device="npu"),
    ]
    err = validate_delegation(configs, caller_slot_name="agent", target="npu-peer", current_depth=0)
    assert err is not None
    assert "NPU" in err


def test_validate_npu_to_gpu_allowed() -> None:
    configs = [
        make_slot(
            "agent",
            type="llm",
            model="flm-agent",
            labels=("tool-calling",),
            device="npu",
        ),
        make_slot("coder", type="llm", model="qwen", labels=(), device="gpu-rocm"),
    ]
    assert (
        validate_delegation(configs, caller_slot_name="agent", target="coder", current_depth=0)
        is None
    )


def test_validate_gpu_to_npu_allowed() -> None:
    configs = [
        make_slot(
            "primary",
            type="llm",
            model="qwen",
            labels=("tool-calling",),
            device="gpu-rocm",
        ),
        make_slot("agent", type="llm", model="flm", labels=(), device="npu"),
    ]
    assert (
        validate_delegation(configs, caller_slot_name="primary", target="agent", current_depth=0)
        is None
    )


def test_validate_depth_limit_rejected() -> None:
    """At depth=1 (already in a delegated call), another delegation is refused."""
    configs = [
        make_slot("primary", type="llm", model="x", labels=("tool-calling",)),
        make_slot("coder", type="llm", model="y", labels=()),
    ]
    err = validate_delegation(
        configs,
        caller_slot_name="primary",
        target="coder",
        current_depth=MAX_DELEGATION_DEPTH,
    )
    assert err is not None
    assert "depth" in err.lower()


def test_validate_target_wrong_type_rejected() -> None:
    """Target must be a chat slot, not an embedding/image/etc. slot."""
    configs = [
        make_slot("primary", type="llm", model="x", labels=("tool-calling",)),
        make_slot("embed", type="embedding", model="bge", labels=("embeddings",)),
    ]
    err = validate_delegation(configs, caller_slot_name="primary", target="embed", current_depth=0)
    assert err is not None
    assert "not enabled" in err


def test_validate_happy_path_returns_none() -> None:
    configs = [
        make_slot("primary", type="llm", model="x", labels=("tool-calling",)),
        make_slot("coder", type="llm", model="qwen", labels=()),
    ]
    assert (
        validate_delegation(configs, caller_slot_name="primary", target="coder", current_depth=0)
        is None
    )


# ── build_delegation_messages ───────────────────────────────────────


def test_build_messages_with_system_prompt_and_context() -> None:
    target = make_slot(
        "coder",
        type="llm",
        model="qwen",
        labels=(),
        system_prompt="You are a coder.",
    )
    msgs = build_delegation_messages(target, prompt="Fix the bug.", context="It's in line 42.")
    assert msgs[0] == {"role": "system", "content": "You are a coder."}
    assert msgs[1]["role"] == "user"
    assert "Fix the bug." in msgs[1]["content"]
    assert "Context:" in msgs[1]["content"]
    assert "It's in line 42." in msgs[1]["content"]


def test_build_messages_omits_system_when_unset() -> None:
    target = make_slot("coder", type="llm", model="qwen", labels=())
    msgs = build_delegation_messages(target, prompt="hi", context=None)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hi"


def test_build_messages_omits_context_when_none() -> None:
    target = make_slot(
        "coder",
        type="llm",
        model="qwen",
        labels=(),
        system_prompt="persona",
    )
    msgs = build_delegation_messages(target, prompt="hi", context=None)
    assert "Context" not in msgs[1]["content"]


# ── full handler integration ────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_to_chat_happy_path() -> None:
    """Full delegation: caller asks coder a question; coder responds;
    the assistant content is returned as the tool_result body."""
    slots = [
        make_slot("primary", type="llm", model="agent", labels=("tool-calling",)),
        make_slot(
            "coder",
            type="llm",
            model="qwen-coder",
            labels=(),
            system_prompt="You write code.",
        ),
    ]

    seen: dict[str, Any] = {}

    async def chat_completion(body: dict[str, Any]) -> dict[str, Any]:
        seen["body"] = body
        return {"choices": [{"message": {"content": "def f(): pass"}}]}

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(lambda _: httpx.Response(200, json={})),
        lemonade_base_url="http://test",
        caller_slot_name="primary",
        chat_completion=chat_completion,
    )
    result = await dispatch_tool(
        ctx, "route_to_chat", {"target": "coder", "prompt": "write a function"}
    )
    assert result == {"content": "def f(): pass"}
    assert seen["body"]["model"] == "qwen-coder"
    msgs = seen["body"]["messages"]
    assert msgs[0]["content"] == "You write code."
    assert msgs[1]["content"] == "write a function"


@pytest.mark.asyncio
async def test_route_to_chat_target_not_found() -> None:
    slots = [
        make_slot("primary", type="llm", model="agent", labels=("tool-calling",)),
    ]

    async def chat_completion(_: dict[str, Any]) -> dict[str, Any]:
        return {}

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(lambda _: httpx.Response(200, json={})),
        lemonade_base_url="http://test",
        caller_slot_name="primary",
        chat_completion=chat_completion,
    )
    result = await dispatch_tool(ctx, "route_to_chat", {"target": "ghost", "prompt": "x"})
    assert "error" in result
    assert "ghost" in result["error"]


@pytest.mark.asyncio
async def test_route_to_chat_self_blocked() -> None:
    slots = [
        make_slot("primary", type="llm", model="agent", labels=("tool-calling",)),
    ]

    async def chat_completion(_: dict[str, Any]) -> dict[str, Any]:
        return {}

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(lambda _: httpx.Response(200, json={})),
        lemonade_base_url="http://test",
        caller_slot_name="primary",
        chat_completion=chat_completion,
    )
    result = await dispatch_tool(ctx, "route_to_chat", {"target": "primary", "prompt": "x"})
    assert "error" in result
    assert "self" in result["error"].lower()


@pytest.mark.asyncio
async def test_route_to_chat_npu_npu_blocked() -> None:
    slots = [
        make_slot(
            "agent",
            type="llm",
            model="flm",
            labels=("tool-calling",),
            device="npu",
        ),
        make_slot("npu2", type="llm", model="flm2", labels=(), device="npu"),
    ]

    async def chat_completion(_: dict[str, Any]) -> dict[str, Any]:
        return {}

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(lambda _: httpx.Response(200, json={})),
        lemonade_base_url="http://test",
        caller_slot_name="agent",
        chat_completion=chat_completion,
    )
    result = await dispatch_tool(ctx, "route_to_chat", {"target": "npu2", "prompt": "x"})
    assert "error" in result
    assert "NPU" in result["error"]


@pytest.mark.asyncio
async def test_route_to_chat_depth_limit_enforced() -> None:
    """At depth=1 the next route_to_chat call refuses."""
    slots = [
        make_slot("primary", type="llm", model="agent", labels=("tool-calling",)),
        make_slot("coder", type="llm", model="qwen", labels=()),
    ]

    async def chat_completion(_: dict[str, Any]) -> dict[str, Any]:
        return {}

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(lambda _: httpx.Response(200, json={})),
        lemonade_base_url="http://test",
        caller_slot_name="primary",
        chat_completion=chat_completion,
    )
    # Pretend we're already inside a delegated call.
    token = DELEGATION_DEPTH.set(MAX_DELEGATION_DEPTH)
    try:
        result = await dispatch_tool(ctx, "route_to_chat", {"target": "coder", "prompt": "x"})
    finally:
        DELEGATION_DEPTH.reset(token)
    assert "error" in result
    assert "depth" in result["error"].lower()


@pytest.mark.asyncio
async def test_route_to_chat_increments_depth_during_callback() -> None:
    """Inside the chat_completion callback, depth should be 1."""
    slots = [
        make_slot("primary", type="llm", model="agent", labels=("tool-calling",)),
        make_slot("coder", type="llm", model="qwen", labels=()),
    ]
    captured: dict[str, int] = {}

    async def chat_completion(_: dict[str, Any]) -> dict[str, Any]:
        captured["depth_during"] = DELEGATION_DEPTH.get()
        return {"choices": [{"message": {"content": "ok"}}]}

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(lambda _: httpx.Response(200, json={})),
        lemonade_base_url="http://test",
        caller_slot_name="primary",
        chat_completion=chat_completion,
    )
    await dispatch_tool(ctx, "route_to_chat", {"target": "coder", "prompt": "x"})
    assert captured["depth_during"] == 1
    # After the call returns, depth is reset.
    assert DELEGATION_DEPTH.get() == 0


@pytest.mark.asyncio
async def test_route_to_chat_missing_prompt() -> None:
    slots = [
        make_slot("primary", type="llm", model="agent", labels=("tool-calling",)),
        make_slot("coder", type="llm", model="qwen", labels=()),
    ]
    ctx = DispatchContext(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(lambda _: httpx.Response(200, json={})),
        lemonade_base_url="http://test",
        caller_slot_name="primary",
        chat_completion=lambda body: (_ for _ in ()).throw(  # pragma: no cover
            AssertionError("should not be called")
        ),
    )
    result = await dispatch_tool(ctx, "route_to_chat", {"target": "coder"})
    assert "error" in result
    assert "prompt" in result["error"]


@pytest.mark.asyncio
async def test_route_to_chat_context_appended() -> None:
    slots = [
        make_slot("primary", type="llm", model="agent", labels=("tool-calling",)),
        make_slot("coder", type="llm", model="qwen", labels=()),
    ]
    seen: dict[str, Any] = {}

    async def chat_completion(body: dict[str, Any]) -> dict[str, Any]:
        seen["msgs"] = body["messages"]
        return {"choices": [{"message": {"content": "ok"}}]}

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(lambda _: httpx.Response(200, json={})),
        lemonade_base_url="http://test",
        caller_slot_name="primary",
        chat_completion=chat_completion,
    )
    await dispatch_tool(
        ctx,
        "route_to_chat",
        {"target": "coder", "prompt": "p", "context": "ctx"},
    )
    user_msg = seen["msgs"][-1]
    assert "Context:" in user_msg["content"]
    assert "ctx" in user_msg["content"]


@pytest.mark.asyncio
async def test_route_to_chat_no_callback_returns_error() -> None:
    """If the loop didn't wire a chat_completion callback, refuse cleanly."""
    slots = [
        make_slot("primary", type="llm", model="agent", labels=("tool-calling",)),
        make_slot("coder", type="llm", model="qwen", labels=()),
    ]
    ctx = DispatchContext(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(lambda _: httpx.Response(200, json={})),
        lemonade_base_url="http://test",
        caller_slot_name="primary",
        chat_completion=None,
    )
    result = await dispatch_tool(ctx, "route_to_chat", {"target": "coder", "prompt": "x"})
    assert "error" in result
    assert "callback" in result["error"]


@pytest.mark.asyncio
async def test_route_to_chat_non_standard_response_passed_through() -> None:
    """If the target's response doesn't have the standard shape, pass it
    through as ``{response: ...}`` so the LLM can decide what to do."""
    slots = [
        make_slot("primary", type="llm", model="agent", labels=("tool-calling",)),
        make_slot("coder", type="llm", model="qwen", labels=()),
    ]

    async def chat_completion(_: dict[str, Any]) -> dict[str, Any]:
        return {"weird": "shape"}

    ctx = DispatchContext(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(lambda _: httpx.Response(200, json={})),
        lemonade_base_url="http://test",
        caller_slot_name="primary",
        chat_completion=chat_completion,
    )
    result = await dispatch_tool(ctx, "route_to_chat", {"target": "coder", "prompt": "x"})
    assert "response" in result
    assert result["response"] == {"weird": "shape"}
