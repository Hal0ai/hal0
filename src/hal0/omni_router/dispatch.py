"""OmniRouter tool dispatch handlers — plan §7.

Each of the eight tools has a coroutine in this module that:

  1. Validates the tool's argument dict (defensive — Lemonade may have
     parsed the LLM's tool_call already, but a malformed shape would
     crash the loop and abandon the user, so the safer path is to
     return a structured ``{"error": ...}`` tool_result and let the
     LLM apologise).
  2. Uses the injected ``SlotManagerLike`` to pick the target slot
     name via ``route_for_request`` (matching what
     :mod:`hal0.omni_router.filter` decided was eligible).
  3. Calls the appropriate Lemonade ``/v1/*`` endpoint with the
     target slot's model and the tool's arguments.
  4. Returns a JSON-serialisable dict — the OmniRouter loop encodes it
     into the tool_result message envelope.

The handlers do NOT raise on dispatch errors — they return
``{"error": "<message>"}`` so the LLM loop continues. Transport
failures (httpx errors) are caught and surfaced the same way.

Endpoints (per plan §7.2):

  ============================== =================================
  Tool                           Endpoint
  ============================== =================================
  ``generate_image``             ``POST /v1/images/generations``
  ``edit_image``                 ``POST /v1/images/edits``
  ``text_to_speech``             ``POST /v1/audio/speech``
  ``transcribe_audio``           ``POST /v1/audio/transcriptions``
  ``analyze_image``              ``POST /v1/chat/completions``
  ``embed_text``                 ``POST /v1/embeddings``
  ``rerank_documents``           ``POST /v1/rerank``
  ``route_to_chat``              internal — see route_to_chat.py
  ============================== =================================

The base URL is Lemonade's loopback URL (``http://127.0.0.1:13305``
by default per ADR-0008 §1). PR-19 introduces direct-to-FLM-child
routing for ``stt-npu``/``embed-npu`` slots; PR-16 sticks to the
single-URL contract.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx

from hal0.omni_router.filter import SlotManagerLike
from hal0.omni_router.route_to_chat import (
    DELEGATION_DEPTH,
    build_delegation_messages,
    validate_delegation,
)
from hal0.omni_router.tools import ToolDefinition, tools_by_name

# Type alias for the chat-completion callback the OmniRouter loop
# injects so route_to_chat doesn't re-implement /v1/chat/completions.
ChatCompletionFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

log = logging.getLogger(__name__)

# Per-request timeout. Image gen + audio synthesis are slow; default
# generously here — the chat-completion loop's outer deadline is set
# at the router layer.
_DEFAULT_TOOL_TIMEOUT_S = 120.0


class DispatchContext:
    """Carrier for the per-loop dependencies a handler needs.

    Bundled into one object so individual handler signatures stay
    short. The instance is shared across a single chat-completion
    loop; tools see the same SlotManager + httpx client + Lemonade
    URL throughout.
    """

    def __init__(
        self,
        *,
        slot_manager: SlotManagerLike,
        http_client: httpx.AsyncClient,
        lemonade_base_url: str,
        caller_slot_name: str,
        chat_completion: ChatCompletionFn | None = None,
    ) -> None:
        self.slot_manager = slot_manager
        self.http_client = http_client
        self.lemonade_base_url = lemonade_base_url.rstrip("/")
        self.caller_slot_name = caller_slot_name
        # ``chat_completion`` is the callback the OmniRouter loop
        # provides so route_to_chat doesn't need to re-implement the
        # full /v1/chat/completions plumbing; it just hands a body
        # dict back and the loop's own client does the round-trip.
        self.chat_completion = chat_completion


# ── argument validation helpers ─────────────────────────────────────


def _missing(args: Mapping[str, Any], *required: str) -> str | None:
    """Return an error message if any required key is missing/empty,
    else ``None``."""
    for key in required:
        val = args.get(key)
        if val is None:
            return f"missing required argument '{key}'"
        if isinstance(val, str) and not val.strip():
            return f"argument '{key}' must not be empty"
        if isinstance(val, list) and len(val) == 0:
            return f"argument '{key}' must not be empty"
    return None


async def _route_or_error(
    ctx: DispatchContext,
    tool: ToolDefinition,
) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve the target slot name + its config for ``tool``.

    Returns ``(slot_name, slot_cfg)`` on success; ``(None, error_dict)``
    on routing failure (no eligible slot). The error dict is shaped
    as a tool_result body so callers can return it directly.
    """
    target = await ctx.slot_manager.route_for_request(
        tool.target_slot_type,
        required_labels=tool.required_model_labels,
    )
    if target is None:
        return None, {
            "error": (
                f"no enabled slot of type '{tool.target_slot_type}' "
                f"with required labels {list(tool.required_model_labels)!r}"
            )
        }
    configs = await ctx.slot_manager.iter_configs()
    cfg = next((c for c in configs if c.get("name") == target), None)
    if cfg is None:
        # Race: slot config disappeared between routing + lookup.
        return None, {"error": f"slot '{target}' vanished mid-dispatch"}
    return target, cfg


def _model_id_of(cfg: dict[str, Any]) -> str:
    model = cfg.get("model") or {}
    if isinstance(model, dict):
        default = model.get("default", "")
        if isinstance(default, str) and default:
            return default
    return str(cfg.get("name", ""))


async def _post_json(
    ctx: DispatchContext,
    path: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """POST JSON to a Lemonade endpoint; return parsed body or an
    ``{"error": ...}`` envelope. Never raises."""
    url = f"{ctx.lemonade_base_url}{path}"
    try:
        resp = await ctx.http_client.post(url, json=body, timeout=_DEFAULT_TOOL_TIMEOUT_S)
    except httpx.TimeoutException:
        return {"error": f"timeout calling {path}"}
    except (httpx.ConnectError, httpx.NetworkError, httpx.HTTPError) as exc:
        return {"error": f"transport failure calling {path}: {exc}"}
    if not (200 <= resp.status_code < 300):
        body_text = resp.text[:500] if resp.text else ""
        return {
            "error": f"upstream returned HTTP {resp.status_code} for {path}: {body_text}",
        }
    try:
        return resp.json()
    except ValueError:
        # Non-JSON body (e.g. audio bytes from /v1/audio/speech) —
        # return a metadata envelope; full binary is too big for a
        # tool_result and would derail the LLM context window.
        return {
            "content_type": resp.headers.get("content-type", ""),
            "byte_length": len(resp.content),
            "note": (
                "Binary payload returned by upstream. hal0 surfaces only "
                "metadata in the tool_result to keep context windows "
                "tractable; the dashboard renders the binary separately "
                "via the same endpoint."
            ),
        }


# ── handlers ────────────────────────────────────────────────────────


async def handle_generate_image(ctx: DispatchContext, args: Mapping[str, Any]) -> dict[str, Any]:
    err = _missing(args, "prompt")
    if err is not None:
        return {"error": err}
    target, cfg_or_err = await _route_or_error(ctx, tools_by_name()["generate_image"])
    if target is None:
        return cfg_or_err or {"error": "no image slot"}
    body: dict[str, Any] = {
        "model": _model_id_of(cfg_or_err or {}),
        "prompt": args["prompt"],
    }
    if args.get("size"):
        body["size"] = args["size"]
    if "n" in args and args["n"] is not None:
        body["n"] = args["n"]
    return await _post_json(ctx, "/v1/images/generations", body)


async def handle_edit_image(ctx: DispatchContext, args: Mapping[str, Any]) -> dict[str, Any]:
    err = _missing(args, "image", "prompt")
    if err is not None:
        return {"error": err}
    target, cfg_or_err = await _route_or_error(ctx, tools_by_name()["edit_image"])
    if target is None:
        return cfg_or_err or {"error": "no image-edit slot"}
    body: dict[str, Any] = {
        "model": _model_id_of(cfg_or_err or {}),
        "image": args["image"],
        "prompt": args["prompt"],
    }
    if args.get("size"):
        body["size"] = args["size"]
    return await _post_json(ctx, "/v1/images/edits", body)


async def handle_text_to_speech(ctx: DispatchContext, args: Mapping[str, Any]) -> dict[str, Any]:
    err = _missing(args, "input")
    if err is not None:
        return {"error": err}
    target, cfg_or_err = await _route_or_error(ctx, tools_by_name()["text_to_speech"])
    if target is None:
        return cfg_or_err or {"error": "no tts slot"}
    body: dict[str, Any] = {
        "model": _model_id_of(cfg_or_err or {}),
        "input": args["input"],
    }
    if args.get("voice"):
        body["voice"] = args["voice"]
    return await _post_json(ctx, "/v1/audio/speech", body)


async def handle_transcribe_audio(ctx: DispatchContext, args: Mapping[str, Any]) -> dict[str, Any]:
    err = _missing(args, "audio")
    if err is not None:
        return {"error": err}
    target, cfg_or_err = await _route_or_error(ctx, tools_by_name()["transcribe_audio"])
    if target is None:
        return cfg_or_err or {"error": "no transcription slot"}
    body: dict[str, Any] = {
        "model": _model_id_of(cfg_or_err or {}),
        # Lemonade's /v1/audio/transcriptions is a multipart endpoint
        # in the OpenAI contract; tool-call args come in as a JSON
        # blob from the LLM, so PR-16 wraps that as a single-field
        # JSON body and lets Lemonade's compatibility layer convert.
        # Real binary uploads still go through the dashboard's direct
        # /v1/audio/transcriptions route (PR-14 voice slot).
        "file": args["audio"],
    }
    if args.get("language"):
        body["language"] = args["language"]
    return await _post_json(ctx, "/v1/audio/transcriptions", body)


async def handle_analyze_image(ctx: DispatchContext, args: Mapping[str, Any]) -> dict[str, Any]:
    err = _missing(args, "image", "question")
    if err is not None:
        return {"error": err}
    target, cfg_or_err = await _route_or_error(ctx, tools_by_name()["analyze_image"])
    if target is None:
        return cfg_or_err or {"error": "no vision-capable llm slot"}
    # Vision goes through /v1/chat/completions with an image-URL/data
    # content part in the user message — OpenAI's standard shape.
    body: dict[str, Any] = {
        "model": _model_id_of(cfg_or_err or {}),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": args["question"]},
                    {
                        "type": "image_url",
                        "image_url": {"url": args["image"]},
                    },
                ],
            }
        ],
    }
    return await _post_json(ctx, "/v1/chat/completions", body)


async def handle_embed_text(ctx: DispatchContext, args: Mapping[str, Any]) -> dict[str, Any]:
    err = _missing(args, "input")
    if err is not None:
        return {"error": err}
    target, cfg_or_err = await _route_or_error(ctx, tools_by_name()["embed_text"])
    if target is None:
        return cfg_or_err or {"error": "no embedding slot"}
    body: dict[str, Any] = {
        "model": _model_id_of(cfg_or_err or {}),
        "input": args["input"],
    }
    return await _post_json(ctx, "/v1/embeddings", body)


async def handle_rerank_documents(ctx: DispatchContext, args: Mapping[str, Any]) -> dict[str, Any]:
    err = _missing(args, "query", "documents")
    if err is not None:
        return {"error": err}
    target, cfg_or_err = await _route_or_error(ctx, tools_by_name()["rerank_documents"])
    if target is None:
        return cfg_or_err or {"error": "no reranking slot"}
    body: dict[str, Any] = {
        "model": _model_id_of(cfg_or_err or {}),
        "query": args["query"],
        "documents": args["documents"],
    }
    if "top_n" in args and args["top_n"] is not None:
        body["top_n"] = args["top_n"]
    return await _post_json(ctx, "/v1/rerank", body)


async def handle_route_to_chat(ctx: DispatchContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Special-case dispatcher — see :mod:`hal0.omni_router.route_to_chat`.

    Validates guardrails synchronously, increments the depth contextvar
    for the duration of the delegated call, builds the messages array,
    and hands the body to the loop's injected chat_completion
    callback so we don't re-implement /v1/chat/completions plumbing.
    """
    err = _missing(args, "target", "prompt")
    if err is not None:
        return {"error": err}
    if ctx.chat_completion is None:
        return {"error": "route_to_chat has no chat_completion callback configured"}

    configs = await ctx.slot_manager.iter_configs()
    current_depth = DELEGATION_DEPTH.get()
    rejection = validate_delegation(
        configs,
        caller_slot_name=ctx.caller_slot_name,
        target=str(args["target"]),
        current_depth=current_depth,
    )
    if rejection is not None:
        return {"error": rejection}

    target_cfg = next((c for c in configs if c.get("name") == args["target"]), None)
    assert target_cfg is not None  # validate_delegation guaranteed this

    messages = build_delegation_messages(
        target_cfg,
        prompt=str(args["prompt"]),
        context=str(args["context"]) if args.get("context") else None,
    )
    body = {
        "model": _model_id_of(target_cfg),
        "messages": messages,
    }
    token = DELEGATION_DEPTH.set(current_depth + 1)
    try:
        response = await ctx.chat_completion(body)
    finally:
        DELEGATION_DEPTH.reset(token)

    # Extract the assistant content from the standard OpenAI response.
    try:
        choices = response.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                return {"content": content}
    except (AttributeError, IndexError, TypeError):
        pass
    # Pass through whatever we got — the LLM sees the raw shape and
    # decides how to apologise.
    return {"response": response}


# ── public registry ─────────────────────────────────────────────────


HANDLERS: dict[str, Callable[[DispatchContext, Mapping[str, Any]], Awaitable[dict[str, Any]]]] = {
    "generate_image": handle_generate_image,
    "edit_image": handle_edit_image,
    "text_to_speech": handle_text_to_speech,
    "transcribe_audio": handle_transcribe_audio,
    "analyze_image": handle_analyze_image,
    "embed_text": handle_embed_text,
    "rerank_documents": handle_rerank_documents,
    "route_to_chat": handle_route_to_chat,
}


async def dispatch_tool(
    ctx: DispatchContext, tool_name: str, args: Mapping[str, Any]
) -> dict[str, Any]:
    """Look up a tool's handler and run it; return the tool_result body.

    Returns ``{"error": "unknown tool '...'"}`` on a tool name the
    handler table doesn't know — the LLM can apologise. We don't
    raise so an unexpected tool_call never crashes the loop.
    """
    handler = HANDLERS.get(tool_name)
    if handler is None:
        return {"error": f"unknown tool '{tool_name}'"}
    try:
        return await handler(ctx, args)
    except Exception as exc:  # never crash the loop
        log.exception("omni_router.dispatch_failed", extra={"tool": tool_name})
        return {"error": f"dispatch failed: {type(exc).__name__}: {exc}"}


__all__ = [
    "HANDLERS",
    "ChatCompletionFn",
    "DispatchContext",
    "dispatch_tool",
    "handle_analyze_image",
    "handle_edit_image",
    "handle_embed_text",
    "handle_generate_image",
    "handle_rerank_documents",
    "handle_route_to_chat",
    "handle_text_to_speech",
    "handle_transcribe_audio",
]
