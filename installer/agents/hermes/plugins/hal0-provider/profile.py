"""Hermes ``ProviderProfile`` for the local hal0-api — canonical, shipped source.

Design notes:

* ``api_mode = "chat_completions"`` — hal0-api speaks the OpenAI
  chat-completions transport verbatim (``POST /v1/chat/completions``), so
  Hermes drives inference through its own OpenAI-compat client. This profile is
  purely *declarative*: it advertises the endpoint, capabilities, and a live
  model inventory; it does not itself stream tokens.
* ``base_url = "http://127.0.0.1:8080/v1"`` — loopback hal0-api (never a slot
  port). No Bearer on the hal0 LAN (post-ADR-0012 identity model); identity is
  carried by ``X-hal0-Agent`` when we make our own discovery call.
* ``default_aux_model = "hal0/agent"`` — pins compression / vision /
  summarization / web_extract aux calls at the local agent slot. ``hal0/agent``
  is a *virtual* name hal0 resolves per-request via its LiveSlotResolver, so
  retargeting the ``agent`` role on the hal0 side hot-swaps what this resolves
  to with **no gateway restart** (restart-free role aliases).
* ``fetch_models`` performs live ``/v1/models`` discovery against hal0's
  OpenAI surface, sending ``X-hal0-Model-Filter: hal0`` so the ~hundreds of
  passthrough upstream ids are curated down to hal0-owned rows, then drops
  routing aliases (``is_alias``) so the picker shows only real models. It holds
  **no cache** — every call reflects the current live inventory (hot-swap).

Subclasses the upstream ``ProviderProfile`` dataclass (``providers.base``),
which resolves inside the Hermes venv at runtime. A vendored copy of the frozen
pin (``9de9c25f``) keeps the module importable in hal0's own venv for unit
tests. All discovery paths are best-effort: a transport failure falls back to
``fallback_models`` (or ``None``) so a missing hal0-api can't wedge Hermes.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from typing import Any

# ── Vendored-ABC + import-fallback ─────────────────────────────────────────
# The real ``ProviderProfile`` dataclass lives in the Hermes venv. The pinned
# surface (NousResearch/hermes-agent ``9de9c25f``, ``providers/base.py``) is
# vendored verbatim below so this module stays importable in hal0's own venv.
try:  # pragma: no cover — the real import only resolves inside the Hermes venv
    from providers.base import ProviderProfile  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — covered by the hal0-venv fallback path
    try:
        from agent.provider_profile import ProviderProfile  # type: ignore[import-not-found]
    except ImportError:
        from dataclasses import dataclass, field

        @dataclass
        class ProviderProfile:  # type: ignore[no-redef]
            """Vendored frozen copy of Hermes ``providers.base.ProviderProfile``."""

            name: str
            api_mode: str = "chat_completions"
            aliases: tuple = ()

            display_name: str = ""
            description: str = ""
            signup_url: str = ""

            env_vars: tuple = ()
            base_url: str = ""
            models_url: str = ""
            auth_type: str = "api_key"
            supports_health_check: bool = True

            supports_vision: bool = False
            supports_vision_tool_messages: bool = True

            fallback_models: tuple = ()
            hostname: str = ""

            default_headers: dict[str, str] = field(default_factory=dict)

            fixed_temperature: Any = None
            default_max_tokens: int | None = None
            default_aux_model: str = ""

            def get_hostname(self) -> str:
                return self.hostname

            def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return messages

            def build_extra_body(
                self, *, session_id: str | None = None, **context: Any
            ) -> dict[str, Any]:
                return {}

            def build_api_kwargs_extras(
                self, *, reasoning_config: dict | None = None, **context: Any
            ) -> tuple[dict[str, Any], dict[str, Any]]:
                return {}, {}

            def get_max_tokens(self, model: str | None) -> int | None:
                return self.default_max_tokens

            def fetch_models(
                self,
                *,
                api_key: str | None = None,
                base_url: str | None = None,
                timeout: float = 8.0,
            ) -> list[str] | None:
                return None


# ── Defaults / env ─────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_AGENT_ID = "hermes"
DEFAULT_AUX_MODEL = "hal0/agent"


def _resolve_base_url(override: str | None) -> str:
    if override:
        return override.rstrip("/")
    return os.environ.get("HAL0_PROVIDER_BASE", DEFAULT_BASE_URL).rstrip("/")


def _resolve_agent_id() -> str:
    return os.environ.get("HAL0_AGENT_ID", DEFAULT_AGENT_ID)


# ── Routing-alias filter (vendored from hal0.services.models_service) ───────
# hal0 core is NOT importable inside the Hermes venv, so the alias predicate is
# vendored here. Kept in lockstep with ``models_service.is_alias`` /
# ``ALIAS_NAMES`` (pin: rework/descar). ``/v1/models`` advertises these routing
# shortcuts as pseudo-models; they must not enter the provider's inventory.
_ALIAS_NAMES = frozenset(
    {
        "chat",
        "primary",
        "medium",
        "tiny",
        "embed",
        "rerank",
        "npu",
        "coding",
        "coder",
        "whisper",
        "moonshine",
        "vibevoice",
        "kokoro",
        "tts-1",
        "tts-1-hd",
        "bge-reranker",
        "nomic-embed",
    }
)


def is_alias(model_id: str) -> bool:
    """Return True for routing aliases that aren't real models (see hal0 core)."""
    if model_id.startswith("haloai:"):
        return True
    return model_id in _ALIAS_NAMES


# ── SSE streaming helpers — self-contained OpenAI chunk reassembly ──────────
# hal0 speaks the OpenAI chat-completions SSE transport, so Hermes' own client
# consumes the token stream. These helpers exist so the passthrough contract
# (content backfill, gap tolerance, tool-calling / reasoning-content survival)
# is verifiable in hal0's venv without a live gateway.

_DONE_SENTINEL = "[DONE]"


def iter_sse_data(lines: Iterable[bytes | str]) -> Iterator[str]:
    """Yield the ``data:`` payloads of an SSE stream.

    Tolerates gaps: blank keep-alive lines and ``:``-comment heartbeats are
    skipped, non-``data:`` fields are ignored, and the terminal ``[DONE]``
    sentinel stops iteration.
    """
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if payload == _DONE_SENTINEL:
            return
        yield payload


def parse_sse_chunks(lines: Iterable[bytes | str]) -> Iterator[dict[str, Any]]:
    """Parse SSE ``data:`` payloads into chunk dicts, dropping malformed ones."""
    for payload in iter_sse_data(lines):
        try:
            obj = json.loads(payload)
        except (ValueError, TypeError):
            continue  # gap tolerance — a truncated/garbled chunk is skipped
        if isinstance(obj, dict):
            yield obj


def assemble_stream(chunks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Reassemble the final message from OpenAI ``chat.completion.chunk`` deltas.

    Backfills the role (present only on the first delta), concatenates content
    across chunks, preserves ``reasoning_content`` verbatim, and reassembles
    indexed ``tool_calls`` with gap tolerance (missing / non-contiguous /
    repeated indices are folded by index, then emitted in index order).
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    role: str | None = None
    finish_reason: str | None = None

    for chunk in chunks:
        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            if delta.get("role"):
                role = delta["role"]
            if delta.get("content"):
                content_parts.append(delta["content"])
            if delta.get("reasoning_content"):
                reasoning_parts.append(delta["reasoning_content"])
            for tc in delta.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                idx = int(tc.get("index", 0) or 0)
                slot = tool_calls.setdefault(
                    idx,
                    {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if tc.get("id"):
                    slot["id"] = tc["id"]
                if tc.get("type"):
                    slot["type"] = tc["type"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

    message: dict[str, Any] = {"role": role or "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    return {"message": message, "finish_reason": finish_reason}


# ── The profile ────────────────────────────────────────────────────────────


class Hal0ProviderProfile(ProviderProfile):  # type: ignore[misc]
    """``ProviderProfile`` pinning Hermes chat + aux slots at the local hal0-api.

    Instantiated once and registered under ``name = "hal0"``. The base class is
    a dataclass; this subclass keeps a plain ``__init__`` (so Hermes can also
    construct it arg-free for top-level subclass discovery) that seeds the hal0
    defaults through the dataclass constructor, plus an ``_http_client`` seam
    for hermetic ``fetch_models`` tests.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        default_aux_model: str = DEFAULT_AUX_MODEL,
        http_client: Any | None = None,
        include_aliases: bool = False,
    ) -> None:
        resolved_base = _resolve_base_url(base_url)
        super().__init__(  # type: ignore[call-arg]
            name="hal0",
            api_mode="chat_completions",
            display_name="hal0 (local)",
            description="hal0 ProviderProfile — pins chat + aux slots to the local hal0-api.",
            base_url=resolved_base,
            models_url=f"{resolved_base}/models",
            auth_type="none",
            supports_health_check=True,
            supports_vision=True,
            supports_vision_tool_messages=True,
            default_aux_model=default_aux_model,
            hostname="127.0.0.1",
        )
        # Non-dataclass instance state (kept off the frozen field surface).
        self._http_client = http_client
        self._include_aliases = include_aliases

    # ── discovery headers ──────────────────────────────────────────────

    def _discovery_headers(self, api_key: str | None = None) -> dict[str, str]:
        headers = {
            "X-hal0-Model-Filter": "hal0",
            "X-hal0-Agent": _resolve_agent_id(),
            "Accept": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    # ── live model inventory ───────────────────────────────────────────

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Live ``/v1/models`` discovery against hal0, curated to owned models.

        Returns the current inventory of real (non-alias) hal0-owned model ids,
        re-read on every call (no cache → restart-free hot-swap). On any
        transport/parse failure, falls back to ``fallback_models`` or ``None``
        so Hermes degrades to its configured defaults rather than crashing.
        """
        resolved_base = (base_url or self.base_url or DEFAULT_BASE_URL).rstrip("/")
        models_url = self.models_url or f"{resolved_base}/models"
        headers = self._discovery_headers(api_key)

        client = self._http_client
        owns_client = False
        try:
            if client is None:
                import httpx

                client = httpx.Client(timeout=timeout)
                owns_client = True
            try:
                response = client.get(models_url, headers=headers)
            finally:
                if owns_client:
                    client.close()
        except Exception:
            return list(self.fallback_models) or None

        if getattr(response, "status_code", 200) >= 400:
            return list(self.fallback_models) or None

        try:
            payload = response.json()
        except (ValueError, TypeError):
            return list(self.fallback_models) or None

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return list(self.fallback_models) or None

        ids: list[str] = []
        for item in data:
            mid = item.get("id") if isinstance(item, dict) else None
            if not isinstance(mid, str) or not mid:
                continue
            if not self._include_aliases and is_alias(mid):
                continue
            if mid not in ids:
                ids.append(mid)
        return ids

    # ── message passthrough (tool-calling / reasoning / vision) ────────

    def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Pass messages through verbatim.

        hal0 speaks OpenAI chat-completions natively, so assistant
        ``tool_calls``, ``tool``-role results, ``reasoning_content`` and
        multimodal (vision) ``content`` block arrays reach hal0 unmodified — no
        provider-specific reshaping is needed or wanted.
        """
        return messages
