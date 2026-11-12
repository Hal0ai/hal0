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
    answer = managed_mint.mint("pi-coder", "system:pi-coder", rotate=False, store=store)
    assert answer["status"] == "existing" and answer["key"] is None


def test_a_held_key_the_store_verifies_is_valid_and_not_rotated(store: BridgeStore) -> None:
    held = managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)["key"]
    answer = managed_mint.mint(
        "pi-coder",
        "system:pi-coder",
        rotate=True,
        candidates=["hal0bk_pi-coder.stale", "not-a-key", held],
        store=store,
    )
    assert answer == {"status": "valid", "index": 2, "key": None, "detail": ""}
    assert store.verify_key(held) is not None  # still the live key


def test_only_keys_the_store_does_not_know_lead_to_a_rotate(store: BridgeStore) -> None:
    held = managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)["key"]
    answer = managed_mint.mint(
        "pi-coder", "system:pi-coder", rotate=True, candidates=["hal0bk_pi-coder.x"], store=store
    )
    assert answer["status"] == "rotated" and store.verify_key(held) is None


def test_a_key_of_another_client_is_not_accepted(store: BridgeStore) -> None:
    managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)
    _row, theirs = store.add_client("someone", "api:admin")
    answer = managed_mint.mint(
        "pi-coder", "system:pi-coder", rotate=False, candidates=[theirs], store=store
    )
    assert answer["status"] == "existing"


def test_a_store_that_cannot_be_read_never_rotates(store: BridgeStore, monkeypatch) -> None:
    held = managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)["key"]

    def broken(*a, **k):
        raise OSError("unable to open database file")

    monkeypatch.setattr(store, "get_client", broken)
    answer = managed_mint.mint(
        "pi-coder", "system:pi-coder", rotate=True, candidates=[held], store=store
    )
    assert answer["status"] == "error" and answer["key"] is None


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
    import io

    seen: list[list[str]] = []

    def fake(name, by, *, rotate, candidates):
        seen.append(list(candidates))
        return {"status": "minted", "key": "k", "detail": f"{name} {by} {rotate}"}

    monkeypatch.setattr(managed_mint, "mint", fake)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"keys": ["hal0bk_pi-coder.a"]}'))
    assert managed_mint.main(["pi-coder", "system:pi-coder", "--rotate", "--keys-on-stdin"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out)["detail"] == "pi-coder system:pi-coder True"
    assert seen == [["hal0bk_pi-coder.a"]]
    assert managed_mint.main(["only-one"]) == 2


def test_main_refuses_a_malformed_stdin(monkeypatch, capsys) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO('{"keys": "nope"}'))
    assert managed_mint.main(["pi-coder", "system:pi-coder", "--keys-on-stdin"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_as_hal0_argv_drops_hermes_home_before_setting_home(monkeypatch) -> None:
    from hal0.system import seam

    monkeypatch.setattr(seam.shutil, "which", lambda tool: "/usr/sbin/runuser")
    monkeypatch.setattr(seam, "hal0_service_home", lambda: "/srv/hal0")
    argv = seam.as_hal0_argv(["python", "-m", "x"])
    assert argv[:4] == ["runuser", "-u", "hal0", "--"]
    assert argv[4:8] == ["env", "-u", "HERMES_HOME", "HOME=/srv/hal0"]
