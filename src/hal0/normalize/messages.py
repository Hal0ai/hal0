"""Per-request normalisation helpers that operate on the OpenAI ``messages`` array.

Currently exposes :func:`normalize_system_messages`, which collapses any
``role='system'`` entries into one and hoists the result to position 0. The
Qwen3.6-35B-A3B finetune served by ``hal0-slot-agent`` ships with a Jinja chat
template that hard-raises when ``role='system'`` appears at any index > 0 *or*
when more than one system message is present in the array. The Hermes dashboard
SPA, Open WebUI and LibreChat build the messages array by append, so a stale
session system message can land mid-array — and a third-party client can stack
two system blocks deliberately. We normalise server-side so every client is
correct by construction rather than relying on each surface remembering both
ordering rules.
"""

from __future__ import annotations

from typing import Any

__all__ = ["normalize_system_messages"]


def normalize_system_messages(messages: Any) -> Any:
    """Collapse every ``role='system'`` entry into one and hoist it to position 0.

    Two template-level rules this satisfies:

      1. **Position rule.** ``role='system'`` must appear at index 0 (the
         Qwen3 Jinja emits ``raise_exception('System message must be at the
         beginning')`` from line ~85 of the chat template when this fails).
      2. **Cardinality rule.** At most one ``role='system'`` entry may exist
         in the array; multiple systems are likewise rejected by the same
         template.

    Semantics:

      * Non-list input (or empty list) is returned unchanged.
      * Lists with NO ``role='system'`` entries are returned unchanged — no
        allocation. This is the hot path; the dashboard SPA's history filter
        strips system messages, so user→hal0/agent requests land here and pass
        through without a copy.
      * Otherwise we join every system entry's ``content`` with ``"\\n\\n"``
        (matching the convention OpenAI and Anthropic adopt for stacked-system
        payloads) and emit a single ``{role: 'system', content: joined}`` at
        position 0. Relative order among non-system entries is preserved, so
        ``[user, sys1, asst, sys2]`` becomes
        ``[sys1+sys2-joined, user, asst]``.

    Returns either the input list (same object) or a brand-new list — callers
    may ``is``-compare to detect the in-place case.
    """
    if not isinstance(messages, list) or not messages:
        return messages
    sys_contents: list[str] = [
        m.get("content", "") if isinstance(m, dict) else ""
        for m in messages
        if isinstance(m, dict) and m.get("role") == "system"
    ]
    if not sys_contents:
        return messages
    others = [m for m in messages if not (isinstance(m, dict) and m.get("role") == "system")]
    return [{"role": "system", "content": "\n\n".join(sys_contents)}, *others]
