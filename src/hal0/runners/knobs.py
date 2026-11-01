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
from dataclasses import dataclass
from typing import Literal

from hal0.errors import UnprocessableEntity

#: Engine context window in tokens when no profile ``ctx_size`` narrows it.
HALOGEN_NATIVE_CTX = 262144


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
        help="Total token positions the engine reserves for its KV cache, shared by every KV slot.",
        memory=True,
    ),
    EngineKnob(
        env="HALOGEN_KV_SLOTS",
        label="KV slots",
        kind="int",
        default="2",
        minimum=1,
        maximum=64,
        help="How many conversations the engine keeps warm at once, each in its own KV slot.",
        memory=True,
    ),
    EngineKnob(
        env="HALOGEN_MAX_TOK",
        label="Max tokens per pass",
        kind="enum",
        default="16384",
        choices=("16384", "32768"),
        help="Most tokens the engine takes in one pass; the larger choice needs more working memory.",
        memory=True,
    ),
    EngineKnob(
        env="HALOGEN_WEIGHTS_LOCK",
        label="Lock weights in memory",
        kind="bool",
        default="1",
        help="Pins the model weights in RAM so the kernel never pages them out.",
    ),
    EngineKnob(
        env="HALOGEN_KV8",
        label="8-bit KV cache",
        kind="bool",
        default="0",
        help="Stores the KV cache in 8 bits per value, which shrinks it but changes the numbers the model produces.",
        memory=True,
    ),
    EngineKnob(
        env="HALOGEN_PROMPT_CACHE",
        label="Prompt cache",
        kind="enum",
        default="2",
        choices=("0", "1", "2"),
        help="Selects the engine's prompt-cache mode; 0 turns it off and 2 is the default.",
    ),
    EngineKnob(
        env="HALOGEN_MAX_TOKENS_DEFAULT",
        label="Default max tokens",
        kind="int",
        default=None,
        minimum=1,
        maximum=65536,
        help="Cap on generated tokens for a request that sets none of its own; unset keeps the engine's default.",
    ),
    EngineKnob(
        env="HALOGEN_HOST_RESERVE_GIB",
        label="Host memory reserve (GiB)",
        kind="int",
        default="20",
        minimum=_HOST_RESERVE_FLOOR_GIB,
        help="Memory in GiB the engine leaves free for the rest of the host, never under 10 so the lookup table keeps its page cache.",
    ),
)

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

_TOKEN = re.compile(r"(HALOGEN_[A-Z0-9_]+)=(.*)")
_WHOLE_NUMBER = re.compile(r"[0-9]+")


def parse_env_tokens(flags: str | None) -> dict[str, str]:
    """Read a halogen profile's ``flags`` text as ``{NAME: value}``.

    Every shell token must be ``HALOGEN_NAME=value`` with a non-empty value;
    quoting is shell quoting, so ``HALOGEN_X="two words"`` is one variable.
    A later duplicate wins. ``None`` or blank text yields ``{}``. Raises
    ``UnprocessableEntity`` (``profiles.halogen_flag_malformed``) for invalid
    shell text, a token of any other shape, an empty value, or a value with a
    line break (it would split an ``Environment=`` line in the unit file).
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
        out[name] = value
    return out


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
        return None if value in ("0", "1") else f"{knob.env} must be 0 or 1; got {value!r}"
    if knob.kind == "enum":
        if value in knob.choices:
            return None
        return f"{knob.env} must be one of {', '.join(knob.choices)}; got {value!r}"
    # int
    if _WHOLE_NUMBER.fullmatch(value) is None:
        return f"{knob.env} must be a whole number; got {value!r}"
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
    knobs = {knob.env: knob for knob in HALOGEN_KNOBS}
    for name, value in parse_env_tokens(flags).items():
        owner = _flag_owner(name)
        if owner is not None:
            raise UnprocessableEntity(
                f"{name} is owned by {owner} and cannot be set in profile flags",
                code="profiles.halogen_flag_denied",
                details={"flag": name, "owner": owner},
            )
        knob = knobs.get(name)
        if knob is None:
            continue
        problem = _range_problem(knob, value)
        if problem is not None:
            raise UnprocessableEntity(
                problem,
                code="profiles.halogen_flag_out_of_range",
                details={"flag": name, "value": value},
            )
