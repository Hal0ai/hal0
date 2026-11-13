"""Does the store's embedder need the operator? One rule, two readers (#3518).

``hal0 doctor``'s ``memory_embedder`` row and the Memory health path
(:mod:`hal0.memory.health_path`) both ask this of an
:func:`hal0.memory.embedder_record.embedder_status` payload. They must agree,
so the rule lives here and neither keeps a copy.

The case it exists to tell apart is the ordinary upgraded box: its store was
built with the engine default, ``[memory.models]`` still holds the shipped
default, and the two differ. Nothing the operator did caused that and the
engine stays on the recorded embedder, so it is ``informational``, never a
reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

from hal0.memory.embedder_record import SOURCE_SWITCH, shipped_default_embedder

#: Reason codes, in the order they are reported. The first one names the cause
#: to deal with first: an unreadable record or an absent store makes the other
#: comparisons unreliable.
RECORD_UNREADABLE = "record_unreadable"
STORE_UNAVAILABLE = "store_unavailable"
CONFLICT = "conflict"
MIXED = "mixed"
NEEDS_SWITCH = "needs_switch"


class EmbedderVerdict(NamedTuple):
    """``reasons`` is empty when the embedder needs nothing from the operator."""

    reasons: tuple[str, ...]
    informational: bool


def embedder_verdict(status: Mapping[str, Any]) -> EmbedderVerdict:
    """Classify an ``embedder_status`` payload. Pure; reads nothing.

    Reasons, any number of which may hold at once:

    * ``record_unreadable``: the record file exists and cannot be used;
    * ``store_unavailable``: the store's location cannot be seen;
    * ``conflict``: the running engine or its unit files name another embedder
      than the record;
    * ``mixed``: the store may hold vectors from two embedders;
    * ``needs_switch``: the operator configured a provider or model of their
      own that differs from the record.

    ``informational`` is true when record and config differ and none of the
    reasons hold: the config is still at its shipped default, or the store
    was put on its embedder by ``hal0 memory embedder switch``. In the second
    case the operator chose the store's embedder on purpose; the config
    naming another one is then a fact to state, not a problem.
    """
    recorded = (status.get("recorded_provider"), status.get("recorded"))
    configured = (status.get("configured_provider"), status.get("configured"))
    differs = recorded != configured
    operator_set = configured != tuple(shipped_default_embedder())
    reasons: list[str] = []
    if status.get("record_error"):
        reasons.append(RECORD_UNREADABLE)
    if status.get("store_unavailable"):
        reasons.append(STORE_UNAVAILABLE)
    if status.get("conflict"):
        reasons.append(CONFLICT)
    if status.get("mixed"):
        reasons.append(MIXED)
    switched = status.get("source") == SOURCE_SWITCH
    if differs and operator_set and not switched:
        reasons.append(NEEDS_SWITCH)
    return EmbedderVerdict(tuple(reasons), informational=differs and not reasons)


__all__ = [
    "CONFLICT",
    "MIXED",
    "NEEDS_SWITCH",
    "RECORD_UNREADABLE",
    "STORE_UNAVAILABLE",
    "EmbedderVerdict",
    "embedder_verdict",
]
