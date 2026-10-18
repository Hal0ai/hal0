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

# Short/alternative → primary long canonicalisation. Used to compute the dedup
# key and to match the deny sets; the emitted token keeps its original
# spelling. The spellings come from llama.cpp's own option table
# (``common/arg.cpp`` at the runner pin, b11574): every alternative spelling
# of a flag hal0 manages or screens must be here, or the screen misses it
# (``-m /etc/shadow`` once redirected the model past a ``--model`` check).
# Unknown flags fall through unaliased and dedup against their own spelling.
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
    # Managed flags (base layer).
    "-m": "--model",
    "-a": "--alias",
    "--gpu-layers": "--n-gpu-layers",
    # Model facts (model layer).
    "-mm": "--mmproj",
    # Derived placement (slot layer).
    "-devd": "--spec-draft-device",
    "--device-draft": "--spec-draft-device",
    "-mmdev": "--mmproj-device",
    # Remote model fetches.
    "-mu": "--model-url",
    "-mmu": "--mmproj-url",
    "-hf": "--hf-repo",
    "-hfr": "--hf-repo",
    "-hff": "--hf-file",
    "-hft": "--hf-token",
    "-dr": "--docker-repo",
    "-hfd": "--spec-draft-hf",
    "-hfrd": "--spec-draft-hf",
    "--hf-repo-draft": "--spec-draft-hf",
    # Host file reads and writes.
    "-md": "--spec-draft-model",
    "--model-draft": "--spec-draft-model",
    "-f": "--file",
    "-sysf": "--system-prompt-file",
    "-jf": "--json-schema-file",
    "-bf": "--binary-file",
    "-lcs": "--lookup-cache-static",
    "-lcd": "--lookup-cache-dynamic",
    "-o": "--output-file",
    "--output": "--output-file",
    "--kl-divergence-base": "--save-all-logits",
    "--audio": "--image",
    "--video": "--image",
    "--ui-config-file": "--webui-config-file",
    # Listener / network policy.
    "--ui-mcp-proxy": "--webui-mcp-proxy",
    "-ag": "--agent",
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
        "--gpu-layers",
        "--device",
        "-dev",
        "--threads",
        "-t",
    }
)

#: llama.cpp's built-in preset switches (the "presets" group at the end of
#: ``common/arg.cpp`` at the runner pin, b11574). Each one sets a model, a
#: port and a sampler at once, and most download the weights from Hugging
#: Face (``--embd-gemma-default`` …), so none may come from a profile.
LLAMA_PRESET_FLAGS: frozenset[str] = frozenset(
    {
        "--embd-gemma-default",
        "--fim-qwen-1.5b-default",
        "--fim-qwen-3b-default",
        "--fim-qwen-7b-default",
        "--fim-qwen-7b-spec",
        "--fim-qwen-14b-spec",
        "--fim-qwen-30b-default",
        "--gpt-oss-20b-default",
        "--gpt-oss-120b-default",
        "--vision-gemma-4b-default",
        "--vision-gemma-12b-default",
        "--spec-default",
    }
)

#: Every flag spelling llama.cpp b11574 declares with no value argument — a
#: plain switch or a ``bool`` switch with a ``--no-`` form (``common/arg.cpp``:
#: options whose handler takes no value). The token after one of these is the
#: next argument, never its value, so ``--metrics /etc/shadow`` is a switch
#: followed by a bare positional. A flag llama.cpp does not declare keeps the
#: "the next non-flag token is its value" heuristic.
VALUELESS_FLAGS: frozenset[str] = frozenset(
    {
        "--agent",
        "--backend-sampling",
        "--cache-idle-slots",
        "--cache-list",
        "--cache-prompt",
        "--check",
        "--check-tensors",
        "--completion-bash",
        "--cont-batching",
        "--context-shift",
        "--conversation",
        "--cors-credentials",
        "--cpu-moe",
        "--cpu-moe-draft",
        "--dflash",
        "--diffusion-visual",
        "--display-prompt",
        "--eagle3",
        "--embd-gemma-default",
        "--embedding",
        "--embeddings",
        "--escape",
        "--fim-qwen-1.5b-default",
        "--fim-qwen-14b-spec",
        "--fim-qwen-30b-default",
        "--fim-qwen-3b-default",
        "--fim-qwen-7b-default",
        "--fim-qwen-7b-spec",
        "--gpt-oss-120b-default",
        "--gpt-oss-20b-default",
        "--hellaswag",
        "--help",
        "--ids",
        "--ignore-eos",
        "--in-prefix-bos",
        "--interactive",
        "--interactive-first",
        "--jinja",
        "--kl-divergence",
        "--kv-offload",
        "--kv-unified",
        "--list-devices",
        "--log-disable",
        "--log-jsonl",
        "--log-prefix",
        "--log-timestamps",
        "--log-verbose",
        "--lora-init-without-apply",
        "--metrics",
        "--mmproj-auto",
        "--mmproj-offload",
        "--models-autoload",
        "--mtp",
        "--multiline-input",
        "--multiple-choice",
        "--nextn",
        "--no-agent",
        "--no-bos",
        "--no-cache-idle-slots",
        "--no-cache-prompt",
        "--no-cont-batching",
        "--no-context-shift",
        "--no-conversation",
        "--no-cors-credentials",
        "--no-display-prompt",
        "--no-escape",
        "--no-host",
        "--no-jinja",
        "--no-kv-offload",
        "--no-kv-unified",
        "--no-log-jsonl",
        "--no-log-prefix",
        "--no-log-timestamps",
        "--no-mmproj",
        "--no-mmproj-auto",
        "--no-mmproj-offload",
        "--no-models-autoload",
        "--no-op-offload",
        "--no-parse-special",
        "--no-perf",
        "--no-ppl",
        "--no-prefill-assistant",
        "--no-reasoning-preserve",
        "--no-repack",
        "--no-show-timings",
        "--no-skip-chat-parsing",
        "--no-slots",
        "--no-spec-draft-backend-sampling",
        "--no-ui",
        "--no-ui-mcp-proxy",
        "--no-warmup",
        "--no-webui",
        "--no-webui-mcp-proxy",
        "--offline",
        "--op-offload",
        "--parse-special",
        "--perf",
        "--ppl",
        "--prefill-assistant",
        "--process-output",
        "--prompt-cache-all",
        "--prompt-cache-ro",
        "--props",
        "--reasoning-preserve",
        "--repack",
        "--rerank",
        "--reranking",
        "--reuse-port",
        "--save-logits",
        "--show-count",
        "--show-statistics",
        "--show-timings",
        "--simple-io",
        "--single-turn",
        "--skip-chat-parsing",
        "--slots",
        "--spec-default",
        "--spec-draft-backend-sampling",
        "--spec-draft-cpu-moe",
        "--special",
        "--spm-infill",
        "--stdin",
        "--swa-full",
        "--ui",
        "--ui-mcp-proxy",
        "--usage",
        "--verbose",
        "--verbose-prompt",
        "--version",
        "--vision-gemma-12b-default",
        "--vision-gemma-4b-default",
        "--warmup",
        "--webui",
        "--webui-mcp-proxy",
        "--winogrande",
        "-ag",
        "-bs",
        "-cb",
        "-cl",
        "-cmoe",
        "-cmoed",
        "-cnv",
        "-e",
        "-h",
        "-i",
        "-if",
        "-kvo",
        "-kvu",
        "-mli",
        "-nkvo",
        "-no-ag",
        "-no-cnv",
        "-no-kvu",
        "-nocb",
        "-nr",
        "-pps",
        "-sp",
        "-st",
        "-tgs",
        "-v",
    }
)

# ── Host-owned flags ──────────────────────────────────────────────────────────
#
# llama-server flags that reach outside the slot's own tune: fetching a model
# from the network, reading or writing an arbitrary host file, opening or
# shaping a listener, or setting auth/TLS. hal0 manages all of these (the
# model file, the loopback listener, the API gateway's auth), so a profile —
# or a blocked slot's recorded tune — may never set them. Canonical (long)
# spellings from llama.cpp's ``common/arg.cpp`` at the runner pin (b11574);
# :data:`FLAG_ALIASES` maps every short/alternative spelling onto them.
HOST_OWNED_FLAGS: frozenset[str] = (
    frozenset(
        {
            # Remote model fetches.
            "--model-url",
            "--mmproj-url",
            "--hf-repo",
            "--hf-file",
            "--hf-token",
            "--docker-repo",
            "--spec-draft-hf",
            "--server-base",
            # Host file reads and writes.
            "--spec-draft-model",
            "--file",
            "--system-prompt-file",
            "--in-file",
            "--binary-file",
            "--prompt-cache",
            "--grammar-file",
            "--json-schema-file",
            "--lora",
            "--lora-scaled",
            "--control-vector",
            "--control-vector-scaled",
            "--lookup-cache-static",
            "--lookup-cache-dynamic",
            "--context-file",
            "--output-file",
            "--save-all-logits",
            "--logits-output-dir",
            "--log-file",
            "--log-prompts-dir",
            "--slot-save-path",
            "--media-path",
            "--path",
            "--image",
            "--video-ffmpeg-dir",
            "--positive-file",
            "--negative-file",
            "--tts-speaker-file",
            "--webui-config-file",
            "--mcp-servers-config",
            "--models-dir",
            "--models-preset",
            # Listener / network policy (and the router mode that spawns servers).
            "--rpc",
            "--reuse-port",
            "--api-prefix",
            "--cors-origins",
            "--cors-methods",
            "--cors-headers",
            "--cors-credentials",
            "--webui-mcp-proxy",
            # ``--agent`` is the shortcut for ``--tools all`` plus the MCP proxy.
            "--agent",
            "--tools",
            "--tools-runtime",
            "--mcp-servers-json",
            "--models-max",
            "--models-autoload",
            # Auth / TLS.
            "--api-key",
            "--api-key-file",
            "--ssl-key-file",
            "--ssl-cert-file",
        }
    )
    | LLAMA_PRESET_FLAGS
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
#: model's mmproj and the profile's ``vision``, and ``--spec-type`` follows the
#: model's drafter or MTP heads and the profile's ``speculation`` (ADR-0031 §3;
#: llama.cpp appends ``--spec-type`` values, so it has exactly one owner).
MODEL_FACT_FLAGS: frozenset[str] = frozenset(
    {
        "--spec-type",
        "--jinja",
        "--no-jinja",
        "--chat-template",
        "--chat-template-file",
        "--mmproj",
        "--no-mmproj",
        "--mmproj-auto",
        "--no-mmproj-auto",
    }
)
#: Placement the slot layer derives itself (from the slot's device).
DERIVED_PLACEMENT_FLAGS: frozenset[str] = frozenset({"--spec-draft-device", "--mmproj-device"})


# ── llama-server options set through the environment ─────────────────────────
#
# llama.cpp reads every option from an environment variable as well as from
# argv (``common_arg::set_env`` in ``common/arg.cpp`` at the runner pin,
# b11574): 152 ``LLAMA_ARG_*`` names (plus their ``LLAMA_ARG_NO_*`` negations)
# and three without the prefix — ``LLAMA_API_KEY`` (``--api-key``),
# ``HF_TOKEN`` (``--hf-token``) and ``MTMD_BACKEND_DEVICE`` (``--mmproj-device``).
# A slot's ``[server].env`` reaches the llama-server container, so through it a
# slot could set any flag the profile screen refuses (``LLAMA_ARG_MODEL``,
# ``LLAMA_ARG_AGENT``, ``LLAMA_ARG_TOOLS`` …). Tuning belongs on the profile,
# so the slot env carries none of them: every ``LLAMA_ARG_*`` name and the
# three unprefixed ones are refused.
LLAMA_ENV_PREFIX = "LLAMA_ARG_"
LLAMA_ENV_NAMES: frozenset[str] = frozenset({"LLAMA_API_KEY", "HF_TOKEN", "MTMD_BACKEND_DEVICE"})


def llama_env_offenders(env: object) -> list[str]:
    """The names in ``env`` that would set a llama-server option, sorted."""
    if not isinstance(env, dict):
        return []
    return sorted(
        str(k) for k in env if str(k).startswith(LLAMA_ENV_PREFIX) or str(k) in LLAMA_ENV_NAMES
    )


def deny_llama_env(env: object, *, segment: str) -> None:
    """Raise :class:`~hal0.errors.BadRequest` if ``env`` sets a llama-server
    option (``slot.llama_env_denied``)."""
    offenders = llama_env_offenders(env)
    if offenders:
        raise BadRequest(
            f"{segment} may not set {', '.join(map(repr, offenders))}: llama-server reads "
            "these as launch options, which belong on the slot's profile (or are hal0's "
            "to set); remove them from the slot's [server].env",
            code="slot.llama_env_denied",
            details={"segment": segment, "keys": offenders},
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
    """Canonicalize a flag token for dedup/denylist matching.

    * The ``flag=value`` join form (``--model=/etc/passwd``, ``-m=/etc/passwd``)
      is split before the lookup, so it can never sail past an exact-string
      deny check the two-token form would hit.
    * In a ``--long`` flag, ``_`` reads as ``-``: llama.cpp rewrites it the
      same way before matching (``common/arg.cpp``), so ``--api_key`` *is*
      ``--api-key``.
    * Every short/alternative spelling folds onto its primary long flag
      through :data:`FLAG_ALIASES`.
    """
    if "=" in flag:
        flag = flag.split("=", 1)[0]
    if flag.startswith("--"):
        flag = flag.replace("_", "-")
    return FLAG_ALIASES.get(flag, flag)


@dataclass(frozen=True)
class _Pair:
    canon: str | None  # None => bare positional (never deduped)
    flag: str | None
    values: tuple[str, ...]
    source: str = ""  # which input segment this token came from (provenance)


def _takes_value(flag: str) -> bool:
    """Whether ``flag`` may take the next token as its value.

    A joined ``--flag=value`` already carries it, and a switch llama.cpp
    declares with no value (:data:`VALUELESS_FLAGS`) never takes one.
    """
    if "=" in flag:
        return False
    name = flag.replace("_", "-") if flag.startswith("--") else flag
    return name not in VALUELESS_FLAGS


def _split_pairs(tokens: list[str], sources: list[str] | None = None) -> list[_Pair]:
    """Group a flat token list into ``(flag, value?)`` pairs, order preserved.

    A flag consumes the following token as its value iff that token is not
    itself a flag (so ``--jinja --metrics`` are two valueless bools, while
    ``-b 8192`` and ``--temp 0`` carry a value). A joined ``--flag=value``
    already carries its value, and a switch llama.cpp declares with no value
    (:data:`VALUELESS_FLAGS`) never takes one, so neither consumes anything:
    the next non-flag token is a bare positional (``--temp=0.7 /etc/shadow``,
    ``--metrics /etc/shadow``). Bare positionals are kept under ``canon=None``
    so dedup never touches them.

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
            if _takes_value(tok) and i + 1 < n and not _is_flag(tokens[i + 1]):
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
            joined = "=" in pair.flag
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
        hint = (
            "; for speculative decoding set the profile's speculation field "
            "(auto, off or ngram) instead of --spec-type"
            if any(_canon(f) == "--spec-type" for f in offenders)
            else ""
        )
        raise BadRequest(
            f"{segment} may not set {', '.join(map(repr, offenders))}: hal0 derives "
            f"these from the model file and the slot's device (ADR-0029){hint}",
            code="profiles.model_fact_flag_denied",
            details={"segment": segment, "flags": offenders},
        )


def _deny_host_owned_flags(tokens: list[str], *, segment: str) -> None:
    """Raise when ``tokens`` set a flag that reaches outside the slot's tune
    (:data:`HOST_OWNED_FLAGS`: remote model fetches, host file reads/writes,
    listener or auth/TLS settings), in any spelling."""
    offenders = [
        str(p.flag)
        for p in _split_pairs(tokens)
        if p.canon is not None and p.canon in HOST_OWNED_FLAGS
    ]
    if offenders:
        raise BadRequest(
            f"{segment} may not set {', '.join(map(repr, offenders))}: these fetch a "
            "model, read or write a host file, open or shape a listener, or set "
            "auth/TLS, and hal0 manages all of that for the slot",
            code="slot.host_owned_flag_denied",
            details={"segment": segment, "flags": offenders},
        )


def _deny_positional_args(tokens: list[str], *, segment: str) -> None:
    """Raise when ``tokens`` carry a bare positional (a value with no flag)."""
    offenders = [str(p.values[0]) for p in _split_pairs(tokens) if p.canon is None]
    if offenders:
        raise BadRequest(
            f"{segment} may not carry bare arguments {', '.join(map(repr, offenders))}: "
            "every value must follow its flag",
            code="slot.positional_arg_denied",
            details={"segment": segment, "args": offenders},
        )


def screen_profile_segment(tokens: list[str], *, segment: str) -> None:
    """Every launch-time refusal for profile-layer tokens, in reporting order.

    Hardware first (``-ngl`` is also managed; the "belongs on the slot"
    message is the actionable one), then managed, model-fact / derived
    placement, host-owned flags and bare positionals.
    """
    _deny_slot_hardware_flags(tokens, segment=segment)
    _deny_managed_flags(tokens, segment=segment)
    _deny_profile_owned_elsewhere(tokens, segment=segment)
    _deny_host_owned_flags(tokens, segment=segment)
    _deny_positional_args(tokens, segment=segment)


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
    disk), so it is screened before merging (:func:`screen_profile_segment`):
    a managed flag (:data:`MANAGED_ARGS_DENYLIST`), a hardware flag
    (:data:`SLOT_HARDWARE_FLAGS`), a model-fact flag (:data:`MODEL_FACT_FLAGS`),
    derived placement (``--spec-draft-device``, ``--mmproj-device``), a
    host-owned flag (:data:`HOST_OWNED_FLAGS`) or a bare positional refuses to
    launch, in any spelling — the same rules the profile write path enforces.

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
            screen_profile_segment(seg, segment=label)
        elif label in UNTRUSTED_SEGMENT_LABELS:
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
    "HOST_OWNED_FLAGS",
    "LLAMA_ENV_NAMES",
    "LLAMA_ENV_PREFIX",
    "LLAMA_PRESET_FLAGS",
    "MANAGED_ARGS_DENYLIST",
    "PROFILE_SEGMENT_LABELS",
    "SLOT_HARDWARE_FLAGS",
    "UNTRUSTED_SEGMENT_LABELS",
    "VALUELESS_FLAGS",
    "FlagProvenance",
    "NormalizedArgv",
    "ResolvedArgv",
    "deny_llama_env",
    "deny_reasoning_format_none",
    "llama_env_offenders",
    "normalize_argv",
    "resolve_argv",
    "screen_profile_segment",
    "strip_managed_flags",
    "strip_reasoning_format_none",
]
