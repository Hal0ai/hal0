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
    UNKNOWN_AGENT,
    apply_envelope,
    coerce_tags,
    lifecycle_word_of,
    resolve_agent,
    resolve_observation_scopes,
    session_id_of,
)

UUID4 = "3f2b8c1e-5a47-4d09-9e6b-7c1a2d3e4f50"
UUID7 = "019a2b3c-4d5e-7f60-8a1b-2c3d4e5f6071"
HEX32 = "3f2b8c1e5a474d099e6b7c1a2d3e4f50"
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
    assert env.tags == ("topic:cats", "agent:openwebui", "kind:durable")


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
        # A lifecycle word overrides an explicit durable.
        (["kind:durable", "checkpoint"], "ephemeral"),
        (["chat"], "ephemeral"),
        # A session id alone does not make a write ephemeral.
        ([UUID4], "durable"),
    ],
)
def test_exactly_one_kind_tag(tags, kind):
    env = apply_envelope(tags, agent="hermes")
    assert env.kind == kind
    assert [t for t in env.tags if t.lower().startswith("kind:")] == [f"kind:{kind}"]


def test_an_unrecognised_kind_value_is_user_vocabulary():
    env = apply_envelope(["kind:decision"], agent="hermes")
    assert env.tags == ("kind:decision", "agent:hermes", "kind:durable")


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
        # A UUID without its dashes: exactly 32 hexadecimal digits.
        (HEX32, HEX32),
        (HEX32.upper(), HEX32.upper()),
        (f" {HEX32} ", HEX32),
        ("0" * 32, "0" * 32),
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
        # Hex-looking but longer than a UUID: a full commit hash, a digest.
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
    assert env.tags == ("hal0", "agent:claude-code", "kind:durable")
    assert env.metadata == {"session_id": UUID4}


def test_several_session_tags_move_to_one_list_key():
    env = apply_envelope([UUID4, f"session:{UUID7}", UUID4, HEX32], agent="codex")
    assert env.tags == ("agent:codex", "kind:durable")
    assert env.metadata == {"session_ids": [UUID4, UUID7, HEX32]}


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
    assert env.tags == ("agent:hermes", "kind:durable")


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
    assert env.tags == (tag, "agent:hermes", "kind:durable")
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
            ("delegation", "agent:hermes", "kind:durable"),
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
                "project:hal0",
                "agent:hermes",
                "kind:ephemeral",
            ),
            {"session_id": UUID4, "lifecycle": "checkpoint"},
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


# ── Rule 5: project ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tags", "project", "expected"),
    [
        (["project:hal0"], None, ("project:hal0", "agent:pi", "kind:durable")),
        ([], "hal0", ("agent:pi", "kind:durable", "project:hal0")),
        ([], "project:hal0", ("agent:pi", "kind:durable", "project:hal0")),
        (["project:hal0"], "hal0", ("project:hal0", "agent:pi", "kind:durable")),
        (
            ["project:hal0-web"],
            "hal0",
            ("project:hal0-web", "agent:pi", "kind:durable", "project:hal0"),
        ),
        ([], "", ("agent:pi", "kind:durable")),
        ([], "   ", ("agent:pi", "kind:durable")),
    ],
)
def test_project_tag_passes_through_or_comes_from_the_explicit_argument(tags, project, expected):
    assert apply_envelope(tags, agent="pi", project=project).tags == expected


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
    assert env.tags == ("hal0", "hal0-web", "agent:pi", "kind:durable")
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
    assert env.tags == (tag, "agent:hermes", "kind:durable")
    assert env.metadata == {}


def test_caller_tag_order_is_kept_and_exact_duplicates_collapse():
    env = apply_envelope(["b", "a", "b", "c", "a"], agent="hermes")
    assert env.tags == ("b", "a", "c", "agent:hermes", "kind:durable")


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
    assert env.tags[-2:] == (f"agent:{UNKNOWN_AGENT}", "kind:durable")


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


# ── Rule 7: observation scopes ──────────────────────────────────────────────


def test_the_scope_constants_are_engine_modes():
    assert DEFAULT_OBSERVATION_SCOPES == "shared"
    assert COMBINED_OBSERVATION_SCOPES == "combined"


@pytest.mark.parametrize(
    "tags",
    [
        [],
        ["agent:hermes", "kind:durable"],
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
    project = apply_envelope(["checkpoint", "hal0"], agent="pi", project="hal0")
    plain = apply_envelope(["checkpoint", "hal0"], agent="pi")
    assert resolve_observation_scopes(None, project.tags) == [["project:hal0"]]
    assert resolve_observation_scopes(None, plain.tags) == "shared"
