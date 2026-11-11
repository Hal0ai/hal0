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

:func:`tags_readable` evaluates groups the same way inside hal0, for the
reads whose engine route takes no ``tag_groups`` (the documents listing) and
as a second check on what a filtered read returned.
"""

from __future__ import annotations

from typing import Any

from hal0.memory.envelope import (
    AGENT_TAG_PREFIX,
    PROJECT_TAG_PREFIX,
    PROJECT_UNRESOLVED_TAG,
    SCOPE_GLOBAL_TAG,
    is_identified,
)

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
    identity (none, ``anonymous`` or ``unknown``) reads no private memory at
    all, and no private memory owned by ``unknown`` is readable by anyone.

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
    if is_identified(agent_id):
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


def unresolved_scope_groups(agent_id: str | None) -> list[dict[str, Any]]:
    """Engine ``tag_groups`` for a coding client that has no project.

    Such a caller's writes are stamped ``project:unresolved`` (the write
    envelope), which is a marker and not a project, so
    :func:`project_scope_groups` would hide them from the client that wrote
    them. This scope reads global memory plus the caller's own unresolved
    writes::

        scope:global  OR  (project:unresolved AND agent:<self>)

    The first leaf uses ``match: "any"`` and so still admits a fact with no
    tags at all, exactly as :func:`project_scope_groups` does. The second is
    an ``and`` of two ``any_strict`` leaves: another agent's unresolved
    writes, which may come from any repository, stay outside it. No project's
    memory is inside it.

    With no identity there is nothing to own, and the result is the global
    scope alone. Like :func:`project_scope_groups` the result is a list of one
    group, to be concatenated with :func:`reflect_visibility_scope`.
    """
    global_scope: dict[str, Any] = {"tags": [SCOPE_GLOBAL_TAG], "match": "any"}
    if not is_identified(agent_id):
        return [global_scope]
    own_unresolved = {
        "and": [
            {"tags": [PROJECT_UNRESOLVED_TAG], "match": "any_strict"},
            {"tags": [f"{AGENT_TAG_PREFIX}{agent_id}"], "match": "any_strict"},
        ]
    }
    return [{"or": [global_scope, own_unresolved]}]


_LEAF_MATCHES = frozenset({"any", "all", "any_strict", "all_strict", "exact"})


class _Unevaluable(ValueError):
    """A group hal0 cannot evaluate. Raised, not answered ``False``: a ``False``
    from a leaf inside a ``not`` would read as "admitted"."""


def _group_admits(group: Any, have: frozenset[str]) -> bool:
    if not isinstance(group, dict):
        raise _Unevaluable
    if "tags" in group:
        wanted = group["tags"]
        match = group.get("match", "any_strict")
        if (
            not isinstance(wanted, list)
            or not all(isinstance(tag, str) for tag in wanted)
            or match not in _LEAF_MATCHES
            # A fuzzy leaf is resolved against the bank's tag list by the
            # engine, which hal0 does not have here.
            or group.get("resolve", "exact") != "exact"
        ):
            raise _Unevaluable
        want = frozenset(wanted)
        if match == "exact":
            return want == have
        overlap = bool(want & have) if match.startswith("any") else want <= have
        if match.endswith("_strict"):
            return bool(have) and overlap
        return not have or overlap
    for combinator in ("and", "or"):
        if combinator in group:
            children = group[combinator]
            if not isinstance(children, list):
                raise _Unevaluable
            # Every child is evaluated, so an unevaluable one is never skipped
            # by a short circuit.
            verdicts = [_group_admits(child, have) for child in children]
            return all(verdicts) if combinator == "and" else any(verdicts)
    if "not" in group:
        return not _group_admits(group["not"], have)
    raise _Unevaluable


def tags_readable(groups: list[dict[str, Any]], tags: Any) -> bool:
    """True when a memory carrying ``tags`` is inside ``groups``.

    The engine semantics in the module docstring, evaluated in hal0: the
    groups are AND-ed, a leaf defaults to ``any_strict``, ``any`` and ``all``
    admit an untagged memory, and ``exact`` is set equality. If any group, at
    any depth, is one hal0 cannot evaluate (an unknown shape, an unknown
    match, a fuzzy leaf), the answer is ``False`` for every memory, so a
    caller of this function fails closed. ``tags`` that is not a list counts
    as no tags.
    """
    have = (
        frozenset(t for t in tags if isinstance(t, str)) if isinstance(tags, list) else frozenset()
    )
    try:
        return all([_group_admits(group, have) for group in groups])
    except _Unevaluable:
        return False


__all__ = [
    "VISIBILITY_PRIVATE_TAG",
    "project_scope_groups",
    "reflect_visibility_scope",
    "tags_readable",
    "unresolved_scope_groups",
]
