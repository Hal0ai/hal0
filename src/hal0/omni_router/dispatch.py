"""OmniRouter tool dispatch handlers — plan §7.

Each of the eight tools has a coroutine in this module that:

  1. Validates the tool's argument dict (defensive — the model may have
     parsed the LLM's tool_call already, but a malformed shape would
     crash the loop and abandon the user, so the safer path is to
     return a structured ``{"error": ...}`` tool_result and let the
     LLM apologise).
  2. Uses the injected ``SlotManagerLike`` to pick the typed target slot
     via ``resolve_for_request`` (matching what
     :mod:`hal0.omni_router.filter` decided was eligible).
  3. Calls the appropriate hal0 ``/v1/*`` endpoint with the
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

The base URL is hal0-api's own loopback URL (``http://127.0.0.1:8080``
by default). PR-19 introduces direct-to-FLM-child
routing for ``flm-stt``/``flm-embed`` slots; PR-16 sticks to the
single-URL contract.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx

from hal0.omni_router.filter import SlotManagerLike
from hal0.omni_router.route_to_chat import (
    DELEGATION_DEPTH,
    build_delegation_messages_for_slot,
    validate_delegation_slots,
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
    loop; tools see the same SlotManager + httpx client + API
    URL throughout.
    """

    def __init__(
        self,
        *,
        slot_manager: SlotManagerLike,
        http_client: httpx.AsyncClient,
        api_base_url: str,
        caller_slot_name: str,
        chat_completion: ChatCompletionFn | None = None,
    ) -> None:
        self.slot_manager = slot_manager
        self.http_client = http_client
        self.api_base_url = api_base_url.rstrip("/")
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
) -> tuple[Any | None, dict[str, Any] | None]:
    """Resolve the target loaded slot for ``tool``.

    Returns ``(loaded_slot, None)`` on success; ``(None, error_dict)`` on
    routing failure (no eligible slot). The error dict is shaped as a
    tool_result body so callers can return it directly.
    """
    target = await ctx.slot_manager.resolve_for_request(
        tool.target_slot_type,
        required_labels=tool.required_model_labels,
    )
    if target is None:
        return None, {
            "error": (
                f"no configured slot of type '{tool.target_slot_type}' "
                f"with required labels {list(tool.required_model_labels)!r}"
            )
        }
    return target, None


def _model_id_of(slot: Any) -> str:
    model_id = getattr(slot, "model_id", "")
    if isinstance(model_id, str) and model_id:
        return model_id
    return str(getattr(slot, "name", ""))


async def _post_json(
    ctx: DispatchContext,
    path: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """POST JSON to a hal0 /v1 endpoint; return parsed body or an
    ``{"error": ...}`` envelope. Never raises."""
    url = f"{ctx.api_base_url}{path}"
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


# ── single-GPU self-eviction guard (#2191) ───────────────────────────

#: Tools whose dispatch goes through the image slot and so can flip the
#: exclusive GPU into image mode (``GpuArbiter.ensure_img``).
IMAGE_TOOLS: frozenset[str] = frozenset({"generate_image", "edit_image"})

#: Restores detached from a cancelled request, kept referenced until done.
_DETACHED_RESTORES: set[asyncio.Task[Any]] = set()


async def restore_caller_after_images(ctx: DispatchContext) -> str | None:
    """Bring the caller LLM back after an image round flipped the exclusive GPU.

    On a single-GPU box the img and llm slots share the GPU: dispatching
    ``/v1/images/generations`` makes :class:`~hal0.slots.arbiter.GpuArbiter`
    enter image mode, which unloads the llm group — including the very slot
    whose tool_call is being served. Left there, the loop's next chat round
    503s (``gpu.image_mode``), the rendered image is orphaned, and the GPU
    stays parked in image mode for the idle-restore window.

    The loop calls this ONCE per tool round, after every tool call in the
    round has finished (a round may carry several image renders dispatched
    in parallel; restoring after the first would pull the GPU from under
    the rest). Renders this loop cannot see — another request's, or one a
    cancelled caller left on the queue — are covered by going through
    :meth:`~hal0.slots.arbiter.GpuArbiter.restore_llm_when_idle`, which waits
    for ComfyUI's queue to drain before freeing anything. The restore itself
    is shielded from the request's cancellation: once started it runs to
    completion detached, so a client leaving mid-restore cannot leave ComfyUI
    freed with the LLM set half loaded. Acts only when the arbiter is in
    image mode AND the caller is an llm-group slot — i.e. one the flip
    evicted. An NPU/CPU caller, a GPU that did not flip, or a box with no
    arbiter is left alone.

    Returns ``None`` when the caller is available again (or never was
    unavailable), else a one-line reason it still is not — a pinned image
    mode (an operator's explicit choice, respected) or a failed restore.
    The reason is for the client; the loop must not ask the evicted LLM to
    consume it.
    """
    arbiter = getattr(ctx.slot_manager, "arbiter", None)
    if arbiter is None:
        return None
    try:
        from hal0.slots.arbiter import ArbiterPinned, GpuMode, gpu_exclusive_group

        if arbiter.mode != GpuMode.IMG:
            return None
        caller_cfg = next(
            (
                c
                for c in await ctx.slot_manager.iter_configs()
                if str(c.get("name") or "") == ctx.caller_slot_name
            ),
            None,
        )
        if caller_cfg is None or gpu_exclusive_group(caller_cfg) != "llm":
            return None
        restore = asyncio.ensure_future(arbiter.restore_llm_when_idle())
        _DETACHED_RESTORES.add(restore)
        restore.add_done_callback(_DETACHED_RESTORES.discard)
        try:
            await asyncio.shield(restore)
        except asyncio.CancelledError:
            # The request is gone; the restore keeps running on its own.
            log.info("omni.image_caller_restore_detached caller=%s", ctx.caller_slot_name)
            raise
        except ArbiterPinned as exc:
            return (
                f"GPU image mode is pinned, so the caller LLM slot "
                f"{ctx.caller_slot_name!r} was not restored: {exc}"
            )
    except Exception as exc:  # never lose the rendered image over the restore
        log.warning(
            "omni.image_caller_restore_failed caller=%s error=%s", ctx.caller_slot_name, exc
        )
        return (
            f"the caller LLM slot {ctx.caller_slot_name!r} could not be restored "
            f"after image generation: {exc}"
        )
    return None


# ── handlers ────────────────────────────────────────────────────────


async def handle_generate_image(ctx: DispatchContext, args: Mapping[str, Any]) -> dict[str, Any]:
    err = _missing(args, "prompt")
    if err is not None:
        return {"error": err}
    target, err_body = await _route_or_error(ctx, tools_by_name()["generate_image"])
    if target is None:
        return err_body or {"error": "no image slot"}
    body: dict[str, Any] = {
        "model": _model_id_of(target),
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
    target, err_body = await _route_or_error(ctx, tools_by_name()["edit_image"])
    if target is None:
        return err_body or {"error": "no image-edit slot"}
    body: dict[str, Any] = {
        "model": _model_id_of(target),
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
    target, err_body = await _route_or_error(ctx, tools_by_name()["text_to_speech"])
    if target is None:
        return err_body or {"error": "no tts slot"}
    body: dict[str, Any] = {
        "model": _model_id_of(target),
        "input": args["input"],
    }
    if args.get("voice"):
        body["voice"] = args["voice"]
    return await _post_json(ctx, "/v1/audio/speech", body)


async def handle_transcribe_audio(ctx: DispatchContext, args: Mapping[str, Any]) -> dict[str, Any]:
    err = _missing(args, "audio")
    if err is not None:
        return {"error": err}
    target, err_body = await _route_or_error(ctx, tools_by_name()["transcribe_audio"])
    if target is None:
        return err_body or {"error": "no transcription slot"}
    body: dict[str, Any] = {
        "model": _model_id_of(target),
        # /v1/audio/transcriptions is a multipart endpoint
        # in the OpenAI contract; tool-call args come in as a JSON
        # blob from the LLM, so PR-16 wraps that as a single-field
        # JSON body and lets the upstream compatibility layer convert.
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
    target, err_body = await _route_or_error(ctx, tools_by_name()["analyze_image"])
    if target is None:
        return err_body or {"error": "no vision-capable llm slot"}
    # Vision goes through /v1/chat/completions with an image-URL/data
    # content part in the user message — OpenAI's standard shape.
    body: dict[str, Any] = {
        "model": _model_id_of(target),
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
    target, err_body = await _route_or_error(ctx, tools_by_name()["embed_text"])
    if target is None:
        return err_body or {"error": "no embedding slot"}
    body: dict[str, Any] = {
        "model": _model_id_of(target),
        "input": args["input"],
    }
    return await _post_json(ctx, "/v1/embeddings", body)


async def handle_rerank_documents(ctx: DispatchContext, args: Mapping[str, Any]) -> dict[str, Any]:
    err = _missing(args, "query", "documents")
    if err is not None:
        return {"error": err}
    target, err_body = await _route_or_error(ctx, tools_by_name()["rerank_documents"])
    if target is None:
        return err_body or {"error": "no reranking slot"}
    body: dict[str, Any] = {
        "model": _model_id_of(target),
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

    target_name = str(args["target"])
    caller_slot = await ctx.slot_manager.loaded_slot(ctx.caller_slot_name)
    target_slot = await ctx.slot_manager.loaded_slot(target_name)
    current_depth = DELEGATION_DEPTH.get()
    rejection = validate_delegation_slots(
        caller_slot,
        target_slot,
        caller_slot_name=ctx.caller_slot_name,
        target=target_name,
        current_depth=current_depth,
    )
    if rejection is not None:
        return {"error": rejection}

    assert target_slot is not None  # validate_delegation_slots guaranteed this

    messages = build_delegation_messages_for_slot(
        target_slot,
        prompt=str(args["prompt"]),
        context=str(args["context"]) if args.get("context") else None,
    )
    body = {
        "model": _model_id_of(target_slot),
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
