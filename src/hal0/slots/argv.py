"""normalize_argv — the single dedup/last-wins pass for llama-server argv.

The launch argv for a slot is assembled from several sources that historically
just concatenate (``container._llama_launch_plan``): the structural prefix
(``--host``/``--port``/``--model``/``--alias``/``--ctx-size``), the profile's
bench-tuned ``flags``, a resolved ``--chat-template-file``, and the slot's
``[server].extra_args``. Nothing dedups *across* those segments, so a flag the
profile sets and the slot also overrides (``-b``, ``-ctk``, ``-ngl``,
``--jinja`` …) is emitted twice. llama-server silently takes the **last**
occurrence, so the command still runs — but the rendered argv is a confusing,
unauditable soup of conflicting duplicates (the live ``agent`` slot ships
``-b`` x2, ``-ngl 999`` then ``-ngl 99``, ``--jinja`` x2).

This module collapses that to one source of truth at the argv layer:

  * **Dedup by canonical key, keep the LAST occurrence.** Because llama-server
    already used the last value, keeping it is *effective-value-preserving* —
    the slot launches identically, the argv is just clean. This is the property
    the golden-parity tests pin.
  * **Canonicalize for the key only; emit the original spelling.** ``-b`` and
    ``--batch-size`` share a key (so they dedup against each other), but the
    surviving token keeps whatever spelling the winning source used — we never
    rewrite ``-b`` into ``--batch-size`` behind the operator's back.
  * **Append-list flags are never deduped** (``--lora``/``--draft-model``/
    ``--override-kv``): llama-server treats repeats additively.
  * **Order is preserved** (each surviving flag stays at its last position),
    so the structural prefix stays first and the diff vs today is exactly "the
    earlier duplicates removed".

It is a pure function over a token list, so every assembly path
(``_llama_launch_plan``, the resolved-command preview) can route through it
without restructuring how they build the list.
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Short→long canonicalisation. Used ONLY to compute the dedup key; the emitted
# token keeps its original spelling. Seeded from the flags hal0 profiles +
# slots actually use on llama-server; unknown flags fall through unaliased and
# dedup against their own literal spelling (still correct, just less aggressive).
FLAG_ALIASES: dict[str, str] = {
    "-b": "--batch-size",
    "-ub": "--ubatch-size",
    "-ngl": "--n-gpu-layers",
    "-ctk": "--cache-type-k",
    "-ctv": "--cache-type-v",
    "-t": "--threads",
    "-tb": "--threads-batch",
    "-fa": "--flash-attn",
    "-dev": "--device",
    "-sm": "--split-mode",
    "-c": "--ctx-size",
    "-ts": "--tensor-split",
    "-mg": "--main-gpu",
    "-np": "--parallel",
    "-ngld": "--n-gpu-layers-draft",
}

# Flags whose semantics are "may be repeated" — never deduped. Keyed by the
# canonical (long) spelling.
APPEND_FLAGS: frozenset[str] = frozenset(
    {
        "--lora",
        "--draft-model",
        "--override-kv",
    }
)


@dataclass(frozen=True)
class NormalizedArgv:
    """Result of :func:`normalize_argv`.

    ``argv`` is the deduped token list. ``removed`` is the count of duplicate
    tokens dropped (for logging / the dashboard "cleaned N duplicate flags"
    affordance). ``winners`` maps each canonical flag key to the spelling that
    survived — the seed of a future provenance view.
    """

    argv: list[str]
    removed: int
    winners: dict[str, str]


def _is_flag(tok: str) -> bool:
    """True for ``--long`` and ``-x``/``-ngl`` short flags; False for values.

    A leading ``-`` followed by a letter is a flag; a leading ``-`` followed by
    a digit/dot is a negative number (a value, e.g. ``-1`` for ``-ngl -1``).
    """
    if tok.startswith("--"):
        return len(tok) > 2
    return len(tok) > 1 and tok[0] == "-" and tok[1].isalpha()


def _canon(flag: str) -> str:
    return FLAG_ALIASES.get(flag, flag)


@dataclass(frozen=True)
class _Pair:
    canon: str | None  # None => bare positional (never deduped)
    flag: str | None
    values: tuple[str, ...]
    source: str = ""  # which input segment this token came from (provenance)


def _split_pairs(tokens: list[str], sources: list[str] | None = None) -> list[_Pair]:
    """Group a flat token list into ``(flag, value?)`` pairs, order preserved.

    A flag consumes the following token as its value iff that token is not
    itself a flag (so ``--jinja --metrics`` are two valueless bools, while
    ``-b 8192`` and ``--temp 0`` carry a value). Bare positionals are kept
    under ``canon=None`` so dedup never touches them.

    ``sources`` is an optional parallel list labelling each token's origin
    segment; a pair takes the source of its flag (or positional) token.
    """
    pairs: list[_Pair] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        src = sources[i] if sources is not None else ""
        if _is_flag(tok):
            if i + 1 < n and not _is_flag(tokens[i + 1]):
                pairs.append(_Pair(_canon(tok), tok, (tokens[i + 1],), src))
                i += 2
            else:
                pairs.append(_Pair(_canon(tok), tok, (), src))
                i += 1
        else:
            pairs.append(_Pair(None, None, (tok,), src))
            i += 1
    return pairs


def _dedup(pairs: list[_Pair]) -> tuple[list[str], int, dict[str, _Pair]]:
    """Last-wins dedup over ``pairs``. Shared core of the two public entrypoints.

    Returns ``(argv, removed, winners)`` where ``winners`` maps each surviving
    canonical flag key to the winning :class:`_Pair` (in emission order, so the
    dict iteration order matches the flag order in ``argv``).
    """
    last_index: dict[str, int] = {}
    for idx, p in enumerate(pairs):
        if p.canon is not None and p.canon not in APPEND_FLAGS:
            last_index[p.canon] = idx

    out: list[str] = []
    winners: dict[str, _Pair] = {}
    removed = 0
    for idx, p in enumerate(pairs):
        if p.canon is None:  # positional — never deduped
            out.extend(p.values)
            continue
        if p.canon in APPEND_FLAGS:  # repeatable — kept verbatim
            assert p.flag is not None
            out.append(p.flag)
            out.extend(p.values)
            continue
        if last_index[p.canon] == idx:
            assert p.flag is not None
            out.append(p.flag)
            out.extend(p.values)
            winners[p.canon] = p
        else:
            removed += 1  # earlier duplicate, dropped in favour of a later one
    return out, removed, winners


def normalize_argv(tokens: list[str]) -> NormalizedArgv:
    """Dedup ``tokens`` keeping the last occurrence of each scalar/bool flag.

    Effective-value-preserving: the surviving value for every flag equals the
    last value in ``tokens`` (what llama-server used anyway). Append-list flags
    and bare positionals are kept verbatim, in order.
    """
    out, removed, winners = _dedup(_split_pairs(tokens))
    return NormalizedArgv(
        argv=out, removed=removed, winners={k: p.flag for k, p in winners.items() if p.flag}
    )


@dataclass(frozen=True)
class FlagProvenance:
    """One surviving flag and the input segment it was resolved from."""

    flag: str
    value: str | None
    source: str


@dataclass(frozen=True)
class ResolvedArgv:
    """Deduped argv plus per-flag provenance — the auditable resolution.

    ``provenance`` lists each surviving scalar/bool flag (append-list flags are
    omitted — they aren't deduped, so "which source won" is meaningless) with
    the segment that won it, in argv order.
    """

    argv: list[str]
    provenance: list[FlagProvenance]
    removed: int


def resolve_argv(segments: list[tuple[str, list[str]]]) -> ResolvedArgv:
    """Resolve ordered ``(source_label, tokens)`` segments into one deduped argv.

    Same last-wins semantics as :func:`normalize_argv`, but each segment's
    tokens carry its label, so the result records which source set each flag's
    final value (e.g. ``-b`` from ``profile`` vs ``--jinja`` from
    ``extra_args``). Segments are concatenated in order before dedup, so a later
    segment overrides an earlier one — pass them lowest-precedence first.
    """
    tokens: list[str] = []
    sources: list[str] = []
    for label, seg in segments:
        for tok in seg:
            tokens.append(tok)
            sources.append(label)

    out, removed, winners = _dedup(_split_pairs(tokens, sources))
    provenance = [
        FlagProvenance(
            flag=p.flag,  # type: ignore[arg-type]  # winners only holds real flags
            value=(p.values[0] if p.values else None),
            source=p.source,
        )
        for p in winners.values()
    ]
    return ResolvedArgv(argv=out, provenance=provenance, removed=removed)


# ── merge_flags: string ⊕ string, last-wins per-flag ─────────────────────────
#
# Folded in from the retired ``hal0.slots.flag_merge`` module so it shares this
# module's tokenizer + short/long alias table (``-b`` now dedups against
# ``--batch-size``, which the old ``--``-only tokenizer could not do). Combines
#
#     model_defaults (registry Model.defaults.extra_args)
#     slot_extra     (SlotConfig.server.extra_args)
#
# into one argv-ready string: the slot string wins on any colliding flag, and
# APPEND_FLAGS (--lora / --draft-model / --override-kv) are kept additively.


def merge_flags(model_defaults: str | None, slot_extra: str | None) -> str:
    """Combine model-default and slot-override CLI flag strings (last-wins).

    Args:
        model_defaults: ``Model.defaults.extra_args`` from the registry, or
            ``None`` if the model has no defaults.
        slot_extra: ``SlotConfig.server.extra_args``, or ``None``.

    Returns:
        A single trimmed string with the slot's flags winning any collision
        with a model default (short/long aliases collapse against each other;
        APPEND_FLAGS are kept). Empty inputs collapse to ``""``.

        On malformed input (unbalanced quotes that ``shlex.split`` rejects)
        this falls back to a whitespace concat with a structured warning, so
        the launcher still gets *something* runnable instead of crashing.
    """
    left = model_defaults if (model_defaults and model_defaults.strip()) else ""
    right = slot_extra if (slot_extra and slot_extra.strip()) else ""
    if not left and not right:
        return ""

    try:
        left_tokens = shlex.split(left) if left else []
        right_tokens = shlex.split(right) if right else []
    except ValueError as exc:
        log.warning(
            "flag_merge: unbalanced quotes; falling back to dumb concat",
            extra={"event": "flag_merge.malformed_input", "reason": str(exc)},
        )
        return " ".join(p.strip() for p in (left, right) if p.strip())

    # model defaults first, slot override last → resolve keeps the slot's value
    # on any collision (last-wins). Re-quote each surviving token so a value
    # with embedded spaces survives the launcher's re-``shlex.split``.
    merged = normalize_argv(left_tokens + right_tokens).argv
    return " ".join(shlex.quote(tok) for tok in merged)


__all__ = [
    "APPEND_FLAGS",
    "FLAG_ALIASES",
    "FlagProvenance",
    "NormalizedArgv",
    "ResolvedArgv",
    "merge_flags",
    "normalize_argv",
    "resolve_argv",
]
