"""OmniRouter.run_loop tests — plan §7.1 + ADR-0008 §8.

Covers the OpenAI tool-calling loop:

  * No tool_calls → return after one round.
  * One tool_call → dispatch, fold result, continue, terminate.
  * Multiple parallel tool_calls in one response → fan out, fold all.
  * Loop budget terminates pathological cases.
  * Caller without ``tool-calling`` skips the loop entirely.
  * Streaming knob from caller is stripped (PR-18 deferral).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from hal0.omni_router.router import OmniRouter
from tests.omni_router.conftest import FakeSlotManager, make_http_client, make_slot


def _caller(tool_calling: bool = True) -> dict[str, Any]:
    return make_slot(
        "primary",
        type="llm",
        model="agent",
        labels=("tool-calling",) if tool_calling else (),
    )


def _img_slot() -> dict[str, Any]:
    return make_slot("img", type="image", model="sdxl", labels=("image",))


def _make_router(handler, slots: list[dict[str, Any]]) -> OmniRouter:
    return OmniRouter(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(handler),
        api_base_url="http://test",
    )


# ── one round, no tool_calls ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_exits_after_one_round_when_no_tool_calls() -> None:
    rounds: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        rounds.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    router = _make_router(handler, [_caller(), _img_slot()])
    result = await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert len(rounds) == 1
    assert result["choices"][0]["message"]["content"] == "hi"


@pytest.mark.asyncio
async def test_loop_includes_active_tools_on_first_request() -> None:
    seen_tools: list[Any] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        seen_tools.append(body.get("tools"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    router = _make_router(handler, [_caller(), _img_slot()])
    await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": []},
    )
    # First (and only) request carried the tools array, including
    # generate_image because the img slot is configured.
    assert seen_tools[0] is not None
    names = {t["function"]["name"] for t in seen_tools[0]}
    assert "generate_image" in names


# ── single tool_call ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_dispatches_single_tool_call_and_continues() -> None:
    rounds: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        rounds.append(body)
        if req.url.path == "/v1/chat/completions":
            if len(rounds) == 1:
                # Round 1: model emits a tool_call.
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {
                                                "name": "generate_image",
                                                "arguments": json.dumps({"prompt": "a cat"}),
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                )
            # Round 2: model emits the final assistant text.
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "here you go"}}]},
            )
        if req.url.path == "/v1/images/generations":
            return httpx.Response(200, json={"data": [{"url": "ok"}]})
        return httpx.Response(404)

    router = _make_router(handler, [_caller(), _img_slot()])
    result = await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "draw"}]},
    )
    # We saw two chat-completions rounds.
    chat_rounds = [r for r in rounds if "tools" in r or "messages" in r]
    assert len(chat_rounds) == 2
    # Round 2's messages carry the assistant's tool_call turn AND the
    # tool result.
    second_msgs = chat_rounds[1]["messages"]
    roles = [m.get("role") for m in second_msgs]
    assert "assistant" in roles
    assert "tool" in roles
    # Tool-result content is the JSON-encoded dispatch body.
    tool_msg = next(m for m in second_msgs if m.get("role") == "tool")
    parsed = json.loads(tool_msg["content"])
    assert parsed == {"data": [{"url": "ok"}]}
    assert result["choices"][0]["message"]["content"] == "here you go"


# ── multiple parallel tool_calls in one response ─────────────────────


@pytest.mark.asyncio
async def test_loop_dispatches_multiple_tool_calls_in_one_round() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/chat/completions":
            body = json.loads(req.read())
            # Detect first vs second request by message count.
            if len(body.get("messages", [])) <= 1:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "c1",
                                            "type": "function",
                                            "function": {
                                                "name": "generate_image",
                                                "arguments": json.dumps({"prompt": "p1"}),
                                            },
                                        },
                                        {
                                            "id": "c2",
                                            "type": "function",
                                            "function": {
                                                "name": "embed_text",
                                                "arguments": json.dumps({"input": ["x"]}),
                                            },
                                        },
                                    ],
                                }
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"choices": [{"message": {"content": "done"}}]})
        if req.url.path == "/v1/images/generations":
            return httpx.Response(200, json={"data": "img"})
        if req.url.path == "/v1/embeddings":
            return httpx.Response(200, json={"data": "emb"})
        return httpx.Response(404)

    router = _make_router(
        handler,
        [
            _caller(),
            _img_slot(),
            make_slot("embed", type="embedding", model="bge", labels=("embeddings",)),
        ],
    )
    result = await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "x"}]},
    )
    assert result["choices"][0]["message"]["content"] == "done"


# ── empty tool list shortcut ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_skips_loop_when_no_tools_active() -> None:
    """Caller without ``tool-calling`` → no loop, single passthrough."""
    rounds: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        rounds.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    router = _make_router(handler, [_caller(tool_calling=False)])
    await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [], "tools": []},
    )
    assert len(rounds) == 1
    # No tools were injected — the body was passed through as-is.
    # (The original body had ``tools: []`` from the caller; we don't
    # overwrite when no tools are active.)


# ── loop budget ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_budget_terminates_on_pathological_tool_call_storm() -> None:
    """A model that emits tool_calls forever still terminates."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/chat/completions":
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "c",
                                        "type": "function",
                                        "function": {
                                            "name": "generate_image",
                                            "arguments": json.dumps({"prompt": "loop"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"data": "img"})

    router = _make_router(handler, [_caller(), _img_slot()])
    result = await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "x"}]},
    )
    # We don't crash — we return *something*. The last response is the
    # final tool_call-laden response (loop budget exhausted).
    assert result is not None


# ── streaming knob deferred ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_forces_stream_false() -> None:
    """Plan deferral — PR-16 returns non-streaming responses; PR-18
    layers streaming. The loop must override any client-set
    ``stream=true`` to keep the response shape uniform."""
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.read()))
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    router = _make_router(handler, [_caller(), _img_slot()])
    await router.run_loop(
        caller_slot_name="primary",
        body={
            "model": "agent",
            "messages": [],
            "stream": True,
        },
    )
    assert seen[0]["stream"] is False


# ── omni knob is stripped ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_strips_omni_knob_from_outbound_body() -> None:
    """The dispatcher's opt-in field ``omni`` is hal0-internal; it
    must NOT be forwarded upstream."""
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.read()))
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    router = _make_router(handler, [_caller(), _img_slot()])
    await router.run_loop(
        caller_slot_name="primary",
        body={
            "model": "agent",
            "messages": [],
            "omni": True,
        },
    )
    assert "omni" not in seen[0]


# ── active_tools surface ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_tools_surface_round_trip() -> None:
    router = _make_router(
        lambda _: httpx.Response(200, json={}),
        [_caller(), _img_slot()],
    )
    tools = await router.active_tools("primary")
    names = {t.name for t in tools}
    assert "generate_image" in names


# ── dispatch surface ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_surface_round_trip() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/embeddings":
            return httpx.Response(200, json={"data": [{"embedding": [0.1]}]})
        return httpx.Response(404)

    router = _make_router(
        handler,
        [
            _caller(),
            make_slot("embed", type="embedding", model="bge", labels=("embeddings",)),
        ],
    )
    result = await router.dispatch(
        caller_slot_name="primary",
        tool_name="embed_text",
        arguments={"input": ["hi"]},
    )
    assert result == {"data": [{"embedding": [0.1]}]}


# ── route_to_chat depth limit through the loop ───────────────────────


@pytest.mark.asyncio
async def test_route_to_chat_depth_limit_through_loop() -> None:
    """The loop wires the chat_completion callback into the dispatch
    context; route_to_chat re-enters the same loop's transport, but
    the depth contextvar prevents a third level."""
    requests: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        requests.append(body)
        if req.url.path != "/v1/chat/completions":
            return httpx.Response(404)
        # The OUTER call (caller=primary) emits a route_to_chat.
        # The DELEGATED call (caller=coder via callback) should NOT
        # emit another route_to_chat — but we don't trust the model,
        # so we test: if it does, depth guardrail catches it.
        # For this test we simulate the outer model emitting a single
        # route_to_chat, then the inner target returning content
        # directly (no nesting).
        is_inner = body.get("model") == "qwen-coder"
        if is_inner:
            return httpx.Response(200, json={"choices": [{"message": {"content": "code result"}}]})
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "rc1",
                                        "type": "function",
                                        "function": {
                                            "name": "route_to_chat",
                                            "arguments": json.dumps(
                                                {"target": "coder", "prompt": "do it"}
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "final"}}]},
        )

    slots = [
        _caller(),
        make_slot("coder", type="llm", model="qwen-coder", labels=()),
    ]
    router = OmniRouter(
        slot_manager=FakeSlotManager(slots),
        http_client=make_http_client(handler),
        api_base_url="http://test",
    )
    result = await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "x"}]},
    )
    assert result["choices"][0]["message"]["content"] == "final"
    # Make sure the inner delegated call to qwen-coder happened.
    inner_calls = [r for r in requests if r.get("model") == "qwen-coder"]
    assert len(inner_calls) == 1


# ── transport-layer error shape ──────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_chat_completion_transport_error_returned() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    router = _make_router(handler, [_caller()])
    # No active tools (no peer slots) → loop takes the shortcut path
    # and calls _chat_completion once. Even on transport failure the
    # result is an envelope, not an exception.
    result = await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": []},
    )
    assert "error" in result


# ── tool_calls arguments can be dict OR JSON string ──────────────────


@pytest.mark.asyncio
async def test_loop_handles_dict_arguments_shape() -> None:
    """Some backends ship ``arguments`` as a dict not a JSON string;
    accept both."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/chat/completions":
            body = json.loads(req.read())
            if len(body.get("messages", [])) <= 1:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "c1",
                                            "type": "function",
                                            "function": {
                                                "name": "generate_image",
                                                "arguments": {"prompt": "x"},
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        return httpx.Response(200, json={"data": "img"})

    router = _make_router(handler, [_caller(), _img_slot()])
    result = await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "x"}]},
    )
    assert result["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_loop_handles_malformed_arguments_gracefully() -> None:
    """Malformed JSON in tool_call.arguments → empty dict → handler
    surfaces missing-arg error, loop continues without crashing."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/chat/completions":
            body = json.loads(req.read())
            if len(body.get("messages", [])) <= 1:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "c1",
                                            "type": "function",
                                            "function": {
                                                "name": "generate_image",
                                                "arguments": "{{{not json",
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"choices": [{"message": {"content": "sorry"}}]})
        return httpx.Response(404)

    router = _make_router(handler, [_caller(), _img_slot()])
    result = await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "x"}]},
    )
    assert result["choices"][0]["message"]["content"] == "sorry"
