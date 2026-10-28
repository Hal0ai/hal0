"""Bridge client store (ADR-0043): bridge.db holds client key hashes.

The store is machine state, so it lives in SQLite rather than TOML. These
tests pin the behaviour the auth layer and the admin CLI/API depend on:
only a hash of each key is stored, a revoked or rotated key stops working,
and the file is created owner-only because it carries credential hashes.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from hal0.bridge.store import BridgeStore, BridgeStoreError


@pytest.fixture
def store(tmp_path: Path) -> Iterator[BridgeStore]:
    opened = BridgeStore(tmp_path / "bridge.db")
    opened.open()
    yield opened


def test_open_creates_0600_and_is_idempotent(store: BridgeStore, tmp_path: Path) -> None:
    assert oct(os.stat(tmp_path / "bridge.db").st_mode & 0o777) == "0o600"
    store.add_client("keep", created_by="cli")
    store.open()  # second open applies nothing new and keeps the rows
    assert [c.name for c in store.list_clients()] == ["keep"]
    assert oct(os.stat(tmp_path / "bridge.db").st_mode & 0o777) == "0o600"


def test_add_verify_revoke(store: BridgeStore) -> None:
    row, key = store.add_client("claude-code", created_by="cli")
    assert row.revoked_at is None
    assert row.last_seen_at is None
    verified = store.verify_key(key)
    assert verified is not None
    assert verified.name == "claude-code"
    assert verified.last_seen_at is not None
    store.revoke_client("claude-code")
    assert store.verify_key(key) is None


def test_add_existing_or_revoked_is_409(store: BridgeStore) -> None:
    store.add_client("x", created_by="cli")
    with pytest.raises(BridgeStoreError) as live:
        store.add_client("x", created_by="cli")
    assert live.value.code == "bridge.client_exists"
    store.revoke_client("x")
    with pytest.raises(BridgeStoreError) as revoked:
        store.add_client("x", created_by="cli")
    assert revoked.value.code == "bridge.client_exists"


def test_rotate_invalidates_old_key_and_refuses_revoked(store: BridgeStore) -> None:
    _, old = store.add_client("r", created_by="cli")
    _, new = store.rotate_client("r")
    assert store.verify_key(old) is None
    assert store.verify_key(new) is not None
    store.revoke_client("r")
    with pytest.raises(BridgeStoreError) as revoked:
        store.rotate_client("r")
    assert revoked.value.code == "bridge.client_revoked"
    with pytest.raises(BridgeStoreError) as unknown:
        store.rotate_client("nope")
    assert unknown.value.code == "bridge.client_not_found"


def test_invalid_name_and_unknown(store: BridgeStore) -> None:
    with pytest.raises(BridgeStoreError) as bad:
        store.add_client("Bad.Name", created_by="cli")
    assert bad.value.code == "bridge.invalid_name"
    with pytest.raises(BridgeStoreError) as unknown:
        store.revoke_client("nope")
    assert unknown.value.code == "bridge.client_not_found"
    with pytest.raises(BridgeStoreError) as unknown_scopes:
        store.set_scopes("nope", ["memory"])
    assert unknown_scopes.value.code == "bridge.client_not_found"
    assert store.get_client("nope") is None


def test_plaintext_never_stored(store: BridgeStore, tmp_path: Path) -> None:
    _, key = store.add_client("p", created_by="cli")
    db = tmp_path / "bridge.db"
    on_disk = db.read_bytes()
    # WAL mode keeps recent writes in the sidecar until a checkpoint.
    wal = Path(f"{db}-wal")
    if wal.exists():
        on_disk += wal.read_bytes()
    assert key.encode() not in on_disk


def test_scopes_default_normalize_and_set(store: BridgeStore) -> None:
    row, _ = store.add_client("s", created_by="cli")
    assert row.scopes == frozenset({"agents", "memory"})
    row, _ = store.add_client("t", created_by="cli", scopes=["admin"])
    assert row.scopes == frozenset({"agents", "admin"})  # agents is implied
    assert store.set_scopes("t", ["memory"]).scopes == frozenset({"agents", "memory"})
    with pytest.raises(BridgeStoreError) as bad:
        store.add_client("u", created_by="cli", scopes=["root"])
    assert bad.value.code == "bridge.invalid_scope"
    assert store.get_client("u") is None
    with pytest.raises(BridgeStoreError) as bad_set:
        store.set_scopes("t", ["root"])
    assert bad_set.value.code == "bridge.invalid_scope"
    unchanged = store.get_client("t")
    assert unchanged is not None
    assert unchanged.scopes == frozenset({"agents", "memory"})
    _, new = store.rotate_client("t")
    verified = store.verify_key(new)
    assert verified is not None
    assert verified.scopes == frozenset({"agents", "memory"})


def test_rotate_keeps_note_and_created_fields(store: BridgeStore) -> None:
    before, _ = store.add_client("n", created_by="cli", note="laptop")
    after, _ = store.rotate_client("n")
    assert after.note == "laptop"
    assert after.created_at == before.created_at
    assert after.created_by == "cli"


def test_verify_rejects_malformed_forged_and_mismatched_keys(store: BridgeStore) -> None:
    _, key = store.add_client("a", created_by="cli")
    _, other = store.add_client("b", created_by="cli")
    assert store.verify_key("sk-not-a-bridge-key") is None
    assert store.verify_key("hal0bk_a.forged-token") is None
    # b's token under a's embedded name: the hash matches no row for "a"
    assert store.verify_key("hal0bk_a." + other.split(".", 1)[1]) is None
    assert store.verify_key(key) is not None


def test_list_and_as_dict(store: BridgeStore) -> None:
    store.add_client("b", created_by="cli", scopes=["memory"])
    store.add_client("a", created_by="api")
    store.revoke_client("a")
    rows = store.list_clients()
    assert [r.name for r in rows] == ["a", "b"]
    a = rows[0].as_dict()
    assert a["name"] == "a"
    assert a["created_by"] == "api"
    assert a["revoked_at"] is not None
    assert rows[1].as_dict()["scopes"] == ["agents", "memory"]
    assert "key_hash" not in a
