"""``hal0 doctor all`` ``bridge-store`` row (ADR-0043).

The row audits ``bridge.db`` read-only: it never creates, migrates or
re-permissions the file, so every case here builds the store with
:class:`~hal0.bridge.store.BridgeStore` first and then checks what the row
says about it.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from hal0.bridge.store import BridgeStore
from hal0.cli import doctor_all as da


def _store(tmp_path: Path, *names: str) -> Path:
    """A real ``bridge.db`` (0600, migrated) holding one live client per name."""
    db = tmp_path / "bridge.db"
    store = BridgeStore(db)
    store.open()
    for name in names:
        store.add_client(name, created_by="test")
    return db


def test_disabled_passes_without_touching_the_file(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    c = da.check_bridge_store(path=db, enabled=False)
    assert (c.key, c.label, c.status, c.critical) == (
        "bridge-store",
        "Agent bridge store",
        "pass",
        False,
    )
    assert "disabled" in c.detail
    assert not db.exists()


def test_absent_file_passes_as_no_clients_yet(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    c = da.check_bridge_store(path=db, enabled=True)
    assert c.status == "pass"
    assert "no clients yet" in c.detail
    assert not db.exists()  # the row is read-only: it must not create the store


def test_store_with_two_clients_passes_with_schema_version(tmp_path: Path) -> None:
    db = _store(tmp_path, "alpha", "beta")
    c = da.check_bridge_store(path=db, enabled=True)
    assert c.status == "pass"
    assert "2 clients" in c.detail
    assert "schema v1" in c.detail


def test_one_client_is_singular(tmp_path: Path) -> None:
    db = _store(tmp_path, "alpha")
    c = da.check_bridge_store(path=db, enabled=True)
    assert c.status == "pass"
    assert "1 client," in c.detail


def test_revoked_clients_are_counted_apart(tmp_path: Path) -> None:
    db = _store(tmp_path, "alpha", "beta")
    BridgeStore(db).revoke_client("beta")
    c = da.check_bridge_store(path=db, enabled=True)
    assert c.status == "pass"
    assert "1 client," in c.detail
    assert "1 revoked" in c.detail


def test_empty_store_passes_with_zero_clients(tmp_path: Path) -> None:
    db = _store(tmp_path)
    c = da.check_bridge_store(path=db, enabled=True)
    assert c.status == "pass"
    assert "0 clients" in c.detail
    assert "schema v1" in c.detail


def test_mode_wider_than_0600_warns(tmp_path: Path) -> None:
    db = _store(tmp_path, "alpha")
    os.chmod(db, 0o644)
    c = da.check_bridge_store(path=db, enabled=True)
    assert c.status == "warn"
    assert not c.critical
    assert "644" in c.detail
    assert "600" in c.detail


def test_foreign_owner_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _store(tmp_path, "alpha")
    # The service user resolves to a uid that is not the file's owner.
    monkeypatch.setattr(da, "_resolve_uid", lambda _user: os.getuid() + 1)
    c = da.check_bridge_store(path=db, enabled=True)
    assert c.status == "warn"
    assert "owned by uid" in c.detail


def test_owner_is_not_checked_when_the_service_user_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dev box has no ``hal0`` account: nothing to compare the owner with."""
    db = _store(tmp_path, "alpha")
    monkeypatch.setattr(da, "_resolve_uid", lambda _user: None)
    assert da.check_bridge_store(path=db, enabled=True).status == "pass"


def test_permission_error_on_stat_warns_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _store(tmp_path, "alpha")
    real_stat = os.stat

    def _deny(p: object, *a: object, **kw: object) -> os.stat_result:
        if str(p) == str(db):
            raise PermissionError(13, "Permission denied", str(db))
        return real_stat(p, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "stat", _deny)
    c = da.check_bridge_store(path=db, enabled=True)
    assert c.status == "warn"
    assert "cannot read" in c.detail
    assert str(db) in c.detail


def test_unreadable_file_warns_cannot_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The doctor run as an operator: the stat works, the open does not."""
    db = _store(tmp_path, "alpha")
    real_access = os.access
    monkeypatch.setattr(
        os,
        "access",
        lambda p, mode, **kw: False if str(p) == str(db) else real_access(p, mode, **kw),
    )
    c = da.check_bridge_store(path=db, enabled=True)
    assert c.status == "warn"
    assert "cannot read" in c.detail
    assert "hal0 doctor as hal0 or root" in c.detail


def test_garbage_file_warns_instead_of_raising(tmp_path: Path) -> None:
    db = tmp_path / "bridge.db"
    db.write_bytes(b"this is not a sqlite database" * 20)
    os.chmod(db, 0o600)
    c = da.check_bridge_store(path=db, enabled=True)
    assert c.status == "warn"
    assert "cannot read" in c.detail


def test_empty_file_without_schema_warns(tmp_path: Path) -> None:
    """A 0-byte file (a start that died before migrating) is not a healthy store."""
    db = tmp_path / "bridge.db"
    db.touch(mode=0o600)
    c = da.check_bridge_store(path=db, enabled=True)
    assert c.status == "warn"
    assert "schema" in c.detail
    # Read-only: the row did not apply the migration it is complaining about.
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_stale_schema_warns(tmp_path: Path) -> None:
    db = _store(tmp_path, "alpha")
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM schema_migrations")
        conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (0, 'x')")
    c = da.check_bridge_store(path=db, enabled=True)
    assert c.status == "warn"
    assert "schema v0" in c.detail
    assert "v1" in c.detail


def test_enabled_defaults_to_the_bridge_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no ``enabled`` argument the row reads ``[bridge].enabled`` from hal0.toml."""
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    toml = tmp_path / "hal0.toml"
    toml.write_text("[bridge]\nenabled = false\n")
    monkeypatch.setattr("hal0.config.paths.hal0_toml", lambda: toml)
    c = da.check_bridge_store(path=tmp_path / "bridge.db")
    assert c.status == "pass"
    assert "disabled" in c.detail


def test_unparsable_config_does_not_hide_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken hal0.toml is another row's finding; this row falls back to the default (on)."""
    toml = tmp_path / "hal0.toml"
    toml.write_text("[bridge\n")
    monkeypatch.setattr("hal0.config.paths.hal0_toml", lambda: toml)
    db = _store(tmp_path, "alpha")
    c = da.check_bridge_store(path=db)
    assert c.status == "pass"
    assert "1 client," in c.detail
