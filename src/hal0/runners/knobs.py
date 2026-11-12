"""Engine knobs and the ``HALOGEN_*`` flag screen for the halogen runner.

The halogen engine (Flash-Next, see :mod:`hal0.runners`) is configured
entirely through ``HALOGEN_<NAME>`` environment variables. hal0 keeps those
choices in the profile's ``flags`` text as ``HALOGEN_NAME=value`` tokens
(ADR-0029: the profile owns tuning), so this module is the one place that
knows three things about that text:

* **Which knobs the dashboard renders** — :data:`HALOGEN_KNOBS`, a curated,
  typed subset of the engine's variables (upstream ships no machine-readable
  schema; every other variable stays reachable as free text).
* **Who owns each variable** — :data:`HALOGEN_MANAGED_ENV` (hal0 sets them
  from the slot port or the model row, or keeps them out of the container),
  :data:`HALOGEN_TUNE_ENV` (the slot's tune block sets them) and
  :data:`HALOGEN_DENIED_ENV` (held back for a later phase). A profile flag may
  not name any of them.
* **How to read and check the text** — :func:`parse_env_tokens` and
  :func:`screen_halogen_flags`.

Pure data and string handling: this module imports nothing from
``hal0.profiles`` or ``hal0.providers`` (``hal0.runners`` is a leaf; the
profiles package imports from here, not the other way round — see the
``hal0.runners`` module docstring). Only :mod:`hal0.errors` and the standard
library.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from hal0.errors import UnprocessableEntity

#: Engine context window in tokens when no profile ``ctx_size`` narrows it.
HALOGEN_NATIVE_CTX = 262144

#: Tune values the engine cannot honour, keyed by tune key: the slot's
#: ``tune_effective.tune_unavailable`` read shape. The engine drafts with its
#: own MTP heads (``auto``) or not at all (``off``); it has no n-gram lookup.
HALOGEN_TUNE_UNAVAILABLE: dict[str, str] = {
    "speculation": "ngram: this engine has no n-gram drafter (auto or off)",
}

#: The same read shape for the xdna runner (ADR-0057). Its launch runs one
#: sequence (``-np 1``) on the NPU fork, which has no MTP heads, no drafter
#: kinds and no tested n-gram path: ``auto`` resolves to no speculation there.
XDNA_TUNE_UNAVAILABLE: dict[str, str] = {
    "speculation": "ngram: the xdna runner has no speculative path (auto or off)",
}


@dataclass(frozen=True, slots=True)
class EngineKnob:
    """One engine variable the dashboard renders as a typed control.

    ``default`` is the engine's (or the seed profile's) value as flags text,
    ``None`` when the variable is normally left unset. ``minimum`` / ``maximum``
    bound an ``int`` knob; ``choices`` lists the values of an ``enum`` knob (a
    ``bool`` knob takes ``0`` or ``1``). ``memory`` marks a knob the capacity
    estimator counts; ``restart`` that a change takes effect on the next slot
    start.
    """

    env: str
    label: str
    kind: Literal["int", "bool", "enum"]
    default: str | None
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    help: str = ""
    memory: bool = False
    restart: bool = True


#: Lowest ``HALOGEN_HOST_RESERVE_GIB`` the screen accepts: below it the engine
#: leaves the lookup table no page cache and long prompts crawl.
_HOST_RESERVE_FLOOR_GIB = 10

HALOGEN_KNOBS: tuple[EngineKnob, ...] = (
    EngineKnob(
        env="HALOGEN_KV_POOL_POSITIONS",
        label="KV pool positions",
        kind="int",
        default="262144",
        minimum=1024,
        help="Attention positions kept resident across all conversations, about 29.5 KiB each, so this is the main memory knob; a request reserves its prompt plus max_tokens positions when it is admitted.",
        memory=True,
    ),
    EngineKnob(
        env="HALOGEN_KV_SLOTS",
        label="KV slots",
        kind="int",
        default="2",
        minimum=1,
        maximum=64,
        help="Conversations generating at the same moment (about 115 MB each; past eight total throughput stops growing); the KV pool, not this, decides how many stay resident.",
        memory=True,
    ),
    EngineKnob(
        env="HALOGEN_MAX_TOK",
        label="Prefill chunk",
        kind="enum",
        default="16384",
        choices=("16384", "32768"),
        help="Largest single prefill call, never the full context; it sizes the working memory (about 12.6 GiB at 32768, 8.1 GiB at 16384), and 16384 costs about 9 % of prefill speed.",
        memory=True,
    ),
    EngineKnob(
        env="HALOGEN_WEIGHTS_LOCK",
        label="Lock weights in memory",
        kind="bool",
        default="1",
        help="Locks the weights in RAM so the kernel cannot reclaim them under memory pressure; needs an unlimited memlock limit, which hal0 sets.",
    ),
    EngineKnob(
        env="HALOGEN_KV8",
        label="8-bit KV cache",
        kind="bool",
        default="0",
        help="Stores the attention cache in 8 bits, about half the pool memory, at the cost of a numeric change (drafted text can differ) and decode 0.2 to 1.9 % slower.",
        memory=True,
    ),
    EngineKnob(
        env="HALOGEN_PROMPT_CACHE",
        label="Prompt cache",
        kind="enum",
        default="2",
        choices=("0", "1", "2"),
        help="2 resumes a cached prompt from anywhere (the default, though a warm answer is not always byte-identical to a cold one), 1 saves only at prefill-chunk boundaries (byte-identical repeats), 0 turns the cache off.",
    ),
    EngineKnob(
        env="HALOGEN_MAX_TOKENS_DEFAULT",
        label="Default max tokens",
        kind="int",
        default=None,
        minimum=1,
        maximum=65536,
        help="Token budget a request gets when it sends none (8192 on chat by default), covering thinking and the answer; it must not exceed the 65536 cap.",
    ),
    EngineKnob(
        env="HALOGEN_HOST_RESERVE_GIB",
        label="Host memory reserve (GiB)",
        kind="int",
        default="20",
        minimum=_HOST_RESERVE_FLOOR_GIB,
        help="System RAM in GiB left free when the pool is sized; the lookup table is read through that file cache, so too little makes long prompts crawl, and raising it shrinks the pool (hal0 requires at least 10).",
    ),
)

_KNOBS_BY_ENV: dict[str, EngineKnob] = {knob.env: knob for knob in HALOGEN_KNOBS}

#: Knob rows by runner key (``RUNNER_IMAGES`` key), for the dashboard schema.
ENGINE_KNOBS: dict[str, tuple[EngineKnob, ...]] = {"halogen": HALOGEN_KNOBS}

#: Owner label -> the names it owns. Order is irrelevant; the labels are the
#: ``owner`` values of a ``profiles.halogen_flag_denied`` error.
_SLOT_PORT_ENV = frozenset({"HALOGEN_API_PORT", "HALOGEN_PORT"})
_MODEL_ROW_ENV = frozenset(
    {
        "HALOGEN_CHECKPOINT",
        "HALOGEN_MODEL_ID",
        "HALOGEN_TOKENIZER",
        "HALOGEN_NGRAM_TABLE",
        "HALOGEN_MTP_HEAD",
    }
)
_HAL0_ENV = frozenset(
    {
        "HALOGEN_BIND",
        "HALOGEN_ENGINE",
        "HALOGEN_DOWNLOAD",
        "HALOGEN_API_KEY",
        "HALOGEN_API_KEY_FILE",
    }
)

#: Name prefixes that a frozenset of exact names cannot hold.
HALOGEN_MANAGED_PREFIXES: tuple[str, ...] = ("HALOGEN_NPU_",)
HALOGEN_DENIED_PREFIXES: tuple[str, ...] = ("HALOGEN_RECORD_",)

#: Variables hal0 sets itself or keeps out of the container: the listener
#: (slot port, loopback bind), the checkpoint and the files beside it (model
#: row), downloads and auth (hal0), plus every ``HALOGEN_NPU_*`` name
#: (see :data:`HALOGEN_MANAGED_PREFIXES`).
HALOGEN_MANAGED_ENV: frozenset[str] = _SLOT_PORT_ENV | _MODEL_ROW_ENV | _HAL0_ENV

#: Variables the slot's tune block sets (context, thinking, reasoning effort,
#: reasoning budget, vision, speculation); a profile flag may not shadow them.
HALOGEN_TUNE_ENV: frozenset[str] = frozenset(
    {
        "HALOGEN_CTX",
        "HALOGEN_ENABLE_THINKING",
        "HALOGEN_REASONING_EFFORT",
        "HALOGEN_MAX_THINKING_TOKENS",
        "HALOGEN_VISION_TOWER",
        "HALOGEN_DRAFTER_DEFAULT",
        "HALOGEN_PLD",
    }
)

#: Variables held back for a later phase (they need a mount or a privacy
#: decision): the cache location plus every ``HALOGEN_RECORD_*`` name
#: (see :data:`HALOGEN_DENIED_PREFIXES`).
HALOGEN_DENIED_ENV: frozenset[str] = frozenset({"HALOGEN_CACHE_DIR", "HALOGEN_CACHE_FILE"})


def runner_knob_denied(key: str) -> list[str]:
    """Profile-flag names runner ``key`` may not set, for the dashboard screen.

    The exact names sorted, then each owned name prefix as ``PREFIX*``. Only
    halogen has knobs today; every other runner gets ``[]``.
    """
    if key != "halogen":
        return []
    prefixes = (*HALOGEN_MANAGED_PREFIXES, *HALOGEN_DENIED_PREFIXES)
    return sorted(HALOGEN_MANAGED_ENV | HALOGEN_TUNE_ENV | HALOGEN_DENIED_ENV) + [
        p + "*" for p in prefixes
    ]


_TOKEN = re.compile(r"(HALOGEN_[A-Z0-9_]+)=(.*)")
_WHOLE_NUMBER = re.compile(r"[0-9]+")
#: What the root unit validator refuses in a value: any control byte, and more
#: than this many bytes (``installer/wrappers/hal0-systemctl``). A profile holding
#: either would save and then fail every load with "quadlet rejected".
_CONTROL_BYTE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_VALUE_BYTES = 1024
#: Longest digit string converted with ``int()``. Python refuses 4300+ digits
#: with ``ValueError`` (a 500 on the API path), and no knob takes a value near
#: ten digits, so a longer string is out of range without being converted.
_MAX_INT_DIGITS = 9
#: Longest slice of a refused value echoed in an error message or ``details``.
_ECHO_CHARS = 40


def whole_number(value: str) -> int | None:
    """``value`` as a non-negative whole number; ``None`` when it is not plain digits.

    For a flag the sizing path reads that is no curated knob, so it has no range to
    check it against. Same digit limit as a knob: past it the value is not converted.
    """
    if _WHOLE_NUMBER.fullmatch(value) is None or len(value) > _MAX_INT_DIGITS:
        return None
    return int(value)


def _echo(value: str) -> str:
    """``value`` shortened for an error message or ``details``."""
    return value if len(value) <= _ECHO_CHARS else value[:_ECHO_CHARS] + "..."


def parse_env_tokens(flags: str | None) -> dict[str, str]:
    """Read a halogen profile's ``flags`` text as ``{NAME: value}``.

    Every shell token must be ``HALOGEN_NAME=value`` with a non-empty value;
    quoting is shell quoting, so ``HALOGEN_X="two words"`` is one variable.
    A later duplicate wins. ``None`` or blank text yields ``{}``. Raises
    ``UnprocessableEntity`` (``profiles.halogen_flag_malformed``) for invalid
    shell text, a token of any other shape, an empty value, or a value the unit
    file cannot carry: one holding a control byte such as a line break, or longer
    than 1024 bytes.
    """
    if not flags or not flags.strip():
        return {}
    try:
        tokens = shlex.split(flags)
    except ValueError as exc:
        raise UnprocessableEntity(
            f"profile flags are not valid shell text: {exc}",
            code="profiles.halogen_flag_malformed",
            details={"flags": flags},
        ) from exc
    out: dict[str, str] = {}
    for token in tokens:
        match = _TOKEN.fullmatch(token)
        if match is None:
            raise UnprocessableEntity(
                f"halogen profile flags must be HALOGEN_NAME=value tokens; got {token!r}",
                code="profiles.halogen_flag_malformed",
                details={"token": token},
            )
        name, value = match.groups()
        if not value:
            raise UnprocessableEntity(
                f"{name} needs a value; remove the token to leave it at the engine default",
                code="profiles.halogen_flag_malformed",
                details={"token": token},
            )
        if _CONTROL_BYTE.search(value) is not None:
            raise UnprocessableEntity(
                f"{name} holds a control character, which the slot's unit file cannot carry",
                code="profiles.halogen_flag_malformed",
                details={"flag": name},
            )
        if len(value.encode("utf-8", "surrogatepass")) > _MAX_VALUE_BYTES:
            raise UnprocessableEntity(
                f"{name} is longer than the {_MAX_VALUE_BYTES} bytes the slot's unit file can carry",
                code="profiles.halogen_flag_malformed",
                details={"flag": name},
            )
        out[name] = value
    return out


def native_ctx_problem(ctx_size: int | None, flags: str | None) -> str | None:
    """Why a halogen context of ``ctx_size`` cannot launch under ``flags``, or ``None``.

    Past :data:`HALOGEN_NATIVE_CTX` the entrypoint exits 2 at once unless
    ``HALOGEN_ROPE_YARN`` is set, leaving the reason only in the journal. Flags
    that do not parse carry nothing; reporting them is the flag screen's job.
    """
    if ctx_size is None or ctx_size <= HALOGEN_NATIVE_CTX:
        return None
    try:
        carried = parse_env_tokens(flags)
    except UnprocessableEntity:
        carried = {}
    if "HALOGEN_ROPE_YARN" in carried:
        return None
    return (
        f"context {ctx_size} is past this engine's native {HALOGEN_NATIVE_CTX}-token window; "
        "that needs HALOGEN_ROPE_YARN in the profile's flags, or the engine refuses to start"
    )


def _flag_owner(name: str) -> str | None:
    """Who owns ``name``, as the ``owner`` of a denial; ``None`` if nobody."""
    if name in _SLOT_PORT_ENV:
        return "slot port"
    if name in _MODEL_ROW_ENV:
        return "model row"
    if name in HALOGEN_TUNE_ENV:
        return "tune"
    if name in _HAL0_ENV or name.startswith(HALOGEN_MANAGED_PREFIXES):
        return "hal0"
    if name in HALOGEN_DENIED_ENV or name.startswith(HALOGEN_DENIED_PREFIXES):
        return "phase 2"
    return None


def _range_problem(knob: EngineKnob, value: str) -> str | None:
    """Why ``value`` is not allowed for ``knob``, or ``None`` when it is."""
    if knob.kind == "bool":
        return None if value in ("0", "1") else f"{knob.env} must be 0 or 1; got {_echo(value)!r}"
    if knob.kind == "enum":
        if value in knob.choices:
            return None
        return f"{knob.env} must be one of {', '.join(knob.choices)}; got {_echo(value)!r}"
    # int
    if _WHOLE_NUMBER.fullmatch(value) is None:
        return f"{knob.env} must be a whole number; got {_echo(value)!r}"
    if len(value) > _MAX_INT_DIGITS:
        return f"{knob.env} must be a whole number of at most {_MAX_INT_DIGITS} digits; got {len(value)} digits"
    number = int(value)
    low, high = knob.minimum, knob.maximum
    if low is not None and number < low:
        message = f"{knob.env} must be at least {low}; got {number}"
        if knob.env == "HALOGEN_HOST_RESERVE_GIB":
            message += (
                " (below that the engine leaves the lookup table no page cache"
                " and long prompts crawl)"
            )
        return message
    if high is not None and number > high:
        return f"{knob.env} must be at most {high}; got {number}"
    return None


def knob_value(carried: Mapping[str, str], env: str) -> str:
    """The profile's value for knob ``env``, or the knob's default when absent or odd.

    "Odd" is anything the write-time screen would refuse (not a whole number, out
    of range, not one of the choices). Unparseable flags are refused at launch, but
    an out-of-range value in a hand-edited file is not: it reaches the engine as
    written while sizing books the default. ``""`` for a knob with no default.
    """
    knob = _KNOBS_BY_ENV[env]
    value = carried.get(env)
    if value is not None and _range_problem(knob, value) is None:
        return value
    return "" if knob.default is None else knob.default


# ── XDNA (AMD NPU llama-server runner) knob set ───────────────────────────────
#
# Every ``GGML_XDNA_*`` variable the ggml-xdna backend reads, one-line-
# documented, copied from the backend README's variable table (the allow-list
# this module screens against). Unknown ``GGML_XDNA_*`` names are refused —
# this is the single source of truth for "a name the backend actually reads".
XDNA_ENV_DOCS: dict[str, str] = {
    "GGML_XDNA_W4": "comma-separated weight tensors kept at 4-bit for the decode GEMV (empty = all 8-bit)",
    "GGML_XDNA_HEAD": "1 runs the decode vocabulary projection on the array instead of the host",
    "GGML_XDNA_HEAD_EXACT_ROWS": "vocabulary rows the decode head keeps at 8-bit when GGML_XDNA_HEAD=1",
    "GGML_XDNA_TOKEN": "how a token's queued runs go to the device (1 joined, t>1 batched, 0 per-command)",
    "GGML_XDNA_SPIN_US": "microseconds to poll for completion before blocking (-1 = poll to completion)",
    "GGML_XDNA_GLUE_THREADS": "host threads for decode-sized host-side glue ops",
    "GGML_XDNA_GLUE_THREADS_BIG": "host threads for host-side glue ops on chunks of 32+ tokens",
    "GGML_XDNA_HOST_DECODE_THREADS": "host threads for per-op decode of layers outside the fused path",
    "GGML_XDNA_HOST_DECODE_PIN": "pin host decode threads one per core (OpenMP builds)",
    "GGML_XDNA_ARENA_MB": "size in MiB of each device-buffer arena chunk",
    "GGML_XDNA_CONV": "1 runs the standalone prefill SSM_CONV on the array instead of the host",
    "GGML_XDNA_GDN_CONV": "1 computes the prefill GDN conv input on the array instead of the host",
    "GGML_XDNA_KERNEL_DIR": "directory to search for kernel artifacts before the built-in locations",
    "GGML_XDNA_FUSED_LAYER": "0 disables the fused decode recurrent layer (per-op path runs instead)",
    "GGML_XDNA_GEMV_GROUP": "decode projections outside the fused layer, run on the array",
    "GGML_XDNA_GEMV_PROMOTE": "merge a decode GEMV group of mixed weight formats into one dispatch",
    "GGML_XDNA_QUEUE": "queue the fused layers (each one is waited for)",
    "GGML_XDNA_ATTN": "run decode attention on the array",
    "GGML_XDNA_ATTN_LAYER": "fuse a whole attention layer's q/k/v projections into one dispatch",
    "GGML_XDNA_ATTN_TAIL": "fuse an attention layer's tail (output proj, residual, norm, FFN)",
    "GGML_XDNA_GLUE": "run the host-side glue ops inside an NPU chunk instead of splitting the graph",
    "GGML_XDNA_HOST_BO": "backend host buffer type (plain CPU buffers)",
    "GGML_XDNA_PGEMM": "run the prefill GEMM on the array",
    "GGML_XDNA_INT8": "use the native int8 prefill GEMM",
    "GGML_XDNA_PGEMM_ADD": "fold the residual ADD into the norm's pass",
    "GGML_XDNA_PGEMM_NORM_A": "write the RMS norm directly into the next GEMM's A operand",
    "GGML_XDNA_PGEMM_GATED_A": "write a recurrent layer's gated output into the output projection's A",
    "GGML_XDNA_PGEMM_GATE_A": "write the attention gate into the output projection's A",
    "GGML_XDNA_PGEMM_GLU": "compute SwiGLU from the gate/up GEMMs on the array",
    "GGML_XDNA_PGEMM_GLU_A": "write SwiGLU directly into the down projection's A operand",
    "GGML_XDNA_GDN_MM": "run the prefill gated delta rule on the array",
    "GGML_XDNA_GDN_IN": "feed the prefill GDN's conv input/norms/state copy straight into the array",
    "GGML_XDNA_FA_MM": "run the prefill attention (FLASH_ATTN_EXT) on the array",
    "GGML_XDNA_PROF": "1: print a per-section time table at exit; 2: also list host-run ops once",
    "GGML_XDNA_PROF_TRACE": "with GGML_XDNA_PROF=1, write per-section timings to this file",
    "GGML_XDNA_GATED_FMT": "activation layout the gated/fused decode path expects (model-build dependent)",
}

#: Extra, non ``GGML_XDNA_*`` env var the xdna knob set allows: OpenMP's wait
#: policy. ``PASSIVE`` (the hal0 default) yields host cores between dispatches
#: instead of spinning them, which matters when the NPU is shared (coexistence
#: with an flm slot).
_XDNA_EXTRA_ENV = frozenset({"OMP_WAIT_POLICY"})

#: Env defaults hal0 applies for every xdna slot unless the operator's own
#: ``[server].env`` / tune knob overrides them.
XDNA_ENV_DEFAULTS: dict[str, str] = {
    "GGML_XDNA_PGEMM_GLU_A": "0",
    "OMP_WAIT_POLICY": "PASSIVE",
}

#: Env values hal0 PINS on every xdna slot: correctness facts, not knobs
#: (ADR-0057 decision 3). ``GGML_XDNA_PGEMM_GLU_A=1`` (the fork's own default)
#: fuses SwiGLU into the down projection's A operand and silently corrupts
#: prefill logits, so the slot-config write refuses any other value
#: (:func:`refuse_xdna_pinned_env`) and the launch path writes these over
#: whatever the merged env says, so a hand-edited TOML cannot flip it back on.
XDNA_PINNED_ENV: dict[str, str] = {
    "GGML_XDNA_PGEMM_GLU_A": "0",
}

#: Valid ``pmode`` values (AMD ``xrt-smi configure --pmode``). hal0 only
#: validates and surfaces this — see :func:`xdna_env_defaults`'s module
#: docstring note below; the container cannot apply it itself.
XDNA_PMODES: tuple[str, ...] = ("default", "turbo", "balanced", "powersaver", "performance")


def xdna_env_defaults() -> dict[str, str]:
    """hal0's default ``GGML_XDNA_*``/``OMP_WAIT_POLICY`` env for an xdna slot."""
    return dict(XDNA_ENV_DEFAULTS)


#: Doc string for the optional ``[xdna].host_xrt`` slot-config key (84b3b186's
#: pattern: an absolute host path RO-bind-mounted into the container, with
#: ``HAL0_XDNA_HOST_XRT`` set to the same path so the entrypoint prefers it
#: over the image's baked XRT).
XDNA_HOST_XRT_DOC = (
    "Absolute host path to an XRT install, RO bind-mounted into the "
    "container as HAL0_XDNA_HOST_XRT; unset uses the image's baked XRT."
)


def xdna_knob_schema() -> dict[str, Any]:
    """The xdna runner's dashboard knob schema (``GET /api/runners/xdna/knobs``).

    ``{"pmodes": [...], "env": [{"name", "default", "doc", "fixed"}, ...],
    "host_xrt": {"doc": ...}}`` — built straight off :data:`XDNA_PMODES`,
    :data:`XDNA_ENV_DOCS`, :data:`XDNA_ENV_DEFAULTS` and
    :data:`XDNA_PINNED_ENV` so the dashboard never hardcodes the knob set.
    ``fixed`` marks a pinned variable: shown, never editable.
    """
    return {
        "pmodes": list(XDNA_PMODES),
        "env": [
            {
                "name": name,
                "default": XDNA_ENV_DEFAULTS.get(name),
                "doc": doc,
                "fixed": name in XDNA_PINNED_ENV,
            }
            for name, doc in sorted(XDNA_ENV_DOCS.items())
        ],
        "host_xrt": {"doc": XDNA_HOST_XRT_DOC},
    }


def screen_xdna_env(env: Mapping[str, str] | None) -> None:
    """Refuse an xdna slot's env dict naming an unknown ``GGML_XDNA_*`` key.

    Any ``GGML_XDNA_*`` name not in :data:`XDNA_ENV_DOCS` is rejected —
    that is the allow-list this screen enforces (ADR-0028 / the xdna-runner
    plan: the backend's documented variable list is the whole knob surface,
    nothing invented). ``OMP_WAIT_POLICY`` and any non-``GGML_XDNA_``-
    prefixed key pass through unexamined.
    """
    if not env:
        return
    for name in env:
        if name.startswith("GGML_XDNA_") and name not in XDNA_ENV_DOCS:
            raise UnprocessableEntity(
                f"{name} is not a GGML_XDNA_* variable hal0's xdna runner knows about",
                code="runners.xdna_env_unknown",
                details={"flag": name, "allowed": sorted(XDNA_ENV_DOCS)},
            )


def refuse_xdna_pinned_env(env: Mapping[str, Any] | None) -> None:
    """Refuse an xdna env override that sets a pinned variable to another value.

    :data:`XDNA_PINNED_ENV` holds correctness facts; restating the pinned
    value is harmless and passes, anything else is a 400 naming the key.
    """
    from hal0.errors import BadRequest

    if not env:
        return
    for name, pinned in XDNA_PINNED_ENV.items():
        if name in env and str(env[name]).strip() != pinned:
            raise BadRequest(
                f"{name} is pinned to {pinned!r} on the xdna runner: any other value "
                "silently corrupts prefill logits (wrong tokens, no error); "
                "remove it from [xdna.env]",
                code="validation.xdna_env_pinned",
                details={"key": name, "value": str(env[name]), "pinned": pinned},
            )


def validate_xdna_pmode(pmode: str | None) -> str | None:
    """Refuse a ``pmode`` outside :data:`XDNA_PMODES`; ``None`` passes through.

    Validation + status-surfacing only: the container cannot change the
    host's NPU power mode (xrt-smi talks to the NPU root device, not
    anything namespaced into the container). TODO(xdna-runner): once a host
    agent applies this, call ``xrt-smi configure --pmode <value>`` there and
    stop treating pmode as display-only.
    """
    if pmode is None:
        return None
    if pmode not in XDNA_PMODES:
        raise UnprocessableEntity(
            f"pmode must be one of {', '.join(XDNA_PMODES)}; got {_echo(pmode)!r}",
            code="runners.xdna_pmode_invalid",
            details={"value": pmode, "allowed": list(XDNA_PMODES)},
        )
    return pmode


def screen_halogen_flags(flags: str | None) -> None:
    """Refuse a halogen profile's ``flags`` text that hal0 cannot accept.

    Parses with :func:`parse_env_tokens`, then for each variable raises
    ``UnprocessableEntity``:

    * ``profiles.halogen_flag_denied`` (``details`` ``flag`` and ``owner``) when
      the name is in :data:`HALOGEN_MANAGED_ENV`, :data:`HALOGEN_TUNE_ENV`,
      :data:`HALOGEN_DENIED_ENV` or under one of their prefixes. ``owner`` is
      the hal0 field that owns the fact: ``slot port``, ``model row``, ``tune``,
      ``hal0`` or ``phase 2``.
    * ``profiles.halogen_flag_out_of_range`` when a knob in
      :data:`HALOGEN_KNOBS` carries a value outside its range or choices.

    Names that are neither owned nor curated pass: the rest of the engine's
    flag reference stays reachable as free text.
    """
    for name, value in parse_env_tokens(flags).items():
        owner = _flag_owner(name)
        if owner is not None:
            raise UnprocessableEntity(
                f"{name} is owned by {owner} and cannot be set in profile flags",
                code="profiles.halogen_flag_denied",
                details={"flag": name, "owner": owner},
            )
        knob = _KNOBS_BY_ENV.get(name)
        if knob is None:
            continue
        problem = _range_problem(knob, value)
        if problem is not None:
            raise UnprocessableEntity(
                problem,
                code="profiles.halogen_flag_out_of_range",
                details={"flag": name, "value": _echo(value)},
            )
