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
  - Otherwise → the operator default: the serving slot's profile
    ``enable_thinking`` when it sets one (ADR-0029), else suppression
    (``enable_thinking: false``). The one exception is below.

Grammar exemption (#2020, narrowed by #2713): the installer-seeded
``hal0-brain-sft.jinja`` renders the ``enable_thinking is false`` branch as a
literal ``<think>\\n\\n</think>`` block in the prompt. Combined with a
constrained-decoding grammar (``response_format`` json_object / json_schema,
llama.cpp ``grammar`` / ``json_schema``), llama-server's sampler-chain
construction throws ``Failed to initialize samplers: std::exception`` and the
request hard-400s. So for a request that engages a grammar AND is served under
a template known to collide (``GRAMMAR_COLLIDING_TEMPLATES``, decided by the
caller from the registry row's ``chat_template`` fact — the same fact that picks
``--chat-template-file`` at launch) AND carries no explicit preference from the
caller or the profile, hal0 does not invent the suppression: no kwarg is added
and the template default applies.

The exemption used to cover every grammar request regardless of template. A
Qwen3/Qwen3.5 GGUF template does not collide (#2713: explicit
``enable_thinking=false`` plus ``json_schema`` returns valid JSON), and leaving
such a thinking-by-default model unsuppressed lets the reasoning eat the whole
completion budget — Hindsight's fact extraction returned 0 facts on every
retain. An explicit choice (the caller's, or a profile ``enable_thinking``) is
always applied, even on the colliding template: a loud 400 beats a silent
zero-fact retain. A default of ``true`` is always applied (thinking ON is
grammar-compatible; only the empty-think-block branch collides).

Idempotent and non-mutating.
"""

from __future__ import annotations

from typing import Any

_TOP_LEVEL_KEYS = ("enable_thinking", "thinking")

# ``response_format.type`` values that engage a constrained-decoding grammar.
_GRAMMAR_RESPONSE_FORMATS = frozenset({"json_object", "json_schema"})
# llama.cpp's native top-level grammar levers, accepted on /v1/chat/completions.
_GRAMMAR_KEYS = ("grammar", "json_schema")

#: Chat-template ids (``/api/chat-templates``, the registry row's
#: ``chat_template`` fact) whose ``enable_thinking=false`` branch collides with a
#: constrained-decoding grammar in llama-server sampler init (#2020).
GRAMMAR_COLLIDING_TEMPLATES = frozenset({"hal0-brain-sft"})


def template_collides_with_grammar(chat_template: str | None) -> bool:
    """True when ``chat_template`` is a template id known to hard-400 when its
    empty think-block meets a JSON grammar. ``None`` (the GGUF-embedded
    template) is not known to collide."""
    return bool(chat_template) and chat_template in GRAMMAR_COLLIDING_TEMPLATES


def _wants_structured_output(body: dict[str, Any]) -> bool:
    """True when the request constrains decoding with a grammar.

    Covers the OpenAI ``response_format`` (json_object / json_schema) and
    llama.cpp's native top-level ``grammar`` / ``json_schema``.
    """
    fmt = body.get("response_format")
    if isinstance(fmt, dict) and fmt.get("type") in _GRAMMAR_RESPONSE_FORMATS:
        return True
    return any(body.get(key) not in (None, "", {}) for key in _GRAMMAR_KEYS)


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
    body: dict[str, Any],
    *,
    default_thinking: bool | None = None,
    grammar_collides: bool = False,
) -> dict[str, Any]:
    """Return a copy of ``body`` whose reasoning is steered via
    ``chat_template_kwargs.enable_thinking``.

    A top-level boolean ``enable_thinking`` / ``thinking`` is translated into the
    chat-template kwarg (Qwen3's working lever) and stripped, so a caller asking
    for ``enable_thinking: false`` actually gets no reasoning. Absent any caller
    preference, reasoning follows ``default_thinking`` — the operator's explicit
    default (the serving slot's profile ``enable_thinking``), where ``None``
    means "no operator preference" and falls back to suppression.

    ``grammar_collides`` is True when the serving model's chat template is in
    :data:`GRAMMAR_COLLIDING_TEMPLATES`; only then, and only with no explicit
    preference from caller or operator, is a grammar request left without the
    default suppression (#2020, #2713)."""
    # Caller used the precise lever already — respect it verbatim.
    if _explicit_kwarg_set(body):
        return body
    # Non-bool ``thinking`` (e.g. Anthropic {"type": "enabled"}) is an explicit
    # opt-in we leave untouched.
    if "thinking" in body and not isinstance(body["thinking"], bool):
        return body

    intent = _caller_intent(body)
    if intent is not None:
        want = intent
    elif default_thinking is not None:
        want = default_thinking
    else:
        # #2020: never *invent* a suppression for a grammar-constrained request
        # served under a template whose empty think-block collides with sampler
        # init. Every other grammar request is suppressed like any request
        # (#2713: unsuppressed Qwen3-family reasoning eats the completion budget).
        if grammar_collides and _wants_structured_output(body):
            return body
        want = False

    ctk = {**(body.get("chat_template_kwargs") or {}), "enable_thinking": want}
    # Drop the ineffective top-level booleans; keep everything else.
    new = {k: v for k, v in body.items() if not (k in _TOP_LEVEL_KEYS and isinstance(v, bool))}
    new["chat_template_kwargs"] = ctk
    return new
