"""Migration 015: drop the retired ADR-0029 model tuning.

012 copied the tuning columns to a holding table and kept the columns; the
upgrade floor is past that now, so 015 drops the columns, the holding table
and the two tuning keys of ``extra``. File facts (``chat_template``, ``mtp``,
``jinja``) and every other ``extra`` key must survive.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import hal0.db.migrations as packaged
from hal0.db.connection import connect
from hal0.db.migrate import migrate

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
