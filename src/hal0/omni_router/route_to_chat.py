"""``route_to_chat`` dispatch — plan §7.4.

One-shot delegation: the calling LLM hands a single self-contained
message to another chat slot and receives the assistant content as
the tool_result. The target's persona is untouched — the dispatcher
prepends the target's own ``system_prompt`` (if configured), then a
user message built from the caller's ``prompt`` (plus optional
``context``). The conversation history is NOT replayed.

Guardrails (all four are mandatory per plan §7.4):

  1. ``target`` must be an enabled ``type=llm`` slot.
  2. ``target`` must not equal the caller (no self-delegation).
  3. Source and target must not BOTH be ``device=npu`` (would force
     an FLM swap mid-request; the FLM trio context is exclusive).
  4. Nested delegation is blocked at ``depth=1``. The contextvar
     :data:`DELEGATION_DEPTH` tracks recursion.

On any guardrail failure the dispatcher returns a tool_result envelope
``{"error": "<message>"}``. The calling LLM is expected to surface
the error to the user; hal0 does not raise.
"""

from __future__ import annotations

import contextvars
from typing import Any

# Recursion guard. A contextvar (not a module-global) so two concurrent
# requests can't shadow each other's depth, and so a fan-out coroutine
# stays in the parent's counted depth.
DELEGATION_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "hal0_omni_router_delegation_depth", default=0
)

# Plan §7.4: depth=1 means one level of delegation is permitted. The
# initial call enters with depth=0; the FIRST route_to_chat increments
# to 1; any nested call would see depth >= 1 and refuse.
MAX_DELEGATION_DEPTH = 1


def _slot_by_name(configs: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((c for c in configs if c.get("name") == name), None)


def _is_chat_slot(cfg: dict[str, Any]) -> bool:
    """Chat slots are ``type=llm`` and enabled. NPU exclusivity is
    enforced separately."""
    return cfg.get("type") == "llm" and bool(cfg.get("enabled", True))


def _model_of(cfg: dict[str, Any]) -> str:
    """Pull the slot's default model name out of its config dict.

    Mirrors :func:`hal0.slots.manager._model_default` 's shape: the
    chat completion call uses the slot's ``model.default`` as the
    body's ``model`` field. If the slot has no default configured we
    fall back to the slot name (Lemonade will 404 — surfaced as a
    structured tool_result error rather than crashing the loop).
    """
    model = cfg.get("model") or {}
    if isinstance(model, dict):
        default = model.get("default", "")
        if isinstance(default, str) and default:
            return default
    return str(cfg.get("name", ""))


def _system_prompt_of(cfg: dict[str, Any]) -> str:
    """Pull a slot's configured system_prompt — empty string if absent.

    The ``system_prompt`` field is not yet a typed SlotConfig field
    (PR-18 adds the persona dropdown that authors it). For PR-16 we
    read it permissively from the slot's extra dict — slots without
    one delegate with no system message, which matches plan §7.4's
    "persona stays unchanged" semantics for an unconfigured persona.
    """
    raw = cfg.get("system_prompt")
    if isinstance(raw, str):
        return raw
    # Fall back to a nested ``extra`` namespace if a future config
    # surfaces it there. Treat any non-string as absent.
    extra = cfg.get("extra")
    if isinstance(extra, dict):
        raw = extra.get("system_prompt")
        if isinstance(raw, str):
            return raw
    return ""


def build_delegation_messages(
    target_cfg: dict[str, Any],
    *,
    prompt: str,
    context: str | None,
) -> list[dict[str, str]]:
    """Build the messages array for a delegation chat request.

    Per plan §7.4 step 2:
      ``[{system: target.system_prompt}, {user: prompt + ("\\n\\nContext:\\n" + context)?}]``.

    The system message is omitted entirely when the target slot has
    no ``system_prompt`` (rather than sending an empty-string system,
    which some backends treat as "blank persona").
    """
    messages: list[dict[str, str]] = []
    system = _system_prompt_of(target_cfg)
    if system:
        messages.append({"role": "system", "content": system})
    user_content = prompt
    if context:
        user_content = f"{prompt}\n\nContext:\n{context}"
    messages.append({"role": "user", "content": user_content})
    return messages


def validate_delegation(
    configs: list[dict[str, Any]],
    *,
    caller_slot_name: str,
    target: str,
    current_depth: int,
) -> str | None:
    """Run all four guardrails. Returns an error string on rejection,
    or ``None`` if the delegation may proceed.

    Pure-sync so unit tests can exhaustively exercise the matrix
    without touching the contextvar.
    """
    # Guardrail 4: depth limit.
    if current_depth >= MAX_DELEGATION_DEPTH:
        return f"route_to_chat refused: maximum delegation depth ({MAX_DELEGATION_DEPTH}) reached"

    # Guardrail 1: target must exist as an enabled chat slot.
    target_cfg = _slot_by_name(configs, target)
    if target_cfg is None or not _is_chat_slot(target_cfg):
        return f"slot '{target}' not enabled"

    # Guardrail 2: no self-delegation.
    if target == caller_slot_name:
        return f"route_to_chat refused: cannot delegate to self ('{target}')"

    # Guardrail 3: no NPU↔NPU delegation.
    caller_cfg = _slot_by_name(configs, caller_slot_name)
    if (
        caller_cfg is not None
        and caller_cfg.get("device") == "npu"
        and target_cfg.get("device") == "npu"
    ):
        return (
            "route_to_chat refused: NPU↔NPU delegation would force "
            "an FLM swap; pick a non-NPU target"
        )

    return None


__all__ = [
    "DELEGATION_DEPTH",
    "MAX_DELEGATION_DEPTH",
    "build_delegation_messages",
    "validate_delegation",
]
