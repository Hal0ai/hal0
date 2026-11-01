"""``ensure_managed_bridge_client``: hal0 mints a companion's bridge client, narrowly."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from hal0.bridge.managed import ManagedClientRefused, ensure_managed_bridge_client
from hal0.bridge.store import BridgeStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[BridgeStore]:
    opened = BridgeStore(tmp_path / "bridge.db")
    opened.open()
    yield opened


def _ensure(store: BridgeStore, **kw: object):
    return ensure_managed_bridge_client(
        "openwebui",
        scopes=("agents", "memory"),
        created_by="system:openwebui",
        store=store,
        may_mint=True,
        **kw,  # type: ignore[arg-type]
    )


def test_mints_once_and_returns_plaintext_only_on_the_mint(store: BridgeStore) -> None:
    row, plain = _ensure(store)
    assert row is not None and plain is not None and plain.startswith("hal0bk_openwebui.")
    assert row.created_by == "system:openwebui" and row.scopes == {"agents", "memory"}
    again, second = _ensure(store)
    assert again is not None and second is None
    assert len(store.list_clients()) == 1
    assert store.verify_key(plain) is not None


def test_a_caller_that_may_not_mint_creates_nothing(store: BridgeStore) -> None:
    row, plain = ensure_managed_bridge_client(
        "openwebui",
        scopes=("agents",),
        created_by="system:openwebui",
        store=store,
        may_mint=False,
    )
    assert (row, plain) == (None, None)
    assert store.list_clients() == []


def test_a_caller_that_may_not_mint_still_reads_an_existing_row(store: BridgeStore) -> None:
    _ensure(store)
    row, plain = ensure_managed_bridge_client(
        "openwebui",
        scopes=("agents",),
        created_by="system:openwebui",
        store=store,
        may_mint=False,
    )
    assert row is not None and plain is None


def test_a_client_someone_else_made_is_never_adopted(store: BridgeStore) -> None:
    store.add_client("openwebui", created_by="api:admin")
    with pytest.raises(ManagedClientRefused):
        _ensure(store)
    assert store.get_client("openwebui").created_by == "api:admin"  # type: ignore[union-attr]


def test_a_revoked_managed_client_is_returned_not_reminted(store: BridgeStore) -> None:
    _, plain = _ensure(store)
    store.revoke_client("openwebui")
    row, second = _ensure(store)
    assert row is not None and row.revoked_at is not None and second is None
    assert plain is not None and store.verify_key(plain) is None


def test_default_may_mint_follows_the_service_user_predicate(
    store: BridgeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hal0.bridge.managed.is_hal0_service_user", lambda: False)
    row, _ = ensure_managed_bridge_client(
        "openwebui", scopes=("agents",), created_by="system:openwebui", store=store
    )
    assert row is None
