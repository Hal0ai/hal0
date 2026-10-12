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

from dataclasses import dataclass

from hal0.errors import BadRequest

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

# Segment labels whose tokens are free-form operator text rather than
# hal0-computed (ADR-0029: only the profile layer). They are screened against
# ``MANAGED_ARGS_DENYLIST`` before they are merged in.
UNTRUSTED_SEGMENT_LABELS: frozenset[str] = frozenset({"profile"})

# The profile layer additionally refuses hardware flags (the slot's typed
# fields), model-fact flags and the MTP draft device: a profile file is
# operator-editable on disk, so the launch re-screens what the write path
# already rejects.
PROFILE_SEGMENT_LABELS: frozenset[str] = frozenset({"profile"})

#: Flags that are facts about the model file (ADR-0029): hal0 adds ``--jinja``
#: and ``--chat-template-file`` from the model, the projector follows the
#: model's mmproj and the profile's ``vision``.
MODEL_FACT_FLAGS: frozenset[str] = frozenset(
    {
        "--jinja",
        "--chat-template",
        "--chat-template-file",
        "--mmproj",
        "--no-mmproj",
        "--mmproj-auto",
        "--no-mmproj-auto",
    }
)
#: Placement the slot layer derives itself (from the slot's device).
DERIVED_PLACEMENT_FLAGS: frozenset[str] = frozenset({"--spec-draft-device"})


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
    """Canonicalize a flag token for dedup/denylist matching.

    Handles the GNU ``--long=value`` join form by splitting off the ``=value``
    suffix before the alias lookup — otherwise ``--model=/etc/passwd`` never
    equals ``--model`` and sails past every exact-string denylist check
    (``MANAGED_ARGS_DENYLIST``/``SLOT_HARDWARE_FLAGS``) despite being
    equivalent to the two-token form. Only ``--long`` flags use this form;
    short flags (``-b``) don't take an ``=`` join in practice, so they're left
    alone.
    """
    if flag.startswith("--") and "=" in flag:
        flag = flag.split("=", 1)[0]
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
    already-persisted state (custom profiles stamped before the guards
    existed) can be sanitized in place. Matching is
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


def strip_reasoning_format_none(tokens: list[str]) -> tuple[list[str], int]:
    """Drop every ``--reasoning-format none`` (and ``--reasoning-format=none``).

    Upstream llama.cpp answers HTTP 400 "Failed to initialize samplers" to every
    ``response_format`` (json_object / json_schema) request when the server runs
    with ``--reasoning-format none`` (#2545), so hal0 must never launch with it.
    This is the one owner of that rule: the migration that heals
    already-persisted profiles and the profile-centric converter both call it.

    Only the value ``none`` is removed. ``--reasoning-format deepseek`` / ``auto``
    / any other value is a deliberate choice and is kept untouched.

    Returns:
        ``(clean_tokens, removed_count)`` — the surviving tokens in order, and
        how many ``--reasoning-format none`` settings were dropped.
    """
    clean: list[str] = []
    removed = 0
    for pair in _split_pairs(tokens):
        if pair.canon == "--reasoning-format" and pair.flag is not None:
            joined = pair.flag.startswith("--reasoning-format=")
            value = pair.flag.split("=", 1)[1] if joined else (pair.values or ("",))[0]
            if value.strip().lower() == "none":
                removed += 1
                if joined:
                    # `--flag=value` takes no separate value token; whatever
                    # _split_pairs grouped after it is an unrelated token.
                    clean.extend(pair.values)
                continue
        if pair.flag is not None:
            clean.append(pair.flag)
        clean.extend(pair.values)
    return clean, removed


def deny_reasoning_format_none(
    tokens: list[str],
    *,
    segment: str,
    grandfathered: list[str] | None = None,
) -> None:
    """Raise :class:`~hal0.errors.BadRequest` if ``tokens`` set ``--reasoning-format none``.

    The write-side half of :func:`strip_reasoning_format_none` (#2545): new
    profile / model-default saves are rejected with the reason, while
    :func:`resolve_argv` strips the pair on every launch as the backstop for
    anything already persisted. ``grandfathered`` is the previously stored
    token list on an update — a row that already carries the pair passes
    unchanged (the edit drawer re-sends stored text verbatim, so rejecting it
    would make the row un-editable, the #1411 round-trip trap); only a save
    that INTRODUCES it is rejected.
    """
    _, found = strip_reasoning_format_none(tokens)
    if not found:
        return
    if grandfathered is not None and strip_reasoning_format_none(grandfathered)[1]:
        return
    raise BadRequest(
        f"{segment} may not set `--reasoning-format none`: upstream llama.cpp answers "
        "HTTP 400 'Failed to initialize samplers' to every response_format "
        "(json_object / json_schema) request when the server runs with it, so "
        "structured output and tool-call grammars would stop working. Omit the "
        "flag (use `--reasoning off` to disable thinking), or pick another "
        "--reasoning-format value such as `deepseek`.",
        code="slot.reasoning_format_none_denied",
        details={"segment": segment, "flags": ["--reasoning-format", "none"]},
    )


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


def _deny_profile_owned_elsewhere(tokens: list[str], *, segment: str) -> None:
    """Raise when profile flags carry a model fact or derived placement flag."""
    offenders = [
        str(p.flag)
        for p in _split_pairs(tokens)
        if p.canon is not None and p.canon in (MODEL_FACT_FLAGS | DERIVED_PLACEMENT_FLAGS)
    ]
    if offenders:
        raise BadRequest(
            f"{segment} may not set {', '.join(map(repr, offenders))}: hal0 derives "
            "these from the model file and the slot's device (ADR-0029)",
            code="profiles.model_fact_flag_denied",
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
    tokens carry its label, so the result records which layer set each flag's
    final value (``base`` / ``model`` / ``profile`` / ``slot``, ADR-0029).
    Segments are concatenated in order before dedup, so a later segment
    overrides an earlier one — pass them lowest-precedence first.

    The ``profile`` segment is operator text (a profile file is editable on
    disk), so it is screened before merging: a managed flag
    (:data:`MANAGED_ARGS_DENYLIST`), a hardware flag
    (:data:`SLOT_HARDWARE_FLAGS`), a model-fact flag (:data:`MODEL_FACT_FLAGS`)
    or ``--spec-draft-device`` refuses to launch, the same rules the profile
    write path enforces.

    Raises:
        hal0.errors.BadRequest: the profile segment sets a flag it may not.

    Every segment also has ``--reasoning-format none`` silently stripped
    (:func:`strip_reasoning_format_none`, #2545): saves that introduce it are
    rejected (:func:`deny_reasoning_format_none`), and this is the backstop.
    """
    tokens: list[str] = []
    sources: list[str] = []
    for label, seg in segments:
        # Strip BEFORE the deny: ``-ngl`` sits in both denylists, and for a
        # profile-sourced segment the partition rule is "silently ignored",
        # not "fail the launch". Denying first would hard-reject every
        # grandfathered profile that still carries ``-ngl 999``.
        if label in PROFILE_SEGMENT_LABELS:
            _deny_slot_hardware_flags(seg, segment=label)
            _deny_profile_owned_elsewhere(seg, segment=label)
        if label in UNTRUSTED_SEGMENT_LABELS:
            _deny_managed_flags(seg, segment=label)
        # Backstop for #2545: whatever a segment carries (a row persisted
        # before the write-side screen, a hand-edited TOML), the launched
        # command never has `--reasoning-format none`. Per segment, so a
        # lower-precedence `deepseek` is not shadowed by a stripped `none`.
        seg, _ = strip_reasoning_format_none(seg)
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


__all__ = [
    "APPEND_FLAGS",
    "FLAG_ALIASES",
    "MANAGED_ARGS_DENYLIST",
    "PROFILE_SEGMENT_LABELS",
    "SLOT_HARDWARE_FLAGS",
    "UNTRUSTED_SEGMENT_LABELS",
    "FlagProvenance",
    "NormalizedArgv",
    "ResolvedArgv",
    "deny_reasoning_format_none",
    "normalize_argv",
    "resolve_argv",
    "strip_managed_flags",
    "strip_reasoning_format_none",
]
