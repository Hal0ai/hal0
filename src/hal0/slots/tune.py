"""Effective launch tune for a slot: profile values plus the slot's ``[tune]`` overrides.

ADR-0029 keeps every launch tune on the profile. A slot may carry a sparse
``[tune]`` table (:class:`hal0.config.schema.SlotTuneConfig`) that overrides a
few of them. :func:`effective_tune` is the one place that merge happens, and it
records where each value came from so the console can show it.

A ``--reasoning on|off`` token in the profile's ``flags`` is a hard pin on
thinking: it wins over both the slot and the profile ``enable_thinking``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hal0.config.schema import TUNE_KEYS
from hal0.errors import BadRequest
from hal0.slots.argv import _split_pairs

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


def reasoning_flag_in(flags: str) -> bool | None:
    """The ``--reasoning`` value pinned in ``flags``: ``on`` True, ``off`` False, else None.

    Last occurrence wins. Every spelling argv folds (``-rea``, ``--reasoning=off``,
    ``--reasoning_x``) is honoured because the match goes through ``_split_pairs``.
    """
    pinned: bool | None = None
    for pair in _split_pairs((flags or "").split()):
        if pair.canon != "--reasoning":
            continue
        value: str | None
        if pair.flag is not None and "=" in pair.flag:
            value = pair.flag.split("=", 1)[1]
        else:
            value = pair.values[0] if pair.values else None
        if value is None:
            continue
        low = value.strip().lower()
        pinned = True if low == "on" else False if low == "off" else None
    return pinned


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
    table = slot_tune_table(slot_cfg)
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
