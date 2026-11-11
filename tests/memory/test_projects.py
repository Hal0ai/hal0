"""The project resolver and registry (ADR-0058 decision 4a): pure functions.

Table-driven. The names are neutral examples; the shapes are the ones found in
live stores: repository directory names, per-repository coding-agent bank ids,
purpose-named operator banks, paths, and text that is not a name at all.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hal0.memory.projects import (
    DEFAULT_DENY,
    MAX_SLUG_LENGTH,
    ROLE_WORDS,
    ProjectRegistry,
    ProjectResolution,
    normalise_slug,
    project_tags_of,
    registry_from_config,
    resolve_project,
    strip_legacy_bank_prefix,
)

# ── normalise_slug ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("atlas", "atlas"),
        ("Atlas", "atlas"),
        ("  atlas  ", "atlas"),
        ("atlas-web", "atlas-web"),
        ("atlas_web", "atlas_web"),
        ("atlas.io", "atlas.io"),
        ("v1.2.3", "v1.2.3"),
        ("My Repo", "my-repo"),
        ("my   repo", "my-repo"),
        ("my - repo", "my-repo"),
        ("a--b", "a--b"),  # hyphens of the name's own are kept as written
        ("acme/widgets", "acme-widgets"),
        ("acme\\widgets", "acme-widgets"),
        ("example.com+acme+widgets", "example.com-acme-widgets"),
        ("atlas (fork)", "atlas-fork"),
        ("atlas!!!", "atlas"),
        ("--atlas--", "atlas"),
        (".atlas", "atlas"),
        ("_atlas_", "atlas"),
        ("-._atlas._-", "atlas"),
        ("project:atlas", "project-atlas"),
        ("atlas\nweb\tapi", "atlas-web-api"),
        ("atlas\x00web", "atlas-web"),
    ],
)
def test_normalise_slug(text, slug):
    assert normalise_slug(text) == slug


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        # A path: separators become hyphens, and ".." never survives.
        ("/srv/checkouts/atlas", "srv-checkouts-atlas"),
        ("../atlas", "atlas"),
        ("../../etc/passwd", "etc-passwd"),
        ("a/../b", "a-b"),
        ("a/./b", "a-.-b"),
        ("atlas..web", "atlas-web"),
        ("atlas...web", "atlas-web"),
        ("a.-.b", "a.-.b"),
        ("C:\\Users\\dev\\atlas", "c-users-dev-atlas"),
        ("~/atlas", "atlas"),
        ("%2e%2e%2fatlas", "2e-2e-2fatlas"),
    ],
)
def test_normalise_slug_on_paths(text, slug):
    assert normalise_slug(text) == slug
    assert ".." not in slug and "/" not in slug and "\\" not in slug


#: "atlas" in full-width letters (U+FF41 and so on), which are not ASCII letters.
FULL_WIDTH_ATLAS = "".join(chr(ord(letter) + 0xFEE0) for letter in "atlas")


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("café", "caf"),
        ("naïve-app", "na-ve-app"),
        ("Ünïcödé", "n-c-d"),
        ("atlas-プロジェクト", "atlas"),
        (FULL_WIDTH_ATLAS, None),
        ("проект", None),
        ("プロジェクト", None),
        ("🚀", None),
        ("atlas 🚀 web", "atlas-web"),
    ],
)
def test_normalise_slug_keeps_ascii_only(text, slug):
    assert normalise_slug(text) == slug


@pytest.mark.parametrize(
    "text", ["", "   ", "-", "..", "...", "/", "///", "._-", "../..", "\t\n", "!!!"]
)
def test_a_name_with_nothing_left_is_none(text):
    assert normalise_slug(text) is None


@pytest.mark.parametrize("value", [None, 7, 1.5, True, ["atlas"], {"a": 1}, b"atlas", object()])
def test_a_value_that_is_not_a_string_is_none(value):
    assert normalise_slug(value) is None


def test_a_long_name_is_cut_to_the_cap():
    assert MAX_SLUG_LENGTH == 100
    assert normalise_slug("a" * 100) == "a" * 100
    assert normalise_slug("a" * 101) == "a" * 100
    assert normalise_slug("a" * 5000) == "a" * 100
    # The cut never leaves a trailing separator behind.
    assert normalise_slug("a" * 99 + "-b") == "a" * 99
    assert normalise_slug("a" * 98 + "._b") == "a" * 98


@pytest.mark.parametrize(
    "text",
    [
        "atlas",
        "My Repo",
        "a/../b",
        "café",
        "a.-.b",
        "x" * 300,
        "a" * 99 + "-b",
        "--A  b..c__",
        "example.com+acme+widgets",
    ],
)
def test_normalise_slug_is_idempotent(text):
    once = normalise_slug(text)
    assert normalise_slug(once) == once


# ── strip_legacy_bank_prefix ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bank_id", "project"),
    [
        ("coding-agent::atlas", "atlas"),
        ("coding-agent::atlas-web", "atlas-web"),
        ("claude-code::atlas", "atlas"),
        ("codex::atlas", "atlas"),
        ("pi::atlas", "atlas"),
        ("opencode::Atlas Web", "atlas-web"),
        ("dev-claude-code::atlas", "atlas"),  # a client-side bank id prefix
        ("agent_1::atlas", "atlas"),
        ("harness.v2::atlas", "atlas"),
        ("  coding-agent::atlas  ", "atlas"),
        # hal0's own per-project bank from before ADR-0005.
        ("project__atlas", "atlas"),
        ("project__Atlas Web", "atlas-web"),
    ],
)
def test_project_shaped_bank_ids(bank_id, project):
    assert strip_legacy_bank_prefix(bank_id) == project


@pytest.mark.parametrize(
    "bank_id",
    [
        # No "::" at all: a namespace, a default, a single-word harness bank.
        "shared",
        "default",
        "agents",
        "pi-coder",
        "claude_code",
        "atlas",
        "private__hermes",
        # A purpose-named bank: the first part is not a lower-case identifier.
        "Team::Agents",
        "Team::Platform",
        "Team::atlas",
        "ACME::atlas",
        "My Team::atlas",
        # A role word in the project position names a kind of bank.
        "coding::agent",
        "coding-agent::agents",
        "team::shared",
        "team::private",
        "team::platform",
        "team::default",
        "team::Memory",
        # Three or more parts: the project's position is a client setting.
        "coding-agent::atlas::session-1",
        "a::b::c::d",
        # Nothing usable on one side or the other.
        "::atlas",
        "coding-agent::",
        "coding-agent::..",
        "coding-agent::///",
        "::",
        "",
        "   ",
        "project__",
        "project__..",
        "project__agents",
        # A single colon is the namespace grammar, not a bank id.
        "project:atlas",
        "coding-agent:atlas",
    ],
)
def test_bank_ids_that_are_not_project_shaped(bank_id):
    assert strip_legacy_bank_prefix(bank_id) is None


@pytest.mark.parametrize("value", [None, 3, ["coding-agent::atlas"], b"coding-agent::atlas"])
def test_a_bank_id_that_is_not_a_string_is_none(value):
    assert strip_legacy_bank_prefix(value) is None


def test_role_words_are_generic_words_not_one_operators_banks():
    assert {"agent", "agents", "shared", "private", "platform", "default"} <= ROLE_WORDS
    assert all(word == normalise_slug(word) for word in ROLE_WORDS)


def test_a_bank_segment_still_goes_through_the_registry():
    registry = ProjectRegistry(aliases={"atlas-mono": "atlas"})
    segment = strip_legacy_bank_prefix("coding-agent::atlas-mono")
    assert segment == "atlas-mono"
    assert resolve_project(bank_segment=segment, registry=registry).slug == "atlas"
    denied = resolve_project(
        bank_segment=strip_legacy_bank_prefix("coding-agent::tmp"), registry=registry
    )
    assert denied.slug is None and denied.denied


# ── resolve_project ─────────────────────────────────────────────────────────

REGISTRY = ProjectRegistry(
    aliases={"atlas-mono": "atlas", "old-atlas": "atlas-mono", "docs-site": "handbook"},
    registered=frozenset({"atlas", "borealis"}),
)


def _r(slug=None, source="none", *, unresolved=False, registered=False, denied=False, slugs=()):
    return ProjectResolution(
        slug=slug,
        source=source,
        unresolved=unresolved,
        registered=registered,
        denied=denied,
        slugs=tuple(slugs),
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # Precedence: field, then bank segment, then the caller's project tags.
        ({"field": "borealis"}, _r("borealis", "field", registered=True)),
        ({"bank_segment": "borealis"}, _r("borealis", "bank", registered=True)),
        ({"tags": ["project:borealis"]}, _r("borealis", "tag", registered=True)),
        (
            {"field": "atlas", "bank_segment": "borealis", "tags": ["project:cirrus"]},
            _r("atlas", "field", registered=True),
        ),
        (
            {"bank_segment": "borealis", "tags": ["project:cirrus"]},
            _r("borealis", "bank", registered=True),
        ),
        # A source that normalises to nothing is absent; the next one is tried.
        ({"field": "", "bank_segment": "borealis"}, _r("borealis", "bank", registered=True)),
        ({"field": "   ", "tags": ["project:cirrus"]}, _r("cirrus", "tag")),
        ({"field": "..", "bank_segment": "///", "tags": ["project:cirrus"]}, _r("cirrus", "tag")),
        ({"field": None, "bank_segment": None, "tags": []}, _r()),
        ({"field": 7, "bank_segment": ["x"]}, _r()),
        # Spelling: one rule for all three sources.
        ({"field": "Borealis"}, _r("borealis", "field", registered=True)),
        ({"field": "My Repo"}, _r("my-repo", "field")),
        ({"field": "project:Borealis"}, _r("borealis", "field", registered=True)),
        ({"field": "PROJECT:borealis"}, _r("borealis", "field", registered=True)),
        ({"tags": [" Project:Borealis "]}, _r("borealis", "tag", registered=True)),
        ({"tags": ["topic:cats", "project:cirrus", "kind:durable"]}, _r("cirrus", "tag")),
        # Tags: each one is normalised, aliased and checked against the deny
        # list. One distinct project left is the project.
        ({"tags": ["project:atlas", "project:atlas"]}, _r("atlas", "tag", registered=True)),
        ({"tags": ["project:atlas", "Project:Atlas"]}, _r("atlas", "tag", registered=True)),
        ({"tags": ["project:atlas", "project:atlas-mono"]}, _r("atlas", "tag", registered=True)),
        ({"tags": ["project:tmp", "project:cirrus"]}, _r("cirrus", "tag")),
        ({"tags": ["project:cirrus", "project:!!!", "project:"]}, _r("cirrus", "tag")),
        # Two or more distinct projects left: the write is filed under each,
        # in the caller's order, and there is no single project.
        (
            {"tags": ["project:atlas", "project:borealis"]},
            _r(source="tag", registered=True, slugs=["atlas", "borealis"]),
        ),
        (
            {"tags": ["project:Cirrus", "topic:cats", "project:old-atlas", "project:cirrus"]},
            _r(source="tag", slugs=["cirrus", "atlas"]),
        ),
        (
            {"tags": ["project:tmp", "project:Borealis", "project:..", "project:atlas"]},
            _r(source="tag", registered=True, slugs=["borealis", "atlas"]),
        ),
        # None left, although the caller sent a project tag: no project, and
        # reported as denied so the envelope drops the tag.
        ({"tags": ["project:"]}, _r(source="tag", denied=True)),
        ({"tags": ["project:!!!"]}, _r(source="tag", denied=True)),
        ({"tags": ["project:..", "Project:  "]}, _r(source="tag", denied=True)),
        ({"tags": ["project:tmp", "project:home"]}, _r(source="tag", denied=True)),
        ({"tags": ["project:tmp", "project:!!!"]}, _r(source="tag", denied=True)),
        # Not project tags at all.
        ({"tags": ["projects:atlas", "my-project:atlas", "project"]}, _r()),
        ({"tags": "project:cirrus"}, _r()),  # a bare string is not a tag list here
        # The unresolved marker is not a name: alone it is nothing, and it
        # does not make a real project tag ambiguous.
        ({"tags": ["project:unresolved"]}, _r()),
        ({"tags": ["project:unresolved", "project:cirrus"]}, _r("cirrus", "tag")),
        # Aliases, followed to the end of a chain.
        ({"field": "atlas-mono"}, _r("atlas", "field", registered=True)),
        ({"field": "old-atlas"}, _r("atlas", "field", registered=True)),
        ({"field": "Atlas Mono"}, _r("atlas", "field", registered=True)),
        ({"bank_segment": "atlas-mono"}, _r("atlas", "bank", registered=True)),
        ({"tags": ["project:atlas-mono"]}, _r("atlas", "tag", registered=True)),
        # The target of an alias counts as registered.
        ({"field": "docs-site"}, _r("handbook", "field", registered=True)),
        ({"field": "handbook"}, _r("handbook", "field", registered=True)),
        # An unregistered name is still a project.
        ({"field": "cirrus"}, _r("cirrus", "field")),
        # The deny list: a denied name is "no project", and resolution stops.
        ({"field": "tmp"}, _r(source="field", denied=True)),
        ({"field": "TMP"}, _r(source="field", denied=True)),
        ({"field": "workdir"}, _r(source="field", denied=True)),
        ({"bank_segment": "scratch"}, _r(source="bank", denied=True)),
        ({"tags": ["project:home"]}, _r(source="tag", denied=True)),
        ({"field": "unresolved"}, _r(source="field", denied=True)),
        ({"field": "tmp", "bank_segment": "borealis"}, _r(source="field", denied=True)),
        ({"field": "tmp", "tags": ["project:borealis"]}, _r(source="field", denied=True)),
        # A field or bank segment decides alone: the tags are not consulted,
        # denied or not.
        ({"field": "cirrus", "tags": ["project:tmp"]}, _r("cirrus", "field")),
        (
            {"bank_segment": "atlas", "tags": ["project:tmp", "project:borealis"]},
            _r("atlas", "bank", registered=True),
        ),
    ],
)
def test_resolve_project(kwargs, expected):
    assert resolve_project(registry=REGISTRY, **kwargs) == expected


@pytest.mark.parametrize(
    "tags",
    [
        ["project:tmp"],
        ["project:TMP", "project:atlas"],
        ["project:atlas", " Project:Home ", "project:borealis"],
        ["project:scratch", "project:workdir", "project:unknown"],
        ["project:unresolved", "project:default"],
        ["project:My Repo", "project:tmp", "project:"],
    ],
)
@pytest.mark.parametrize("field", [None, "cirrus", "tmp", "!!!"])
def test_no_denied_or_unnormalised_name_is_ever_resolved(tags, field):
    """Whatever the combination, what comes out is made of names the resolver
    itself wrote: normalised, aliased, and not on the deny list."""
    resolution = resolve_project(field=field, tags=tags, registry=REGISTRY)
    for slug in resolution.project_slugs:
        assert slug == normalise_slug(slug)
        assert slug == REGISTRY.canonical(slug)
        assert not REGISTRY.is_denied(slug)
    assert bool(resolution.slug) + bool(resolution.slugs) <= 1
    assert len(resolution.slugs) != 1


@pytest.mark.parametrize("name", DEFAULT_DENY)
def test_every_default_deny_name_is_no_project(name):
    resolution = resolve_project(field=name, registry=ProjectRegistry())
    assert resolution.slug is None and resolution.denied and not resolution.unresolved


def test_the_default_deny_list():
    assert DEFAULT_DENY == (
        "tmp",
        "temp",
        "root",
        "home",
        "workdir",
        "scratch",
        "scratchpad",
        "unknown",
        "default",
    )
    assert ProjectRegistry().deny == frozenset(DEFAULT_DENY)
    assert ProjectRegistry().aliases == {}
    assert ProjectRegistry().registered == frozenset()


def test_work_is_a_project_name_by_default():
    """``work`` is a plausible repository name, so the shipped deny list does
    not refuse it (``workdir`` stays refused). An operator can still deny it."""
    assert "work" not in DEFAULT_DENY
    resolution = resolve_project(field="work", registry=ProjectRegistry())
    assert resolution.slug == "work" and not resolution.denied
    from_bank = resolve_project(
        bank_segment=strip_legacy_bank_prefix("coding-agent::work"),
        registry=ProjectRegistry(),
        coding=True,
    )
    assert from_bank.slug == "work" and not from_bank.unresolved
    assert resolve_project(field="workdir", registry=ProjectRegistry()).denied
    denied = resolve_project(field="work", registry=ProjectRegistry(deny=frozenset({"work"})))
    assert denied.slug is None and denied.denied


@pytest.mark.parametrize(
    ("kwargs", "unresolved"),
    [
        ({}, True),
        ({"field": "tmp"}, True),
        ({"tags": ["project:tmp", "project:!!!"]}, True),
        ({"field": "..", "bank_segment": ""}, True),
        ({"field": "cirrus"}, False),
        ({"bank_segment": "atlas-mono"}, False),
        ({"tags": ["project:a", "project:b"]}, False),
    ],
)
def test_unresolved_is_for_a_coding_client_that_ends_with_no_project(kwargs, unresolved):
    coding = resolve_project(registry=REGISTRY, coding=True, **kwargs)
    other = resolve_project(registry=REGISTRY, coding=False, **kwargs)
    assert coding.unresolved is unresolved
    assert other.unresolved is False
    assert coding.slug == other.slug and coding.source == other.source


def test_an_alias_can_rescue_a_denied_spelling_but_not_point_at_one():
    # Aliases run before the deny list, so a denied directory name the
    # operator mapped to a real project resolves to that project.
    registry = ProjectRegistry(aliases={"workdir": "atlas"})
    assert resolve_project(field="workdir", registry=registry).slug == "atlas"
    # A registry built by hand with an alias onto a denied name still yields
    # no project: the deny list has the last word.
    bad = ProjectRegistry(aliases={"atlas": "tmp"})
    assert resolve_project(field="atlas", registry=bad).denied


def test_an_alias_cycle_in_a_hand_built_registry_terminates():
    looped = ProjectRegistry(aliases={"a": "b", "b": "a"})
    assert resolve_project(field="a", registry=looped).slug in {"a", "b"}


def test_the_resolution_knows_its_tag():
    one = resolve_project(field="Atlas", registry=REGISTRY)
    assert one.tag == "project:atlas" and one.project_slugs == ("atlas",)
    assert resolve_project(field="tmp", registry=REGISTRY).tag is None
    assert ProjectResolution().tag is None and ProjectResolution().project_slugs == ()
    several = resolve_project(tags=["project:atlas", "project:cirrus"], registry=REGISTRY)
    assert several.tag is None and several.project_slugs == ("atlas", "cirrus")
    # Registered only when every project is.
    assert several.registered is False


@pytest.mark.parametrize(
    ("tags", "found"),
    [
        (
            ["a", "project:x", "Project:Y", " project:z ", "project:x"],
            ["project:x", "Project:Y", " project:z "],
        ),
        (["project:unresolved", "PROJECT:UNRESOLVED", "project:x"], ["project:x"]),
        ([None, 3, "projects:x", "project"], []),
        ((), []),
        (None, []),
    ],
)
def test_project_tags_of(tags, found):
    assert project_tags_of(tags) == found


# ── the registry ────────────────────────────────────────────────────────────


def test_registry_lookups():
    assert REGISTRY.canonical("old-atlas") == "atlas"
    assert REGISTRY.canonical("cirrus") == "cirrus"
    assert REGISTRY.is_registered("atlas") and REGISTRY.is_registered("handbook")
    assert not REGISTRY.is_registered("atlas-mono")
    assert REGISTRY.is_known("atlas-mono") and REGISTRY.is_known("docs-site")
    assert not REGISTRY.is_known("cirrus")
    assert REGISTRY.is_denied("tmp") and REGISTRY.is_denied("unresolved")
    assert not REGISTRY.is_denied("atlas")


def test_registry_from_config_normalises_whatever_it_is_given():
    config = SimpleNamespace(
        aliases={"Atlas Mono": "Atlas", "same": "Same", "": "x", "y": ""},
        deny=["TMP", "My Scratch", ""],
        registered=["Atlas", "atlas", "Borealis"],
    )
    registry = registry_from_config(config)
    assert registry.aliases == {"atlas-mono": "atlas"}
    assert registry.deny == frozenset({"tmp", "my-scratch"})
    assert registry.registered == frozenset({"atlas", "borealis"})


def test_registry_from_config_defaults():
    assert registry_from_config(None) == ProjectRegistry()
    bare = registry_from_config(SimpleNamespace())
    assert bare.deny == frozenset(DEFAULT_DENY) and not bare.aliases and not bare.registered
    # An explicit empty deny list is the operator's choice, not the default.
    assert registry_from_config(SimpleNamespace(deny=[])).deny == frozenset()


def test_the_unresolved_marker_is_denied_even_with_an_empty_deny_list():
    registry = ProjectRegistry(deny=frozenset())
    assert resolve_project(field="tmp", registry=registry).slug == "tmp"
    assert resolve_project(field="unresolved", registry=registry).denied
