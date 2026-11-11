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
    tags_readable,
    unresolved_scope_groups,
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


# ── identities that contain a colon or whitespace ──────────────────────────
#
# The REST front door only admits ``[a-zA-Z0-9_-]`` identities, but the
# provider is also reached with ids that come from elsewhere (a bridge key's
# bound name, a direct caller). An odd identity must stay one opaque string:
# it reads its own private facts and nobody else's.

ODD_IDENTITIES = [
    "work:evil",
    "work evil",
    " work",
    "work ",
    "work\t",
    "agent:work",
    "a:b:c",
    "visibility:private",
    "work visibility:private",
    "   ",
]


@pytest.mark.parametrize("caller", ODD_IDENTITIES)
def test_an_odd_identity_is_one_exact_tag(caller):
    private = {"tags": ["visibility:private"], "match": "any_strict"}
    assert reflect_visibility_scope(caller) == [
        {"or": [{"not": private}, {"tags": [f"agent:{caller}"], "match": "any_strict"}]}
    ]


@pytest.mark.parametrize("caller", ODD_IDENTITIES)
def test_an_odd_identity_reads_its_own_private_facts_and_nobody_elses(caller):
    scope = reflect_visibility_scope(caller)
    assert _readable(scope, [f"agent:{caller}", "visibility:private"])
    assert _readable(scope, ["agent:work"])
    assert _readable(scope, [])
    for other in ("work", "evil", "b", "private", caller.strip() + "x"):
        assert not _readable(scope, [f"agent:{other}", "visibility:private"]), other
    assert not _readable(scope, ["visibility:private"])


@pytest.mark.parametrize("caller", ODD_IDENTITIES)
@pytest.mark.parametrize(
    "tags",
    [
        ["agent:work", "visibility:private"],
        ["agent:work:evil", "visibility:private"],
        ["agent: work", "visibility:private"],
        ["agent:work ", "visibility:private"],
        ["agent:agent:work", "visibility:private"],
        ["agent:visibility:private", "visibility:private"],
        ["agent:work"],
        ["visibility:private"],
    ],
)
def test_an_odd_identity_is_judged_as_recall_judges_it(caller, tags):
    assert _readable(reflect_visibility_scope(caller), tags) is HindsightProvider._is_visible_to(
        {"tags": tags}, caller
    )


@pytest.mark.parametrize("agent", ["work:evil", "work evil", " work ", "a:b:c", "agent:work"])
def test_an_odd_identity_is_stamped_as_given_on_a_write(agent):
    env = apply_envelope(["agent:work", "visibility:private", "topic:cats"], agent=agent)
    agent_tags = [tag for tag in env.tags if tag.strip().lower().startswith("agent:")]
    assert agent_tags == [f"agent:{agent}"]


# ── unresolved_scope_groups: a coding client with no project ────────────────
#
# Such a client's writes are stamped ``project:unresolved``. That tag is a
# marker shared by every repository's unresolved writes, so the read scope is
# global memory plus the caller's OWN unresolved writes, and no project's.

PI_UNRESOLVED = ["agent:pi", "kind:durable", "project:unresolved"]
CODEX_UNRESOLVED = ["agent:codex", "kind:durable", "project:unresolved"]


def test_unresolved_scope_groups_shape():
    assert unresolved_scope_groups("pi") == [
        {
            "or": [
                {"tags": ["scope:global"], "match": "any"},
                {
                    "and": [
                        {"tags": ["project:unresolved"], "match": "any_strict"},
                        {"tags": ["agent:pi"], "match": "any_strict"},
                    ]
                },
            ]
        }
    ]
    # No identity owns nothing: the global scope alone.
    assert unresolved_scope_groups(None) == project_scope_groups(None)
    assert unresolved_scope_groups("") == project_scope_groups(None)


@pytest.mark.parametrize(
    ("caller", "readable"),
    [
        ("pi", [GLOBAL_FACT, UNTAGGED_FACT, PI_UNRESOLVED]),
        ("codex", [GLOBAL_FACT, UNTAGGED_FACT, CODEX_UNRESOLVED]),
        ("hermes", [GLOBAL_FACT, UNTAGGED_FACT]),
        (None, [GLOBAL_FACT, UNTAGGED_FACT]),
    ],
)
def test_an_unresolved_caller_reads_global_untagged_and_its_own_unresolved(caller, readable):
    facts = [
        ATLAS_FACT,
        BOREALIS_FACT,
        GLOBAL_FACT,
        UNTAGGED_FACT,
        PI_UNRESOLVED,
        CODEX_UNRESOLVED,
        PRE_SCOPE_FACT,
        EPHEMERAL_FACT,
    ]
    groups = unresolved_scope_groups(caller)
    assert [tags for tags in facts if _readable(groups, tags)] == readable


def test_the_unresolved_scope_reads_no_project():
    groups = unresolved_scope_groups("pi")
    assert not _readable(groups, ATLAS_FACT)
    # Not even a project fact the caller wrote itself.
    assert not _readable(groups, ["agent:pi", "kind:durable", "project:atlas"])


def test_the_unresolved_scope_still_admits_untagged_facts_by_design():
    """Like ``project_scope_groups``: ``match: any`` on the global leaf keeps a
    memory from before tags existed readable."""
    (group,) = unresolved_scope_groups("pi")
    global_leaf, own_unresolved = group["or"]
    assert global_leaf == project_scope_groups(None)[0]
    assert _matches(global_leaf, UNTAGGED_FACT)
    # The ownership half is strict: an untagged fact is nobody's.
    assert not _matches(own_unresolved, UNTAGGED_FACT)


def test_the_unresolved_scope_reads_what_the_envelope_writes():
    mine = list(apply_envelope(["source:chat"], agent="pi", coding=True).tags)
    theirs = list(apply_envelope(["source:chat"], agent="codex", coding=True).tags)
    denied = ProjectResolution(source="bank", unresolved=True, denied=True)
    refused = list(apply_envelope([], agent="pi", project_resolution=denied, coding=True).tags)
    global_write = list(apply_envelope(["topic:cats"], agent="hermes").tags)
    atlas = ProjectResolution(slug="atlas", source="bank")
    project_write = list(apply_envelope([], agent="pi", project_resolution=atlas, coding=True).tags)

    groups = unresolved_scope_groups("pi")
    assert _readable(groups, mine) and _readable(groups, refused)
    assert not _readable(groups, theirs)
    assert _readable(groups, global_write)
    assert not _readable(groups, project_write)


@pytest.mark.parametrize(
    ("caller", "tags", "readable"),
    [
        ("pi", PI_UNRESOLVED, True),
        ("pi", [*PI_UNRESOLVED, VISIBILITY_PRIVATE_TAG], True),
        ("pi", CODEX_UNRESOLVED, False),
        ("pi", WORK_PRIVATE_GLOBAL, False),
        ("work", WORK_PRIVATE_GLOBAL, True),
        ("pi", GLOBAL_FACT, True),
        ("pi", UNTAGGED_FACT, True),
        ("pi", ATLAS_FACT, False),
    ],
)
def test_the_unresolved_scope_and_visibility_are_both_enforced(caller, tags, readable):
    groups = reflect_visibility_scope(caller) + unresolved_scope_groups(caller)
    assert _readable(groups, tags) is readable


# ── tags_readable: the same semantics, evaluated inside hal0 ────────────────

_SAMPLE_TAG_SETS = [
    [],
    ATLAS_FACT,
    BOREALIS_FACT,
    GLOBAL_FACT,
    PI_UNRESOLVED,
    CODEX_UNRESOLVED,
    PRE_SCOPE_FACT,
    EPHEMERAL_FACT,
    WORK_PRIVATE_ATLAS,
    WORK_PRIVATE_GLOBAL,
    ["visibility:private"],
    ["a"],
    ["a", "b"],
]

_SAMPLE_GROUPS = [
    [],
    reflect_visibility_scope(None),
    reflect_visibility_scope("work"),
    reflect_visibility_scope("pi", ["a", "b"], "all"),
    reflect_visibility_scope("pi", ["a", "b"], "all_strict"),
    reflect_visibility_scope("pi", ["a", "b"], "exact"),
    reflect_visibility_scope("pi", ["a"], None),
    project_scope_groups("atlas"),
    project_scope_groups(None),
    unresolved_scope_groups("pi"),
    reflect_visibility_scope("work") + project_scope_groups("atlas"),
    reflect_visibility_scope("pi") + unresolved_scope_groups("pi"),
    [{"tags": ["a"]}],
    [{"not": {"tags": ["a"], "match": "any"}}],
    [{"and": [{"tags": ["a"]}, {"not": {"tags": ["b"]}}]}],
]


@pytest.mark.parametrize("groups", _SAMPLE_GROUPS)
def test_tags_readable_agrees_with_the_engine_semantics(groups):
    """hal0's own evaluator against the one this file restates from the
    engine: the two must never disagree on a group hal0 builds."""
    for tags in _SAMPLE_TAG_SETS:
        assert tags_readable(groups, tags) is _readable(groups, tags), (groups, tags)


def test_tags_readable_restates_the_engine_facts():
    assert tags_readable([{"tags": ["a"], "match": "any"}], [])
    assert not tags_readable([{"tags": ["a"], "match": "any_strict"}], [])
    assert not tags_readable([{"tags": ["a"]}], [])  # default is any_strict
    assert not tags_readable([{"not": {"tags": ["a"], "match": "any"}}], [])
    assert tags_readable([{"not": {"tags": ["a"], "match": "any_strict"}}], [])
    assert tags_readable([{"tags": ["a"]}, {"tags": ["b"]}], ["a", "b"])
    assert not tags_readable([{"tags": ["a"]}, {"tags": ["b"]}], ["a"])


@pytest.mark.parametrize(
    "group",
    [
        {},
        {"tags": "a"},
        {"tags": ["a"], "match": "fuzzy"},
        {"tags": ["a"], "match": "any", "resolve": "fuzzy"},
        {"and": {"tags": ["a"]}},
        {"or": "a"},
        {"not": [{"tags": ["a"]}]},
        {"nor": [{"tags": ["a"]}]},
        "a",
        None,
        {"tags": ["a", 7]},
        # Negating something unknown must not read as "admitted", and an
        # unknown child must not be skipped by a short circuit.
        {"not": {"tags": ["a"], "match": "fuzzy"}},
        {"not": {"not": {"tags": ["a"], "resolve": "fuzzy"}}},
        {"not": {}},
        {"or": [{"tags": ["a"], "match": "any"}, {"tags": ["b"], "match": "fuzzy"}]},
        {"and": [{"tags": ["zzz"]}, {"nor": []}]},
    ],
)
def test_tags_readable_admits_nothing_through_a_group_it_cannot_evaluate(group):
    for tags in ([], ["a"], ["a", "b"], ["c"]):
        assert not tags_readable([group], tags)
        # Whatever else is asked for beside it.
        assert not tags_readable([{"tags": ["a"], "match": "any"}, group], tags)
        assert not tags_readable([group, {"tags": ["zzz"]}], tags)


def test_tags_readable_takes_an_explicit_exact_resolve():
    """``resolve: "exact"`` is the engine's default spelled out."""
    leaf = {"tags": ["a"], "match": "any_strict", "resolve": "exact"}
    assert tags_readable([leaf], ["a"]) and not tags_readable([leaf], ["b"])


def test_tags_readable_treats_tags_that_are_not_a_list_as_untagged():
    groups = project_scope_groups("atlas")
    assert tags_readable(groups, None) and tags_readable(groups, "project:borealis")
    assert not tags_readable(reflect_visibility_scope("pi", ["a"], "any_strict"), None)
    # A non-string entry is not a tag.
    assert tags_readable(groups, [7, "project:atlas"])
