"""Migration 015: drop the retired ADR-0029 model tuning.

012 copied the tuning columns to a holding table and kept the columns; the
upgrade floor is past that now, so 015 drops the columns, the holding table
and the two tuning keys of ``extra``. File facts (``chat_template``, ``mtp``,
``jinja``) and every other ``extra`` key must survive.
"""

from __future__ import annotations

import importlib
import json
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest

import hal0.db.migrations as packaged
from hal0.config import paths
from hal0.db.connection import connect
from hal0.db.migrate import MigrationBackupError, applied_versions, migrate

_TUNING_COLUMNS = (
    "profile",
    "extra_args",
    "n_gpu_layers",
    "context_size",
    "rope_freq_base",
    "preferred_runner",
)


def _pre_015(tmp_path: Path) -> Path:
    pre = tmp_path / "pre015"
    pre.mkdir()
    for sql in Path(packaged.__file__).parent.glob("*.sql"):
        if int(sql.name[:3]) < 15:
            shutil.copy(sql, pre)
    return pre


def _columns(conn) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(model)")}


def _tables(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_015_drops_the_tuning_columns_and_the_holding_table(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        migrate(conn, _pre_015(tmp_path))
        assert set(_TUNING_COLUMNS) <= _columns(conn)
        assert "legacy_model_tuning" in _tables(conn)

        assert migrate(conn) == [15]

        assert not set(_TUNING_COLUMNS) & _columns(conn)
        assert {"path", "chat_template", "mtp", "jinja", "mmproj", "extra"} <= _columns(conn)
        assert "legacy_model_tuning" not in _tables(conn)
        assert migrate(conn) == []


def test_015_keeps_rows_file_facts_and_other_extra_keys(tmp_path: Path) -> None:
    extra = {"_enable_thinking": True, "_vision": False, "keep": 1}
    with connect(tmp_path / "t.db") as conn:
        migrate(conn, _pre_015(tmp_path))
        conn.execute(
            "INSERT INTO model (id, path, chat_template, mtp, jinja, profile, extra_args,"
            " n_gpu_layers, context_size, rope_freq_base, preferred_runner, extra)"
            " VALUES ('tuned', '/m/t.gguf', 'chatml', 1, 0, 'agent', '-ctk q8_0', 99, 65536,"
            " 1e6, 'standard', ?)",
            (json.dumps(extra),),
        )
        conn.execute("INSERT INTO model (id, path, extra) VALUES ('plain', '/m/p.gguf', NULL)")
        conn.execute("INSERT INTO model (id, path, extra) VALUES ('odd', '/m/o.gguf', 'not json')")

        assert migrate(conn) == [15]

        tuned = conn.execute("SELECT * FROM model WHERE id='tuned'").fetchone()
        assert (tuned["path"], tuned["chat_template"], tuned["mtp"], tuned["jinja"]) == (
            "/m/t.gguf",
            "chatml",
            1,
            0,
        )
        assert json.loads(tuned["extra"]) == {"keep": 1}
        assert conn.execute("SELECT extra FROM model WHERE id='plain'").fetchone()[0] is None
        assert conn.execute("SELECT extra FROM model WHERE id='odd'").fetchone()[0] == "not json"


def test_registry_works_on_the_reduced_schema(tmp_path: Path) -> None:
    from hal0.registry.model import Model
    from hal0.registry.sqlite_store import SqliteModelRegistry

    reg = SqliteModelRegistry(db_path=str(tmp_path / "reg.db"))
    reg.add(Model(id="m1", path="/models/m1.gguf", chat_template="chatml", mtp=True, jinja=False))
    got = reg.get("m1")
    assert (got.chat_template, got.mtp, got.jinja) == ("chatml", True, False)
    reg.add(Model(id="m2", path="/models/m2.gguf"))
    assert {m.id for m in reg.list()} == {"m1", "m2"}
    reg.remove("m2")
    assert {m.id for m in reg.list()} == {"m1"}


# ── the backup guard in migrate() ─────────────────────────────────────────────


def _backups() -> list[Path]:
    return sorted((paths.var_lib() / "backups").glob("hal0-pre-015-*.db"))


def _tuned_pre_015(conn, tmp_path: Path, *, drop_holding: bool = False) -> None:
    """A 1.7.x database: 012 copied this model's tune into the holding table."""
    migrate(conn, _pre_015(tmp_path))
    conn.execute("INSERT INTO model (id, path, context_size) VALUES ('tuned', '/m/t.gguf', 65536)")
    conn.execute("INSERT INTO legacy_model_tuning (id, context_size) VALUES ('tuned', 65536)")
    if drop_holding:
        conn.execute("DROP TABLE legacy_model_tuning")


def test_a_blocked_box_is_backed_up_before_the_tune_is_dropped(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        _tuned_pre_015(conn, tmp_path)

        assert migrate(conn) == [15]

        assert "context_size" not in _columns(conn)
    backups = _backups()
    assert len(backups) == 1
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    snapshot = sqlite3.connect(backups[0])
    try:
        assert snapshot.execute("SELECT context_size FROM model").fetchall() == [(65536,)]
        assert snapshot.execute("SELECT context_size FROM legacy_model_tuning").fetchall() == [
            (65536,)
        ]
        versions = snapshot.execute("SELECT version FROM schema_migrations").fetchall()
        assert (15,) not in versions
    finally:
        snapshot.close()


@pytest.mark.parametrize(
    "seed",
    [
        "INSERT INTO model (id, path, preferred_runner) VALUES ('m', '/m', 'standard')",
        "INSERT INTO model (id, path, extra) VALUES ('m', '/m', '{\"_vision\": true}')",
    ],
    ids=["model column only", "extra key only"],
)
def test_a_value_left_on_the_model_row_alone_is_backed_up(tmp_path: Path, seed: str) -> None:
    """The converter deleted holding rows but never cleared the model columns."""
    with connect(tmp_path / "t.db") as conn:
        migrate(conn, _pre_015(tmp_path))
        conn.execute("DROP TABLE legacy_model_tuning")
        conn.execute(seed)
        assert migrate(conn) == [15]
    assert len(_backups()) == 1


@pytest.mark.parametrize("failure", ["directory", "vacuum"])
def test_a_failed_backup_aborts_and_leaves_the_database_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    if failure == "directory":

        def _no_dir() -> Path:
            raise PermissionError(13, "backups directory is not writable")

        monkeypatch.setattr("hal0.config.migrations.runner_system._backups_dir", _no_dir)
    else:

        def _no_room(_conn, _target) -> None:
            raise sqlite3.OperationalError("database or disk is full")

        # By module object: the package re-exports the function as hal0.db.migrate.
        monkeypatch.setattr(importlib.import_module("hal0.db.migrate"), "_vacuum_into", _no_room)

    with connect(tmp_path / "t.db") as conn:
        _tuned_pre_015(conn, tmp_path)

        with pytest.raises(MigrationBackupError) as excinfo:
            migrate(conn)

        assert "migration 015 was not applied" in str(excinfo.value)
        assert "doctor perms --fix" in str(excinfo.value)
        assert "context_size" in _columns(conn)
        assert "legacy_model_tuning" in _tables(conn)
        assert conn.execute("SELECT context_size FROM model").fetchall()[0][0] == 65536
        assert 15 not in applied_versions(conn)
    assert _backups() == []  # no empty file a later reader would take for a backup


def test_a_converted_box_needs_no_backup(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        migrate(conn, _pre_015(tmp_path))
        conn.execute("DROP TABLE legacy_model_tuning")
        conn.execute("INSERT INTO model (id, path, extra) VALUES ('m', '/m', '{\"keep\": 1}')")

        assert migrate(conn) == [15]
    assert _backups() == []


def test_a_new_database_needs_no_backup(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        assert 15 in migrate(conn)
    assert _backups() == []


def test_the_guard_keeps_to_the_tuning_migration(tmp_path: Path) -> None:
    """The bridge store runs its own version numbers through migrate()."""
    other = tmp_path / "other"
    other.mkdir()
    (other / "015_something_else.sql").write_text("CREATE TABLE model (id TEXT, profile TEXT);")
    (other / "016_rows.sql").write_text("INSERT INTO model VALUES ('m', 'p');")
    (other / "017_more.sql").write_text("CREATE TABLE later (id TEXT);")
    with connect(tmp_path / "t.db") as conn:
        assert migrate(conn, other) == [15, 16, 17]
    assert _backups() == []
