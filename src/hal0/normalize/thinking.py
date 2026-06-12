"""Reasoning-suppression policy for dispatcher-bound chat requests.

We steer reasoning with ``chat_template_kwargs.enable_thinking`` — the Qwen3-family
lever the jinja chat template honors (llama-server runs with ``--jinja``): the
template emits an empty ``<think></think>`` so the model skips reasoning entirely
(when false), or a normal reasoning block (when true).

Why NOT the top-level ``enable_thinking``: the legacy daemon translated a
top-level ``enable_thinking`` into a ``/no_think`` *prompt* injection.
Abliterated / "aggressive" Qwen3 fine-tunes (e.g. the qwen3.6-35b-a3b-uncensored
primary) ignore that soft marker, emit an unbounded reasoning block, and never
produce ``content`` (verified live: empty content, finish_reason=length). The
chat-template kwarg is applied by the template itself, so suppression no longer
depends on the model obeying an instruction.

Policy:
  - Caller set ``chat_template_kwargs.enable_thinking`` → respected verbatim.
  - Caller set a non-bool ``thinking`` (e.g. Anthropic ``{"type": "enabled"}``)
    → explicit opt-in, passed through untouched.
  - Caller set a top-level boolean ``enable_thinking`` / ``thinking`` → translated
    into ``chat_template_kwargs.enable_thinking`` (the lever that actually works)
    and the ineffective top-level field is dropped.
  - Otherwise → default to suppression (``enable_thinking: false``).

Idempotent and non-mutating.
"""

from __future__ import annotations

from typing import Any

_TOP_LEVEL_KEYS = ("enable_thinking", "thinking")


def _explicit_kwarg_set(body: dict[str, Any]) -> bool:
    ctk = body.get("chat_template_kwargs")
    return isinstance(ctk, dict) and "enable_thinking" in ctk


def _caller_intent(body: dict[str, Any]) -> bool | None:
    """The caller's top-level boolean thinking intent, or None if unset."""
    for key in _TOP_LEVEL_KEYS:
        val = body.get(key)
        if isinstance(val, bool):
            return val
    return None


def apply_thinking_policy(
    body: dict[str, Any], *, default_thinking: bool = False
) -> dict[str, Any]:
    """Return a copy of ``body`` whose reasoning is steered via
    ``chat_template_kwargs.enable_thinking``.

    A top-level boolean ``enable_thinking`` / ``thinking`` is translated into the
    chat-template kwarg (Qwen3's working lever) and stripped, so a caller asking
    for ``enable_thinking: false`` actually gets no reasoning. Absent any caller
    preference, reasoning defaults to ``default_thinking`` — the per-slot default
    (slot TOML ``enable_thinking``), falling back to suppression (False)."""
    # Caller used the precise lever already — respect it verbatim.
    if _explicit_kwarg_set(body):
        return body
    # Non-bool ``thinking`` (e.g. Anthropic {"type": "enabled"}) is an explicit
    # opt-in we leave untouched.
    if "thinking" in body and not isinstance(body["thinking"], bool):
        return body

    intent = _caller_intent(body)
    want = default_thinking if intent is None else intent

    ctk = {**(body.get("chat_template_kwargs") or {}), "enable_thinking": want}
    # Drop the ineffective top-level booleans; keep everything else.
    new = {k: v for k, v in body.items() if not (k in _TOP_LEVEL_KEYS and isinstance(v, bool))}
    new["chat_template_kwargs"] = ctk
    return new
