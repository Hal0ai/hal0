"""Effective launch tune for a slot: profile values plus the slot's ``[tune]`` overrides.

ADR-0029 keeps every launch tune on the profile. A slot may carry a sparse
``[tune]`` table (:class:`hal0.config.schema.SlotTuneConfig`) that overrides a
few of them. :func:`effective_tune` is the one place that merge happens, and it
records where each value came from so the console can show it.

A ``--reasoning on|off`` token in the profile's ``flags`` is a hard pin on
thinking: it wins over both the slot and the profile ``enable_thinking``.
"""

from __future__ import annotations

import logging
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, get_args

from pydantic import ValidationError

from hal0.config.schema import TUNE_KEYS, ReasoningEffort, SlotTuneConfig
from hal0.errors import BadRequest
from hal0.slots.argv import _split_pairs

log = logging.getLogger(__name__)

#: ``(slot, key, value)`` triples already warned about, so a hand-edited bad
#: ``[tune]`` key logs once per distinct value, not once per read.
_invalid_warned: set[tuple[str, str, str]] = set()

#: Effort levels llama-server accepts on ``--reasoning-effort`` (``default`` is
#: hal0's "send nothing" and is not a flag value).
_EFFORT_FLAG_VALUES = frozenset(get_args(ReasoningEffort)) - {"default"}

#: Fallback per key when the profile object does not carry the attribute.
_DEFAULTS: dict[str, Any] = {
    "ctx_size": None,
    "enable_thinking": None,
    "reasoning_effort": "default",
    "reasoning_budget": -1,
    "vision": "auto",
    "speculation": "auto",
}

_RESTART_KEYS = frozenset(k for k in TUNE_KEYS if k != "enable_thinking")


def slot_tune_table(slot_cfg: Mapping[str, Any]) -> dict[str, Any]:
    """The slot's raw ``tune`` table; ``{}`` when absent or not a mapping. ``None`` values dropped."""
    table = slot_cfg.get("tune")
    if not isinstance(table, Mapping):
        return {}
    return {k: v for k, v in table.items() if v is not None}


def slot_tune_table_valid(slot_cfg: Mapping[str, Any]) -> dict[str, Any]:
    """The slot's ``[tune]`` table with every invalid key dropped.

    Each key is validated alone with :class:`~hal0.config.schema.SlotTuneConfig`'s
    own rules, so a hand-edited value outside them (an unknown effort, a
    non-integer budget, a window out of range, flag-shaped text) never reaches
    the launch command or the console: the key falls back to the profile's
    value and one warning is logged per distinct bad value.
    """
    out: dict[str, Any] = {}
    for key, value in slot_tune_table(slot_cfg).items():
        if key not in TUNE_KEYS:
            continue
        try:
            if isinstance(value, bool) and key in ("ctx_size", "reasoning_budget"):
                raise ValueError("a boolean is not a number")
            checked = getattr(SlotTuneConfig.model_validate({key: value}), key)
        except (ValidationError, ValueError):
            seen = (str(slot_cfg.get("name") or ""), key, repr(value))
            if seen not in _invalid_warned:
                _invalid_warned.add(seen)
                log.warning(
                    "slot.tune_key_invalid slot=%s key=%s value=%r: ignored, the profile's "
                    "value applies",
                    seen[0],
                    key,
                    value,
                )
            continue
        if checked is not None:
            out[key] = checked
    return out


def flag_value_in(flags: str, canon: str) -> str | None:
    """The value of the last ``canon`` flag in ``flags`` that carries one, else None.

    Every spelling argv folds (aliases, ``--flag=value``) is honoured because
    the match goes through ``_split_pairs``. Malformed flag text reads as absent.
    """
    try:
        tokens = shlex.split(flags or "")
    except ValueError:  # stored flags can be malformed; the save screen refuses new ones
        return None
    found: str | None = None
    for pair in _split_pairs(tokens):
        if pair.canon != canon:
            continue
        if pair.flag is not None and "=" in pair.flag:
            found = pair.flag.split("=", 1)[1]
        elif pair.values:
            found = pair.values[0]
    return found


def reasoning_flag_in(flags: str) -> bool | None:
    """The ``--reasoning`` value pinned in ``flags``: ``on`` True, ``off`` False, else None.

    Last occurrence wins; a later `--reasoning auto` or unknown value resets an
    earlier pin.
    """
    value = flag_value_in(flags, "--reasoning")
    if value is None:
        return None
    low = value.strip().lower()
    return True if low == "on" else False if low == "off" else None


def effort_flag_in(flags: str) -> str | None:
    """The effort ``--reasoning-effort`` pins in ``flags``, or None (absent or not a level)."""
    value = flag_value_in(flags, "--reasoning-effort")
    low = (value or "").strip().lower()
    return low if low in _EFFORT_FLAG_VALUES else None


def budget_flag_in(flags: str) -> int | None:
    """The token budget ``--reasoning-budget`` pins in ``flags``, or None (absent or not >= -1)."""
    value = flag_value_in(flags, "--reasoning-budget")
    try:
        budget = int((value or "").strip())
    except ValueError:
        return None
    return budget if budget >= -1 else None


@dataclass(frozen=True, slots=True)
class EffectiveTune:
    """A slot's merged launch tune. ``sources`` maps each tune key to its origin."""

    name: str
    flags: str
    runner: str | None
    ctx_size: int | None
    enable_thinking: bool | None
    reasoning_effort: str
    reasoning_budget: int
    vision: str
    speculation: str
    sources: Mapping[str, str]
    thinking_pinned: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        """The six tune values plus ``sources``."""
        out: dict[str, Any] = {k: getattr(self, k) for k in TUNE_KEYS}
        out["sources"] = dict(self.sources)
        return out


def effective_tune(slot_cfg: Mapping[str, Any], profile: Any) -> EffectiveTune:
    """Merge ``profile`` with the slot's ``[tune]`` overrides.

    ``profile`` may be a ``ProfileConfig``, a ``ResolvedProfile`` or any object
    with the same attributes; missing ones fall back to the profile defaults.
    """
    table = slot_tune_table_valid(slot_cfg)
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for key in TUNE_KEYS:
        if key in table:
            values[key] = table[key]
            sources[key] = "slot"
        else:
            values[key] = getattr(profile, key, _DEFAULTS[key])
            sources[key] = "profile"
    flags = getattr(profile, "flags", "") or ""
    pinned = reasoning_flag_in(flags)
    if pinned is not None:
        values["enable_thinking"] = pinned
        sources["enable_thinking"] = "profile-flags"
    # A free `--reasoning-effort` / `--reasoning-budget` in the profile's flags
    # beats the profile's typed field in the launch command (same layer, later),
    # so it is what runs. A slot override still wins, except a "send nothing"
    # value (effort ``default``, budget ``-1``): that cannot cancel a flag.
    flag_effort = effort_flag_in(flags)
    if flag_effort is not None and (
        sources["reasoning_effort"] != "slot" or values["reasoning_effort"] == "default"
    ):
        values["reasoning_effort"] = flag_effort
        sources["reasoning_effort"] = "profile-flags"
    flag_budget = budget_flag_in(flags)
    if flag_budget is not None and (
        sources["reasoning_budget"] != "slot" or values["reasoning_budget"] == -1
    ):
        values["reasoning_budget"] = flag_budget
        sources["reasoning_budget"] = "profile-flags"
    return EffectiveTune(
        name=getattr(profile, "name", ""),
        flags=flags,
        runner=getattr(profile, "runner", None),
        sources=sources,
        thinking_pinned=pinned,
        **values,
    )


def tune_restart_keys() -> frozenset[str]:
    """Tune keys whose change needs a slot restart (everything but ``enable_thinking``)."""
    return _RESTART_KEYS


def guard_tune_write(
    base_cfg: Mapping[str, Any], updates: Mapping[str, Any], profile: Any | None
) -> None:
    """Refuse a slot write whose ``tune`` table cannot take effect.

    No-op without a ``tune`` update, and a clear (``tune`` is None) is never
    refused. ``profile`` is None when the caller could not resolve it, which
    skips the flag-pin check.
    """
    if "tune" not in updates or updates["tune"] is None:
        return
    merged_type = updates.get("type", base_cfg.get("type")) or "llm"
    merged_device = updates.get("device", base_cfg.get("device"))
    if merged_type != "llm" or merged_device == "npu":
        raise BadRequest(
            "per-slot [tune] overrides apply only to llm slots on a non-NPU device "
            f"(type={merged_type!r}, device={merged_device!r})",
            code="slot.tune_not_supported",
            details={"type": merged_type, "device": merged_device},
        )
    tune = updates["tune"]
    wants_thinking = (
        tune.get("enable_thinking")
        if isinstance(tune, Mapping)
        else getattr(tune, "enable_thinking", None)
    )
    if wants_thinking is not None and profile is not None:
        pinned = reasoning_flag_in(getattr(profile, "flags", "") or "")
        if pinned is not None:
            raise BadRequest(
                "enable_thinking is pinned by `--reasoning` in the profile's flags; "
                "edit the profile's flags to change it",
                code="slot.tune_pinned_by_flags",
                details={"pinned": pinned},
            )
