"""Token store CRUD + verification tests.

Covers:

  - Round-trip create → list → verify → revoke.
  - Argon2id hashing — secret never persisted in plaintext.
  - Duplicate label rejection.
  - Invalid scope rejection.
  - Atomic write — corrupt mid-write doesn't lock the user out.
  - last_used_at bump on successful verify.
  - Wire-format parsing rejects malformed tokens (no prefix, no '.', etc.).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.auth.tokens import (
    SCHEMA_VERSION,
    DuplicateLabel,
    InvalidScope,
    TokenNotFound,
    TokenStore,
    TokenStoreError,
    auth_enabled,
)


@pytest.fixture
def store(tmp_path: Path) -> TokenStore:
    return TokenStore(tmp_path / "tokens.toml")


def test_auth_enabled_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1.0 flip (security review §36): unset → True.

    Pre-v1 this test asserted ``is False`` — open by default. The flip
    is the headline behaviour change of §36's fix.
    """
    monkeypatch.delenv("HAL0_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    assert auth_enabled() is True


def test_auth_disabled_env_opts_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """``HAL0_AUTH_DISABLED=1`` is the v1.0 opt-out for trusted-LAN dev."""
    monkeypatch.delenv("HAL0_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("HAL0_AUTH_DISABLED", "1")
    assert auth_enabled() is False


def test_disabled_beats_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit opt-out wins over an explicit opt-in."""
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_AUTH_DISABLED", "1")
    assert auth_enabled() is False


@pytest.mark.parametrize(
    "val,expected",
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("nope", False),
    ],
)
def test_auth_enabled_env_parse(monkeypatch: pytest.MonkeyPatch, val: str, expected: bool) -> None:
    # HAL0_AUTH_DISABLED must be unset so HAL0_AUTH_ENABLED is what's
    # tested. (The conftest autouse sets DISABLED=1 by default.)
    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HAL0_AUTH_ENABLED", val)
    assert auth_enabled() is expected


def test_create_returns_raw_token_once(store: TokenStore) -> None:
    tok, raw = store.create(label="openwebui", scope="all")
    assert raw.startswith("hal0_")
    assert "." in raw
    assert tok.id in raw
    assert tok.label == "openwebui"
    assert tok.scope == "all"
    # The hash is NOT the raw token — argon2id produces a $argon2id$ prefix.
    assert tok.hash.startswith("$argon2id$")
    assert raw not in tok.hash


def test_persisted_file_has_no_plaintext_secret(store: TokenStore) -> None:
    _, raw = store.create(label="openwebui", scope="all")
    contents = store.path.read_text(encoding="utf-8")
    assert raw not in contents, "raw token must NEVER appear in tokens.toml"
    # The argon2 hash IS in there — that's by design.
    assert "$argon2id$" in contents
    # Schema version is recorded for future migrations.
    assert f"schema_version = {SCHEMA_VERSION}" in contents


def test_verify_round_trip(store: TokenStore) -> None:
    tok, raw = store.create(label="bridge", scope="all")
    matched = store.verify(raw)
    assert matched is not None
    assert matched.id == tok.id
    assert matched.last_used_at is not None  # bumped on first use


def test_verify_wrong_secret(store: TokenStore) -> None:
    _tok, raw = store.create(label="bridge", scope="all")
    # Tamper with the secret half but keep the id half so we hit the
    # right hash slot before failing argon2 verify.
    prefix, _, _secret = raw.rpartition(".")
    bogus = f"{prefix}.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    assert store.verify(bogus) is None


def test_verify_unknown_id(store: TokenStore) -> None:
    store.create(label="bridge", scope="all")
    # Random unknown id — must NOT leak existence by timing or any other channel.
    assert store.verify("hal0_deadbeef.somesecret") is None


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "no-prefix",
        "Bearer hal0_abc.def",  # the strip happens in the middleware, not here
        "hal0_",
        "hal0_abc",
        "hal0_abcde123",  # no '.'
        "hal0_short.x",  # id too short
    ],
)
def test_verify_malformed(store: TokenStore, malformed: str) -> None:
    store.create(label="bridge", scope="all")
    assert store.verify(malformed) is None


def test_duplicate_label_rejected(store: TokenStore) -> None:
    store.create(label="dup", scope="all")
    with pytest.raises(DuplicateLabel) as exc:
        store.create(label="dup", scope="admin")
    assert exc.value.code == "auth.duplicate_label"
    assert exc.value.status == 409


def test_invalid_scope_rejected(store: TokenStore) -> None:
    with pytest.raises(InvalidScope) as exc:
        store.create(label="bad", scope="superadmin")
    assert exc.value.code == "auth.invalid_scope"
    assert exc.value.status == 400


def test_empty_label_rejected(store: TokenStore) -> None:
    with pytest.raises(TokenStoreError):
        store.create(label="", scope="all")
    with pytest.raises(TokenStoreError):
        store.create(label="   ", scope="all")


def test_revoke_removes_token(store: TokenStore) -> None:
    tok, raw = store.create(label="bridge", scope="all")
    assert store.verify(raw) is not None
    store.revoke(tok.id)
    assert store.verify(raw) is None
    assert store.get(tok.id) is None


def test_revoke_unknown_id_raises(store: TokenStore) -> None:
    with pytest.raises(TokenNotFound) as exc:
        store.revoke("notarealid")
    assert exc.value.code == "auth.token_not_found"
    assert exc.value.status == 404


def test_list_returns_metadata_without_hash(store: TokenStore) -> None:
    tok, _ = store.create(label="bridge", scope="all")
    rows = store.list()
    assert len(rows) == 1
    meta = rows[0].metadata()
    assert meta["id"] == tok.id
    assert meta["label"] == "bridge"
    assert meta["scope"] == "all"
    assert "hash" not in meta


def test_reload_picks_up_external_edits(store: TokenStore) -> None:
    store.create(label="initial", scope="all")
    # Simulate an out-of-band write that adds a row.
    contents = store.path.read_text(encoding="utf-8")
    extra = """
[[tokens]]
id = "deadbeef"
label = "external"
hash = "$argon2id$v=19$m=65536,t=3,p=4$AAAA$BBBB"
scope = "v1-only"
created_at = "2026-05-15T10:00:00Z"
"""
    store.path.write_text(contents + extra, encoding="utf-8")

    # The fingerprint check in _ensure_loaded() picks the change up on
    # the next access — no explicit reload() needed. (task #12)
    assert any(t.label == "external" for t in store.list()) is True
    # The explicit reload() escape hatch still works and is idempotent.
    store.reload()
    assert any(t.label == "external" for t in store.list()) is True


def test_create_persists_across_instances(tmp_path: Path) -> None:
    """A new TokenStore reads the previous instance's writes from disk."""
    path = tmp_path / "tokens.toml"
    store_a = TokenStore(path)
    _, raw = store_a.create(label="bridge", scope="all")

    store_b = TokenStore(path)
    matched = store_b.verify(raw)
    assert matched is not None
    assert matched.label == "bridge"


def test_atomic_write_no_orphan_tmp(store: TokenStore) -> None:
    store.create(label="a", scope="all")
    store.create(label="b", scope="admin")
    leftover = list(store.path.parent.glob(".tokens.toml.*"))
    assert leftover == [], f"orphan tmp files: {leftover}"


def test_last_used_at_persists(store: TokenStore) -> None:
    _, raw = store.create(label="bridge", scope="all")
    store.verify(raw)
    # Re-read and confirm the bump survived the rewrite.
    fresh = TokenStore(store.path)
    rows = fresh.list()
    assert rows[0].last_used_at is not None


def test_external_token_add_takes_effect_without_restart(tmp_path: Path) -> None:
    """A token minted into tokens.toml by another process (the CLI's
    ``hal0 auth token add`` is the motivating case) must validate on the
    very next request without anyone calling ``store.reload()`` —
    otherwise operators have to bounce ``hal0-api.service`` every time
    they issue a credential. See task #12.
    """
    path = tmp_path / "tokens.toml"

    # Process A: long-lived API. Mints an initial token and serves a
    # request with it — same instance is reused for the second request,
    # mirroring the FastAPI app.state singleton.
    api_store = TokenStore(path)
    _, raw_initial = api_store.create(label="initial", scope="all")
    assert api_store.verify(raw_initial) is not None, (
        "initial token must validate before the external write"
    )

    # Process B: CLI in another shell. Opens its own TokenStore against
    # the same file, mints a token, drops out. The atomic rename in
    # _save() guarantees the on-disk file has a fresh inode + mtime.
    cli_store = TokenStore(path)
    _, raw_added = cli_store.create(label="added-by-cli", scope="all")

    # Process A again: NO reload() call, NO restart. The next verify()
    # must pick the new token up via the fingerprint check.
    matched = api_store.verify(raw_added)
    assert matched is not None, (
        "newly-added token failed to validate without an API restart — "
        "fingerprint-driven reload is broken"
    )
    assert matched.label == "added-by-cli"
    # And the original token still works — reload must merge, not replace.
    assert api_store.verify(raw_initial) is not None


def test_corrupt_toml_raises_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "tokens.toml"
    path.write_text("this is not valid TOML [[", encoding="utf-8")
    store = TokenStore(path)
    with pytest.raises(TokenStoreError) as exc:
        store.list()
    assert exc.value.code == "auth.store_error"
