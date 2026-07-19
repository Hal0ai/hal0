"""Provider-agnostic OpenAI tool-calling loop — the ONE core.

Both ``hal0.api.routes.board_chat`` (the hal0-brain sidebar chat, SSE
streamed) and ``hal0.omni_router.router.OmniRouter.run_loop`` (the
non-streaming ``/v1/chat/completions`` re-entrant loop) drive the same
machine: send a completion request with ``tools=[...]``, extract any
``tool_calls`` from the response (falling back to text-embedded calls
for models that leak them as content), dispatch each one, fold the
result back as a ``role: tool`` message, and repeat until the model
stops calling tools or a round budget is exhausted. This module is that
machine, decoupled from both callers' transport concerns:

  * :func:`run_tool_loop` — the loop itself. An **async generator**
    that yields structured event dicts (``thinking`` / ``token`` /
    ``response`` / ``error`` / ``done``, plus whatever ``dispatch_fn``
    itself yields for a round's tool calls). Driving it (``async for``)
    is what advances the loop — nothing runs until iterated.
  * :func:`openai_tool_schema` — the ``{"type": "function", ...}``
    wire-shape builder, shared by board_chat's ``_fn()`` and
    :meth:`hal0.omni_router.tools.ToolDefinition.to_openai_tool`.
  * :func:`extract_tool_calls`, :func:`assistant_message`,
    :func:`assistant_text`, :func:`assistant_thinking`,
    :func:`split_thinking`, :func:`parse_text_tool_calls`,
    :func:`build_tool_message` — the stateless helpers the loop is
    built from; several are unit-tested directly (imported by name)
    from ``board_chat`` and re-exported there unchanged.

Design note — why an async generator, and why ``dispatch_fn`` yields
too: board_chat's SSE surface needs TRUE incremental streaming (a
gated tool call pauses the turn for up to five minutes, pinging the
client every 15 s while it polls the ApprovalQueue) — a plain
callback invoked from a nested coroutine cannot reach back into an
*enclosing* generator's ``yield``; only real generator delegation
(``async for ev in dispatch_fn(...): yield ev``) preserves the
cooperative suspend/resume that lets a test approve/deny an operation
*while the turn is paused* (see
``tests/board/test_board_chat_tool_use_e2e.py``). ``dispatch_fn`` is
therefore an async generator too: given a round's tool calls, it may
yield any number of caller-shaped events, and — for each ``id`` — the
LAST event of type ``tool_result`` it yields for that id is the
authoritative result folded into the ``role: tool`` message. An event
carrying ``_engine_only: True`` updates that authoritative result
WITHOUT being forwarded to the loop's own consumer — this is what
lets board_chat silently attach a "still pending" hint to a
timed-out approval without emitting a second SSE frame (matching the
pre-refactor behavor exactly).

The ``response`` event is an internal marker (never part of
board_chat's documented SSE contract — callers that stream it
verbatim must filter it out) carrying the raw completion dict for
callers, like OmniRouter, that just want the final response returned
rather than streamed.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

# ── OpenAI wire-shape helpers ─────────────────────────────────────────────


def openai_tool_schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Render one tool as the OpenAI ``tools=[...]`` wire shape."""
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def build_tool_message(tool_call_id: str, name: str, result: Any) -> dict[str, Any]:
    """The ``role: tool`` message folding a dispatch result back to the LLM."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": json.dumps(result),
    }


# ── response parsing ─────────────────────────────────────────────────────


def extract_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull + normalise ``tool_calls`` (arguments -> dict) from a completion.

    Returns the flat shape ``{"id", "name", "arguments"}`` — ``arguments``
    is always a dict regardless of whether the backend shipped it as a
    JSON-encoded string (the OpenAI wire convention) or a native dict.
    """
    choices = response.get("choices") or []
    if not choices:
        return []
    msg = choices[0].get("message") or {}
    out: list[dict[str, Any]] = []
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
                if not isinstance(args, dict):
                    args = {}
            except ValueError:
                args = {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}
        out.append({"id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": args})
    return out


def assistant_message(response: dict[str, Any]) -> dict[str, Any] | None:
    """The raw assistant turn message (for replay into the next round)."""
    choices = response.get("choices") or []
    if not choices:
        return None
    msg = choices[0].get("message")
    return msg if isinstance(msg, dict) else None


def assistant_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    return content if isinstance(content, str) else ""


def assistant_thinking(response: dict[str, Any]) -> str:
    """Pull explicit reasoning fields off the assistant message."""
    choices = response.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    for key in ("reasoning_content", "reasoning"):
        value = msg.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# Reasoning models interleave chain-of-thought with the reply, either as a
# separate message field (DeepSeek-style ``reasoning_content``) or inline
# ``<think>...</think>`` tags (Qwen-style). Both are split out here so a
# streaming caller can fold them into a "thinking" frame instead of
# rendering raw think-tags in the chat bubble.
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def split_thinking(content: str) -> tuple[str, str]:
    """Split ``<think>`` blocks out of assistant content -> (thinking, visible)."""
    if "<think>" not in content:
        # DeepSeek-R1-style chat templates prefill the opening tag, so the
        # completion can start mid-reasoning and carry only a closing tag.
        if "</think>" in content:
            reasoning, _, visible = content.partition("</think>")
            return reasoning.strip(), visible.strip()
        return "", content
    thinking_parts = _THINK_RE.findall(content)
    visible = _THINK_RE.sub("", content)
    rest = visible.split("<think>", 1)
    if len(rest) == 2:  # unterminated trailing <think> — all of it is reasoning
        visible = rest[0]
        thinking_parts.append(rest[1])
    return "\n".join(p.strip() for p in thinking_parts if p.strip()), visible.strip()


# ── text-embedded tool-call fallback ──────────────────────────────────────
#
# Small / non-native models — e.g. a chat slot whose llama-server was started
# WITHOUT ``--jinja``, so llama.cpp never applies the tool grammar — emit tool
# calls as TEXT instead of the OpenAI-native ``message.tool_calls`` field. The
# call then leaks into the chat bubble (``<tool_call>{"name": "get_board"...}``,
# a fenced JSON block, a bare ``<function=...>`` tag, or a whole-content JSON
# object) and never runs. As a fallback we scan the assistant text for those
# conventions and — ONLY when the extracted name is a real surfaced tool, so
# ordinary prose can never misfire — synthesise the call and strip its syntax
# from the visible reply.
_TEXT_TOOLCALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
_FUNCTION_TAG_RE = re.compile(
    r"<function=([A-Za-z0-9_.\-]+)>\s*(\{.*?\})?\s*</function>", re.DOTALL | re.IGNORECASE
)
# Attribute form emitted by the fpx8-agent steward / minicpm5 / hermes tool-use
# templates: ``<function name="NAME"> ... </function>``. The body is EITHER a
# JSON object OR nested ``<parameter name="k">v</parameter>`` tags (the antml
# convention). ``name`` may be single- or double-quoted.
_FUNCTION_ATTR_RE = re.compile(
    r"""<function\s+name\s*=\s*["']([A-Za-z0-9_.\-]+)["']\s*>(.*?)</function>""",
    re.DOTALL | re.IGNORECASE,
)
_PARAMETER_RE = re.compile(
    r"""<parameter\s+name\s*=\s*["']([A-Za-z0-9_.\-]+)["']\s*>(.*?)</parameter>""",
    re.DOTALL | re.IGNORECASE,
)
# The antml/hermes wrapper that brackets one or more attribute-form calls.
_FUNCTION_CALLS_WRAP_RE = re.compile(
    r"<function_calls>\s*(.*?)\s*</function_calls>", re.DOTALL | re.IGNORECASE
)
_FENCED_JSON_RE = re.compile(r"```(?:json|tool_call)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _parse_attr_body(body: str) -> dict[str, Any]:
    """Arguments from an attribute-form ``<function name=...>`` body.

    Handles both shapes the tool-use templates emit: nested
    ``<parameter name="k">v</parameter>`` tags (each value JSON-decoded when it
    parses, else kept as the raw string), or a plain JSON object body.
    """
    params = _PARAMETER_RE.findall(body)
    if params:
        args: dict[str, Any] = {}
        for key, raw in params:
            val = raw.strip()
            try:
                args[key] = json.loads(val)
            except ValueError:
                args[key] = val
        return args
    if body.strip().startswith("{"):
        return _coerce_args(body.strip())
    return {}


def _coerce_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _toolcall_from_obj(obj: Any) -> tuple[str, dict[str, Any]] | None:
    """Pull ``(name, arguments)`` from a parsed dict in the common shapes."""
    if not isinstance(obj, dict):
        return None
    fn = obj.get("function") if isinstance(obj.get("function"), dict) else {}
    name = obj.get("name") or fn.get("name")
    if not isinstance(name, str) or not name:
        return None
    for key in ("arguments", "parameters", "args"):
        if obj.get(key) is not None:
            return name, _coerce_args(obj[key])
    if fn.get("arguments") is not None:
        return name, _coerce_args(fn["arguments"])
    return name, {}


def parse_text_tool_calls(
    text: str, known_names: frozenset[str]
) -> tuple[list[dict[str, Any]], str]:
    """Best-effort extraction of text-embedded tool calls.

    Returns ``(calls, cleaned_text)``. Only calls whose name is in
    ``known_names`` are accepted; matched spans are removed from the
    returned text so the raw tool syntax is never shown to the operator.
    An empty ``known_names`` disables the fallback entirely (a caller
    that doesn't offer text-fallback-eligible tools, e.g. OmniRouter,
    passes ``frozenset()`` and gets exactly the pre-refactor no-op).
    """
    if not text or not known_names:
        return [], text
    calls: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []

    def _accept(name: str, args: dict[str, Any], span: tuple[int, int]) -> None:
        if name in known_names:
            calls.append({"id": f"txt-{len(calls)}-{name}", "name": name, "arguments": args})
            spans.append(span)

    # 1) <tool_call>...</tool_call> — JSON body, nested <function=...> tag, or
    #    the attribute-form <function name="..."> tag.
    for m in _TEXT_TOOLCALL_RE.finditer(text):
        body = m.group(1).strip()
        fn = _FUNCTION_TAG_RE.search(body)
        if fn:
            _accept(fn.group(1), _coerce_args(fn.group(2) or "{}"), m.span())
            continue
        attr = _FUNCTION_ATTR_RE.search(body)
        if attr:
            _accept(attr.group(1), _parse_attr_body(attr.group(2)), m.span())
            continue
        try:
            parsed = _toolcall_from_obj(json.loads(body))
        except ValueError:
            parsed = None
        if parsed:
            _accept(parsed[0], parsed[1], m.span())

    # 2) <function_calls>…</function_calls> wrapper (antml/hermes) holding one or
    #    more attribute-form <function name="NAME"> tags — the whole wrapper is a
    #    single span so its bracket tags never leak into the visible reply.
    for m in _FUNCTION_CALLS_WRAP_RE.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue
        before = len(calls)
        for fm in _FUNCTION_ATTR_RE.finditer(m.group(1)):
            name = fm.group(1)
            if name in known_names:
                calls.append(
                    {
                        "id": f"txt-{len(calls)}-{name}",
                        "name": name,
                        "arguments": _parse_attr_body(fm.group(2)),
                    }
                )
        if len(calls) > before:
            spans.append(m.span())

    # 3) bare attribute-form <function name="NAME">…</function> outside a wrapper.
    for m in _FUNCTION_ATTR_RE.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue
        _accept(m.group(1), _parse_attr_body(m.group(2)), m.span())

    # 4) bare <function=NAME>{...}</function> outside a wrapper.
    for m in _FUNCTION_TAG_RE.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue
        _accept(m.group(1), _coerce_args(m.group(2) or "{}"), m.span())

    # 5) fenced ```json {...}``` blocks.
    for m in _FENCED_JSON_RE.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue
        try:
            parsed = _toolcall_from_obj(json.loads(m.group(1)))
        except ValueError:
            parsed = None
        if parsed:
            _accept(parsed[0], parsed[1], m.span())

    # 6) the whole trimmed reply is a single JSON tool-call object.
    if not calls:
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = _toolcall_from_obj(json.loads(stripped))
            except ValueError:
                parsed = None
            if parsed:
                start = text.index(stripped)
                _accept(parsed[0], parsed[1], (start, start + len(stripped)))

    if not calls:
        return [], text
    cleaned = text
    for s, e in sorted(spans, reverse=True):
        cleaned = cleaned[:s] + cleaned[e:]
    return calls, cleaned.strip()


# ── the loop ───────────────────────────────────────────────────────────────

#: An OpenAI chat-completion request body in, the parsed response dict out.
LlmFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

#: Given the current round's tool calls (``[{"id", "name", "arguments"}, ...]``),
#: an async generator yielding event dicts. For each tool call's ``id``, the
#: LAST yielded event of type ``tool_result`` is the authoritative result
#: folded into the ``role: tool`` message; mark an event ``_engine_only:
#: True`` to update that result WITHOUT forwarding the event to
#: :func:`run_tool_loop`'s own consumer.
DispatchFn = Callable[[list[dict[str, Any]]], AsyncIterator[dict[str, Any]]]

#: Optional hook invoked (and awaited, if it returns an awaitable) for every
#: event the loop produces — a caller that only wants incidental visibility
#: (e.g. a debug log) without driving its own SSE sink can use this instead
#: of inspecting every item from the generator.
OnEvent = Callable[[dict[str, Any]], Any]


async def _notify(on_event: OnEvent | None, event: dict[str, Any]) -> None:
    if on_event is None:
        return
    result = on_event(event)
    if hasattr(result, "__await__"):
        await result


async def run_tool_loop(
    llm_fn: LlmFn,
    tools: list[dict[str, Any]],
    dispatch_fn: DispatchFn,
    *,
    body: dict[str, Any],
    max_rounds: int,
    known_tool_names: frozenset[str] = frozenset(),
    on_event: OnEvent | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Drive the tool-calling loop against ``llm_fn``; yield structured events.

    ``body`` is the caller-owned request dict (already carrying ``model`` and
    a ``messages`` list); this function sets ``body["tools"]`` and
    ``body["stream"] = False`` and appends to ``body["messages"]`` in place
    as rounds progress — the same list a caller may already be holding a
    reference to.

    Event shapes yielded (all but ``response`` mirror board_chat's
    documented SSE contract 1:1):

      ``{"type": "response", "data": <raw completion dict>}``
        Internal marker, one per round, BEFORE any error/tool-call
        handling — lets a non-streaming caller (OmniRouter) recover the
        final completion without inspecting every event type. Streaming
        callers must filter this out before forwarding events downstream.
      ``{"type": "thinking", "text": ...}``
      ``{"type": "token", "text": ...}``
      ``{"type": "error", "message": ...}``
      ``{"type": "done"}``
      plus whatever ``dispatch_fn`` yields for the round's tool calls
      (``tool_call`` / ``tool_result`` / caller-defined events).
    """
    messages: list[dict[str, Any]] = body["messages"]
    body["tools"] = tools
    body["stream"] = False

    for _round in range(max_rounds):
        response = await llm_fn(body)
        await _notify(on_event, {"type": "response", "data": response})
        yield {"type": "response", "data": response}

        if isinstance(response, dict) and response.get("error"):
            err_event = {"type": "error", "message": str(response["error"])}
            done_event = {"type": "done"}
            await _notify(on_event, err_event)
            yield err_event
            await _notify(on_event, done_event)
            yield done_event
            return

        explicit_thinking = assistant_thinking(response)
        inline_thinking, text = split_thinking(assistant_text(response))

        # Native tool_calls first; fall back to text-embedded calls (a slot
        # without --jinja leaks them as content). The fallback strips the
        # matched syntax from `text` so the raw call isn't shown as a token.
        tool_calls = extract_tool_calls(response)
        if not tool_calls:
            tool_calls, text = parse_text_tool_calls(text, known_tool_names)

        thinking = "\n".join(t for t in (explicit_thinking, inline_thinking) if t)
        if thinking:
            thinking_event = {"type": "thinking", "text": thinking}
            await _notify(on_event, thinking_event)
            yield thinking_event
        if text:
            token_event = {"type": "token", "text": text}
            await _notify(on_event, token_event)
            yield token_event

        if not tool_calls:
            done_event = {"type": "done"}
            await _notify(on_event, done_event)
            yield done_event
            return

        assistant_msg = assistant_message(response)
        if assistant_msg is not None:
            messages.append(assistant_msg)

        tool_results: dict[str, Any] = {}
        async for ev in dispatch_fn(tool_calls):
            engine_only = ev.pop("_engine_only", False)
            if ev.get("type") == "tool_result" and ev.get("id") is not None:
                tool_results[ev["id"]] = ev.get("result")
            if not engine_only:
                await _notify(on_event, ev)
                yield ev

        for tc in tool_calls:
            result = tool_results.get(tc["id"], {"error": "no dispatch result"})
            messages.append(build_tool_message(tc["id"], tc["name"], result))

        body["messages"] = messages

    budget_event = {"type": "error", "message": "tool loop budget exhausted"}
    done_event = {"type": "done"}
    await _notify(on_event, budget_event)
    yield budget_event
    await _notify(on_event, done_event)
    yield done_event


__all__ = [
    "DispatchFn",
    "LlmFn",
    "OnEvent",
    "assistant_message",
    "assistant_text",
    "assistant_thinking",
    "build_tool_message",
    "extract_tool_calls",
    "openai_tool_schema",
    "parse_text_tool_calls",
    "run_tool_loop",
    "split_thinking",
]
