"""The memory write envelope (ADR-0058 decision 4): one pure normaliser.

Table-driven, one table per rule. The session-id and lifecycle examples are
the shapes found in a live tag space: bare session UUIDs (versions 4 and 7),
the six lifecycle words, and ordinary tags that must survive (issue keys, PR
numbers, dates, abbreviated commit hashes).
"""

from __future__ import annotations

import pytest

from hal0.memory.envelope import (
    COMBINED_OBSERVATION_SCOPES,
    DEFAULT_OBSERVATION_SCOPES,
    LIFECYCLE_WORDS,
    PROJECT_UNRESOLVED_TAG,
    SCOPE_GLOBAL_TAG,
    UNKNOWN_AGENT,
    apply_envelope,
    coerce_tags,
    lifecycle_word_of,
    resolve_agent,
    resolve_observation_scopes,
    session_id_of,
)
from hal0.memory.projects import ProjectResolution

UUID4 = "3f2b8c1e-5a47-4d09-9e6b-7c1a2d3e4f50"
UUID7 = "019a2b3c-4d5e-7f60-8a1b-2c3d4e5f6071"
HEX32 = "3f2b8c1e5a474d099e6b7c1a2d3e4f50"
MD5 = "d41d8cd98f00b204e9800998ecf8427e"
SHA1 = "06f87da60c4b1e2d3a4f5b6c7d8e9f0a1b2c3d4e"
SHA256 = "a1" * 32


# ── Rule 2: agent identity ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        ("hermes", "hermes"),
        ("hermes__hal0-brain", "hermes__hal0-brain"),
        ("Hal0-GitOps", "Hal0-GitOps"),
        (None, UNKNOWN_AGENT),
        ("", UNKNOWN_AGENT),
        ("anonymous", UNKNOWN_AGENT),
    ],
)
def test_agent_identity_resolves_or_falls_back(identity, expected):
    assert resolve_agent(identity) == expected
    assert f"agent:{expected}" in apply_envelope([], agent=identity).tags


def test_unknown_agent_fallback_is_the_literal_already_in_stores():
    assert UNKNOWN_AGENT == "unknown"


@pytest.mark.parametrize("forged", ["agent:brain", "AGENT:brain", " Agent:brain ", "agent:"])
def test_caller_agent_tag_is_replaced_by_the_resolved_identity(forged):
    env = apply_envelope([forged, "topic:cats"], agent="openwebui")
    assert env.tags == ("topic:cats", "agent:openwebui", "kind:durable", "scope:global")


# ── Rules 2 and 4: exactly one kind ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("tags", "kind"),
    [
        ([], "durable"),
        (["hal0", "slots"], "durable"),
        (["kind:durable"], "durable"),
        (["kind:ephemeral"], "ephemeral"),
        (["KIND:Ephemeral"], "ephemeral"),
        ([" kind:durable "], "durable"),
        # Both supplied: nothing the caller called ephemeral is promoted.
        (["kind:durable", "kind:ephemeral"], "ephemeral"),
        (["kind:ephemeral", "kind:durable"], "ephemeral"),
        # An explicit durable wins over a lifecycle word, wherever it sits.
        (["kind:durable", "checkpoint"], "durable"),
        (["checkpoint", "kind:durable"], "durable"),
        (["chat", " KIND:Durable "], "durable"),
        # No kind sent: a lifecycle word makes the write ephemeral.
        (["chat"], "ephemeral"),
        (["kind:ephemeral", "checkpoint"], "ephemeral"),
        (["kind:durable", "kind:ephemeral", "checkpoint"], "ephemeral"),
        # A session id alone does not make a write ephemeral.
        ([UUID4], "durable"),
    ],
)
def test_exactly_one_kind_tag(tags, kind):
    env = apply_envelope(tags, agent="hermes")
    assert env.kind == kind
    assert [t for t in env.tags if t.lower().startswith("kind:")] == [f"kind:{kind}"]


@pytest.mark.parametrize(
    ("tags", "final_tags"),
    [
        # The topic tag ``chat`` on a write the caller called durable.
        (["chat", "kind:durable"], ("chat", "agent:hermes", "kind:durable", "scope:global")),
        (["kind:durable", "chat"], ("chat", "agent:hermes", "kind:durable", "scope:global")),
        (
            ["kind:durable", "checkpoint", "Bootstrap", "hal0"],
            ("checkpoint", "Bootstrap", "hal0", "agent:hermes", "kind:durable", "scope:global"),
        ),
    ],
)
def test_explicit_durable_keeps_lifecycle_words_as_ordinary_tags(tags, final_tags):
    env = apply_envelope(tags, agent="hermes")
    assert env.tags == final_tags
    assert env.kind == "durable"
    assert env.metadata == {}


def test_explicit_durable_still_moves_session_ids():
    env = apply_envelope(["kind:durable", "chat", UUID4], agent="hermes")
    assert env.tags == ("chat", "agent:hermes", "kind:durable", "scope:global")
    assert env.metadata == {"session_id": UUID4}


@pytest.mark.parametrize(
    "tags",
    [
        ["kind:ephemeral", "chat"],
        ["chat", "kind:ephemeral"],
        # Both kinds sent: ephemeral wins, and the word is moved.
        ["kind:durable", "kind:ephemeral", "chat"],
        ["chat", "kind:ephemeral", "kind:durable"],
    ],
)
def test_explicit_ephemeral_still_moves_lifecycle_words(tags):
    env = apply_envelope(tags, agent="hermes")
    assert env.tags == ("agent:hermes", "kind:ephemeral")
    assert env.metadata == {"lifecycle": "chat"}


def test_an_unrecognised_kind_value_is_user_vocabulary():
    env = apply_envelope(["kind:decision"], agent="hermes")
    assert env.tags == ("kind:decision", "agent:hermes", "kind:durable", "scope:global")


# ── Rule 3: session ids ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tag", "session_id"),
    [
        # Bare UUIDs, any version, any case, padded.
        (UUID4, UUID4),
        (UUID7, UUID7),
        (UUID4.upper(), UUID4.upper()),
        (f"  {UUID7} ", UUID7),
        ("00000000-0000-0000-0000-000000000000", "00000000-0000-0000-0000-000000000000"),
        # The session namespace, in its four spellings.
        (f"session:{UUID4}", UUID4),
        ("session:20261109_153000_ab12cd", "20261109_153000_ab12cd"),
        ("Session:abc", "abc"),
        ("session_id:abc", "abc"),
        ("session-id:abc", "abc"),
        ("sessionid:abc", "abc"),
        ("session: padded ", "padded"),
        ("session:ns:with:colons", "ns:with:colons"),
    ],
)
def test_session_shaped_tags_are_recognised(tag, session_id):
    assert session_id_of(tag) == session_id


@pytest.mark.parametrize(
    "tag",
    [
        # Real tags that must stay tags.
        "hal0",
        "#hal0",
        "H0-229",
        "h0-311",
        "pr-3501",
        "PR-3493",
        "3527",
        "2026-11",
        "2026-11-09",
        "#2026-11-07",
        "ADR-0057",
        "provider-429",
        "agent-identity",
        "self-report",
        "hal0-brain",
        "model_id",
        # Hex-looking but short: a word, a date, an abbreviated commit hash.
        "deadbeef",
        "20261109",
        "06f87da60",
        "79ed55c3b1a2",
        "a" * 31,
        # Bare hexadecimal at any length: a UUID without dashes, an MD5
        # checksum, a full commit hash, a digest.
        HEX32,
        HEX32.upper(),
        MD5,
        "0" * 32,
        "a" * 33,
        SHA1,
        SHA1.upper(),
        SHA256,
        "0" * 40,
        "f" * 64,
        HEX32 + HEX32,
        # Not hexadecimal throughout.
        "g" * 32,
        f"{HEX32}-x",
        # UUID-like but malformed, or carried under another namespace.
        "3f2b8c1e-5a47-4d09-9e6b-7c1a2d3e4f5",
        "3f2b8c1e_5a47_4d09_9e6b_7c1a2d3e4f50",
        f"run:{UUID4}",
        f"thread:{UUID4}",
        f"user:{HEX32}",
        # The session namespace with nothing in it, or a different word.
        "session:",
        "session:   ",
        "session",
        "session-end",
        "sessions:abc",
        "my-session:abc",
    ],
)
def test_ordinary_tags_are_not_session_ids(tag):
    assert session_id_of(tag) is None


def test_one_session_tag_moves_to_session_id_metadata():
    env = apply_envelope([UUID4, "hal0"], agent="claude-code")
    assert env.tags == ("hal0", "agent:claude-code", "kind:durable", "scope:global")
    assert env.metadata == {"session_id": UUID4}


def test_several_session_tags_move_to_one_list_key():
    env = apply_envelope([UUID4, f"session:{UUID7}", UUID4, "session:s3"], agent="codex")
    assert env.tags == ("agent:codex", "kind:durable", "scope:global")
    assert env.metadata == {"session_ids": [UUID4, UUID7, "s3"]}


def test_an_md5_checksum_tag_passes_through():
    env = apply_envelope([MD5, "artifact"], agent="hermes")
    assert env.tags == (MD5, "artifact", "agent:hermes", "kind:durable", "scope:global")
    assert env.metadata == {}


def test_any_bare_uuid_is_moved_whatever_it_identifies():
    """A tag cannot say whether a UUID names a session, a document or a
    request, so every bare UUID is moved, under the ``session_id`` key."""
    document_uuid = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    env = apply_envelope([document_uuid, "design-doc"], agent="hermes")
    assert env.tags == ("design-doc", "agent:hermes", "kind:durable", "scope:global")
    assert env.metadata == {"session_id": document_uuid}


@pytest.mark.parametrize("empty", ["", None, 0])
def test_a_falsy_caller_session_id_is_treated_as_absent(empty):
    env = apply_envelope([UUID4], agent="hermes", metadata={"session_id": empty})
    assert env.tags == ("agent:hermes", "kind:durable", "scope:global")
    assert env.metadata == {"session_id": UUID4}


def test_a_falsy_caller_session_id_with_several_tag_ids():
    env = apply_envelope([UUID4, UUID7], agent="hermes", metadata={"session_id": ""})
    assert env.metadata == {"session_ids": [UUID4, UUID7]}


@pytest.mark.parametrize(
    ("tags", "caller_metadata", "additions"),
    [
        # The caller already names this session: nothing to add.
        ([UUID4], {"session_id": UUID4}, {}),
        # The caller names another session: its own value stands.
        ([UUID7], {"session_id": UUID4}, {"session_ids": [UUID7]}),
        ([UUID4, UUID7], {"session_id": UUID4}, {"session_ids": [UUID7]}),
        # A key the caller set is never overwritten.
        ([UUID4, UUID7], {"session_ids": ["mine"]}, {}),
        ([UUID7], {"session_id": UUID4, "session_ids": ["mine"]}, {}),
    ],
)
def test_caller_session_metadata_is_never_overwritten(tags, caller_metadata, additions):
    env = apply_envelope(tags, agent="hermes", metadata=caller_metadata)
    assert env.metadata == additions
    assert env.tags == ("agent:hermes", "kind:durable", "scope:global")


# ── Rule 4: lifecycle words ─────────────────────────────────────────────────


def test_lifecycle_words_are_the_six_named_ones():
    assert (
        frozenset({"checkpoint", "session-end", "pre-compress", "continuity", "bootstrap", "chat"})
        == LIFECYCLE_WORDS
    )
    assert isinstance(LIFECYCLE_WORDS, frozenset)


@pytest.mark.parametrize("word", sorted(LIFECYCLE_WORDS))
def test_each_lifecycle_word_becomes_ephemeral_plus_metadata(word):
    env = apply_envelope([word, "hal0"], agent="hermes")
    assert env.tags == ("hal0", "agent:hermes", "kind:ephemeral")
    assert env.metadata == {"lifecycle": word}


@pytest.mark.parametrize(
    ("tag", "word"),
    [("Checkpoint", "checkpoint"), (" SESSION-END ", "session-end"), ("CHAT", "chat")],
)
def test_lifecycle_words_match_whatever_their_case_or_padding(tag, word):
    assert lifecycle_word_of(tag) == word


@pytest.mark.parametrize(
    "tag",
    [
        "checkpoints",
        "#checkpoint",
        "chat:general",
        "chatbot",
        "session_end",
        "precompress",
        "bootstrap-v2",
        "source:chat",
        "knowledge:continuity",
    ],
)
def test_lifecycle_words_match_whole_bare_tags_only(tag):
    assert lifecycle_word_of(tag) is None
    env = apply_envelope([tag], agent="hermes")
    assert env.tags == (tag, "agent:hermes", "kind:durable", "scope:global")
    assert env.metadata == {}


@pytest.mark.parametrize(
    ("tags", "final_tags", "metadata"),
    [
        # The four Hermes plugin writes, as the plugin sends them.
        (
            ["chat", "agent:hermes"],
            ("agent:hermes", "kind:ephemeral"),
            {"lifecycle": "chat"},
        ),
        (
            ["continuity", "pre-compress", "agent:hermes"],
            ("agent:hermes", "kind:ephemeral"),
            {"lifecycle": "continuity,pre-compress"},
        ),
        (
            ["session-end", "checkpoint", "agent:hermes"],
            ("agent:hermes", "kind:ephemeral"),
            {"lifecycle": "session-end,checkpoint"},
        ),
        (
            ["delegation", "agent:hermes"],
            ("delegation", "agent:hermes", "kind:durable", "scope:global"),
            {},
        ),
        # The provisioning self-report.
        (
            ["bootstrap", "self-report"],
            ("self-report", "agent:hermes", "kind:ephemeral"),
            {"lifecycle": "bootstrap"},
        ),
        # A coding-agent write: session UUID beside real vocabulary.
        (
            [UUID4, "checkpoint", "harness:claude-code", "source:chat", "project:hal0"],
            (
                "harness:claude-code",
                "source:chat",
                "agent:hermes",
                "kind:ephemeral",
                "project:hal0",
            ),
            {
                "session_id": UUID4,
                "lifecycle": "checkpoint",
                "project": "hal0",
                "project_source": "tag",
            },
        ),
        # A repeated word is recorded once.
        (
            ["checkpoint", "Checkpoint"],
            ("agent:hermes", "kind:ephemeral"),
            {"lifecycle": "checkpoint"},
        ),
    ],
)
def test_real_world_tag_sets(tags, final_tags, metadata):
    env = apply_envelope(tags, agent="hermes")
    assert env.tags == final_tags
    assert env.metadata == metadata


def test_caller_lifecycle_metadata_is_never_overwritten():
    env = apply_envelope(["checkpoint"], agent="hermes", metadata={"lifecycle": "mine"})
    assert env.metadata == {}
    assert env.kind == "ephemeral"


# ── Rules 6 to 11: project (decision 4a) ────────────────────────────────────

ATLAS = ProjectResolution(slug="atlas", source="field")
NOTHING = ProjectResolution()
DENIED = ProjectResolution(source="field", denied=True)
CODING_NOTHING = ProjectResolution(unresolved=True)
CODING_DENIED = ProjectResolution(source="bank", denied=True, unresolved=True)
SEVERAL = ProjectResolution(source="tag", slugs=("atlas", "borealis"))


@pytest.mark.parametrize(
    ("tags", "resolution", "expected"),
    [
        # Rule 6: a resolved project is the one project tag.
        ([], ATLAS, ("agent:pi", "kind:durable", "project:atlas")),
        (["project:other"], ATLAS, ("agent:pi", "kind:durable", "project:atlas")),
        (["project:atlas"], ATLAS, ("agent:pi", "kind:durable", "project:atlas")),
        (
            ["Project:Other", " project:third ", "topic:cats"],
            ATLAS,
            ("topic:cats", "agent:pi", "kind:durable", "project:atlas"),
        ),
        (["checkpoint"], ATLAS, ("agent:pi", "kind:ephemeral", "project:atlas")),
        # Rule 7: several projects, one tag each, as the resolver spelled them.
        (
            ["Project:Atlas", "topic:cats", "project:borealis"],
            SEVERAL,
            ("topic:cats", "agent:pi", "kind:durable", "project:atlas", "project:borealis"),
        ),
        (
            ["checkpoint"],
            SEVERAL,
            ("agent:pi", "kind:ephemeral", "project:atlas", "project:borealis"),
        ),
        # Rule 8: a refused name is no project, and the caller's tag goes too.
        ([], DENIED, ("agent:pi", "kind:durable", "scope:global")),
        (["project:tmp"], DENIED, ("agent:pi", "kind:durable", "scope:global")),
        (["project:tmp", "checkpoint"], DENIED, ("agent:pi", "kind:ephemeral")),
        # The resolution is the only source of project tags: a caller tag it
        # does not account for is removed, never kept as sent.
        (
            ["project:atlas", "project:borealis"],
            NOTHING,
            ("agent:pi", "kind:durable", "scope:global"),
        ),
        (["project:Whatever"], NOTHING, ("agent:pi", "kind:durable", "scope:global")),
        (["project:atlas"], CODING_NOTHING, ("agent:pi", "kind:durable", "project:unresolved")),
        # Rule 9: a coding client with no project, whatever the kind.
        ([], CODING_NOTHING, ("agent:pi", "kind:durable", "project:unresolved")),
        (["checkpoint"], CODING_NOTHING, ("agent:pi", "kind:ephemeral", "project:unresolved")),
        ([], CODING_DENIED, ("agent:pi", "kind:durable", "project:unresolved")),
        (["project:tmp"], CODING_DENIED, ("agent:pi", "kind:durable", "project:unresolved")),
        # Rule 10: durable, no project, no scope of the caller's own.
        ([], None, ("agent:pi", "kind:durable", "scope:global")),
        ([], NOTHING, ("agent:pi", "kind:durable", "scope:global")),
        (["topic:cats"], NOTHING, ("topic:cats", "agent:pi", "kind:durable", "scope:global")),
        (["scope:team"], NOTHING, ("scope:team", "agent:pi", "kind:durable")),
        (["Scope:Team"], NOTHING, ("Scope:Team", "agent:pi", "kind:durable")),
        # Rule 11: ephemeral, no project.
        (["checkpoint"], NOTHING, ("agent:pi", "kind:ephemeral")),
        (["kind:ephemeral"], None, ("agent:pi", "kind:ephemeral")),
        # A scope:global tag of the caller's own is kept, once, at the end.
        (["scope:global"], NOTHING, ("agent:pi", "kind:durable", "scope:global")),
        (
            ["Scope:Global", "scope:global", "topic:cats"],
            NOTHING,
            ("topic:cats", "agent:pi", "kind:durable", "scope:global"),
        ),
        (["scope:global"], ATLAS, ("agent:pi", "kind:durable", "project:atlas", "scope:global")),
        (["scope:global", "checkpoint"], NOTHING, ("agent:pi", "kind:ephemeral", "scope:global")),
        (
            ["scope:global"],
            CODING_NOTHING,
            ("agent:pi", "kind:durable", "project:unresolved", "scope:global"),
        ),
        # project:unresolved is the envelope's marker: a caller cannot claim it.
        (["project:unresolved"], NOTHING, ("agent:pi", "kind:durable", "scope:global")),
        (["Project:Unresolved"], None, ("agent:pi", "kind:durable", "scope:global")),
        (["project:unresolved", "checkpoint"], NOTHING, ("agent:pi", "kind:ephemeral")),
        (
            ["project:unresolved", "project:atlas"],
            None,
            ("agent:pi", "kind:durable", "project:atlas"),
        ),
        (
            ["project:unresolved"],
            CODING_NOTHING,
            ("agent:pi", "kind:durable", "project:unresolved"),
        ),
    ],
)
def test_project_rules(tags, resolution, expected):
    assert apply_envelope(tags, agent="pi", project_resolution=resolution).tags == expected


@pytest.mark.parametrize(
    ("tags", "expected", "metadata"),
    [
        # One tag: the same resolution a named project gets.
        (
            ["project:atlas"],
            ("agent:pi", "kind:durable", "project:atlas"),
            {"project": "atlas", "project_source": "tag"},
        ),
        (
            ["topic:cats", " Project:My Repo "],
            ("topic:cats", "agent:pi", "kind:durable", "project:my-repo"),
            {"project": "my-repo", "project_source": "tag"},
        ),
        # A denied tag never survives, alone or beside others.
        (
            ["project:tmp"],
            ("agent:pi", "kind:durable", "scope:global"),
            {"project_source": "denied"},
        ),
        (
            ["project:tmp", "project:atlas"],
            ("agent:pi", "kind:durable", "project:atlas"),
            {"project": "atlas", "project_source": "tag"},
        ),
        (
            ["project:scratch", "PROJECT:Home", "project:workdir"],
            ("agent:pi", "kind:durable", "scope:global"),
            {"project_source": "denied"},
        ),
        (
            ["project:atlas", "project:tmp", "project:Borealis", "project:default"],
            ("agent:pi", "kind:durable", "project:atlas", "project:borealis"),
            {"project": "atlas,borealis", "project_source": "tag"},
        ),
        # A tag with nothing left after normalising is dropped like a denied
        # one: it is not kept as sent, and it earns no project scope.
        (
            ["project:!!!"],
            ("agent:pi", "kind:durable", "scope:global"),
            {"project_source": "denied"},
        ),
        (
            ["project:"],
            ("agent:pi", "kind:durable", "scope:global"),
            {"project_source": "denied"},
        ),
        (
            ["project:..", "project:   ", "checkpoint"],
            ("agent:pi", "kind:ephemeral"),
            {"project_source": "denied", "lifecycle": "checkpoint"},
        ),
        (
            ["project:!!!", "project:atlas"],
            ("agent:pi", "kind:durable", "project:atlas"),
            {"project": "atlas", "project_source": "tag"},
        ),
        # Two or more distinct projects: one tag each, normalised, in order.
        (
            ["project:Borealis", "topic:cats", "project:atlas", "project:BOREALIS"],
            ("topic:cats", "agent:pi", "kind:durable", "project:borealis", "project:atlas"),
            {"project": "borealis,atlas", "project_source": "tag"},
        ),
        # Two spellings of one project are one project.
        (
            ["project:Atlas", "project:atlas"],
            ("agent:pi", "kind:durable", "project:atlas"),
            {"project": "atlas", "project_source": "tag"},
        ),
    ],
)
def test_caller_project_tags_without_a_resolution(tags, expected, metadata):
    """With no resolution handed in, the envelope runs the resolver over the
    caller's tags against the default registry. No row keeps a tag as sent."""
    env = apply_envelope(tags, agent="pi")
    assert env.tags == expected
    assert env.metadata == metadata


@pytest.mark.parametrize("coding", [False, True])
@pytest.mark.parametrize(
    "resolution",
    [None, ATLAS, NOTHING, DENIED, CODING_NOTHING, CODING_DENIED, SEVERAL],
)
@pytest.mark.parametrize(
    "tags",
    [
        ["project:tmp"],
        ["project:TMP", "project:home", "topic:cats"],
        ["project:atlas", " Project:Scratch "],
        ["project:tmp", "project:atlas", "project:borealis", "checkpoint"],
        ["project:My Repo", "project:!!!", "project:"],
        ["project:unresolved", "project:default"],
    ],
)
def test_no_combination_leaves_a_denied_or_unnormalised_project_tag(tags, resolution, coding):
    from hal0.memory.projects import DEFAULT_DENY, normalise_slug

    env = apply_envelope(tags, agent="pi", project_resolution=resolution, coding=coding)
    project_tags = [tag for tag in env.tags if tag.strip().lower().startswith("project:")]
    for tag in project_tags:
        slug = tag[len("project:") :]
        assert tag.startswith("project:") and slug == normalise_slug(slug)
        assert slug not in DEFAULT_DENY
    # Only the resolver's own output, or the envelope's marker, is stamped.
    allowed = {"project:unresolved"}
    if resolution is not None:
        allowed |= {f"project:{slug}" for slug in resolution.project_slugs}
    else:
        allowed |= {"project:atlas", "project:borealis", "project:my-repo"}
    assert set(project_tags) <= allowed
    assert len(project_tags) == len(set(project_tags))


def test_the_coding_flag_alone_marks_a_projectless_write_unresolved():
    assert apply_envelope([], agent="pi", coding=True).tags == (
        "agent:pi",
        "kind:durable",
        "project:unresolved",
    )
    resolved = apply_envelope([], agent="pi", project_resolution=ATLAS, coding=True)
    assert resolved.tags == ("agent:pi", "kind:durable", "project:atlas")


def test_exactly_one_project_tag_when_a_project_is_resolved():
    env = apply_envelope(
        ["project:atlas", "PROJECT:atlas", "project:other", "project:unresolved"],
        agent="pi",
        project_resolution=ATLAS,
    )
    assert [tag for tag in env.tags if tag.lower().startswith("project:")] == ["project:atlas"]
    assert "scope:global" not in env.tags


def test_a_write_whose_project_is_its_bank_gets_no_global_stamp():
    env = apply_envelope(["topic:cats"], agent="pi", stamp_global=False)
    assert env.tags == ("topic:cats", "agent:pi", "kind:durable")


@pytest.mark.parametrize(
    ("resolution", "coding", "caller_metadata", "additions"),
    [
        (ATLAS, False, {}, {"project": "atlas", "project_source": "field"}),
        (
            ProjectResolution(slug="atlas", source="bank"),
            True,
            {},
            {"project": "atlas", "project_source": "bank"},
        ),
        (
            ProjectResolution(slug="atlas", source="tag"),
            False,
            {},
            {"project": "atlas", "project_source": "tag"},
        ),
        (NOTHING, True, {}, {"project": "unresolved", "project_source": "none"}),
        (CODING_DENIED, True, {}, {"project": "unresolved", "project_source": "denied"}),
        (DENIED, False, {}, {"project_source": "denied"}),
        (NOTHING, False, {}, {}),
        (None, False, {}, {}),
        # A key the caller set is the caller's.
        (ATLAS, False, {"project": "mine"}, {"project_source": "field"}),
        (ATLAS, False, {"project_source": "mine"}, {"project": "atlas"}),
        (ATLAS, False, {"project": "", "project_source": None}, {}),
    ],
)
def test_the_project_decision_is_recorded_in_metadata(
    resolution, coding, caller_metadata, additions
):
    env = apply_envelope(
        [], agent="pi", project_resolution=resolution, coding=coding, metadata=caller_metadata
    )
    assert env.metadata == additions


def test_project_rules_combine_with_kind_session_and_lifecycle():
    env = apply_envelope(
        [UUID4, "checkpoint", "harness:claude-code", "project:old-name", "agent:forged"],
        agent="pi",
        project_resolution=ATLAS,
        coding=True,
    )
    assert env.tags == ("harness:claude-code", "agent:pi", "kind:ephemeral", "project:atlas")
    assert env.kind == "ephemeral"
    assert env.metadata == {
        "project": "atlas",
        "project_source": "field",
        "session_id": UUID4,
        "lifecycle": "checkpoint",
    }


def test_an_explicit_durable_project_write_keeps_its_lifecycle_word():
    env = apply_envelope(["kind:durable", "chat"], agent="pi", project_resolution=ATLAS)
    assert env.tags == ("chat", "agent:pi", "kind:durable", "project:atlas")


def test_a_session_write_with_no_project_is_ephemeral_and_unscoped():
    env = apply_envelope([UUID4, "session-end"], agent="pi", project_resolution=NOTHING)
    assert env.tags == ("agent:pi", "kind:ephemeral")
    assert env.metadata == {"session_id": UUID4, "lifecycle": "session-end"}


@pytest.mark.parametrize(
    "metadata",
    [
        {"repo": "hal0"},
        {"project": "hal0"},
        {"cwd": "/srv/checkouts/hal0"},
        {"context": "working on the hal0 repo"},
    ],
)
def test_no_project_tag_is_guessed_from_metadata_or_bare_words(metadata):
    env = apply_envelope(["hal0", "hal0-web"], agent="pi", metadata=metadata)
    assert env.tags == ("hal0", "hal0-web", "agent:pi", "kind:durable", "scope:global")
    assert env.metadata == {}


# ── Rule 6: everything else passes through untouched ────────────────────────


@pytest.mark.parametrize(
    "tag",
    [
        "visibility:private",
        "source:chat",
        "source:bridge:claude-code",
        "knowledge:decision",
        "origin:coding-agent::hal0-mono",
        "harness:claude-code",
        "migrated:2026-11",
        "user:alexander",
        "topic:cats",
        "pr:3411",
        "survey-state:done",
        "area:health-check",
        "role:box-operator",
        "hal0",
        "#infra",
        "H0-229",
        "merge-decision",
        "  padded  ",
        "MixedCase",
    ],
)
def test_namespaced_and_bare_tags_pass_through_untouched(tag):
    env = apply_envelope([tag], agent="hermes")
    assert env.tags == (tag, "agent:hermes", "kind:durable", "scope:global")
    assert env.metadata == {}


def test_caller_tag_order_is_kept_and_exact_duplicates_collapse():
    env = apply_envelope(["b", "a", "b", "c", "a"], agent="hermes")
    assert env.tags == ("b", "a", "c", "agent:hermes", "kind:durable", "scope:global")


# ── Rule 1: normalise, never reject ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ([], []),
        ("solo", ["solo"]),
        (["a", "", "  ", None, "b"], ["a", "b"]),
        (("a", "b"), ["a", "b"]),
        ([3527, True, 1.5], ["3527", "True", "1.5"]),
        (42, ["42"]),
    ],
)
def test_coerce_tags_accepts_any_shape(raw, expected):
    assert coerce_tags(raw) == expected


@pytest.mark.parametrize("raw", [None, "solo", 42, [None, 7, ["nested"]], {"a": 1}, object()])
def test_envelope_never_rejects(raw):
    env = apply_envelope(raw, agent=None)
    assert env.tags[-3:] == (f"agent:{UNKNOWN_AGENT}", "kind:durable", "scope:global")


def test_a_bare_string_is_one_tag_not_its_characters():
    assert apply_envelope("checkpoint", agent="hermes").metadata == {"lifecycle": "checkpoint"}


def test_envelope_does_not_mutate_its_inputs():
    tags = ["checkpoint", UUID4, "hal0"]
    metadata = {"note": "x"}
    apply_envelope(tags, agent="hermes", metadata=metadata)
    assert tags == ["checkpoint", UUID4, "hal0"]
    assert metadata == {"note": "x"}


def test_envelope_is_idempotent_on_its_own_output():
    first = apply_envelope(["hal0", "checkpoint", UUID4], agent="hermes")
    again = apply_envelope(first.tags, agent="hermes", metadata=first.metadata)
    assert again.tags == first.tags
    assert again.metadata == {}


@pytest.mark.parametrize(
    ("tags", "resolution"),
    [
        (["topic:cats"], None),
        (["topic:cats", "scope:global"], None),
        (["topic:cats"], ATLAS),
        (["topic:cats", "checkpoint"], CODING_NOTHING),
    ],
)
def test_a_second_pass_with_the_same_decision_changes_nothing(tags, resolution):
    first = apply_envelope(tags, agent="hermes", project_resolution=resolution)
    again = apply_envelope(
        first.tags, agent="hermes", project_resolution=resolution, metadata=first.metadata
    )
    assert again.tags == first.tags
    assert again.metadata == {}


def test_the_project_tag_constants():
    assert SCOPE_GLOBAL_TAG == "scope:global"
    assert PROJECT_UNRESOLVED_TAG == "project:unresolved"


# ── Observation scopes ──────────────────────────────────────────────────────


def test_the_scope_constants_are_engine_modes():
    assert DEFAULT_OBSERVATION_SCOPES == "shared"
    assert COMBINED_OBSERVATION_SCOPES == "combined"


@pytest.mark.parametrize(
    "tags",
    [
        [],
        ["agent:hermes", "kind:durable"],
        ["agent:hermes", "kind:durable", "scope:global"],
        ["hal0", "source:chat", "agent:hermes", "kind:ephemeral"],
        # Not project tags to the read filter either: a bare word, another
        # case, another namespace.
        ["project", "agent:pi", "kind:durable"],
        ["Project:hal0", "agent:pi", "kind:durable"],
        ["my-project:hal0", "agent:pi", "kind:durable"],
    ],
)
def test_a_write_with_no_project_gets_the_shared_scope(tags):
    assert resolve_observation_scopes(None, tags) == "shared"


@pytest.mark.parametrize(
    "tags",
    [
        ["agent:pi", "kind:durable", "project:unresolved"],
        ["agent:pi", "kind:ephemeral", "project:unresolved"],
        ["source:chat", "agent:pi", "kind:durable", "project:unresolved", "scope:global"],
    ],
)
def test_an_unresolved_write_is_scoped_to_its_writer(tags):
    """``project:unresolved`` is a marker every repository's unresolved writes
    carry, so it is never a scope alone. It is not the untagged scope either:
    an observation built there has no tags, and every scoped read admits an
    untagged memory, so one client's unresolved writes would surface in every
    other client's project. The scope is the marker and the writer."""
    assert resolve_observation_scopes(None, tags) == [["project:unresolved", "agent:pi"]]


def test_the_observation_of_an_unresolved_write_is_readable_by_its_writer_only():
    """The engine writes an explicit scope's tags onto the observation
    verbatim. Held to the read groups (as the engine evaluates them): the
    writer reads it, no other caller does, in a project or out of one."""
    from hal0.memory.read_scope import (
        project_scope_groups,
        reflect_visibility_scope,
        tags_readable,
        unresolved_scope_groups,
    )

    write = apply_envelope(["source:chat"], agent="pi", coding=True)
    (observation,) = resolve_observation_scopes(None, write.tags)
    assert observation == ["project:unresolved", "agent:pi"]

    def reads(agent, project_groups):
        return tags_readable([*reflect_visibility_scope(agent), *project_groups], observation)

    assert reads("pi", unresolved_scope_groups("pi"))
    assert not reads("codex", unresolved_scope_groups("codex"))
    assert not reads("codex", project_scope_groups("atlas"))
    assert not reads("pi", project_scope_groups("atlas"))
    assert not reads(None, project_scope_groups(None))
    # The untagged scope it used to get is what every one of those admits.
    assert reads("codex", project_scope_groups("atlas")) is False
    assert tags_readable(project_scope_groups("atlas"), [])


def test_an_unresolved_write_whose_writer_cannot_be_named_keeps_all_its_tags():
    """The engine reads ``*`` in a scope entry as a glob, and a write with no
    ``agent:`` tag has no writer to name: both fall back to ``combined``,
    which leaves every tag of the write on the observation. Tagged, so still
    outside every other caller's read."""
    assert resolve_observation_scopes(None, ["agent:a*", "project:unresolved"]) == "combined"
    assert resolve_observation_scopes(None, ["project:unresolved"]) == "combined"


@pytest.mark.parametrize(
    ("tags", "scopes"),
    [
        (["agent:pi", "kind:durable"], "shared"),
        (["agent:pi", "kind:durable", "project:atlas"], [["project:atlas"]]),
        (
            ["agent:pi", "kind:durable", "project:atlas", "project:borealis"],
            [["project:atlas"], ["project:borealis"]],
        ),
        (
            ["agent:pi", "kind:ephemeral", "project:unresolved"],
            [["project:unresolved", "agent:pi"]],
        ),
    ],
)
def test_scope_global_does_not_change_the_scope_chosen(tags, scopes):
    """``scope:global`` is a tag for reads. It is not a project and not an
    observation scope: with it or without it the write is scoped the same."""
    assert resolve_observation_scopes(None, tags) == scopes
    assert resolve_observation_scopes(None, [*tags, "scope:global"]) == scopes
    assert resolve_observation_scopes(None, ["scope:global", *tags]) == scopes
    assert resolve_observation_scopes(None, [*tags, "scope:team"]) == scopes


def test_beside_a_real_project_the_real_project_decides():
    mixed = ["project:unresolved", "project:atlas"]
    assert resolve_observation_scopes(None, mixed) == [["project:atlas"]]
    # A caller's explicit choice still stands, as for any write.
    assert resolve_observation_scopes("per_tag", ["project:unresolved"]) == "per_tag"


@pytest.mark.parametrize(
    ("tags", "scopes"),
    [
        (["project:hal0", "agent:pi", "kind:durable"], [["project:hal0"]]),
        # Other tags take no part in the scope.
        (
            ["hal0", "source:chat", "agent:pi", "kind:ephemeral", "project:hal0"],
            [["project:hal0"]],
        ),
        (
            ["agent:pi", "kind:durable", "project:hal0", "visibility:private"],
            [["project:hal0"]],
        ),
        # One scope per project, in tag order, each named once.
        (
            ["project:hal0-web", "agent:pi", "kind:durable", "project:hal0", "project:hal0-web"],
            [["project:hal0-web"], ["project:hal0"]],
        ),
        (
            ["project:example.com+acme+widgets", "agent:pi", "kind:durable"],
            [["project:example.com+acme+widgets"]],
        ),
    ],
)
def test_a_write_with_a_project_gets_one_scope_per_project(tags, scopes):
    assert resolve_observation_scopes(None, tags) == scopes


@pytest.mark.parametrize(
    "tags",
    [
        ["project:*", "agent:pi", "kind:durable"],
        ["project:hal0*", "agent:pi", "kind:durable"],
        ["project:hal0", "project:web*", "agent:pi", "kind:durable"],
    ],
)
def test_a_project_tag_the_engine_would_read_as_a_glob_falls_back_to_combined(tags):
    assert resolve_observation_scopes(None, tags) == "combined"


@pytest.mark.parametrize("requested", ["per_tag", "combined", "all_combinations", "shared"])
@pytest.mark.parametrize("tags", [[], ["project:hal0", "agent:pi", "kind:durable"]])
def test_an_explicit_observation_scope_is_kept(requested, tags):
    assert resolve_observation_scopes(requested, tags) == requested


def test_an_explicit_scope_list_is_kept_for_a_project_write():
    requested = [["topic:*"]]
    assert resolve_observation_scopes(requested, ["project:hal0"]) is requested


def test_the_scope_follows_the_envelopes_own_output():
    project = apply_envelope(["checkpoint", "hal0"], agent="pi", project_resolution=ATLAS)
    plain = apply_envelope(["checkpoint", "hal0"], agent="pi")
    durable = apply_envelope(["hal0"], agent="pi")
    unresolved = apply_envelope(["hal0"], agent="pi", coding=True)
    assert resolve_observation_scopes(None, project.tags) == [["project:atlas"]]
    assert resolve_observation_scopes(None, plain.tags) == "shared"
    assert resolve_observation_scopes(None, durable.tags) == "shared"
    assert "project:unresolved" in unresolved.tags
    assert resolve_observation_scopes(None, unresolved.tags) == [["project:unresolved", "agent:pi"]]
    several = apply_envelope(["project:Atlas", "project:borealis"], agent="pi")
    assert resolve_observation_scopes(None, several.tags) == [
        ["project:atlas"],
        ["project:borealis"],
    ]
