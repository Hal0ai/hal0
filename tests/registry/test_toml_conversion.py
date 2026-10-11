"""The one-shot ``registry.toml`` conversion, and the resurrection it fixes.

Before the conversion existed, every ``SqliteModelRegistry`` re-imported
``registry.toml`` whenever the ``model`` table was empty, and ``hal0 registry
export`` / ``hal0 model import-backup`` wrote that same path. Deleting every
model therefore brought them all back on the next registry open.
"""

from __future__ import annotations

import json
import shutil
from importlib import resources
from pathlib import Path

import pytest
import tomli_w

from hal0.config import paths
from hal0.db import repository
from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.registry.toml_conversion import (
    MARKER,
    RegistryConversionError,
    convert_registry_toml,
)


def _toml_path() -> Path:
    return paths.registry_dir() / "registry.toml"


def _write_toml(models: dict[str, dict]) -> Path:
    path = _toml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump({"models": models}, f)
    return path


def _entry(name: str, **kw) -> dict:
    return {"name": name, "path": f"/models/{name}.gguf", "size_bytes": 1, **kw}


def _marker() -> dict | None:
    with connect(paths.db_path()) as conn:
        migrate(conn)
        row = conn.execute(
            "SELECT detail FROM one_shot_conversion WHERE name = ?", (MARKER,)
        ).fetchone()
    return None if row is None else json.loads(row[0])


def _archived(suffix: str) -> list[Path]:
    return sorted(paths.registry_dir().glob(f"registry.toml.{suffix}-*"))


# ── the bug: deleted models must stay deleted ───────────────────────────────


@pytest.mark.usefixtures("tmp_hal0_home")
def test_deleted_models_stay_deleted_across_registry_reinit() -> None:
    """Regression: delete every model, re-open the registry, still empty.

    A ``registry.toml`` holding the deleted models sits at the path the old
    first-boot import read (and ``hal0 registry export`` wrote).
    """
    reg = ModelRegistry()
    reg.add(Model(id="a", path="/models/a.gguf"))
    reg.add(Model(id="b", path="/models/b.gguf"))
    _write_toml({"a": _entry("a"), "b": _entry("b")})

    assert reg.remove("a") and reg.remove("b")
    assert reg.list() == []

    # A fresh instance (what every request/CLI call/restart constructs).
    assert ModelRegistry().list() == []
    assert ModelRegistry(registry_dir=paths.registry_dir()).list() == []


@pytest.mark.usefixtures("tmp_hal0_home")
def test_deleted_models_stay_deleted_after_conversion_and_restart() -> None:
    """The conversion imports once; deleting afterwards is final."""
    _write_toml({"a": _entry("a"), "b": _entry("b")})
    assert convert_registry_toml()["outcome"] == "imported"
    reg = ModelRegistry()
    assert [m.id for m in reg.list()] == ["a", "b"]

    reg.remove("a")
    reg.remove("b")

    # Restart: the boot safety net runs the conversion again, then the
    # registry is re-opened.
    assert convert_registry_toml()["outcome"] == "absent"
    assert ModelRegistry().list() == []


# ── conversion behaviour ─────────────────────────────────────────────────────


@pytest.mark.usefixtures("tmp_hal0_home")
def test_fresh_database_imports_backs_up_and_renames() -> None:
    # The API opened the (empty) registry before the boot pass ran.
    assert ModelRegistry().list() == []
    src = _write_toml({"a": _entry("a"), "b": _entry("b", backends=["vulkan"])})
    original = src.read_bytes()
    (src.parent / "registry.toml.lock").write_text("")

    result = convert_registry_toml(job_id="j1")

    assert result["outcome"] == "imported"
    assert result["imported"] == 2
    assert not src.exists()
    assert not (src.parent / "registry.toml.lock").exists()
    [archived] = _archived("imported")
    assert archived.read_bytes() == original
    assert result["archived_to"] == str(archived)

    backup = Path(result["backup"])
    assert backup.parent == paths.var_lib() / "backups"
    assert (backup / "registry.toml").read_bytes() == original
    assert (backup / "hal0.db").exists()

    reg = ModelRegistry()
    assert [m.id for m in reg.list()] == ["a", "b"]
    assert reg.get("b").backends == ["vulkan"]
    assert _marker()["outcome"] == "imported"


@pytest.mark.usefixtures("tmp_hal0_home")
def test_import_never_overwrites_an_existing_row() -> None:
    ModelRegistry().add(Model(id="a", path="/models/live.gguf"))
    # Undo 011's pre-marking so this database counts as never converted.
    with connect(paths.db_path()) as conn, tx(conn):
        conn.execute("DELETE FROM one_shot_conversion")
    _write_toml({"a": _entry("a"), "c": _entry("c")})

    result = convert_registry_toml()

    assert result["imported"] == 1
    assert result["already_present"] == 1
    assert ModelRegistry().get("a").path == "/models/live.gguf"


@pytest.mark.usefixtures("tmp_hal0_home")
def test_second_toml_after_conversion_is_retired_not_imported() -> None:
    assert convert_registry_toml()["outcome"] == "absent"
    _write_toml({"zombie": _entry("zombie")})

    result = convert_registry_toml()

    assert result["outcome"] == "retired"
    assert result["imported"] == 0
    assert not _toml_path().exists()
    assert len(_archived("retired")) == 1
    assert ModelRegistry().list() == []


@pytest.mark.usefixtures("tmp_hal0_home")
def test_populated_database_is_premarked_by_migration_011() -> None:
    """An upgraded box: models in the DB before 011 means the old first-boot
    import already ran, so a leftover registry.toml is only archived."""
    pre_011 = paths.var_lib() / "pre011"
    pre_011.mkdir(parents=True)
    for entry in resources.files("hal0.db.migrations").iterdir():
        if entry.name.endswith(".sql") and not entry.name.startswith("011_"):
            shutil.copyfile(str(entry), pre_011 / entry.name)
    with connect(paths.db_path()) as conn:
        migrate(conn, pre_011)
        row = repository.model_to_row(Model(id="kept", path="/models/kept.gguf"))
        with tx(conn):
            conn.execute(
                f"INSERT INTO model ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                list(row.values()),
            )
    _write_toml({"kept": _entry("kept"), "deleted-on-1.3": _entry("deleted-on-1.3")})

    result = convert_registry_toml()

    assert result["outcome"] == "retired"
    assert [m.id for m in ModelRegistry().list()] == ["kept"]
    assert _marker()["outcome"] == "already-initialised"


@pytest.mark.usefixtures("tmp_hal0_home")
def test_no_toml_records_the_marker_once() -> None:
    assert convert_registry_toml() == {"outcome": "absent"}
    assert _marker() == {"outcome": "absent"}
    assert convert_registry_toml() == {"outcome": "absent"}


@pytest.mark.usefixtures("tmp_hal0_home")
def test_provider_is_derived_from_backends_and_explicit_one_kept() -> None:
    _write_toml(
        {
            "kokoro-tts": _entry("kokoro-tts", backends=["kokoro"]),
            "explicit": _entry("explicit", backends=["kokoro"], provider="llama-server"),
        }
    )
    convert_registry_toml()
    reg = ModelRegistry()
    assert reg.get("kokoro-tts").provider == "kokoro"
    assert reg.get("explicit").provider == "llama-server"


@pytest.mark.usefixtures("tmp_hal0_home")
def test_array_of_tables_layout_is_imported() -> None:
    path = _toml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump({"models": [{"id": "a", **_entry("a")}]}, f)
    assert convert_registry_toml()["imported"] == 1
    assert ModelRegistry().has("a")


@pytest.mark.usefixtures("tmp_hal0_home")
def test_invalid_entry_is_skipped_and_reported() -> None:
    _write_toml({"good": _entry("good"), "bad": {"name": "bad", "path": ""}})

    result = convert_registry_toml()

    assert result["imported"] == 1
    assert result["invalid"] == ["bad"]
    assert [m.id for m in ModelRegistry().list()] == ["good"]
    # The skipped entry survives in the backup.
    assert "bad" in (Path(result["backup"]) / "registry.toml").read_text()


# ── fail loud ────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("tmp_hal0_home")
def test_malformed_toml_raises_and_leaves_the_file() -> None:
    path = _toml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("models = = broken\n")

    with pytest.raises(RegistryConversionError):
        convert_registry_toml()

    assert path.exists()
    assert _marker() is None


@pytest.mark.usefixtures("tmp_hal0_home")
def test_failed_backup_raises_before_touching_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_toml({"a": _entry("a")})

    def _no_copy(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr("hal0.registry.toml_conversion.shutil.copy2", _no_copy)

    with pytest.raises(RegistryConversionError, match="back up"):
        convert_registry_toml()

    assert path.exists()
    assert ModelRegistry().list() == []
    assert _marker() is None


@pytest.mark.usefixtures("tmp_hal0_home")
def test_failed_rename_raises_then_the_retry_archives_without_reimport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_toml({"a": _entry("a")})
    real_rename = Path.rename

    def _no_rename(self: Path, target):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "rename", _no_rename)
    with pytest.raises(RegistryConversionError, match="rename"):
        convert_registry_toml()
    assert path.exists()
    assert ModelRegistry().has("a")

    # The operator deletes the model before the retry: it must stay deleted.
    ModelRegistry().remove("a")
    monkeypatch.setattr(Path, "rename", real_rename)
    result = convert_registry_toml()

    assert result["outcome"] == "retired"
    assert not path.exists()
    assert ModelRegistry().list() == []
