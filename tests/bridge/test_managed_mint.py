"""``hal0.bridge.managed_mint``: the hal0-side helper a root CLI runs to mint
or rotate a hal0-managed bridge client (the Pi driver's ``pi-coder``)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hal0.bridge import managed_mint
from hal0.bridge.store import BridgeStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BridgeStore:
    from hal0.config import loader

    monkeypatch.setattr(
        loader,
        "load_hal0_config",
        lambda *a, **k: SimpleNamespace(bridge=SimpleNamespace(enabled=True)),
    )
    monkeypatch.setattr("hal0.bridge.managed.is_hal0_service_user", lambda: True)
    s = BridgeStore(tmp_path / "bridge.db")
    s.open()
    return s


def test_mints_a_memory_client_marked_managed(store: BridgeStore) -> None:
    answer = managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)
    assert answer["status"] == "minted" and answer["key"].startswith("hal0bk_pi-coder.")
    row = store.get_client("pi-coder")
    assert row is not None and row.created_by == "system:pi-coder"
    assert "memory" in row.scopes
    assert store.verify_key(answer["key"]) is not None


def test_rotates_an_existing_managed_client(store: BridgeStore) -> None:
    first = managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)["key"]
    again = managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)
    assert again["status"] == "rotated" and again["key"] != first
    assert store.verify_key(first) is None and store.verify_key(again["key"]) is not None


def test_without_rotate_an_existing_client_gives_no_key(store: BridgeStore) -> None:
    managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)
    assert managed_mint.mint("pi-coder", "system:pi-coder", rotate=False, store=store) == {
        "status": "existing",
        "key": None,
        "detail": "",
    }


def test_never_touches_a_client_someone_else_made(store: BridgeStore) -> None:
    _row, theirs = store.add_client("pi-coder", "api:admin")
    answer = managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)
    assert answer["status"] == "foreign" and answer["key"] is None
    assert store.verify_key(theirs) is not None  # not rotated


def test_a_revoked_managed_client_is_not_revived(store: BridgeStore) -> None:
    managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)
    store.revoke_client("pi-coder")
    answer = managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)
    assert answer["status"] == "revoked" and answer["key"] is None


def test_the_bridge_switched_off_mints_nothing(store: BridgeStore, monkeypatch) -> None:
    from hal0.config import loader

    monkeypatch.setattr(
        loader,
        "load_hal0_config",
        lambda *a, **k: SimpleNamespace(bridge=SimpleNamespace(enabled=False)),
    )
    answer = managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)
    assert answer["status"] == "disabled"
    assert store.get_client("pi-coder") is None


def test_main_prints_one_json_line(store: BridgeStore, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        managed_mint,
        "mint",
        lambda name, by, *, rotate: {
            "status": "minted",
            "key": "k",
            "detail": f"{name} {by} {rotate}",
        },
    )
    assert managed_mint.main(["pi-coder", "system:pi-coder", "--rotate"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out)["detail"] == "pi-coder system:pi-coder True"
    assert managed_mint.main(["only-one"]) == 2
