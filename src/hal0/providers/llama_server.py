"""llama.cpp HTTP-client surface — NOT a launcher.

This module used to own the llama-server launch machinery (build_env /
start_cmd / container_spec / image_ref and the backend→binary selection
that came with it).  That path was retired in WS-15: launching is owned
end-to-end by :mod:`hal0.providers.container` — model-defaults merging
happens in the container assembler (``_llama_argv_segments`` /
``_llama_launch_plan``) and device existence-filtering in
``ContainerProvider.container_spec``.  ``get_provider("llama-server")``
no longer resolves; slots dispatch through ``ContainerProvider``.

What remains is the thin HTTP-client surface against an already-running
llama-server:

  - :meth:`LlamaServerProvider.health` — Tier-1 readiness probe
    (non-empty ``/v1/models`` PLUS a ``max_tokens=1`` sentinel chat
    completion; PLAN.md §5 Tier 1).
  - :meth:`LlamaServerProvider.infer` — ``/v1/chat/completions``
    passthrough with typed transport errors.
  - :meth:`LlamaServerProvider.parse_metrics` — whitelist parser for the
    llama.cpp ``/metrics`` Prometheus text.

plus the typed errors (:class:`ProviderHealthError`,
:class:`ProviderInferError`) those helpers raise.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from hal0.errors import Hal0Error

log = logging.getLogger(__name__)

# ── Timeouts ───────────────────────────────────────────────────────────────────
# TIER1: Health probe gets its own short timeout, infer gets a long
# read budget so big prompts don't trip on a 5s read.
_HEALTH_TIMEOUT = httpx.Timeout(5.0)
_INFER_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)


class ProviderHealthError(Hal0Error):
    """Provider health probe failed (typed for the error envelope)."""

    code = "slot.not_ready"
    status = 503


class ProviderInferError(Hal0Error):
    """Provider inference call failed."""

    code = "dispatch.upstream_failed"
    status = 502


class LlamaServerProvider:
    """HTTP client for a running llama-server instance.

    Deliberately NOT a :class:`hal0.providers.base.Provider`: it has no
    launch surface (no ``build_env`` / ``start_cmd`` / ``container_spec``)
    and is not registered with :func:`hal0.providers.get_provider`.
    Slot lifecycle is owned by ``ContainerProvider``; this class only
    speaks to a llama-server that something else already started.
    """

    name = "llama-server"

    # ── Health / infer ─────────────────────────────────────────────────────────

    async def health(self, port: int) -> dict[str, Any]:
        """Health probe: /v1/models (non-empty) + sentinel /v1/chat/completions.

        TIER1: PLAN.md §5 Tier 1 — health probe must require non-empty
        /v1/models PLUS a /v1/chat/completions with max_tokens=1 before
        reporting ready. Bare /health and "models endpoint returns 200"
        both lie when the model failed to load.
        """
        models_url = f"http://127.0.0.1:{port}/v1/models"
        chat_url = f"http://127.0.0.1:{port}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
                # 1. /v1/models must return non-empty list.
                models_resp = await client.get(models_url)
                models_resp.raise_for_status()
                models_data = models_resp.json()
                models = models_data.get("data", [])
                if not models:
                    return {
                        "ok": False,
                        "status": "models_endpoint_empty",
                        "detail": "/v1/models returned no entries",
                    }
                model_id = models[0].get("id")

                # 2. Sentinel chat completion with max_tokens=1.  # TIER1
                probe_body = {
                    "model": model_id,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "temperature": 0.0,
                    "stream": False,
                }
                chat_resp = await client.post(chat_url, json=probe_body)
                if chat_resp.status_code != 200:
                    return {
                        "ok": False,
                        "status": f"sentinel_completion_http_{chat_resp.status_code}",
                        "detail": chat_resp.text[:200],
                    }
                # Best-effort: if the body parses and has at least one choice, good.
                try:
                    body = chat_resp.json()
                    if not body.get("choices"):
                        return {
                            "ok": False,
                            "status": "sentinel_completion_no_choices",
                        }
                except Exception:
                    return {"ok": False, "status": "sentinel_completion_unparseable"}
                return {"ok": True, "status": "ready", "model": model_id}
        except httpx.HTTPError as exc:
            return {"ok": False, "status": "http_error", "detail": str(exc)}
        except Exception as exc:
            # TIER1: do not silently swallow — return typed status
            # but keep the call non-raising so callers can decide.
            return {"ok": False, "status": "exception", "detail": str(exc)}

    async def infer(self, port: int, body: dict[str, Any]) -> dict[str, Any]:
        """Passthrough /v1/chat/completions to llama-server."""
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=_INFER_TIMEOUT) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderInferError(
                f"llama-server returned HTTP {exc.response.status_code}",
                details={"port": port, "status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderInferError(
                f"llama-server transport error: {exc}",
                details={"port": port},
            ) from exc

    # ── Metrics (optional helper, kept from haloai) ────────────────────────────

    async def parse_metrics(self, raw_text: str) -> dict[str, Any]:
        """Parse llama.cpp /metrics Prometheus text into a flat dict.

        Whitelisted counters/gauges only.  Lines starting with '#' are
        HELP/TYPE comments and are skipped.
        """
        wanted: dict[str, tuple[str, Any]] = {
            "llamacpp:n_decode_total": ("decode_total", int),
            "llamacpp:n_prompt_tokens_total": ("prompt_tokens_total", int),
            "llamacpp:kv_cache_usage_ratio": ("kv_cache_usage", float),
            "llamacpp:requests_processing": ("requests_processing", int),
            "llamacpp:requests_deferred": ("requests_deferred", int),
        }
        out: dict[str, Any] = {}
        for raw_line in raw_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            metric, raw_val = parts[0], parts[1]
            entry = wanted.get(metric)
            if entry is None:
                continue
            key, caster = entry
            try:
                out[key] = caster(float(raw_val)) if caster is int else caster(raw_val)
            except (ValueError, TypeError):
                continue
        return out
