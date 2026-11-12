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
    monkeypatch.setattr("hal0.system.seam.is_hal0_service_user", lambda: True)
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


def test_rotate_is_refused_unless_this_is_the_hal0_user(store: BridgeStore, monkeypatch) -> None:
    held = managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)["key"]
    monkeypatch.setattr("hal0.system.seam.is_hal0_service_user", lambda: False)
    answer = managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)
    assert answer["status"] == "error" and "hal0 service user" in answer["detail"]
    assert store.verify_key(held) is not None  # not rotated


# ── --harness: the helper owns the hal0 account's copy of the section ──────

_URL = "http://127.0.0.1:8080/api/memory/compat"


def _hal0_file(tmp_path: Path, monkeypatch, data: dict | None) -> Path:
    from hal0.memory import compat_client

    home = tmp_path / "hal0-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(compat_client, "_is_root", lambda: False)
    cfg = home / ".hindsight" / "coding-agent.json"
    if data is not None:
        cfg.parent.mkdir()
        cfg.write_text(json.dumps(data))
    return cfg


def _share(store: BridgeStore, candidates: list[str], *, rotate: bool = True) -> dict:
    return managed_mint.mint_and_share(
        "pi-coder",
        "system:pi-coder",
        rotate=rotate,
        candidates=candidates,
        harness="pi",
        api_url=_URL,
        store=store,
    )


def test_a_rotate_is_written_into_hal0s_section_before_the_answer(
    store: BridgeStore, tmp_path: Path, monkeypatch
) -> None:
    managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)
    cfg = _hal0_file(
        tmp_path, monkeypatch, {"flat": 1, "harnesses": {"pi": {"apiToken": "hal0bk_pi-coder.x"}}}
    )

    answer = _share(store, ["hal0bk_pi-coder.dead"])

    assert answer["status"] == "rotated" and answer["shared"] == "written"
    data = json.loads(cfg.read_text())
    assert data["flat"] == 1
    assert data["harnesses"]["pi"] == {
        "apiToken": answer["key"],
        "serverMode": "self-hosted",
        "apiUrl": _URL,
    }
    # The durable record: a re-run that holds only a dead key reuses this one.
    again = _share(store, ["hal0bk_pi-coder.dead"])
    assert again["status"] == "valid" and again["key"] == answer["key"]
    assert "index" not in again and again["shared"] == "unchanged"


def test_a_callers_key_that_verifies_is_written_into_hal0s_section(
    store: BridgeStore, tmp_path: Path, monkeypatch
) -> None:
    held = managed_mint.mint("pi-coder", "system:pi-coder", rotate=True, store=store)["key"]
    cfg = _hal0_file(tmp_path, monkeypatch, {"harnesses": {"pi": {}}})

    answer = _share(store, [held])

    assert answer["status"] == "valid" and answer["index"] == 0 and answer["key"] is None
    assert json.loads(cfg.read_text())["harnesses"]["pi"]["apiToken"] == held


def test_an_operator_section_in_hal0s_file_is_left_alone(
    store: BridgeStore, tmp_path: Path, monkeypatch
) -> None:
    cfg = _hal0_file(tmp_path, monkeypatch, {"harnesses": {"pi": {"apiToken": "operator"}}})
    text = cfg.read_text()

    answer = _share(store, [])

    assert answer["status"] == "minted" and answer["shared"] == "absent"
    assert cfg.read_text() == text


def test_no_hal0_file_is_not_created(store: BridgeStore, tmp_path: Path, monkeypatch) -> None:
    cfg = _hal0_file(tmp_path, monkeypatch, None)
    assert _share(store, [])["shared"] == "absent" and not cfg.exists()


def test_main_with_harness_shares(store: BridgeStore, monkeypatch, capsys) -> None:
    seen: list[tuple] = []

    def fake(name, by, *, rotate, candidates, harness, api_url):
        seen.append((name, by, rotate, list(candidates), harness, api_url))
        return {"status": "minted", "key": "k", "shared": "written"}

    monkeypatch.setattr(managed_mint, "mint_and_share", fake)
    argv = ["pi-coder", "system:pi-coder", "--rotate", "--harness", "pi", "--api-url", _URL]
    assert managed_mint.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["shared"] == "written"
    assert seen == [("pi-coder", "system:pi-coder", True, [], "pi", _URL)]
    assert managed_mint.main(["pi-coder", "system:pi-coder", "--harness", "pi"]) == 2
    assert managed_mint.main(["pi-coder", "system:pi-coder", "--api-url"]) == 2
