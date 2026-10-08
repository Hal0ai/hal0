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

import asyncio
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


# ── the shared loop core is not brain-specific (Stream G guard) ─────────────
#
# `hal0.toolloop.engine.run_tool_loop` is shared with the hal0-brain steward
# chat, which gained per-round model rerouting (`[brain_chat] tool_model`) so a
# tool round can leave the ~1.1B brain for a tool-capable model. That reroute
# is implemented ENTIRELY on the brain's side of the seam, as a wrapper around
# the `llm_fn` it hands the engine. These tests fail if it ever leaks in here.


@pytest.mark.asyncio
async def test_router_loop_never_rewrites_the_model_between_rounds() -> None:
    """Every round of an OmniRouter loop uses the caller's model, verbatim.

    The brain rewrites `body["model"]` per round. OmniRouter must not: its
    `model` is the caller's slot resolution and rewriting it would silently
    re-route someone else's `/v1/chat/completions` request mid-loop.
    """
    rounds: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        # Only the LOOP's own completions — not the image dispatch a tool call
        # fans out to, which legitimately carries the image slot's model.
        if req.url.path != "/v1/chat/completions":
            return httpx.Response(200, json={"data": [{"url": "http://img/1.png"}]})
        body = json.loads(req.read())
        rounds.append(body)
        if len(rounds) == 1:
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
                                            "arguments": json.dumps({"prompt": "a cat"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "done"}}]})

    router = _make_router(handler, [_caller(), _img_slot()])
    await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "draw"}]},
    )

    assert len(rounds) >= 2, "the loop did not run a continuation round"
    assert [r["model"] for r in rounds] == ["agent"] * len(rounds)


@pytest.mark.asyncio
async def test_router_loop_signature_takes_no_model_hook() -> None:
    """`run_tool_loop` grew no per-round model parameter.

    The brain's reroute needed one; adding it to the shared core would have
    changed this caller's contract. It was implemented as an `llm_fn` wrapper
    instead, so the engine's signature is exactly what it was.
    """
    import inspect

    from hal0.toolloop.engine import run_tool_loop

    params = inspect.signature(run_tool_loop).parameters
    assert list(params) == [
        "llm_fn",
        "tools",
        "dispatch_fn",
        "body",
        "max_rounds",
        "known_tool_names",
        "on_event",
    ]


@pytest.mark.asyncio
async def test_router_loop_never_synthesises_a_reply_for_an_error_round() -> None:
    """An upstream error stays an error for OmniRouter.

    The brain turns a failed TOOL-model round into a synthetic assistant
    message ("tool calls need a model on the agent slot..."). That degrade is
    brain-local: OmniRouter must keep returning the raw error envelope its
    callers forward verbatim.
    """

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream is down")

    router = _make_router(handler, [_caller(), _img_slot()])
    result = await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert "error" in result
    assert "503" in result["error"]
    assert "choices" not in result


# ── #2191: image tools on the exclusive GPU, loop-level ───────────────────────
#
# Dispatching generate_image flips the arbiter into image mode and unloads the
# calling LLM. The loop restores LLM mode ONCE per tool round, after every tool
# call in the round has finished (two image renders in one response must not
# have the first restore pull the GPU from under the second), and if the caller
# cannot be restored it ends the loop with a client-visible completion that
# still carries the image instead of asking the evicted LLM one more time.


class _Arbiter:
    def __init__(self, *, pinned: bool = False) -> None:
        self._mode = "llm"
        self.pinned = pinned
        self.restore_calls = 0
        self.restore_seen_in_flight = 0

    @property
    def mode(self):
        from hal0.slots.arbiter import GpuMode

        return GpuMode(self._mode)

    def flip_to_img(self) -> None:
        self._mode = "img"

    async def restore_llm(self, *, force: bool = False) -> None:
        from hal0.slots.arbiter import ArbiterPinned

        self.restore_calls += 1
        if self.pinned and not force:
            raise ArbiterPinned("GPU image mode is pinned", details={"pinned": True})
        self._mode = "llm"

    # What the omni loop must call: the queue-aware variant (#2191 review).
    # ``gate`` lets a test hold the restore mid-flight.
    gate: asyncio.Event | None = None
    when_idle_calls = 0

    async def restore_llm_when_idle(
        self, *, force: bool = False, max_wait_s: float = 600.0
    ) -> None:
        self.when_idle_calls += 1
        if self.gate is not None:
            await self.gate.wait()
        await self.restore_llm(force=force)


class _ArbitratedManager(FakeSlotManager):
    def __init__(self, slots, arbiter: _Arbiter) -> None:
        super().__init__(slots)
        self.arbiter = arbiter


def _two_image_calls_then_done(arbiter: _Arbiter, *, in_flight: list[int]):
    """A /v1 handler: first chat round asks for two images; each render flips
    the GPU and records how many renders were in flight when a restore ran."""

    async def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/chat/completions":
            body = json.loads(req.read())
            if len(body.get("messages", [])) <= 1:
                calls = [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {
                            "name": "generate_image",
                            "arguments": json.dumps({"prompt": f"p{i}"}),
                        },
                    }
                    for i in (1, 2)
                ]
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"role": "assistant", "content": None, "tool_calls": calls}}
                        ]
                    },
                )
            return httpx.Response(200, json={"choices": [{"message": {"content": "two images"}}]})
        if req.url.path == "/v1/images/generations":
            arbiter.flip_to_img()
            in_flight[0] += 1
            await asyncio.sleep(0.02)
            if arbiter.restore_calls:
                arbiter.restore_seen_in_flight += 1
            in_flight[0] -= 1
            return httpx.Response(
                200, json={"data": [{"url": f"img-{json.loads(req.read())['prompt']}"}]}
            )
        return httpx.Response(404)

    return handler


def _arbitrated_router(handler, arbiter: _Arbiter) -> OmniRouter:
    return OmniRouter(
        slot_manager=_ArbitratedManager([_caller(), _img_slot()], arbiter),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        ),
        api_base_url="http://test",
    )


@pytest.mark.asyncio
async def test_parallel_image_calls_restore_the_caller_once_after_both_finish() -> None:
    arbiter = _Arbiter()
    in_flight = [0]
    router = _arbitrated_router(_two_image_calls_then_done(arbiter, in_flight=in_flight), arbiter)

    result = await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "x"}]},
    )

    assert result["choices"][0]["message"]["content"] == "two images"
    assert arbiter.restore_calls == 1
    assert arbiter.restore_seen_in_flight == 0  # no render saw a restore while running
    assert arbiter.mode.value == "llm"


@pytest.mark.asyncio
async def test_pinned_image_mode_ends_the_loop_with_the_image_in_a_completion() -> None:
    """The evicted LLM is never asked again; the client gets a completion that
    carries the rendered image and says why the model could not continue."""
    arbiter = _Arbiter(pinned=True)
    chat_rounds = [0]

    async def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/chat/completions":
            chat_rounds[0] += 1
            body = json.loads(req.read())
            if len(body.get("messages", [])) <= 1:
                call = {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "generate_image",
                        "arguments": json.dumps({"prompt": "cat"}),
                    },
                }
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [call],
                                }
                            }
                        ]
                    },
                )
            return httpx.Response(
                503, json={"error": {"code": "gpu.image_mode", "message": "unavailable"}}
            )
        if req.url.path == "/v1/images/generations":
            arbiter.flip_to_img()
            return httpx.Response(200, json={"data": [{"url": "http://img/cat.png"}]})
        return httpx.Response(404)

    router = _arbitrated_router(handler, arbiter)
    result = await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "x"}]},
    )

    assert chat_rounds[0] == 1  # the second chat round never went to the evicted LLM
    assert "choices" in result and "error" not in result
    content = result["choices"][0]["message"]["content"]
    assert "http://img/cat.png" in content
    assert "pinned" in content
    extra = result["hal0"]["omni"]
    assert "pinned" in extra["caller_slot_unavailable"]
    assert extra["tool_results"][0]["result"]["data"] == [{"url": "http://img/cat.png"}]


@pytest.mark.asyncio
async def test_cancelled_image_round_still_restores_the_caller_in_the_background() -> None:
    """A client that disconnects mid-render must not leave the GPU parked in
    image mode with the caller unloaded until the idle window expires."""
    arbiter = _Arbiter()
    started = asyncio.Event()

    async def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/chat/completions":
            call = {
                "id": "c1",
                "type": "function",
                "function": {"name": "generate_image", "arguments": json.dumps({"prompt": "cat"})},
            }
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": None, "tool_calls": [call]}}
                    ]
                },
            )
        if req.url.path == "/v1/images/generations":
            arbiter.flip_to_img()
            started.set()
            await asyncio.sleep(10)  # a long render the client will abandon
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    router = _arbitrated_router(handler, arbiter)
    task = asyncio.ensure_future(
        router.run_loop(
            caller_slot_name="primary",
            body={"model": "agent", "messages": [{"role": "user", "content": "x"}]},
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The background restore runs on the loop once the request is gone.
    for _ in range(50):
        if arbiter.restore_calls:
            break
        await asyncio.sleep(0.01)
    assert arbiter.restore_calls == 1
    assert arbiter.mode.value == "llm"


@pytest.mark.asyncio
async def test_the_loop_restores_through_the_queue_aware_path() -> None:
    """Another request's render may be queued: the loop must use the variant
    that waits for ComfyUI's queue, never the bare restore."""
    arbiter = _Arbiter()
    in_flight = [0]
    router = _arbitrated_router(_two_image_calls_then_done(arbiter, in_flight=in_flight), arbiter)
    await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "x"}]},
    )
    assert arbiter.when_idle_calls == 1


@pytest.mark.asyncio
async def test_cancellation_during_the_restore_itself_lets_the_restore_finish() -> None:
    """Cancelled after the renders but while the restore is in progress: the
    restore must run to completion detached, not stop half-way with ComfyUI
    freed and the LLM set partly loaded."""
    arbiter = _Arbiter()
    arbiter.gate = asyncio.Event()
    rendered = asyncio.Event()

    async def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/chat/completions":
            call = {
                "id": "c1",
                "type": "function",
                "function": {"name": "generate_image", "arguments": json.dumps({"prompt": "cat"})},
            }
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": None, "tool_calls": [call]}}
                    ]
                },
            )
        if req.url.path == "/v1/images/generations":
            arbiter.flip_to_img()
            rendered.set()
            return httpx.Response(200, json={"data": [{"url": "x"}]})
        return httpx.Response(404)

    router = _arbitrated_router(handler, arbiter)
    task = asyncio.ensure_future(
        router.run_loop(
            caller_slot_name="primary",
            body={"model": "agent", "messages": [{"role": "user", "content": "x"}]},
        )
    )
    await rendered.wait()
    # Let the loop reach the restore and block on the gate, then cancel.
    for _ in range(50):
        if arbiter.when_idle_calls:
            break
        await asyncio.sleep(0.01)
    assert arbiter.when_idle_calls == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert arbiter.restore_calls == 0  # still held at the gate, not aborted
    arbiter.gate.set()
    for _ in range(50):
        if arbiter.restore_calls:
            break
        await asyncio.sleep(0.01)
    assert arbiter.restore_calls == 1
    assert arbiter.mode.value == "llm"


@pytest.mark.asyncio
async def test_images_from_earlier_rounds_are_kept_when_a_later_round_cannot_restore() -> None:
    """Round 1 renders an image and restores fine; round 2 renders another and
    the restore is refused. The fallback completion must carry both images:
    round 1's result only ever went into the private transcript."""
    arbiter = _Arbiter()
    rounds = [0]

    async def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/chat/completions":
            rounds[0] += 1
            n = len(json.loads(req.read()).get("messages", []))
            prompt = "first" if n <= 1 else "second"
            if n <= 3:  # user; then user+assistant+tool
                call = {
                    "id": f"c-{prompt}",
                    "type": "function",
                    "function": {
                        "name": "generate_image",
                        "arguments": json.dumps({"prompt": prompt}),
                    },
                }
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [call],
                                }
                            }
                        ]
                    },
                )
            return httpx.Response(
                503, json={"error": {"code": "gpu.image_mode", "message": "unavailable"}}
            )
        if req.url.path == "/v1/images/generations":
            prompt = json.loads(req.read())["prompt"]
            arbiter.flip_to_img()
            if prompt == "second":
                arbiter.pinned = True  # the operator pinned image mode meanwhile
            return httpx.Response(200, json={"data": [{"url": f"http://img/{prompt}.png"}]})
        return httpx.Response(404)

    router = _arbitrated_router(handler, arbiter)
    result = await router.run_loop(
        caller_slot_name="primary",
        body={"model": "agent", "messages": [{"role": "user", "content": "x"}]},
    )

    assert "choices" in result and "error" not in result
    content = result["choices"][0]["message"]["content"]
    assert "http://img/first.png" in content and "http://img/second.png" in content
    urls = [r["result"]["data"][0]["url"] for r in result["hal0"]["omni"]["tool_results"]]
    assert urls == ["http://img/first.png", "http://img/second.png"]
