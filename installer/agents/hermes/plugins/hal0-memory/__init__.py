"""hal0-memory MemoryProvider plugin (Hermes plugin, issue #242).

Path B from the bootstrap plan's Q1 — Hermes's agent loop calls our
``system_prompt_block()`` / ``prefetch()`` / ``sync_turn()`` lifecycle
methods natively so memory is prompt-injected, not a tool the model
has to remember to call. MCP servers stay registered in parallel via
``config.yaml mcp_servers`` so an operator can still invoke
``memory_delete`` by hand.

This file is **copied** into ``$HERMES_HOME/plugins/memory/hal0-memory/``
at bootstrap install time (see ``hermes_provision._phase_install``).
It is NOT imported by hal0 itself — the upstream ``agent.memory_provider``
import resolves against the hermes-agent venv where the plugin runs.

ADR-0014 contract: the plugin reads ``memory.graph`` from
``$HERMES_HOME/config.yaml`` (handed in via ``initialize()`` kwargs
or read from disk) and honors ``graph.enabled`` (defaults false) plus
``graph.route`` enum. When the route is ``"upstream"`` we pass the
``graph.upstream.{provider,model}`` keys through to the memory_add
call; the hal0-memory MCP server enforces the actual provider switch.
"""

from __future__ import annotations

import json
from typing import Any

# Resolves in the hermes-agent venv.
from agent.memory_provider import MemoryProvider  # type: ignore[import-not-found]

try:  # Optional dep — Hermes already pins httpx; this is defence.
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

DEFAULT_BASE_URL = "http://127.0.0.1:8080/mcp/memory"
DEFAULT_AGENT_ID = "hermes-agent"


class Hal0MemoryProvider(MemoryProvider):
    """Memory provider backed by hal0's Cognee-backed memory MCP.

    Lifecycle:

    1. ``initialize()`` stores config + session metadata.
    2. ``system_prompt_block()`` returns a short "you have a memory
       store at hal0-memory" preamble so the agent surfaces durable
       facts in its system prompt.
    3. ``prefetch()`` fires ``memory_search`` against the
       ``private:hermes-agent`` namespace before each turn.
    4. ``sync_turn()`` fires ``memory_add`` after each turn, batched
       so a single user→assistant exchange becomes one memory item.

    Graph extraction (ADR-0014) defaults OFF. When enabled in
    ``config.yaml memory.graph`` the plugin forwards the route+model
    selection into ``memory_add`` calls; hal0-memory MCP server
    enforces the actual provider switch.
    """

    name = "hal0-memory"

    def __init__(self) -> None:
        self._base_url: str = DEFAULT_BASE_URL
        self._agent_id: str = DEFAULT_AGENT_ID
        self._session_id: str = ""
        self._user_id: str = ""
        self._graph_cfg: dict[str, Any] = {"enabled": False, "route": "upstream"}

    # ── Lifecycle (MemoryProvider ABC) ──────────────────────────────────

    def initialize(self, *args: Any, **kwargs: Any) -> None:
        # Upstream's keyword surface evolves; we read defensively so
        # signature drift in newer Hermes releases doesn't blow up
        # our installer.
        self._session_id = kwargs.get("session_id") or ""
        self._user_id = kwargs.get("user_id") or ""
        cfg = kwargs.get("config") or {}
        memory = (cfg.get("memory") or {}) if isinstance(cfg, dict) else {}
        graph = memory.get("graph") or {}
        if isinstance(graph, dict):
            self._graph_cfg = {
                "enabled": bool(graph.get("enabled", False)),
                "route": str(graph.get("route", "upstream")),
                "upstream": graph.get("upstream") or {},
            }
        # Allow env override of base_url / agent_id for power users.
        import os

        self._base_url = os.environ.get("HAL0_MCP_MEMORY_URL", self._base_url)
        self._agent_id = os.environ.get("HAL0_AGENT_ID", self._agent_id)

    def system_prompt_block(self) -> str:
        return (
            "You have a durable memory store at hal0-memory "
            "(private:hermes-agent namespace). Use memory_search before "
            "asking the user repeat-questions; use memory_add to record "
            "facts worth recalling across sessions."
        )

    def prefetch(self, query: str, **_: Any) -> str:
        result = self._call_mcp("memory_search", {"query": query, "limit": 5})
        items = result.get("items") if isinstance(result, dict) else None
        if not items:
            return ""
        return "Relevant memories:\n" + "\n".join(f"- {item.get('text', '')}" for item in items)

    def queue_prefetch(self, query: str, **kwargs: Any) -> None:
        # No background workers in v0.3 — synchronous prefetch is fine
        # for single-host LAN traffic. Hook reserved for v0.4 if we
        # need it.
        return None

    def sync_turn(self, user: str, assistant: str, **_: Any) -> None:
        # PR-1-bundle: do NOT send ``dataset=private:<agent>``. The MCP
        # server-side ``_resolve_dataset`` rejects ``private:`` prefixes
        # via ``_AGENT_ID_PATTERN`` and now resolves the dataset itself
        # from ``X-hal0-Agent`` + ``X-hal0-Private: 1`` (server fix
        # landed in PR #366). Sending the bogus dataset turned every
        # ``sync_turn`` into a silent 4xx that lost durable memory.
        payload: dict[str, Any] = {
            "text": f"User: {user}\nAssistant: {assistant}",
            "tags": ["chat", "hermes"],
        }
        if self._graph_cfg.get("enabled"):
            payload["graph"] = self._graph_cfg
        self._call_mcp("memory_add", payload)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        # Operator-override tools — agent loop uses the prompt-injection
        # path above, but exposing the tools too lets the user call
        # memory_delete by hand. Schemas mirror hal0-memory MCP server.
        return []

    def handle_tool_call(self, name: str, args: dict[str, Any], **_: Any) -> str:
        result = self._call_mcp(name, args)
        return json.dumps(result)

    def shutdown(self) -> None:
        return None

    # ── HTTP transport ──────────────────────────────────────────────────

    def _call_mcp(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if httpx is None:
            return {"status": "error", "error": "httpx not available"}
        headers = {
            "X-hal0-Agent": self._agent_id,
            "X-hal0-Private": "1",
            "Content-Type": "application/json",
        }
        # Streamable-HTTP MCP transport uses JSON-RPC over POST.
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
        try:
            resp = httpx.post(self._base_url, headers=headers, json=body, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        result = data.get("result") if isinstance(data, dict) else None
        return result if isinstance(result, dict) else {"status": "ok", "raw": data}
