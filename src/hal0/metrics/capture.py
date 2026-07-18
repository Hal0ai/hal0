"""T1 per-request row construction -- llama.cpp ``timings`` EXACT, never estimated.

Parsing helpers shared by the streaming and non-streaming paths in
:mod:`hal0.metrics.seam`. Kept separate from ``seam.py`` so the parsing
logic (which upstream JSON shapes carry which fields) can be unit tested
without any FastAPI/Request scaffolding.

Source-of-truth precedence for decode/prefill throughput (plan §13.2,
spec-obs-metrics.md Part 4.1):

  1. llama.cpp native ``timings`` block (``predicted_per_second`` /
     ``prompt_per_second``) -- ``tps_source='exact'``.
  2. FLM's ``usage.decoding_speed_tps`` -- ``tps_source='exact'`` (FLM's own
     exact instrumentation, not an hal0-side estimate).
  3. Wall-clock ``completion_tokens / elapsed_seconds`` -- ``tps_source='approx'``,
     a last-resort fallback for upstreams that report neither (comfyui has no
     token throughput concept at all and gets `None` here).
"""

from __future__ import annotations

import json
from typing import Any

#: Client identifier truncation -- never store full PII (spec Part 2 note 5).
_CLIENT_TRUNCATE = 24


def truncate_client(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw[:_CLIENT_TRUNCATE]


def parse_json_object(body: bytes | str) -> dict[str, Any] | None:
    """Best-effort JSON object parse. Returns None on any failure/non-dict."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def extract_timings_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull llama.cpp ``timings`` fields out of a parsed response payload.

    llama-server's OpenAI-compatible responses optionally carry a
    top-level ``timings`` object (native ``/completion`` always does;
    ``/v1/chat/completions`` carries it when built with timing support
    enabled). Returns an empty dict when absent -- callers treat that as
    "fall back to approx".
    """
    timings = payload.get("timings")
    if not isinstance(timings, dict):
        return {}
    out: dict[str, Any] = {}
    prefill_tps = timings.get("prompt_per_second")
    decode_tps = timings.get("predicted_per_second")
    prompt_ms = timings.get("prompt_ms")
    cache_n = timings.get("cache_n")
    prompt_n = timings.get("prompt_n")
    predicted_n = timings.get("predicted_n")
    draft_n = timings.get("draft_n")
    draft_n_accepted = timings.get("draft_n_accepted")

    if isinstance(prefill_tps, (int, float)):
        out["prefill_tps"] = float(prefill_tps)
    if isinstance(decode_tps, (int, float)):
        out["decode_tps"] = float(decode_tps)
    if isinstance(prompt_ms, (int, float)):
        out["ttft_ms"] = float(prompt_ms)
    if isinstance(cache_n, (int, float)):
        out["cache_hit"] = 1 if cache_n > 0 else 0
    if isinstance(prompt_n, (int, float)) and isinstance(predicted_n, (int, float)):
        out["ctx_used"] = int(prompt_n) + int(predicted_n)
    if isinstance(draft_n, (int, float)) and draft_n and isinstance(draft_n_accepted, (int, float)):
        out["spec_accept_rate"] = float(draft_n_accepted) / float(draft_n)
    if out:
        out["tps_source"] = "exact"
    return out


def extract_usage_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull ``usage``/finish_reason fields (prompt/completion tokens, FLM exact tps)."""
    out: dict[str, Any] = {}
    usage = payload.get("usage")
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if isinstance(prompt_tokens, (int, float)):
            out["prompt_tokens"] = int(prompt_tokens)
        if isinstance(completion_tokens, (int, float)):
            out["completion_tokens"] = int(completion_tokens)
        flm_tps = usage.get("decoding_speed_tps")
        if isinstance(flm_tps, (int, float)):
            out["decode_tps"] = float(flm_tps)
            out["tps_source"] = "exact"
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            reason = first.get("finish_reason")
            if isinstance(reason, str):
                out["stop_reason"] = reason
    return out


def build_request_metric_row(
    *,
    ts: str,
    request_id: str,
    slot_id: str | None,
    model_id: str | None,
    queue_ms: float | None,
    total_ms: float | None,
    ok: bool,
    error_code: str | None = None,
    client: str | None = None,
    ttft_ms: float | None = None,
    payload: dict[str, Any] | None = None,
    fallback_completion_tokens: int | None = None,
    fallback_elapsed_s: float | None = None,
) -> dict[str, Any]:
    """Assemble one ``request_metric`` row, preferring exact llama/FLM fields.

    ``payload`` is the parsed JSON body (non-streaming) or the last parsed
    SSE data object (streaming); ``fallback_*`` covers the delta-counted
    approximation used when neither timings nor a FLM ``usage`` block are
    present in the payload.
    """
    row: dict[str, Any] = {
        "ts": ts,
        "request_id": request_id,
        "slot_id": slot_id,
        "model_id": model_id,
        "runner": None,
        "device": None,
        "modality": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "ctx_used": None,
        "ttft_ms": ttft_ms,
        "prefill_tps": None,
        "decode_tps": None,
        "tps_source": None,
        "queue_ms": queue_ms,
        "total_ms": total_ms,
        "cache_hit": None,
        "spec_accept_rate": None,
        "stop_reason": None,
        "ok": 1 if ok else 0,
        "error_code": error_code,
        "client": client,
    }

    if payload is not None:
        row.update(extract_usage_fields(payload))
        timings = extract_timings_fields(payload)
        row.update(timings)

    if row.get("decode_tps") is None and fallback_completion_tokens and fallback_elapsed_s:
        if fallback_elapsed_s > 0:
            row["decode_tps"] = fallback_completion_tokens / fallback_elapsed_s
            row["tps_source"] = "approx"
        if row.get("completion_tokens") is None:
            row["completion_tokens"] = fallback_completion_tokens

    return row


__all__ = [
    "build_request_metric_row",
    "extract_timings_fields",
    "extract_usage_fields",
    "parse_json_object",
    "truncate_client",
]
