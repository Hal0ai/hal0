"""Read-scope tag groups (ADR-0058 decisions 4a and 6), held to engine semantics.

The groups are evaluated the way Hindsight 1.2.0 evaluates ``tag_groups`` on
recall and reflect, restated here from the facts verified against a live
engine:

* ``tag_groups`` is a list whose entries are AND-ed;
* a leaf is ``{tags, match}`` and ``match`` defaults to ``any_strict``;
* ``any`` / ``all`` also match a fact with no tags; the ``_strict`` forms do
  not;
* ``{and: [...]}``, ``{or: [...]}`` and ``{not: {...}}`` combine leaves, so
  ``not`` over an ``any`` leaf drops untagged facts;
* ``tags`` next to ``tag_groups`` is refused with 422.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.memory.envelope import apply_envelope
from hal0.memory.hindsight_provider import _VISIBILITY_PRIVATE, HindsightProvider
from hal0.memory.projects import ProjectResolution
from hal0.memory.read_scope import (
    VISIBILITY_PRIVATE_TAG,
    project_scope_groups,
    reflect_visibility_scope,
)


def _leaf_matches(leaf: dict[str, Any], tags: list[str]) -> bool:
    wanted, have = set(leaf["tags"]), set(tags)
    match = leaf.get("match", "any_strict")
    if match == "exact":
        return wanted == have
    overlap = bool(wanted & have) if match.startswith("any") else wanted <= have
    if match.endswith("_strict"):
        return bool(have) and overlap
    return not have or overlap


def _matches(group: dict[str, Any], tags: list[str]) -> bool:
    if "tags" in group:
        return _leaf_matches(group, tags)
    if "and" in group:
        return all(_matches(child, tags) for child in group["and"])
    if "or" in group:
        return any(_matches(child, tags) for child in group["or"])
    return not _matches(group["not"], tags)


def _readable(groups: list[dict[str, Any]], tags: list[str]) -> bool:
    return all(_matches(group, tags) for group in groups)


# ── the evaluator itself, against the verified engine facts ────────────────


def test_the_evaluator_restates_the_engine_facts():
    untagged: list[str] = []
    assert _matches({"tags": ["a"], "match": "any"}, untagged)
    assert not _matches({"tags": ["a"], "match": "any_strict"}, untagged)
    assert not _matches({"tags": ["a"]}, untagged)  # default is any_strict
    assert not _matches({"not": {"tags": ["a"], "match": "any"}}, untagged)
    assert _matches({"not": {"tags": ["a"], "match": "any_strict"}}, untagged)
    assert _readable([{"tags": ["a"]}, {"tags": ["b"]}], ["a", "b"])
    assert not _readable([{"tags": ["a"]}, {"tags": ["b"]}], ["a"])


# ── project_scope_groups ────────────────────────────────────────────────────


def test_project_scope_groups_shape():
    assert project_scope_groups("atlas") == [
        {"tags": ["project:atlas", "scope:global"], "match": "any"}
    ]
    assert project_scope_groups(None) == [{"tags": ["scope:global"], "match": "any"}]
    assert project_scope_groups("") == [{"tags": ["scope:global"], "match": "any"}]


ATLAS_FACT = ["agent:pi", "kind:durable", "project:atlas"]
BOREALIS_FACT = ["agent:pi", "kind:durable", "project:borealis"]
GLOBAL_FACT = ["agent:hermes", "kind:durable", "scope:global"]
UNRESOLVED_FACT = ["agent:pi", "kind:durable", "project:unresolved"]
UNTAGGED_FACT: list[str] = []
#: Written through hal0 before ``scope:global`` existed: tagged, but with
#: neither a project nor the global scope.
PRE_SCOPE_FACT = ["agent:hermes"]
EPHEMERAL_FACT = ["agent:hermes", "kind:ephemeral"]


@pytest.mark.parametrize(
    ("slug", "readable"),
    [
        ("atlas", [ATLAS_FACT, GLOBAL_FACT, UNTAGGED_FACT]),
        ("borealis", [BOREALIS_FACT, GLOBAL_FACT, UNTAGGED_FACT]),
        ("cirrus", [GLOBAL_FACT, UNTAGGED_FACT]),
        (None, [GLOBAL_FACT, UNTAGGED_FACT]),
    ],
)
def test_a_project_scope_reads_its_project_global_and_untagged_facts(slug, readable):
    facts = [
        ATLAS_FACT,
        BOREALIS_FACT,
        GLOBAL_FACT,
        UNRESOLVED_FACT,
        UNTAGGED_FACT,
        PRE_SCOPE_FACT,
        EPHEMERAL_FACT,
    ]
    groups = project_scope_groups(slug)
    assert [tags for tags in facts if _readable(groups, tags)] == readable


def test_reading_across_projects_is_never_the_fallback():
    """A read with no project sees no project's memory."""
    groups = project_scope_groups(None)
    assert not _readable(groups, ATLAS_FACT)
    assert not _readable(groups, UNRESOLVED_FACT)


def test_untagged_facts_stay_readable_by_design():
    """``match: any`` admits a fact with no tags, so a memory from before tags
    existed is not lost; ``any_strict`` would drop it."""
    for slug in ("atlas", None):
        (group,) = project_scope_groups(slug)
        assert group["match"] == "any"
        assert _matches(group, UNTAGGED_FACT)
        assert not _matches({**group, "match": "any_strict"}, UNTAGGED_FACT)


def test_the_groups_read_what_the_envelope_writes():
    atlas = ProjectResolution(slug="atlas", source="field")
    project_write = list(apply_envelope([], agent="pi", project_resolution=atlas).tags)
    global_write = list(apply_envelope(["topic:cats"], agent="hermes").tags)
    unresolved_write = list(apply_envelope([], agent="pi", coding=True).tags)
    session_write = list(apply_envelope(["checkpoint"], agent="hermes").tags)

    in_atlas, in_none = project_scope_groups("atlas"), project_scope_groups(None)
    assert _readable(in_atlas, project_write) and not _readable(in_none, project_write)
    assert _readable(in_atlas, global_write) and _readable(in_none, global_write)
    assert not _readable(in_atlas, unresolved_write)
    assert not _readable(in_none, unresolved_write)
    # An ephemeral write with no project carries neither tag, so a scoped
    # read does not see it.
    assert not _readable(in_atlas, session_write)


# ── composed with the visibility rule ───────────────────────────────────────

WORK_PRIVATE_ATLAS = ["agent:work", "kind:durable", "project:atlas", VISIBILITY_PRIVATE_TAG]
WORK_PRIVATE_GLOBAL = ["agent:work", "kind:durable", "scope:global", VISIBILITY_PRIVATE_TAG]


@pytest.mark.parametrize(
    ("caller", "tags", "readable"),
    [
        ("discord", ATLAS_FACT, True),
        ("discord", GLOBAL_FACT, True),
        ("discord", UNTAGGED_FACT, True),
        ("discord", BOREALIS_FACT, False),
        ("discord", WORK_PRIVATE_ATLAS, False),
        ("discord", WORK_PRIVATE_GLOBAL, False),
        ("work", WORK_PRIVATE_ATLAS, True),
        ("work", WORK_PRIVATE_GLOBAL, True),
        (None, WORK_PRIVATE_ATLAS, False),
        (None, ATLAS_FACT, True),
    ],
)
def test_project_scope_and_visibility_are_both_enforced(caller, tags, readable):
    """Concatenating the two lists ANDs them: a private memory in the caller's
    project stays private, and the caller's own stays readable."""
    groups = reflect_visibility_scope(caller) + project_scope_groups("atlas")
    assert _readable(groups, tags) is readable


def test_the_groups_never_travel_with_a_tags_field():
    """The engine refuses ``tags`` beside ``tag_groups``; both builders return
    groups only, and the caller's own tags ride inside them."""
    groups = reflect_visibility_scope("work", ["topic:cats"], "all_strict")
    groups += project_scope_groups("atlas")
    assert all(isinstance(group, dict) for group in groups)
    assert groups[1] == {"tags": ["topic:cats"], "match": "all_strict"}
    assert len(groups) == 3


# ── reflect_visibility_scope moved here unchanged ───────────────────────────


def test_the_visibility_tag_has_one_owner():
    assert VISIBILITY_PRIVATE_TAG == "visibility:private"
    assert _VISIBILITY_PRIVATE is VISIBILITY_PRIVATE_TAG


def test_reflect_visibility_scope_shape():
    private = {"tags": ["visibility:private"], "match": "any_strict"}
    assert reflect_visibility_scope(None) == [{"not": private}]
    assert reflect_visibility_scope("work") == [
        {"or": [{"not": private}, {"tags": ["agent:work"], "match": "any_strict"}]}
    ]
    assert reflect_visibility_scope("work", ["a"], None)[-1] == {"tags": ["a"], "match": "any"}


@pytest.mark.parametrize("caller", ["hermes", "work", "discord", "unknown"])
@pytest.mark.parametrize(
    "tags",
    [
        ["agent:hermes"],
        [],
        ["agent:hermes", "visibility:private"],
        ["agent:work", "visibility:private", "kind:ephemeral"],
        ["visibility:private"],
        GLOBAL_FACT,
        WORK_PRIVATE_ATLAS,
    ],
)
def test_the_reflect_filter_is_the_rule_recall_enforces(caller, tags):
    assert _readable(reflect_visibility_scope(caller), tags) is HindsightProvider._is_visible_to(
        {"tags": tags}, caller
    )
