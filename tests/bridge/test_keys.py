"""Bridge client key format (ADR-0043).

A bridge key is ``hal0bk_<name>.<token>``: the embedded name lets the auth
layer find the client row without a table scan, and only the sha256 of the
whole string is stored. These tests pin the shape, the hash, and the name
parsing that the auth layer relies on.
"""

from __future__ import annotations

import hashlib

import pytest

from hal0.bridge import keys


def test_mint_shape_and_hash() -> None:
    plain, digest = keys.mint("claude-code")
    assert plain.startswith("hal0bk_claude-code.")
    assert len(plain.split(".", 1)[1]) >= 43
    assert digest == hashlib.sha256(plain.encode()).hexdigest()
    assert digest != plain
    assert keys.hash_key(plain) == digest


def test_mint_is_unique() -> None:
    first, _ = keys.mint("claude-code")
    second, _ = keys.mint("claude-code")
    assert first != second


def test_mint_rejects_invalid_name() -> None:
    with pytest.raises(ValueError):
        keys.mint("Bad.Name")


def test_name_of_roundtrip_and_rejects() -> None:
    plain, _ = keys.mint("ci_probe-1")
    assert keys.name_of(plain) == "ci_probe-1"
    assert keys.name_of("hal0bk_nodot") is None
    assert keys.name_of("sk-other") is None


def test_name_of_rejects_invalid_embedded_name() -> None:
    assert keys.name_of("hal0bk_Upper.token") is None
    assert keys.name_of("hal0bk_.token") is None


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("a", True),
        ("claude-code", True),
        ("Claude", False),
        ("-x", False),
        ("a" * 33, False),
        ("a.b", False),
    ],
)
def test_valid_name(name: str, ok: bool) -> None:
    assert keys.valid_name(name) is ok


def test_normalize_scopes_always_includes_agents() -> None:
    assert keys.normalize_scopes([]) == frozenset({"agents"})
    assert keys.normalize_scopes([" Memory ", "ADMIN"]) == frozenset({"agents", "memory", "admin"})
    assert frozenset({"agents", "memory", "admin"}) == keys.SCOPES


def test_normalize_scopes_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError, match="root"):
        keys.normalize_scopes(["agents", "root"])


def test_reserved_names_cover_the_built_in_identities() -> None:
    """A client named like a bundled agent would alias that agent's memory namespace."""
    assert frozenset({"hermes", "pi", "admin", "anonymous", "shared"}) == (
        keys.RESERVED_CLIENT_NAMES
    )
    # Every ``hal0*`` name is hal0's own caller (hal0, hal0-cli, hal0-doctor, ...).
    assert keys.RESERVED_CLIENT_PREFIXES == ("hermes__", "hal0")


@pytest.mark.parametrize(
    ("name", "reserved"),
    [
        ("hermes", True),
        ("hermes__scout", True),
        ("hal0", True),
        ("hal0-cli", True),
        ("hal0-doctor", True),
        ("hal0_bot", True),
        ("shared", True),
        ("myhal0", False),
        ("hermes2", False),
        ("hermes_scout", False),
        ("my-pi", False),
        ("claude-code", False),
    ],
)
def test_is_reserved_name(name: str, reserved: bool) -> None:
    assert keys.is_reserved_name(name) is reserved


def test_scope_table_is_the_one_owner_of_scopes_and_mounts() -> None:
    """ADR-0043: one table maps each scope to its server and mount; the rest derive from it."""
    from hal0.api import auth
    from hal0.api.routes import bridge as bridge_routes

    assert frozenset(keys.SCOPE_SERVERS) == keys.SCOPES
    assert set(keys.DEFAULT_SCOPES) <= keys.SCOPES
    assert dict(auth.BRIDGE_SCOPE_PREFIXES) == {
        scope: mount for scope, (_server, mount) in keys.SCOPE_SERVERS.items() if scope != "agents"
    }
    assert list(keys.SCOPE_SERVERS) == bridge_routes._ADMIN_SCOPES
