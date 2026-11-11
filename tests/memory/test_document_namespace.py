"""Namespaced document ids in the platform bank (ADR-0058 decisions 4b and 7a).

``<project>::<owner>::<the client's id>``: pure functions, table-driven. The
property that matters is that the mapping is injective across clients and
projects, and that nothing a client sends is ever read as a namespace.
"""

from __future__ import annotations

import hashlib
import itertools

import pytest

from hal0.bridge import keys
from hal0.memory.document_namespace import (
    CLIENT_DOCUMENT_ID_KEY,
    MAX_STORED_ID_LENGTH,
    SEPARATOR,
    SHARED_OWNER,
    chunk_id_part,
    id_prefix,
    is_digested,
    is_namespaced,
    is_shared_id,
    readable_prefixes,
    stored_document_id,
    visible_document_id,
)
from hal0.project_slug import RESERVED_SLUGS, UNRESOLVED_SLUG, normalise_slug

SHARED = ("git:", "gitlog:", "survey-baseline:")


@pytest.mark.parametrize(
    ("slug", "client", "client_id", "stored"),
    [
        ("hal0", "cc", "readme", "hal0::cc::readme"),
        ("hal0", "codex", "readme", "hal0::codex::readme"),
        ("atlas", "cc", "readme", "atlas::cc::readme"),
        (None, "cc", "readme", "unresolved::cc::readme"),
        ("hal0", "cc", "conversation:0f3c", "hal0::cc::conversation:0f3c"),
        ("hal0", "cc", "git:4f2a", "hal0::shared::git:4f2a"),
        ("hal0", "codex", "git:4f2a", "hal0::shared::git:4f2a"),
        ("hal0", "cc", "gitlog:hal0", "hal0::shared::gitlog:hal0"),
        ("hal0", "cc", "survey-baseline:4f2a", "hal0::shared::survey-baseline:4f2a"),
        # A prefix is a prefix, not a substring, and not a near miss.
        ("hal0", "cc", "my-git:4f2a", "hal0::cc::my-git:4f2a"),
        ("hal0", "cc", "git", "hal0::cc::git"),
        ("hal0", "cc", "Git:4f2a", "hal0::cc::Git:4f2a"),
        # No project, nothing shared.
        (None, "cc", "git:4f2a", "unresolved::cc::git:4f2a"),
        # The client's id is taken whole.
        ("hal0", "cc", "atlas::codex::x", "hal0::cc::atlas::codex::x"),
        ("hal0", "cc", "::", "hal0::cc::::"),
    ],
)
def test_stored_document_id(slug, client, client_id, stored):
    assert stored_document_id(slug, client, client_id, SHARED) == stored
    assert visible_document_id(slug, client, stored) == client_id
    assert not is_digested(slug, client, client_id, SHARED)
    assert is_namespaced(stored)


def test_no_shared_prefixes_shares_nothing():
    assert stored_document_id("hal0", "cc", "git:4f2a") == "hal0::cc::git:4f2a"
    assert stored_document_id("hal0", "cc", "git:4f2a", [""]) == "hal0::cc::git:4f2a"
    assert not is_shared_id("git:4f2a", ()) and is_shared_id("git:4f2a", SHARED)


def test_the_mapping_never_collides_across_clients_and_projects():
    slugs = ["hal0", "hal0.web", "hal0-web", "a", "shared", "cc", None]
    clients = ["cc", "codex", "admin", "a", "hal0"]
    ids = ["readme", "git:1", "cc::readme", "shared::git:1", "::", "a::b::c", "x" * 300, "é"]
    seen: dict[str, tuple] = {}
    for slug, client, client_id in itertools.product(slugs, clients, ids):
        stored = stored_document_id(slug, client, client_id, SHARED)
        shared = bool(slug) and client_id.startswith(SHARED)
        owner = (slug, SHARED_OWNER if shared else client, client_id)
        assert seen.setdefault(stored, owner) == owner, (stored, owner, seen[stored])
        assert visible_document_id(slug, client, stored) is not None
        assert len(stored) <= MAX_STORED_ID_LENGTH


def test_the_parts_before_the_id_cannot_hold_the_separator():
    """What makes the split unambiguous: a slug is ``[a-z0-9._-]``, a client
    name ``[a-z0-9_-]``, and the two words that stand in for them are never a
    slug and never a client."""
    assert normalise_slug("a::b") == "a-b" and SEPARATOR not in normalise_slug("x:::y::")
    assert not keys.valid_name("a::b") and not keys.valid_name("a:b")
    assert UNRESOLVED_SLUG in RESERVED_SLUGS
    assert keys.is_reserved_name(SHARED_OWNER) and keys.is_reserved_name(UNRESOLVED_SLUG)
    assert id_prefix(None, "cc") == "unresolved::cc::"
    assert id_prefix("hal0", SHARED_OWNER) == "hal0::shared::"


@pytest.mark.parametrize(
    "client_id",
    ["a title with spaces", "é", "x" * 400, "a/../b", "two..dots", "a\nb", "tab\tbed", ""],
)
def test_an_id_hal0_cannot_address_is_digested(client_id):
    """hal0's bank routes take at most 256 characters of ``[a-zA-Z0-9._:-]``
    with no ``..``; the stored id must stay addressable there."""
    stored = stored_document_id("hal0", "cc", client_id, SHARED)
    digest = hashlib.sha256(client_id.encode()).hexdigest()
    assert stored == f"hal0::cc::sha256-{digest}"
    assert is_digested("hal0", "cc", client_id, SHARED)
    assert len(stored) <= MAX_STORED_ID_LENGTH
    # Shown back from the metadata hal0 wrote, and only if it really maps.
    assert (
        visible_document_id("hal0", "cc", stored, {CLIENT_DOCUMENT_ID_KEY: client_id}) == client_id
    )
    assert visible_document_id("hal0", "cc", stored, {CLIENT_DOCUMENT_ID_KEY: "other"}) == (
        f"sha256-{digest}"
    )
    assert visible_document_id("hal0", "cc", stored) == f"sha256-{digest}"
    # The digest form addresses the same document when it is sent back.
    assert stored_document_id("hal0", "cc", f"sha256-{digest}", SHARED) == stored


def test_a_long_shared_id_keeps_the_shared_namespace():
    client_id = "git:" + "f" * 300
    stored = stored_document_id("hal0", "cc", client_id, SHARED)
    assert stored.startswith("hal0::shared::sha256-")
    assert stored == stored_document_id("hal0", "codex", client_id, SHARED)


@pytest.mark.parametrize(
    ("slug", "client", "stored", "visible"),
    [
        ("hal0", "cc", "hal0::cc::readme", "readme"),
        ("hal0", "cc", "hal0::shared::git:1", "git:1"),
        # Another client's namespace, another project, no namespace at all.
        ("hal0", "cc", "hal0::codex::readme", None),
        ("hal0", "cc", "atlas::cc::readme", None),
        ("hal0", "cc", "unresolved::cc::readme", None),
        ("hal0", "cc", "hal0.web::cc::readme", None),
        ("hal0", "cc", "hal0::cc2::readme", None),
        ("hal0", "cc", "readme", None),
        ("hal0", "cc", "hermes:s-9", None),
        ("hal0", "cc", None, None),
        ("hal0", "cc", 7, None),
        # Unresolved: the client's own namespace only.
        (None, "cc", "unresolved::cc::readme", "readme"),
        (None, "cc", "unresolved::codex::readme", None),
        (None, "cc", "unresolved::shared::git:1", None),
        (None, "cc", "hal0::cc::readme", None),
    ],
)
def test_visible_document_id(slug, client, stored, visible):
    assert visible_document_id(slug, client, stored) == visible


def test_readable_prefixes():
    assert readable_prefixes("hal0", "cc") == ("hal0::cc::", "hal0::shared::")
    assert readable_prefixes(None, "cc") == ("unresolved::cc::",)


@pytest.mark.parametrize(
    ("stored", "namespaced"),
    [
        ("hal0::cc::readme", True),
        ("hal0::shared::git:1", True),
        ("unresolved::cc::x", True),
        ("hal0.web::cc::", True),
        ("hermes:s-9", False),
        ("conversation:0f3c", False),
        ("private:hermes:s-9", False),
        ("hal0::readme", False),
        ("::cc::x", False),
        ("Hal0::cc::x", False),
        ("hal0::c c::x", False),
        ("", False),
        (None, False),
    ],
)
def test_is_namespaced(stored, namespaced):
    assert is_namespaced(stored) is namespaced


def test_chunk_id_part_escapes_as_the_engine_does():
    """``hindsight_api.engine.chunk_ids``: ``~`` then ``_`` are escaped inside
    each part of ``<bank>_<document id>_<index>``."""
    assert chunk_id_part("hal0::cc::conversation:s_1") == "hal0::cc::conversation:s~5F1"
    assert chunk_id_part("a~b_c") == "a~7Eb~5Fc"
    assert chunk_id_part("plain") == "plain"
