"""Hermes ``MemoryProvider`` subclass backed by hal0-memory REST.

Subclasses the upstream ``agent.memory_provider.MemoryProvider`` ABC.
The import resolves inside the Hermes venv at runtime — when the
plugin is loaded out of ``$HERMES_HOME/plugins/memory/hal0-cognee/``.
For hal0's own unit tests we import a vendored stub so the suite stays
runnable without the hermes-agent venv on PYTHONPATH.

Design notes
------------

* ``hal0-cognee`` is the new plugin name. The retired ``hal0-memory``
  stub at ``installer/agents/hermes/plugins/hal0-memory/__init__.py``
  is replaced by this provider in PR-3.
* The agent loop's memory hooks (``prefetch``/``sync_turn``) are sync,
  so we wrap async REST calls via ``asyncio.run``. Each call gets its
  own event loop so we don't fight whatever loop Hermes hosts above us.
* The provider is best-effort — transport failures fall back to empty
  context (prefetch) or silent drop (sync_turn) so a missing hal0-api
  cannot wedge the agent loop. The upstream ``MemoryProviderError``
  taxonomy is reserved for explicit tool calls.
* Identity flows via ``X-hal0-Agent`` (sourced from ``HAL0_AGENT_ID``).
  We NEVER send a ``dataset`` field — the server resolves the namespace
  from the header (see ``Hal0MemoryClient`` docstring + #317).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

try:
    from agent.memory_provider import MemoryProvider  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — exercised inside Hermes venv only
    # Fallback stub so hal0's own test suite can import the provider
    # without the hermes-agent venv on PYTHONPATH. The real ABC ships
    # with Hermes; this minimal shim mirrors the abstract surface our
    # provider actually subclasses against.
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

# Honour the same context gate honcho + supermemory ship: skip writes
# from cron / flush / subagent loops so non-primary contexts don't
# corrupt the user-facing memory namespace.
_SKIP_WRITE_CONTEXTS = frozenset({"cron", "flush", "subagent"})


class Hal0CogneeProvider(MemoryProvider):  # type: ignore[misc]
    """REST-backed memory provider — wraps hal0-memory.

    Vendored under hal0's tree so the installer can deploy it into the
    Hermes plugin directory at provision time. Subclasses the upstream
    ``MemoryProvider`` ABC; see module docstring for the integration
    contract.
    """

    def __init__(self, *, client: Hal0MemoryClient | None = None) -> None:
        self._client_override = client
        self._client: Hal0MemoryClient | None = None
        self._session_id: str = ""
        self._agent_context: str = "primary"

    # ── ABC: identity ──────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "hal0-cognee"

    # ── ABC: lifecycle ─────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Cheap config check — does NOT make a network call.

        The ABC docstring is explicit: ``is_available`` must only check
        installed deps + config. The actual reachability check happens
        lazily on the first REST call.
        """
        # httpx is a hard dep of hal0; we ship the client alongside the
        # provider. No env-var gate — the defaults already point at the
        # local hal0-api socket on the same host.
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
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
            asyncio.run(self._client.aclose())
        except RuntimeError:
            # Already running an event loop (Hermes shutting down inside
            # its own loop); fire-and-forget close.
            logger.debug("hal0-cognee shutdown: nested event loop, skipping aclose")
        finally:
            self._client = None

    # ── ABC: prompt + recall ───────────────────────────────────────────

    def system_prompt_block(self) -> str:
        agent_id = self._client.agent_id if self._client else "hermes-agent"
        return (
            "You have a durable memory store at hal0-cognee "
            f"(private:{agent_id} namespace, resolved server-side). "
            "Use memory_search before asking repeat-questions; "
            "memory_add to persist facts worth recalling across sessions."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not query or self._client is None:
            return ""
        try:
            result = asyncio.run(self._client.search(query, limit=5))
        except Hal0MemoryClientError as exc:
            logger.debug("hal0-cognee prefetch transport failure: %s", exc)
            return ""
        except RuntimeError as exc:  # nested loop or other asyncio drift
            logger.debug("hal0-cognee prefetch asyncio drift: %s", exc)
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
        return "## hal0-cognee memory\n" + "\n".join(lines)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
    ) -> None:
        if self._client is None:
            return
        if self._agent_context in _SKIP_WRITE_CONTEXTS:
            return
        if not user_content and not assistant_content:
            return
        text = f"User: {user_content}\nAssistant: {assistant_content}"
        try:
            asyncio.run(self._client.add(text, tags=["chat", "hermes"]))
        except Hal0MemoryClientError as exc:
            logger.debug("hal0-cognee sync_turn transport failure: %s", exc)
        except RuntimeError as exc:
            logger.debug("hal0-cognee sync_turn asyncio drift: %s", exc)

    # ── ABC: tools ─────────────────────────────────────────────────────

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """No model-visible tools.

        Memory surfaces via the system-prompt block + prefetch context.
        Operator-driven CRUD is exposed via the ``hal0-memory`` MCP
        server which Hermes loads from ``mcp_servers`` config (PR-3).
        Keeping the tool list empty avoids schema bloat that competes
        with the MCP path.
        """
        return []

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        # Defensive: ``get_tool_schemas`` returns []; the loop should
        # never reach this branch. Match the upstream ABC contract by
        # returning a JSON-encoded error string.
        return json.dumps(
            {"status": "error", "error": f"hal0-cognee exposes no tool '{tool_name}'"}
        )

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
        tags = ["builtin-memory", target] if target else ["builtin-memory"]
        try:
            asyncio.run(self._client.add(content, tags=tags, metadata=metadata))
        except Hal0MemoryClientError as exc:
            logger.debug("hal0-cognee on_memory_write transport failure: %s", exc)
        except RuntimeError as exc:
            logger.debug("hal0-cognee on_memory_write asyncio drift: %s", exc)
