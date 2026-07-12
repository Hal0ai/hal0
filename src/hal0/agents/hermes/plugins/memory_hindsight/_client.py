"""Thin synchronous REST client for the hal0-memory REST surface.

Canonical source; the installer seed carries a byte-identical copy (see
``tests/agents/hermes_plugins/test_seed_parity.py``).

**Synchronous** httpx: the Hermes memory hooks are all sync, so the old
async+``asyncio.run`` wrapping bought nothing and broke on the 2nd call
(one ``AsyncClient`` reused across per-call event loops → "Event loop is
closed"). A sync ``httpx.Client`` has no loop affinity and is reused safely
for the session.

Talks to hal0-api's ``/api/memory/*`` routes, NOT the ``/mcp/memory``
JSON-RPC transport. Identity is carried via ``X-hal0-Agent`` per ADR-0012;
there is no Bearer auth on the hal0 LAN.

Two banks, selected per write by ``X-hal0-Private``:
  * ``X-hal0-Agent: hermes`` + ``X-hal0-Private: 1`` → ``private:hermes``
  * ``X-hal0-Agent: hermes`` + ``X-hal0-Private: 0`` → ``shared``
Reads are a server-side UNION of both banks regardless of the flag.

#317 contract: this client NEVER sends a ``dataset`` field — the server
resolves the write target from the headers (PR #366).

Settings resolution (base_url / agent_id), highest wins:
  1. explicit constructor argument
  2. env ``HAL0_MEMORY_BASE`` / ``HAL0_AGENT_ID``
  3. ``memory.hal0.{base_url,agent_id}`` in ``$HERMES_HOME/config.yaml``
  4. built-in defaults (local hal0-api, agent ``hermes``)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
# hal0's registry calls this agent ``hermes``; the server derives the bank
# name from it (``private:<id>``), so this IS the private bank name.
DEFAULT_AGENT_ID = "hermes"
DEFAULT_CONNECT_TIMEOUT = 3.0
DEFAULT_READ_TIMEOUT = 30.0  # recall/extraction can take a while on the iGPU
PROBE_TIMEOUT = 1.0


class Hal0MemoryClientError(RuntimeError):
    """Raised when a hal0-memory REST call fails.

    Wraps the upstream status code + response body. We intentionally do NOT
    import ``agent.memory_provider`` here so this module stays importable
    inside hal0's own venv for unit testing.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _config_yaml_value(*keys: str) -> str | None:
    """Best-effort read of a nested key from ``$HERMES_HOME/config.yaml``.

    Never raises: any missing file, missing yaml module, parse error, or
    absent key resolves to ``None`` so env/defaults take over.
    """
    hermes_home = os.environ.get("HERMES_HOME")
    if not hermes_home:
        return None
    path = Path(hermes_home) / "config.yaml"
    try:
        import yaml

        node: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        for key in keys:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return str(node) if isinstance(node, (str, int)) and str(node).strip() else None
    except Exception:
        return None


def _resolve_base_url(override: str | None) -> str:
    if override:
        return override.rstrip("/")
    env = os.environ.get("HAL0_MEMORY_BASE")
    if env:
        return env.rstrip("/")
    from_cfg = _config_yaml_value("memory", "hal0", "base_url")
    if from_cfg:
        return from_cfg.rstrip("/")
    return DEFAULT_BASE_URL


def _resolve_agent_id(override: str | None) -> str:
    if override:
        return override
    env = os.environ.get("HAL0_AGENT_ID")
    if env:
        return env
    return _config_yaml_value("memory", "hal0", "agent_id") or DEFAULT_AGENT_ID


class Hal0MemoryClient:
    """Synchronous REST client for hal0-memory.

    Instantiated once per ``initialize()`` and reused for the session.
    ``close()`` is idempotent.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        agent_id: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = _resolve_base_url(base_url)
        self._agent_id = _resolve_agent_id(agent_id)
        self._owns_client = http_client is None
        if http_client is None:
            timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
            self._client = httpx.Client(base_url=self._base_url, timeout=timeout)
        else:
            self._client = http_client

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def _headers(self, *, private: bool) -> dict[str, str]:
        return {
            "X-hal0-Agent": self._agent_id,
            "X-hal0-Private": "1" if private else "0",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def probe(self, *, timeout: float = PROBE_TIMEOUT) -> bool:
        """Cheap reachability check (never raises). True = hal0-api answered."""
        try:
            response = self._client.request(
                "GET",
                "/api/memory/list",
                headers=self._headers(private=True),
                params={"limit": 1},
                timeout=timeout,
            )
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    # ── REST verbs ─────────────────────────────────────────────────────

    def add(
        self,
        text: str,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        private: bool = True,
    ) -> dict[str, Any]:
        """POST /api/memory/add. ``private=True`` → hermes-private, else shared."""
        payload: dict[str, Any] = {"text": text}
        if tags is not None:
            payload["tags"] = list(tags)
        if metadata is not None:
            payload["metadata"] = dict(metadata)
        return self._request("POST", "/api/memory/add", json=payload, private=private)

    def search(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        """POST /api/memory/search — semantic retrieval (union of both banks)."""
        return self._request(
            "POST", "/api/memory/search", json={"query": query, "limit": int(limit)}, private=True
        )

    def recall(
        self, query: str, *, types: list[str] | None = None, max_tokens: int = 4096
    ) -> dict[str, Any]:
        """POST /api/memory/recall — token-budgeted consolidated recall (union)."""
        payload: dict[str, Any] = {"query": query, "max_tokens": int(max_tokens)}
        if types is not None:
            payload["types"] = list(types)
        return self._request("POST", "/api/memory/recall", json=payload, private=True)

    def list_items(self, *, limit: int = 50) -> dict[str, Any]:
        """GET /api/memory/list — page through stored items."""
        return self._request("GET", "/api/memory/list", params={"limit": int(limit)}, private=True)

    def delete(self, item_id: str) -> dict[str, Any]:
        """POST /api/memory/delete — remove memory items by id."""
        return self._request("POST", "/api/memory/delete", json={"ids": [item_id]}, private=True)

    # ── transport core ─────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        private: bool = True,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method, path, headers=self._headers(private=private), json=json, params=params
            )
        except httpx.HTTPError as exc:
            raise Hal0MemoryClientError(
                f"hal0-memory transport failure on {method} {path}: {exc}",
            ) from exc

        if response.status_code >= 400:
            raise Hal0MemoryClientError(
                f"hal0-memory {method} {path} returned {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError:
            return {"status": "ok", "raw": response.text}
        return data if isinstance(data, dict) else {"status": "ok", "raw": data}
