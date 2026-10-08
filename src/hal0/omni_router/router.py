"""OmniRouter — the public surface.

Wires :mod:`hal0.omni_router.filter`, :mod:`hal0.omni_router.dispatch`,
and :mod:`hal0.omni_router.route_to_chat` into a single object the
API layer + tests can drive.

Public methods:

  * :meth:`OmniRouter.active_tools` — per-request filter for a chat
    slot. Returns a list of :class:`ToolDefinition`.
  * :meth:`OmniRouter.dispatch` — run a single tool_call; returns the
    tool_result body.
  * :meth:`OmniRouter.run_loop` — the iterative OpenAI tool-calling
    loop. Sends the request with ``tools=[...]``, intercepts any
    ``tool_calls`` in the response, dispatches them in parallel,
    folds the results back as ``role=tool`` messages, and repeats
    until the assistant message has no more tool_calls.

Streaming responses are deferred to PR-18 (UI surface). ``run_loop``
returns the final non-streaming response dict.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from hal0.omni_router.dispatch import (
    IMAGE_TOOLS,
    DispatchContext,
    dispatch_tool,
    restore_caller_after_images,
)
from hal0.omni_router.filter import SlotManagerLike, active_tools_for
from hal0.omni_router.tools import ToolDefinition
from hal0.toolloop.engine import run_tool_loop

log = logging.getLogger(__name__)

# Safety net — the loop must terminate even against a pathological LLM
# that emits tool_calls forever. Plan §7.4 limits delegation depth
# separately; this is the per-request "give up" budget. Eight tools,
# expected single-round usage; 8 is generous.
_MAX_LOOP_ROUNDS = 8


def _completion_without_caller(
    request_body: dict[str, Any],
    caller_slot_name: str,
    reason: str,
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """A chat completion the loop returns when the caller LLM cannot continue (#2191).

    The image tools ran; the caller slot was unloaded by that and could not be
    restored (``reason``). Asking the evicted slot to fold the results in
    would 503 and lose the image, so the loop answers directly: an OpenAI-shaped
    completion whose text names each rendered image, plus an ``hal0.omni``
    block with the verbatim tool results and the reason, for clients that
    want the structured form.
    """
    lines: list[str] = []
    for item in tool_results:
        result = item.get("result")
        data = result.get("data") if isinstance(result, dict) else None
        urls = (
            [d.get("url") for d in data if isinstance(d, dict) and d.get("url")]
            if isinstance(data, list)
            else []
        )
        if urls:
            lines.append(f"{item.get('name')}: " + ", ".join(str(u) for u in urls))
        elif isinstance(result, dict) and result.get("error"):
            lines.append(f"{item.get('name')} failed: {result['error']}")
        else:
            lines.append(f"{item.get('name')}: completed (see hal0.omni.tool_results)")
    content = (
        "\n".join(lines)
        + f"\n\nThe model {caller_slot_name!r} could not continue after the image step: {reason}"
    )
    return {
        "id": f"chatcmpl-omni-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(request_body.get("model") or caller_slot_name),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "hal0": {
            "omni": {
                "caller_slot": caller_slot_name,
                "caller_slot_unavailable": reason,
                "tool_results": tool_results,
            }
        },
    }


class OmniRouter:
    """Client-side OpenAI tool-calling loop.

    Constructed once per hal0-api process; the SlotManager + httpx
    client + API base URL are shared across requests. Lifetime is
    tied to the FastAPI lifespan.

    Args:
        slot_manager: source of slot state + routing.
        http_client: shared httpx client. The OmniRouter does NOT
            own this client; the lifespan owns it (matching the
            Dispatcher pattern in ``dispatcher/router.py``).
        api_base_url: hal0-api's own ``/v1`` surface (#709) — chat completions
            re-enter the full dispatch chain (arbiter guard, readiness
            gates, container routing).
    """

    def __init__(
        self,
        *,
        slot_manager: SlotManagerLike,
        http_client: httpx.AsyncClient,
        api_base_url: str = "http://127.0.0.1:8080",
    ) -> None:
        self._slot_manager = slot_manager
        self._http_client = http_client
        self._api_base_url = api_base_url.rstrip("/")
        # Detached restore tasks (#2191) kept referenced until they finish.
        self._background_restores: set[asyncio.Task[None]] = set()

    # ── filter surface ─────────────────────────────────────────────

    async def active_tools(self, chat_slot_name: str) -> list[ToolDefinition]:
        """Return the active tool list for a chat slot. Plan §7.3."""
        return await active_tools_for(self._slot_manager, chat_slot_name)

    # ── single-tool dispatch surface ───────────────────────────────

    async def dispatch(
        self,
        *,
        caller_slot_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch a single tool_call. Returns the tool_result body."""
        ctx = self._build_context(caller_slot_name)
        return await dispatch_tool(ctx, tool_name, arguments)

    # ── full loop surface ──────────────────────────────────────────

    async def run_loop(
        self,
        *,
        caller_slot_name: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Drive the OpenAI tool-calling loop against ``/v1/chat/completions``.

        Args:
            caller_slot_name: chat slot that owns this request. Used
                for tool filtering, route_to_chat self-detection, and
                NPU-exclusivity.
            body: the OpenAI chat-completion request body. Must carry
                ``model`` + ``messages``; ``tools`` is overwritten by
                the active filter, ``stream`` is forced False
                (streaming is PR-18).

        Returns:
            The final ``/v1/chat/completions`` response dict — the
            one without ``tool_calls``. Callers forward this to the
            client verbatim.
        """
        tools_active = await self.active_tools(caller_slot_name)
        # Empty tool list → no point looping; pass through once.
        if not tools_active:
            return await self._chat_completion(self._strip_omni(body))

        ctx = self._build_context(caller_slot_name)
        # Local mutable copy so the loop core can append tool_result messages.
        working = dict(self._strip_omni(body))
        working["messages"] = list(working.get("messages") or [])
        tool_schemas = [t.to_openai_tool() for t in tools_active]

        round_count = 0
        last_response: dict[str, Any] | None = None
        # #2191: set when an image round left the caller LLM unavailable
        # (pinned image mode, failed restore). The next "chat round" then
        # answers from here instead of asking the evicted slot.
        caller_unavailable: str | None = None
        # Every image result this request produced, across rounds: earlier
        # rounds' images only ever went into the private transcript, so the
        # fallback completion must carry them too.
        image_results: list[dict[str, Any]] = []

        async def _dispatch_round(
            tool_calls: list[dict[str, Any]],
        ) -> AsyncIterator[dict[str, Any]]:
            nonlocal round_count, caller_unavailable
            image_round = any(tc["name"] in IMAGE_TOOLS for tc in tool_calls)
            # Dispatch all tool_calls in parallel — multiple tool_calls in
            # one response are a normal OpenAI shape and we don't want
            # serial latency.
            try:
                results = await asyncio.gather(
                    *(dispatch_tool(ctx, tc["name"], tc["arguments"]) for tc in tool_calls)
                )
            except asyncio.CancelledError:
                # The client went away mid-render. The GPU may already be in
                # image mode with the caller unloaded; nobody else will put it
                # back before the idle window expires, so restore from a
                # detached task and let the cancellation through.
                if image_round:
                    self._restore_in_background(ctx)
                raise
            if image_round:
                image_results.extend(
                    {"id": tc["id"], "name": tc["name"], "result": result}
                    for tc, result in zip(tool_calls, results, strict=True)
                    if tc["name"] in IMAGE_TOOLS
                )
                # One restore per round, after every render in it finished —
                # restoring after the first would pull the GPU from under the
                # rest (#2191). Waits for ComfyUI's queue (other requests'
                # renders) and survives this request's cancellation.
                caller_unavailable = await restore_caller_after_images(ctx)
            for tc, result in zip(tool_calls, results, strict=True):
                yield {"type": "tool_result", "id": tc["id"], "name": tc["name"], "result": result}
            log.debug(
                "omni_router.loop_round",
                extra={
                    "round": round_count,
                    "tool_calls": len(tool_calls),
                    "caller": caller_slot_name,
                },
            )
            round_count += 1

        async def _llm(request_body: dict[str, Any]) -> dict[str, Any]:
            # The caller LLM is gone (#2191): do not send it the tool results
            # — that round would 503 and the client would lose the image.
            # Answer with a completion that carries the results instead.
            if caller_unavailable:
                return _completion_without_caller(
                    request_body, caller_slot_name, caller_unavailable, image_results
                )
            return await self._chat_completion(request_body)

        async for event in run_tool_loop(
            _llm,
            tool_schemas,
            _dispatch_round,
            body=working,
            max_rounds=_MAX_LOOP_ROUNDS,
        ):
            etype = event.get("type")
            if etype == "response":
                last_response = event["data"]
            elif etype == "error" and "budget exhausted" in str(event.get("message", "")):
                # Loop budget exhausted — the core still returns the last
                # response we got via the "response" marker above.
                log.warning(
                    "omni_router.loop_budget_exhausted",
                    extra={"max_rounds": _MAX_LOOP_ROUNDS, "caller": caller_slot_name},
                )

        return last_response or {"error": "loop budget exhausted with no response"}

    # ── helpers ────────────────────────────────────────────────────

    def _restore_in_background(self, ctx: DispatchContext) -> None:
        """Restore the caller LLM from a detached task (cancelled request, #2191)."""

        async def _run() -> None:
            reason = await restore_caller_after_images(ctx)
            if reason:
                log.warning("omni_router.background_restore_incomplete", extra={"reason": reason})

        try:
            task = asyncio.get_running_loop().create_task(_run())
        except RuntimeError:  # pragma: no cover — no loop; nothing to schedule on
            return
        self._background_restores.add(task)
        task.add_done_callback(self._background_restores.discard)

    def _build_context(self, caller_slot_name: str) -> DispatchContext:
        """Build a DispatchContext wired with a chat_completion callback.

        The callback closes over ``self._chat_completion`` so the
        route_to_chat handler can re-enter the loop's transport layer
        without re-implementing it.
        """
        return DispatchContext(
            slot_manager=self._slot_manager,
            http_client=self._http_client,
            api_base_url=self._api_base_url,
            caller_slot_name=caller_slot_name,
            chat_completion=self._chat_completion,
        )

    async def _chat_completion(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST ``/v1/chat/completions`` and return the parsed body.

        Errors are surfaced as a tool-result-shaped dict the loop can
        keep stepping against. Same envelope shape as
        :func:`hal0.omni_router.dispatch._post_json` for consistency.
        """
        url = f"{self._api_base_url}/v1/chat/completions"
        try:
            resp = await self._http_client.post(url, json=body, timeout=300.0)
        except httpx.TimeoutException:
            return {"error": "chat completion timeout"}
        except (httpx.ConnectError, httpx.NetworkError, httpx.HTTPError) as exc:
            return {"error": f"chat completion transport failure: {exc}"}
        if not (200 <= resp.status_code < 300):
            return {
                "error": (f"chat completion upstream HTTP {resp.status_code}: {resp.text[:500]}")
            }
        try:
            return resp.json()
        except ValueError:
            return {"error": "chat completion returned non-JSON body"}

    @staticmethod
    def _strip_omni(body: dict[str, Any]) -> dict[str, Any]:
        """Drop hal0-specific knobs that must not reach the upstream."""
        out = {k: v for k, v in body.items() if k != "omni"}
        return out


__all__ = ["OmniRouter"]
