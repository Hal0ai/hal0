"""Hermes ``MemoryProvider`` backed by hal0-memory REST — canonical, shipped source.

Design notes:

* Identity defaults to ``hermes`` (not ``hermes-agent``) — hal0's registry
  name; the server derives the private bank ``private:hermes`` from it.
* Two banks: ``private:hermes`` (default) + ``shared``. The ``hal0_memory_add``
  tool takes ``shared=true`` to write the shared bank. Reads are a union.
* Exposes explicit ``hal0_memory_{search,recall,add}`` tools so the agent can
  read/write memory directly (robust even if the hal0-memory MCP server's
  tools aren't surfaced to a given session), on top of prompt-injection recall.
* **Synchronous** transport — an async+``asyncio.run`` wrapping breaks on the
  2nd call (reused AsyncClient bound to a closed per-call loop). The Hermes
  memory hooks are sync; a sync client is correct and simpler.

Subclasses the upstream ``agent.memory_provider.MemoryProvider`` ABC, which
resolves inside the Hermes venv at runtime. A vendored stub keeps the module
importable in hal0's own venv for unit tests. All paths are best-effort:
transport failures fall back to empty context / silent drop so a missing
hal0-api can't wedge the agent loop.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

try:
    from agent.memory_provider import MemoryProvider  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — exercised inside Hermes venv only
    from abc import ABC, abstractmethod

    class MemoryProvider(ABC):  # type: ignore[no-redef]
        @property
        @abstractmethod
        def name(self) -> str: ...

        @abstractmethod
        def is_available(self) -> bool: ...

        @abstractmethod
        def initialize(self, session_id: str, **kwargs: Any) -> None: ...

        @abstractmethod
        def get_tool_schemas(self) -> list[dict[str, Any]]: ...


from ._client import Hal0MemoryClient, Hal0MemoryClientError

logger = logging.getLogger(__name__)

# Skip writes from cron / flush / subagent loops so non-primary contexts
# don't corrupt the user-facing memory namespace.
_SKIP_WRITE_CONTEXTS = frozenset({"cron", "flush", "subagent"})

_DEFAULT_AGENT_ID = "hermes"


# ── Tool schemas — explicit read/write surface, with private/shared choice ──

SEARCH_SCHEMA = {
    "name": "hal0_memory_search",
    "description": (
        "Search durable hal0 memory for relevant facts. Returns ranked excerpts "
        "across BOTH the hermes-private and shared banks (reads are a union). "
        "Use before asking the user to repeat themselves."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "limit": {"type": "integer", "description": "Max results (default 10)."},
        },
        "required": ["query"],
    },
}

RECALL_SCHEMA = {
    "name": "hal0_memory_recall",
    "description": (
        "Recall token-budgeted, consolidated memory (Hindsight observations) "
        "across the hermes-private and shared banks. Prefer over search for a "
        "synthesized picture rather than raw excerpts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Topic to recall."},
            "max_tokens": {"type": "integer", "description": "Token budget (default 2048)."},
        },
        "required": ["query"],
    },
}

ADD_SCHEMA = {
    "name": "hal0_memory_add",
    "description": (
        "Persist a durable fact to hal0 memory. Defaults to the hermes-PRIVATE "
        "bank (only Hermes recalls it). Set shared=true to write the SHARED bank, "
        "readable by every agent on this host."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The fact to remember."},
            "shared": {
                "type": "boolean",
                "description": "true → shared bank; false/omitted → hermes private bank.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for later filtering.",
            },
        },
        "required": ["text"],
    },
}

ALL_TOOL_SCHEMAS = [SEARCH_SCHEMA, RECALL_SCHEMA, ADD_SCHEMA]


class Hal0MemoryProvider(MemoryProvider):  # type: ignore[misc]
    """REST-backed memory provider — wraps hal0-memory (private + shared banks)."""

    def __init__(self, *, client: Hal0MemoryClient | None = None) -> None:
        self._client_override = client
        self._client: Hal0MemoryClient | None = None
        self._session_id: str = ""
        self._agent_context: str = "primary"

    # ── ABC: identity ──────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "hal0-memory"

    # ── ABC: lifecycle ─────────────────────────────────────────────────

    def is_available(self) -> bool:
        # Cheap config check, no network call (ABC contract). Defaults point
        # at the local hal0-api on the same host.
        return True

    def initialize(self, session_id: str = "", **kwargs: Any) -> None:
        self._session_id = session_id or kwargs.get("session_id") or ""
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        if self._client_override is not None:
            self._client = self._client_override
            return
        base_url = os.environ.get("HAL0_MEMORY_BASE")
        agent_id = os.environ.get("HAL0_AGENT_ID")
        self._client = Hal0MemoryClient(base_url=base_url, agent_id=agent_id)

    def shutdown(self) -> None:
        if self._client is None:
            return
        try:
            self._client.close()
        finally:
            self._client = None

    def _agent_id(self) -> str:
        return self._client.agent_id if self._client else _DEFAULT_AGENT_ID

    # ── ABC: prompt + recall ───────────────────────────────────────────

    def system_prompt_block(self) -> str:
        return (
            "# hal0 memory\n"
            "You have a durable cross-session memory store (hal0 / Hindsight) with "
            f"two banks: a PRIVATE bank (private:{self._agent_id()}) only you recall, "
            "and a SHARED bank every agent on this host can read. Reads always span "
            "both. Use hal0_memory_search or hal0_memory_recall before asking the user "
            "to repeat themselves; use hal0_memory_add to persist durable facts (set "
            "shared=true only for facts other agents should see)."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not query or self._client is None:
            return ""
        try:
            # No explicit types — inherit the server's default recall mix
            # (hindsight_provider._DEFAULT_RECALL_TYPES: world, experience,
            # observation). An earlier version pinned ["observation", "world"]
            # here, silently dropping "experience" from prefetch context.
            result = self._client.recall(query, max_tokens=2048)
        except Hal0MemoryClientError as exc:
            logger.debug("hal0-memory prefetch transport failure: %s", exc)
            return ""

        items = result.get("items") if isinstance(result, dict) else None
        if not items:
            return ""
        lines = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get("text") or item.get("content") or ""
            if text:
                lines.append(f"- {text}")
        if not lines:
            return ""
        return "## hal0-memory recall\n" + "\n".join(lines)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
    ) -> None:
        if self._client is None or self._agent_context in _SKIP_WRITE_CONTEXTS:
            return
        if not user_content and not assistant_content:
            return
        text = f"User: {user_content}\nAssistant: {assistant_content}"
        try:
            self._client.add(text, tags=["chat", "agent:hermes"], private=True)
        except Hal0MemoryClientError as exc:
            logger.debug("hal0-memory sync_turn transport failure: %s", exc)

    # ── ABC: tools ─────────────────────────────────────────────────────

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return list(ALL_TOOL_SCHEMAS)

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        if self._client is None:
            return json.dumps({"status": "error", "error": "hal0-memory client not initialized"})
        try:
            if tool_name == "hal0_memory_search":
                query = (args.get("query") or "").strip()
                if not query:
                    return json.dumps(
                        {"status": "error", "error": "Missing required parameter: query"}
                    )
                return json.dumps(
                    self._client.search(query, limit=int(args.get("limit", 10) or 10))
                )

            if tool_name == "hal0_memory_recall":
                query = (args.get("query") or "").strip()
                if not query:
                    return json.dumps(
                        {"status": "error", "error": "Missing required parameter: query"}
                    )
                return json.dumps(
                    self._client.recall(query, max_tokens=int(args.get("max_tokens", 2048) or 2048))
                )

            if tool_name == "hal0_memory_add":
                text = (args.get("text") or "").strip()
                if not text:
                    return json.dumps(
                        {"status": "error", "error": "Missing required parameter: text"}
                    )
                shared = bool(args.get("shared", False))
                tags = args.get("tags")
                tag_list = (
                    [str(t) for t in tags] if isinstance(tags, list) and tags else ["agent:hermes"]
                )
                result = self._client.add(text, tags=tag_list, private=not shared)
                if isinstance(result, dict) and "error" not in result:
                    result["bank"] = "shared" if shared else f"private:{self._agent_id()}"
                return json.dumps(result)

            return json.dumps(
                {"status": "error", "error": f"hal0-memory: unknown tool '{tool_name}'"}
            )
        except Hal0MemoryClientError as exc:
            return json.dumps({"status": "error", "error": str(exc)})

    # ── Optional hook: mirror built-in memory writes ───────────────────

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if action != "add" or self._client is None or not content:
            return
        if self._agent_context in _SKIP_WRITE_CONTEXTS:
            return
        base = ["builtin-memory", "agent:hermes"]
        tags = [*base, target] if target else base
        try:
            self._client.add(content, tags=tags, metadata=metadata, private=True)
        except Hal0MemoryClientError as exc:
            logger.debug("hal0-memory on_memory_write transport failure: %s", exc)
