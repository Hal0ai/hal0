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

from hal0.errors import BadRequest

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
    "-kvu": "--kv-unified",
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

# ── Managed-args denylist (§21.7) ─────────────────────────────────────────────
#
# Flags hal0 itself computes and owns for every slot — the structural prefix
# (``--host``/``--port``/``--model``/``--alias``/``--ctx-size`` in
# ``container._llama_argv_segments``' ``base`` segment) plus the schema-driven
# ``[model].n_gpu_layers`` override (its ``slot_overrides`` segment). A slot's
# free-form ``[server].extra_args`` — or a future request-level
# ``llamacpp_args`` — must never be able to silently clobber these: doing so
# can redirect the model file, rebind the listen address/port, or desync the
# advertised alias from the OpenAI-shim's dispatch table. Keyed by canonical
# (long) spelling; ``_canon()`` maps short spellings (``-c``, ``-ngl``) onto
# the same keys, so both forms are caught.
MANAGED_ARGS_DENYLIST: frozenset[str] = frozenset(
    {
        "--model",
        "--ctx-size",
        "--host",
        "--port",
        "--n-gpu-layers",
        "--alias",
    }
)

# ── Slot hardware-flag partition (spec-hw-slot-ownership §5) ──────────────────
#
# The grid-owned hardware flags: the physical/placement knobs the SLOT owns as
# typed fields (``device`` → ``-dev`` + GPU-visibility, ``n_gpu_layers`` →
# ``-ngl``, ``threads`` → ``--threads``). This is the single source of truth for
# the partition guard: a model / profile freeform-flag save HARD-REJECTS any
# flag in this set with a "belongs on the slot" message (symmetric to the §21.7
# ``MANAGED_ARGS_DENYLIST`` hard-reject). ``-dev`` is also enum-owned (device).
#
# Both spellings are listed so a direct membership test (server reject OR the
# client-side model-drawer mirror) catches either form without first routing the
# token through ``_canon()``; ``_canon()`` also maps every short form here onto
# its long partner, so a canonicalising caller matches the long entry too.
SLOT_HARDWARE_FLAGS: frozenset[str] = frozenset(
    {
        "--n-gpu-layers",
        "-ngl",
        "--device",
        "-dev",
        "--threads",
        "-t",
    }
)

# Segment labels whose tokens are free-form / caller-supplied rather than
# hal0-computed, and therefore must be screened against
# ``MANAGED_ARGS_DENYLIST`` before they're merged in:
#   * ``extra_args``       — a slot's ``[server].extra_args``.
#   * ``model_extra_args`` — a model's ``defaults.extra_args`` (the registry
#     row's free-form launcher flags; NOT the schema-computed ``-ngl`` from
#     ``defaults.n_gpu_layers``, which rides the trusted ``model_defaults``
#     segment — see ``container._llama_argv_segments``).
#   * ``slot_profile``     — a DIVERGENT slot profile's flag text (#1636):
#     emitted only when ``slot.profile`` differs from the model's stamped
#     provenance (``defaults.profile``). Profile saves already screen these
#     flags, but the profile file is operator-editable on disk, so the launch
#     merge re-screens them like any other free-form source.
# A future request-level ``llamacpp_args`` segment (§7.1a 5-tier precedence
# rewrite) adds its own label here so it rides the same guard.
UNTRUSTED_SEGMENT_LABELS: frozenset[str] = frozenset(
    {"extra_args", "model_extra_args", "slot_profile"}
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


def _deny_managed_flags(tokens: list[str], *, segment: str) -> None:
    """Raise :class:`~hal0.errors.BadRequest` if ``tokens`` set a managed flag.

    Called on an untrusted segment's raw tokens (e.g. ``[server].extra_args``)
    before they're merged into the argv, so a slot config that tries to
    override ``--model``/``--host``/``--port``/``--ctx-size``/``-ngl``/
    ``--alias`` fails loudly at load time instead of quietly launching with a
    clobbered structural flag.
    """
    offenders: list[str] = []
    for pair in _split_pairs(tokens):
        if pair.canon is not None and pair.canon in MANAGED_ARGS_DENYLIST:
            assert pair.flag is not None
            offenders.append(pair.flag)
    if offenders:
        flags = ", ".join(repr(f) for f in offenders)
        raise BadRequest(
            f"{segment} may not set managed flag(s) {flags}; hal0 computes "
            "these from the slot/model configuration and they cannot be "
            "overridden via extra_args",
            code="slot.managed_arg_denied",
            details={"segment": segment, "flags": offenders},
        )


def strip_managed_flags(
    tokens: list[str], *, denylist: frozenset[str] = MANAGED_ARGS_DENYLIST
) -> tuple[list[str], list[str]]:
    """Drop every denylisted flag (and its value) from ``tokens``.

    The healing counterpart of :func:`_deny_managed_flags`: instead of
    rejecting, it removes each offending flag together with its value so
    already-persisted state (custom profiles / model ``defaults.extra_args``
    stamped before the guards existed) can be sanitized in place. Matching is
    token-exact via ``_canon()`` — ``-c`` maps onto ``--ctx-size`` but
    ``--threads-batch``/``--model_path`` never collide with ``--threads``/
    ``--model``. Pass ``denylist`` to screen a different flag set (e.g.
    ``MANAGED_ARGS_DENYLIST | SLOT_HARDWARE_FLAGS``).

    Returns:
        ``(clean_tokens, removed_flags)`` — the surviving tokens in order, and
        the offending flags by their original spelling (empty = already clean).
    """
    clean: list[str] = []
    removed: list[str] = []
    for pair in _split_pairs(tokens):
        if pair.canon is not None and pair.canon in denylist:
            assert pair.flag is not None
            removed.append(pair.flag)
            continue
        if pair.flag is not None:
            clean.append(pair.flag)
        clean.extend(pair.values)
    return clean, removed


def _deny_slot_hardware_flags(tokens: list[str], *, segment: str) -> None:
    """Raise :class:`~hal0.errors.BadRequest` if ``tokens`` set a slot-hardware flag.

    The partition guard for spec-hw-slot-ownership §5: a MODEL / PROFILE
    freeform-flag save must not carry the grid-owned hardware flags
    (``-ngl``/``--n-gpu-layers``, ``-dev``/``--device``, ``--threads``/``-t``) —
    those belong on the slot's typed hardware grid (device · NGL · THREADS), not
    the device-agnostic model/profile tune. Symmetric to
    :func:`_deny_managed_flags`, but with a "belongs on the slot" message + its
    own ``slot.hardware_flag_denied`` code so the surface can point the operator
    at the right editor. ``_split_pairs`` canonicalises every short spelling onto
    its long partner, so both forms are caught by a single membership test.
    """
    offenders: list[str] = []
    for pair in _split_pairs(tokens):
        if pair.canon is not None and pair.canon in SLOT_HARDWARE_FLAGS:
            assert pair.flag is not None
            offenders.append(pair.flag)
    if offenders:
        flags = ", ".join(repr(f) for f in offenders)
        raise BadRequest(
            f"{segment} may not set hardware flag(s) {flags}; these belong on "
            "the slot's hardware grid (device / NGL / THREADS), not the "
            "device-agnostic model/profile tune. Set them on the slot instead.",
            code="slot.hardware_flag_denied",
            details={"segment": segment, "flags": offenders},
        )


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

    Before merging, any segment labelled in :data:`UNTRUSTED_SEGMENT_LABELS`
    (i.e. ``extra_args``) is screened against :data:`MANAGED_ARGS_DENYLIST`
    (§21.7) — this is the one path free-form ``[server].extra_args`` actually
    takes into the launched command (``container._llama_launch_plan``), so
    it's where a slot config trying to clobber ``--model``/``--host``/
    ``--port``/``--ctx-size``/``-ngl``/``--alias`` must fail loudly instead of
    launching a silently-redirected slot.

    Raises:
        hal0.errors.BadRequest: an untrusted segment sets a managed flag.

    ``slot_profile`` (#1636) additionally gets its hardware flags
    (``SLOT_HARDWARE_FLAGS``) silently stripped rather than hard-rejected: the
    profile save path only started enforcing the §5 partition guard after
    grandfathered/hand-edited profiles could already carry ``-dev``/
    ``--threads``. Before the divergence overlay existed, those flags were
    never read at launch at all (live profile flags were inert); stripping
    here preserves that "ignored" behaviour instead of failing the launch,
    while still guaranteeing the slot's typed hardware fields are the only
    source for those flags — even when the typed field is unset and would
    otherwise leave nothing to out-rank the profile's stale value in the
    last-wins dedup.
    """
    tokens: list[str] = []
    sources: list[str] = []
    for label, seg in segments:
        if label in UNTRUSTED_SEGMENT_LABELS:
            _deny_managed_flags(seg, segment=label)
        if label == "slot_profile":
            seg, _stripped = strip_managed_flags(seg, denylist=SLOT_HARDWARE_FLAGS)
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

    Note:
        This does **not** screen against :data:`MANAGED_ARGS_DENYLIST`. Its
        one live caller (``config.schema.resolve_profile_flags``) merges the
        MTP flag bundle with a profile's own ``flags`` — and every seed
        profile's ``flags`` hardcodes ``-ngl 999`` on the ``slot_extra``
        (winning) side, which would trip the denylist despite ``-ngl`` there
        being an ordinary bench tune, not a slot's ``[server].extra_args``
        escape hatch. The actual ``[server].extra_args`` path is
        :func:`resolve_argv`'s ``extra_args``-labelled segment (see
        :data:`UNTRUSTED_SEGMENT_LABELS`), which does enforce the denylist.
        A future caller merging real request-level free-form args through
        this function should screen the untrusted side with
        :func:`_deny_managed_flags` itself before calling in.
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
    "MANAGED_ARGS_DENYLIST",
    "SLOT_HARDWARE_FLAGS",
    "UNTRUSTED_SEGMENT_LABELS",
    "FlagProvenance",
    "NormalizedArgv",
    "ResolvedArgv",
    "merge_flags",
    "normalize_argv",
    "resolve_argv",
    "strip_managed_flags",
]
