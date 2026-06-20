"""Spec §3 closed-namespace policy — :mod:`hal0.memory.namespace`.

Free-form dataset names used to pass through verbatim, which let any
caller read/write arbitrary engine banks (live-verified on CT105:
a probe wrote into a stale smoke-test bank, and the item was then
unreachable by the id-scoped delete sweep). The namespace set is now
closed: ``shared`` | ``agents`` | ``project:<id>`` | own private.
"""

from __future__ import annotations

import pytest

from hal0.memory.namespace import (
    MemoryNamespaceError,
    is_known_namespace,
    resolve_read_datasets,
    resolve_write_dataset,
)

# ── is_known_namespace ───────────────────────────────────────────────────────


def test_known_namespaces_table() -> None:
    assert is_known_namespace("shared")
    assert is_known_namespace("agents")
    assert is_known_namespace("project:apollo")
    assert is_known_namespace("project:a-b_c42")
    assert is_known_namespace("private:hermes", client_id="hermes")


def test_unknown_namespaces_rejected() -> None:
    assert not is_known_namespace("smoke-gemma4-e4b-v2")
    assert not is_known_namespace("project:")  # empty scoped id
    assert not is_known_namespace("project:has space")
    assert not is_known_namespace("project:" + "x" * 65)  # over identity bound
    assert not is_known_namespace("private:hermes", client_id="other")  # foreign private
    assert not is_known_namespace("private:hermes")  # private without identity


# ── resolve_write_dataset ────────────────────────────────────────────────────


def test_write_allows_spec_table_names() -> None:
    assert resolve_write_dataset("shared", private=False, client_id="a") == "shared"
    assert resolve_write_dataset("agents", private=False, client_id="a") == "agents"
    assert resolve_write_dataset("project:apollo", private=False, client_id="a") == "project:apollo"


def test_write_rejects_free_form_names() -> None:
    with pytest.raises(MemoryNamespaceError, match="unknown namespace"):
        resolve_write_dataset("smoke-gemma4-e4b-v2", private=False, client_id="a")


def test_write_private_prefix_still_requires_toggle() -> None:
    # Pre-existing rule (PR #366) — unchanged by the closed-set hardening.
    with pytest.raises(MemoryNamespaceError, match="cannot address the private namespace"):
        resolve_write_dataset("private:a", private=False, client_id="a")


def test_write_private_toggle_still_promotes() -> None:
    assert resolve_write_dataset("anything", private=True, client_id="a") == "private:a"


def test_write_private_rejects_missing_or_anonymous_client_id() -> None:
    # The MCP/REST identity resolvers emit "anonymous" for an absent/malformed
    # X-hal0-Agent header; a private write under it must be rejected, not
    # mis-scoped into a private:anonymous bank (regression: that bank was
    # accumulating misrouted writes because "anonymous" is truthy).
    for cid in (None, "", "anonymous"):
        with pytest.raises(MemoryNamespaceError, match="authenticated client_id"):
            resolve_write_dataset("anything", private=True, client_id=cid)


# ── resolve_read_datasets ────────────────────────────────────────────────────


def test_read_list_drops_unknown_and_foreign_private() -> None:
    out = resolve_read_datasets(
        ["shared", "agents", "project:apollo", "private:me", "private:other", "junk-bank"],
        private=False,
        client_id="me",
    )
    assert out == ["shared", "agents", "project:apollo", "private:me"]


def test_read_default_expansion_unchanged() -> None:
    assert resolve_read_datasets(None, private=False, client_id=None) == "shared"
    assert resolve_read_datasets(None, private=True, client_id="me") == [
        "shared",
        "private:me",
    ]


def test_read_string_resolves_via_write_rules() -> None:
    with pytest.raises(MemoryNamespaceError, match="unknown namespace"):
        resolve_read_datasets("junk-bank", private=False, client_id="me")
