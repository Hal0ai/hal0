"""Reasoning-suppression policy for lemond-bound chat requests.

lemond (SHA 1bce071) reads TOP-LEVEL ``enable_thinking``/``thinking`` and does its
own ``/no_think`` injection (server.cpp:58-114). We inject top-level
``enable_thinking: false`` unless the caller already expressed a thinking
preference. Idempotent and non-mutating.
"""

from __future__ import annotations

from typing import Any


def _caller_opted(body: dict[str, Any]) -> bool:
    if "enable_thinking" in body or "thinking" in body:
        return True
    ctk = body.get("chat_template_kwargs")
    return isinstance(ctk, dict) and "enable_thinking" in ctk


def apply_thinking_policy(body: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``body`` with ``enable_thinking: false`` injected unless
    the caller already set a thinking control field. A ``no_think`` prompt marker
    is left untouched (passthrough)."""
    if _caller_opted(body):
        return body
    return {**body, "enable_thinking": False}
