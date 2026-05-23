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
import json
import logging
from typing import Any

import httpx

from hal0.omni_router.dispatch import (
    DispatchContext,
    dispatch_tool,
)
from hal0.omni_router.filter import SlotManagerLike, active_tools_for
from hal0.omni_router.tools import ToolDefinition

log = logging.getLogger(__name__)

# Safety net — the loop must terminate even against a pathological LLM
# that emits tool_calls forever. Plan §7.4 limits delegation depth
# separately; this is the per-request "give up" budget. Eight tools,
# expected single-round usage; 8 is generous.
_MAX_LOOP_ROUNDS = 8


class OmniRouter:
    """Client-side OpenAI tool-calling loop.

    Constructed once per hal0-api process; the SlotManager + httpx
    client + Lemonade URL are shared across requests. Lifetime is
    tied to the FastAPI lifespan.

    Args:
        slot_manager: source of slot state + routing.
        http_client: shared httpx client. The OmniRouter does NOT
            own this client; the lifespan owns it (matching the
            Dispatcher pattern in ``dispatcher/router.py``).
        lemonade_base_url: ``http://127.0.0.1:13305`` per ADR-0008 §1.
    """

    def __init__(
        self,
        *,
        slot_manager: SlotManagerLike,
        http_client: httpx.AsyncClient,
        lemonade_base_url: str = "http://127.0.0.1:13305",
    ) -> None:
        self._slot_manager = slot_manager
        self._http_client = http_client
        self._lemonade_base_url = lemonade_base_url.rstrip("/")

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
        # Local mutable copy so we can append tool_result messages.
        working = dict(self._strip_omni(body))
        messages = list(working.get("messages") or [])
        working["messages"] = messages
        working["tools"] = [t.to_openai_tool() for t in tools_active]
        # Streaming deferred to PR-18.
        working["stream"] = False

        last_response: dict[str, Any] | None = None
        for round_idx in range(_MAX_LOOP_ROUNDS):
            response = await self._chat_completion(working)
            last_response = response
            tool_calls = self._extract_tool_calls(response)
            if not tool_calls:
                return response

            # Append the assistant's tool_call message so the model
            # sees its own turn on the next request.
            assistant_message = self._extract_assistant_message(response)
            if assistant_message is not None:
                messages.append(assistant_message)

            # Dispatch all tool_calls in parallel — multiple tool_calls
            # in one response are a normal OpenAI shape and we don't
            # want serial latency.
            results = await asyncio.gather(
                *(
                    dispatch_tool(ctx, tc["function"]["name"], tc["function"]["arguments"])
                    for tc in tool_calls
                )
            )
            for tc, result in zip(tool_calls, results, strict=True):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "name": tc["function"]["name"],
                        "content": json.dumps(result),
                    }
                )
            log.debug(
                "omni_router.loop_round",
                extra={
                    "round": round_idx,
                    "tool_calls": len(tool_calls),
                    "caller": caller_slot_name,
                },
            )

        # Loop budget exhausted — return the last response we got.
        log.warning(
            "omni_router.loop_budget_exhausted",
            extra={"max_rounds": _MAX_LOOP_ROUNDS, "caller": caller_slot_name},
        )
        return last_response or {"error": "loop budget exhausted with no response"}

    # ── helpers ────────────────────────────────────────────────────

    def _build_context(self, caller_slot_name: str) -> DispatchContext:
        """Build a DispatchContext wired with a chat_completion callback.

        The callback closes over ``self._chat_completion`` so the
        route_to_chat handler can re-enter the loop's transport layer
        without re-implementing it.
        """
        return DispatchContext(
            slot_manager=self._slot_manager,
            http_client=self._http_client,
            lemonade_base_url=self._lemonade_base_url,
            caller_slot_name=caller_slot_name,
            chat_completion=self._chat_completion,
        )

    async def _chat_completion(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST ``/v1/chat/completions`` and return the parsed body.

        Errors are surfaced as a tool-result-shaped dict the loop can
        keep stepping against. Same envelope shape as
        :func:`hal0.omni_router.dispatch._post_json` for consistency.
        """
        url = f"{self._lemonade_base_url}/v1/chat/completions"
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

    @staticmethod
    def _extract_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
        """Pull ``tool_calls`` out of a chat-completion response.

        Normalises ``arguments`` to a dict — OpenAI ships it as a
        JSON-encoded string. Malformed JSON yields an empty dict and
        the dispatcher's argument-validator emits the missing-arg
        error.
        """
        choices = response.get("choices") or []
        if not choices:
            return []
        msg = choices[0].get("message") or {}
        raw_calls = msg.get("tool_calls") or []
        out: list[dict[str, Any]] = []
        for tc in raw_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    parsed = json.loads(args)
                    if not isinstance(parsed, dict):
                        parsed = {}
                except ValueError:
                    parsed = {}
            elif isinstance(args, dict):
                parsed = args
            else:
                parsed = {}
            out.append(
                {
                    "id": tc.get("id", ""),
                    "type": tc.get("type", "function"),
                    "function": {
                        "name": fn.get("name", ""),
                        "arguments": parsed,
                    },
                }
            )
        return out

    @staticmethod
    def _extract_assistant_message(response: dict[str, Any]) -> dict[str, Any] | None:
        """Pull the assistant turn message (with tool_calls) for replay."""
        choices = response.get("choices") or []
        if not choices:
            return None
        msg = choices[0].get("message")
        if isinstance(msg, dict):
            return msg
        return None


__all__ = ["OmniRouter"]
