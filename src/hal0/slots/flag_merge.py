"""Merge a model's default CLI flags with a slot's extra-args override.

Used by the llama-server launcher arg-build path (and any future
backend that takes a freeform CLI passthrough) to combine

    model.defaults.extra_args   — registry-level defaults
    slot.server.extra_args      — per-slot override

into a single argv-ready string with collision rules:

  * The slot string wins on any ``--flag (value?)`` pair it contains —
    matching flag/value tuples are stripped from the model defaults.
  * A small set of *append-list* flags (``--lora``, ``--draft-model``,
    ``--override-kv``) skip dedup: llama-server accepts repeated
    occurrences, and silently dropping one of them would surprise the
    operator.
  * Whitespace-only / None inputs are no-ops.
  * Malformed input (e.g. unbalanced quotes that ``shlex.split`` rejects)
    falls back to a dumb concat with a structured warning log — the
    launcher still gets *something* runnable instead of a hard crash.

See docs/internal/models-slots-impl-plan.md §A3.
"""

from __future__ import annotations

import logging
import shlex
from collections.abc import Iterable

log = logging.getLogger(__name__)

# Flags whose semantics are "may be repeated".  llama-server treats each
# occurrence additively, so deduping by flag-name would silently drop
# the user's intent.
_APPEND_LIST_FLAGS: frozenset[str] = frozenset(
    {
        "--lora",
        "--draft-model",
        "--override-kv",
    }
)


def _tokenise(raw: str) -> list[str]:
    """Whitespace-tokenise with shell-quote awareness.

    Raises ``ValueError`` on unbalanced quotes; callers handle the
    malformed-input path.
    """
    return shlex.split(raw, posix=True)


def _split_flag_pairs(tokens: list[str]) -> list[tuple[str, list[str]]]:
    """Group a flat token list into ``(flag, [value?])`` tuples.

    A token starting with ``--`` opens a new flag; the next token is
    its value iff it does not itself start with ``--``.  Tokens that
    appear before any ``--`` (or look like bare positional values) are
    grouped under an empty-string "flag" so the caller can decide what
    to do with them (currently: preserve, no dedup).
    """
    pairs: list[tuple[str, list[str]]] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.startswith("--"):
            value: list[str] = []
            # Lookahead for a single value that isn't itself a flag.
            if i + 1 < n and not tokens[i + 1].startswith("--"):
                value.append(tokens[i + 1])
                i += 2
            else:
                i += 1
            pairs.append((tok, value))
        else:
            # Stray positional — keep it under an empty flag-name so
            # dedup leaves it alone.
            pairs.append(("", [tok]))
            i += 1
    return pairs


def _flatten_pairs(pairs: Iterable[tuple[str, list[str]]]) -> list[str]:
    """Inverse of ``_split_flag_pairs``."""
    out: list[str] = []
    for flag, values in pairs:
        if flag:
            out.append(flag)
        out.extend(values)
    return out


def _dumb_concat(model_defaults: str | None, slot_extra: str | None) -> str:
    """Whitespace-glue fallback used on tokenisation failure."""
    parts = [s for s in (model_defaults, slot_extra) if s and s.strip()]
    return " ".join(p.strip() for p in parts)


def merge_flags(model_defaults: str | None, slot_extra: str | None) -> str:
    """Combine model-default and slot-override CLI flag strings.

    Args:
        model_defaults: ``Model.defaults.extra_args`` from the registry,
            or ``None`` if the model has no defaults.
        slot_extra: ``SlotConfig.server.extra_args``, or ``None`` if
            the slot hasn't overridden anything.

    Returns:
        A single trimmed string with the slot's flags appended *after*
        any non-conflicting model defaults.  Empty inputs collapse to
        ``""``.
    """
    if not (model_defaults and model_defaults.strip()) and not (slot_extra and slot_extra.strip()):
        return ""

    if not (slot_extra and slot_extra.strip()):
        # Slot has nothing to say — return model defaults as-is (trimmed).
        # We still validate parseability so a malformed model string
        # gets caught here too rather than later at exec time.
        assert model_defaults is not None  # for type-checker
        try:
            _tokenise(model_defaults)
        except ValueError as exc:
            log.warning(
                "flag_merge: malformed model_defaults; falling back to dumb concat",
                extra={
                    "event": "flag_merge.malformed_input",
                    "side": "model_defaults",
                    "reason": str(exc),
                },
            )
            return _dumb_concat(model_defaults, slot_extra)
        return model_defaults.strip()

    if not (model_defaults and model_defaults.strip()):
        assert slot_extra is not None
        try:
            _tokenise(slot_extra)
        except ValueError as exc:
            log.warning(
                "flag_merge: malformed slot_extra; falling back to dumb concat",
                extra={
                    "event": "flag_merge.malformed_input",
                    "side": "slot_extra",
                    "reason": str(exc),
                },
            )
            return _dumb_concat(model_defaults, slot_extra)
        return slot_extra.strip()

    # Both sides non-empty: do the real merge.
    try:
        model_tokens = _tokenise(model_defaults)
        slot_tokens = _tokenise(slot_extra)
    except ValueError as exc:
        log.warning(
            "flag_merge: unbalanced quotes; falling back to dumb concat",
            extra={
                "event": "flag_merge.malformed_input",
                "reason": str(exc),
            },
        )
        return _dumb_concat(model_defaults, slot_extra)

    model_pairs = _split_flag_pairs(model_tokens)
    slot_pairs = _split_flag_pairs(slot_tokens)

    # Build the dedup set: every flag the slot uses *except* append-list
    # flags.  Empty-string "flags" (stray positionals) are never deduped.
    slot_flag_names = {flag for flag, _ in slot_pairs if flag and flag not in _APPEND_LIST_FLAGS}

    cleaned_model_pairs = [
        (flag, values) for flag, values in model_pairs if flag not in slot_flag_names
    ]

    cleaned_model_tokens = _flatten_pairs(cleaned_model_pairs)
    cleaned_model_str = " ".join(cleaned_model_tokens).strip()
    slot_str = " ".join(_flatten_pairs(slot_pairs)).strip()

    if cleaned_model_str and slot_str:
        return f"{cleaned_model_str} {slot_str}"
    return cleaned_model_str or slot_str


__all__ = ["merge_flags"]
