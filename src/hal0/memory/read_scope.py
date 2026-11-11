"""Engine tag filters for reads: who may read a memory, and in which project.

A recall returns items, and hal0 can filter them after the engine answers. A
reflect returns prose, which cannot be filtered afterwards, so its rules go to
the engine as ``tag_groups``. This module builds those groups. It is pure: no
I/O, no provider state.

Engine semantics relied on (Hindsight 1.2.0, recall and reflect):

* ``tag_groups`` is a list, and its entries are AND-ed;
* a leaf is ``{"tags": [...], "match": ...}`` with ``any_strict`` as the
  default match; ``{"and": [...]}``, ``{"or": [...]}`` and ``{"not": {...}}``
  combine leaves;
* a leaf with ``match: "any"`` also matches a fact that has no tags at all,
  while ``any_strict`` does not;
* ``not`` over an ``any`` leaf therefore drops untagged facts, so a negation
  uses ``any_strict`` inside;
* a request that carries both ``tags`` and ``tag_groups`` is refused (422).
"""

from __future__ import annotations

from typing import Any

from hal0.memory.envelope import AGENT_TAG_PREFIX, PROJECT_TAG_PREFIX, SCOPE_GLOBAL_TAG

#: The tag of a memory only its owning agent may read (ADR-0005).
VISIBILITY_PRIVATE_TAG = "visibility:private"


def reflect_visibility_scope(
    agent_id: str | None, tags: list[str] | None = None, tags_match: str | None = None
) -> list[dict[str, Any]]:
    """Engine ``tag_groups`` that hold a reflect to what ``agent_id`` may read.

    Recall filters its results after the fact
    (``HindsightProvider._is_visible_to``); a reflect answer is prose and cannot
    be filtered afterwards, so the same rule is given to the engine as a tag
    filter instead: a memory is readable when it is not ``visibility:private``,
    or when it carries the caller's own ``agent:<id>`` tag. A caller with no
    identity reads no private memory at all.

    What this covers and what it does not: the engine applies ``tag_groups``
    to the reflect loop's fact and observation searches, where every item
    carries the tags of the write it came from. It also filters the mental
    model search by the MODEL's own tags, which says nothing about what the
    model was built from: a mental model is a document the engine writes from
    the whole bank, and hal0's seeded models carry no tags. A tag filter
    therefore cannot keep another agent's private notes out of mental-model
    text; ``HindsightProvider.reflect`` leaves mental models out of the loop
    instead wherever the bank holds more than one agent's private memories.

    ``tags`` / ``tags_match`` are the caller's own scoping. The engine refuses
    ``tags`` next to ``tag_groups``, so they ride along as one more group
    (groups are AND-ed) with the engine's top-level default, ``any``.
    """
    readable: dict[str, Any] = {"not": {"tags": [VISIBILITY_PRIVATE_TAG], "match": "any_strict"}}
    if agent_id:
        own = {"tags": [f"{AGENT_TAG_PREFIX}{agent_id}"], "match": "any_strict"}
        readable = {"or": [readable, own]}
    groups = [readable]
    if tags:
        groups.append({"tags": list(tags), "match": tags_match or "any"})
    return groups


def project_scope_groups(slug: str | None) -> list[dict[str, Any]]:
    """Engine ``tag_groups`` that hold a read to one project plus global memory.

    With a slug the read sees facts tagged ``project:<slug>`` or
    ``scope:global``; with ``None`` it sees ``scope:global`` only. Either way
    the one group uses ``match: "any"``, which also admits a fact with no tags
    at all, by design: a memory written before tags existed stays readable.
    A fact that has tags but neither of these (another project's, or one
    written through hal0 before ``scope:global`` was stamped) is outside the
    scope.

    The result is a list of one group, ready to be concatenated with
    :func:`reflect_visibility_scope`: the engine ANDs the entries, so the
    read is held to both rules. ``slug`` is used as given; resolve it first
    (:func:`hal0.memory.projects.resolve_project`).
    """
    tags = [SCOPE_GLOBAL_TAG]
    if slug:
        tags.insert(0, f"{PROJECT_TAG_PREFIX}{slug}")
    return [{"tags": tags, "match": "any"}]


__all__ = [
    "VISIBILITY_PRIVATE_TAG",
    "project_scope_groups",
    "reflect_visibility_scope",
]
