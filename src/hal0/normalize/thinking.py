"""Reasoning-suppression policy for lemond-bound chat requests.

We inject ``chat_template_kwargs.enable_thinking = false`` unless the caller
already expressed a thinking preference. This is the Qwen3-family lever the
jinja chat template honors (llama-server runs with ``--jinja``): the template
emits an empty ``<think></think>`` so the model skips reasoning entirely.

Why NOT the top-level ``enable_thinking``: lemond translates a top-level
``enable_thinking: false`` into a ``/no_think`` *prompt* injection
(server.cpp:58-114). Abliterated / "aggressive" Qwen3 fine-tunes (e.g. the
qwen3.6-35b-a3b-uncensored primary) ignore that soft marker, emit an unbounded
reasoning block, and never produce ``content`` (verified live: empty content,
finish_reason=length). The chat-template kwarg is applied by the template
itself, so suppression no longer depends on the model obeying an instruction.

Idempotent and non-mutating.
"""

from __future__ import annotations

from typing import Any


def _caller_opted(body: dict[str, Any]) -> bool:
    if "enable_thinking" in body or "thinking" in body:
        return True
    ctk = body.get("chat_template_kwargs")
    return isinstance(ctk, dict) and "enable_thinking" in ctk


def apply_thinking_policy(body: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``body`` with ``chat_template_kwargs.enable_thinking: false``
    injected unless the caller already set a thinking control field. Any other
    ``chat_template_kwargs`` entries are preserved. A ``no_think`` prompt marker
    is left untouched (passthrough)."""
    if _caller_opted(body):
        return body
    ctk = {**(body.get("chat_template_kwargs") or {}), "enable_thinking": False}
    return {**body, "chat_template_kwargs": ctk}
