"""Retiring a TOML-era ``registry.toml``, and the resurrection it fixes.

Before this, every ``SqliteModelRegistry`` re-imported ``registry.toml``
whenever the ``model`` table was empty, and ``hal0 registry export`` /
``hal0 model import-backup`` wrote that same path. Deleting every model
therefore brought them all back on the next registry open.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import tomli_w

from hal0.config import paths
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.registry.toml_retirement import retire_registry_toml, retirement_blocker


def _toml_path() -> Path:
    return paths.registry_dir() / "registry.toml"


def _lock_path() -> Path:
    return paths.registry_dir() / "registry.toml.lock"


def _write_toml(*ids: str) -> Path:
    path = _toml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump({"models": {i: {"name": i, "path": f"/models/{i}.gguf"} for i in ids}}, f)
    return path


def _retired() -> list[Path]:
    return sorted(paths.registry_dir().glob("registry.toml.retired-*"))


def _seed_and_delete_all() -> None:
    reg = ModelRegistry()
    reg.add(Model(id="a", path="/models/a.gguf"))
    reg.add(Model(id="b", path="/models/b.gguf"))
    assert reg.remove("a") and reg.remove("b")
    assert reg.list() == []


# ── the bug: a TOML beside the DB never resurrects anything ─────────────────


@pytest.mark.usefixtures("tmp_hal0_home")
def test_toml_beside_db_never_resurrects_before_retirement() -> None:
    """Regression: delete every model, re-open the registry, still empty.

    The ``registry.toml`` holding the deleted models sits where the old
    first-boot import read it, and nothing has retired it yet.
    """
    _write_toml("a", "b")
    _seed_and_delete_all()

    # A fresh instance is what every request, CLI call and restart builds.
    assert ModelRegistry().list() == []
    # The old trigger: an isolated registry dir whose DB is brand new and
    # whose registry.toml sits right next to it.
    assert ModelRegistry(registry_dir=paths.registry_dir()).list() == []


@pytest.mark.usefixtures("tmp_hal0_home")
def test_toml_beside_db_never_resurrects_after_retirement_and_restart() -> None:
    _write_toml("a", "b")
    _seed_and_delete_all()

    assert retire_registry_toml()["outcome"] == "retired"
    # Restart: the boot safety net runs again, then the registry re-opens.
    assert retire_registry_toml() == {"outcome": "absent"}
    assert ModelRegistry().list() == []


@pytest.mark.usefixtures("tmp_hal0_home")
def test_retirement_never_imports_into_an_empty_registry() -> None:
    _write_toml("a", "b")
    retire_registry_toml()
    assert ModelRegistry().list() == []


# ── retirement behaviour ────────────────────────────────────────────────────


@pytest.mark.usefixtures("tmp_hal0_home")
def test_retire_backs_up_renames_and_drops_the_lock() -> None:
    ModelRegistry().add(Model(id="live", path="/models/live.gguf"))
    src = _write_toml("live", "stale")
    original = src.read_bytes()
    _lock_path().write_text("")

    result = retire_registry_toml(job_id="j1")

    assert result["outcome"] == "retired"
    assert not src.exists()
    assert not _lock_path().exists()
    [archived] = _retired()
    assert archived.read_bytes() == original
    assert result["archived_to"] == str(archived)
    backup = Path(result["backup"])
    assert backup.parent == paths.var_lib() / "backups"
    assert (backup / "registry.toml").read_bytes() == original
    # The database is untouched.
    assert [m.id for m in ModelRegistry().list()] == ["live"]


@pytest.mark.usefixtures("tmp_hal0_home")
def test_lock_alone_is_deleted() -> None:
    _lock_path().parent.mkdir(parents=True, exist_ok=True)
    _lock_path().write_text("")
    assert retire_registry_toml() == {"outcome": "absent"}
    assert not _lock_path().exists()


@pytest.mark.usefixtures("tmp_hal0_home")
def test_nothing_to_do_is_a_no_op() -> None:
    assert retire_registry_toml() == {"outcome": "absent"}
    assert not (paths.var_lib() / "backups").exists()


@pytest.mark.usefixtures("tmp_hal0_home")
def test_a_toml_reappearing_later_is_retired_again() -> None:
    _write_toml("a")
    retire_registry_toml()
    _write_toml("b")
    retire_registry_toml()
    assert len(_retired()) == 2
    assert ModelRegistry().list() == []


@pytest.mark.usefixtures("tmp_hal0_home")
def test_malformed_toml_is_retired_without_being_parsed() -> None:
    path = _toml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("models = = broken\n")
    assert retire_registry_toml()["outcome"] == "retired"
    assert not path.exists()


# ── never fatal: the file is inert ──────────────────────────────────────────


@pytest.mark.usefixtures("tmp_hal0_home")
@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0000 file")
def test_unreadable_root_owned_style_file_warns_and_stays(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 0600 file owned by someone else (a `sudo hal0 registry export`) is
    unreadable to the hal0 daemon: warn, leave it, never raise."""
    path = _write_toml("a")
    path.chmod(0)
    try:
        result = retire_registry_toml()

        assert result["outcome"] == "failed"
        assert "cannot back it up" in result["reason"]
        assert path.exists()
        assert _retired() == []
        assert not any((paths.var_lib() / "backups").glob("registry-toml-*"))
        assert "registry.toml_retire_failed" in caplog.text
        assert "not readable" in (retirement_blocker() or "")
        assert ModelRegistry().list() == []
    finally:
        path.chmod(0o600)


@pytest.mark.usefixtures("tmp_hal0_home")
def test_failed_backup_warns_and_leaves_the_file(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write_toml("a")

    def _no_copy(*_a, **_kw):
        raise PermissionError("[Errno 13] Permission denied")

    monkeypatch.setattr("hal0.registry.toml_retirement.shutil.copy2", _no_copy)

    result = retire_registry_toml()

    assert result["outcome"] == "failed"
    assert "Permission denied" in result["reason"]
    assert path.exists()
    assert _retired() == []
    assert "registry.toml_retire_failed" in caplog.text


@pytest.mark.usefixtures("tmp_hal0_home")
def test_failed_rename_warns_and_the_next_run_retires(monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_toml("a")
    real_rename = Path.rename

    def _no_rename(self: Path, target):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "rename", _no_rename)
    result = retire_registry_toml()
    assert result["outcome"] == "failed"
    assert "cannot rename" in result["reason"]
    assert path.exists()

    monkeypatch.setattr(Path, "rename", real_rename)
    assert retire_registry_toml()["outcome"] == "retired"
    assert not path.exists()
    assert ModelRegistry().list() == []


@pytest.mark.usefixtures("tmp_hal0_home")
def test_blocker_is_none_without_a_file_and_explains_a_pending_one() -> None:
    assert retirement_blocker() is None
    _write_toml("a")
    assert "not retired yet" in (retirement_blocker() or "")


@pytest.mark.usefixtures("tmp_hal0_home")
@pytest.mark.parametrize("with_toml", [False, True])
def test_undeletable_lock_only_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, with_toml: bool
) -> None:
    """The stale lock is clutter, not data: failing to delete it must never
    fail the post-activation migrations."""
    if with_toml:
        _write_toml("a")
    _lock_path().parent.mkdir(parents=True, exist_ok=True)
    _lock_path().write_text("")
    real_unlink = Path.unlink

    def _unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name == "registry.toml.lock":
            raise PermissionError("read-only")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _unlink)

    result = retire_registry_toml()

    assert result["outcome"] == ("retired" if with_toml else "absent")
    assert not _toml_path().exists()
    assert _lock_path().exists()
    assert "registry.toml_lock_unlink_failed" in caplog.text


@pytest.mark.usefixtures("tmp_hal0_home")
def test_repeated_rename_failures_keep_one_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rename that fails on every boot must not pile up identical copies."""
    path = _write_toml("a")

    def _no_rename(self: Path, target):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "rename", _no_rename)
    outcomes = {retire_registry_toml()["outcome"] for _ in range(3)}

    assert outcomes == {"failed"}
    backups = sorted((paths.var_lib() / "backups").glob("registry-toml-*"))
    assert len(backups) == 1
    assert (backups[0] / "registry.toml").read_bytes() == path.read_bytes()


@pytest.mark.usefixtures("tmp_hal0_home")
def test_changed_content_gets_a_fresh_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_toml("a")

    def _no_rename(self: Path, target):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "rename", _no_rename)
    retire_registry_toml()
    _write_toml("a", "b")
    retire_registry_toml()

    assert len(list((paths.var_lib() / "backups").glob("registry-toml-*"))) == 2
