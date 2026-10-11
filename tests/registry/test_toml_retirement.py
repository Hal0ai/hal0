"""Retiring a TOML-era ``registry.toml``, and the resurrection it fixes.

Before this, every ``SqliteModelRegistry`` re-imported ``registry.toml``
whenever the ``model`` table was empty, and ``hal0 registry export`` /
``hal0 model import-backup`` wrote that same path. Deleting every model
therefore brought them all back on the next registry open.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomli_w

from hal0.config import paths
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.registry.toml_retirement import RegistryConversionError, retire_registry_toml


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


# ── fail loud ────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("tmp_hal0_home")
def test_failed_backup_raises_and_leaves_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_toml("a")

    def _no_copy(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr("hal0.registry.toml_retirement.shutil.copy2", _no_copy)

    with pytest.raises(RegistryConversionError, match="back up"):
        retire_registry_toml()
    assert path.exists()
    assert _retired() == []


@pytest.mark.usefixtures("tmp_hal0_home")
def test_failed_rename_raises_and_the_retry_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_toml("a")
    real_rename = Path.rename

    def _no_rename(self: Path, target):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "rename", _no_rename)
    with pytest.raises(RegistryConversionError, match="rename"):
        retire_registry_toml()
    assert path.exists()

    monkeypatch.setattr(Path, "rename", real_rename)
    assert retire_registry_toml()["outcome"] == "retired"
    assert not path.exists()
    assert ModelRegistry().list() == []
