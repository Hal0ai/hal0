"""Applying the profile-centric conversion (ADR-0029): backup, write, verify,
roll back, rerun and restore, on an install in the old shapes."""

from __future__ import annotations

import json
import sqlite3
import stat
import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.config import paths
from hal0.config.migrations import profile_centric as pc
from hal0.config.migrations import profile_centric_apply as pca
from tests.config.profile_centric_fixtures import (
    model_columns,
    old_model,
    production_install,
    tables,
    write_db,
    write_slot,
    write_toml,
)


def _snapshot() -> dict[Path, bytes]:
    return {
        p: p.read_bytes()
        for p in paths.etc().rglob("*")
        if p.is_file() and not p.name.endswith(".lock")
    }


def _backups() -> list[Path]:
    d = paths.var_lib() / "backups"
    return sorted(d.glob("profiles-*.tar.gz")) if d.is_dir() else []


def _state() -> dict[str, Any]:
    return json.loads((paths.var_lib() / pc.STATE_FILE).read_text())


def _row(model_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(paths.db_path())
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM model WHERE id = ?", (model_id,)).fetchone())
    finally:
        conn.close()


def _migrations() -> set[int]:
    conn = sqlite3.connect(paths.db_path())
    try:
        return {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    finally:
        conn.close()


@pytest.fixture
def restorable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pca, "_api_is_active", lambda: False)
    monkeypatch.setattr(pca, "_running_version", lambda: "1.4.0-test")


def test_apply_converts_backs_up_and_verifies() -> None:
    production_install()
    plan = pc.plan_conversion()
    assert plan.changed and not plan.blocked, plan.lines()

    report = pca.migrate_profile_centric()

    (backup,) = _backups()
    assert report.backup == str(backup)
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    # Every slot file now passes the strict schema: no tuning key survives.
    from hal0.config.schema import SlotConfig, slot_tuning_keys

    for path in sorted(paths.slots_config_dir().glob("*.toml")):
        data = tomllib.loads(path.read_text())
        assert not slot_tuning_keys(data), path.name
    agent = tomllib.loads((paths.slots_config_dir() / "1.toml").read_text())
    assert agent["profile"] == "thinking-big-chat"
    SlotConfig.model_validate({**agent, "name": agent["name"]})
    profiles = tomllib.loads(paths.profiles_toml().read_text())
    assert "legacy_seeds_migrated" not in profiles
    assert profiles["profile"]["coding-custom"]["flags"]
    # The registry: SQL migration 012 ran, the holding table is gone once
    # every slot converted, and an explicit "no MTP" left the model.
    assert "profile" not in model_columns() and "extra_args" not in model_columns()
    assert pc.HOLDING_TABLE not in tables()
    # The state file is an audit record of the run, never read back.
    state_path = paths.var_lib() / pc.STATE_FILE
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o640
    assert _state() == {
        "complete": True,
        "converted": sorted(s.file for s in plan.slots if s.status == "convert"),
        "blocked": [],
        "backup": str(backup),
    }


def test_rerun_is_a_no_op() -> None:
    production_install()
    pca.migrate_profile_centric()
    before = _snapshot()
    report = pca.migrate_profile_centric()
    assert report.plan is not None and not report.plan.changed, report.lines()
    assert report.backup is None
    assert _snapshot() == before
    assert len(_backups()) == 1


def test_verify_mismatch_blocks_only_that_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    production_install()
    before = _snapshot()
    agent = paths.slots_config_dir() / "1.toml"
    monkeypatch.setattr(pca, "_verify", lambda plan: {"1.toml": "--ctx-size: plan 1, launch 2"})

    report = pca.migrate_profile_centric()

    assert report.plan is not None
    assert report.plan.blockers == ["1.toml"]
    (sp,) = report.plan.blocked
    assert "--ctx-size: plan 1, launch 2" in str(sp.error)
    assert agent.read_bytes() == before[agent]  # that slot alone is put back
    assert str(agent) not in report.written
    others = [p for p in before if p.parent == paths.slots_config_dir() and p != agent]
    assert any(p.read_bytes() != before[p] for p in others)  # the rest converted
    profiles = tomllib.loads(paths.profiles_toml().read_text())
    assert profiles.get("legacy_seeds_migrated") is True  # markers stay while blocked
    assert pc.HOLDING_TABLE in tables()
    assert _state()["complete"] is False and _state()["blocked"] == ["1.toml"]


def test_shared_file_failure_rolls_everything_back(monkeypatch: pytest.MonkeyPatch) -> None:
    production_install()
    pca._ensure_db_migrated(paths.db_path())
    before = _snapshot()
    conn = sqlite3.connect(paths.db_path())
    held_before = conn.execute(f"SELECT * FROM {pc.HOLDING_TABLE} ORDER BY id").fetchall()
    conn.close()

    def _boom(*_a: Any, **_k: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(pca, "_clear_model_tuning", _boom)
    with pytest.raises(pca.ProfileConversionError, match="rolled back"):
        pca.migrate_profile_centric()

    assert _snapshot() == before
    conn = sqlite3.connect(paths.db_path())
    held_after = conn.execute(f"SELECT * FROM {pc.HOLDING_TABLE} ORDER BY id").fetchall()
    conn.close()
    assert sorted(held_after) == sorted(held_before)
    assert not (paths.var_lib() / pc.STATE_FILE).exists()
    assert len(_backups()) == 1  # the restore point stays


def test_no_backup_no_write(monkeypatch: pytest.MonkeyPatch) -> None:
    production_install()
    before = _snapshot()

    def _boom() -> Path:
        raise OSError("read-only file system")

    monkeypatch.setattr(pca, "_backups_dir", _boom)
    with pytest.raises(pca.ProfileConversionError, match="nothing was changed"):
        pca.migrate_profile_centric()
    assert _snapshot() == before


def test_dry_run_writes_nothing() -> None:
    production_install()
    before = _snapshot()
    columns = model_columns()
    report = pca.migrate_profile_centric(dry_run=True)
    assert report.plan is not None and report.plan.changed
    assert _snapshot() == before
    assert model_columns() == columns  # SQL migration 012 not applied either
    assert not _backups()


def test_blocked_slot_keeps_its_file_and_the_holding_table() -> None:
    write_toml(paths.profiles_toml(), {"profile": {}})
    write_slot(
        "1",
        {
            "id": 1,
            "name": "a",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "chat",
            "port": 8081,
            "model": {"default": "m"},
        },
    )
    write_slot(
        "2",
        {
            "id": 2,
            "name": "b",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "chat",
            "port": 8082,
            "model": {"default": "n", "context_size": 50000},
        },
    )
    write_db(
        [
            old_model("m", defaults={"extra_args": "--mmproj /x"}),
            old_model("n", metadata={"context_length": 131072}),
        ]
    )
    blocked_before = (paths.slots_config_dir() / "1.toml").read_bytes()

    pca.migrate_profile_centric()

    assert (paths.slots_config_dir() / "1.toml").read_bytes() == blocked_before
    converted = tomllib.loads((paths.slots_config_dir() / "2.toml").read_text())
    assert "context_size" not in converted["model"]
    assert pc.HOLDING_TABLE in tables()
    # The blocked slot's model keeps the tuning it still launches from.
    assert pc.read_model_rows(paths.db_path())["m"]["extra_args"] == "--mmproj /x"
    assert _state()["complete"] is False
    assert _state()["converted"] == ["2.toml"] and _state()["blocked"] == ["1.toml"]
    # A rerun still reports the blocked slot and touches nothing else.
    plan = pc.plan_conversion()
    assert [s.file for s in plan.blocked] == ["1.toml"]


def test_restore_puts_files_and_model_columns_back(restorable: None) -> None:
    production_install()
    before = _snapshot()
    small_before = _row("small-chat")
    report = pca.migrate_profile_centric()
    assert "profile" not in model_columns()

    pca.restore_backup(report.backup or "")

    after = _snapshot()
    for path, data in before.items():
        assert after.get(path) == data, path
    row = _row("small-chat")
    assert row["profile"] == small_before["profile"] == "coding"
    assert row["extra_args"] == small_before["extra_args"]
    assert json.loads(_row("npu-chat")["extra"])["_enable_thinking"] is True
    assert 12 not in _migrations()
    assert pc.HOLDING_TABLE not in tables()
    assert not (paths.var_lib() / pc.STATE_FILE).exists()
    marker = pca.restore_marker().read_text()
    assert "version=1.4.0-test" in marker

    # Boot-time callers stand down under the marker; --apply converts again.
    skipped = pca.migrate_profile_centric(honour_restore_marker=True)
    assert skipped.skipped_for_restore
    assert _snapshot() == after
    again = pca.migrate_profile_centric()
    assert again.plan is not None and again.plan.changed
    assert not pca.restore_marker().exists()
    assert "profile" not in model_columns()


def test_restore_refuses_while_the_api_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    production_install()
    report = pca.migrate_profile_centric()
    monkeypatch.setattr(pca, "_api_is_active", lambda: True)
    with pytest.raises(pca.ProfileConversionError, match="hal0-api is running"):
        pca.restore_backup(report.backup or "")


def test_fresh_install_has_nothing_to_convert() -> None:
    write_db([])
    report = pca.migrate_profile_centric()
    assert report.plan is not None and not report.plan.changed
    assert not _backups()


def test_lost_state_file_after_conversion_plans_nothing() -> None:
    production_install()
    pca.migrate_profile_centric()
    (paths.var_lib() / pc.STATE_FILE).unlink()
    before = _snapshot()
    report = pca.migrate_profile_centric()
    assert report.plan is not None and not report.plan.changed, report.lines()
    assert _snapshot() == before


def test_old_shape_slot_added_after_conversion_is_converted() -> None:
    production_install()
    pca.migrate_profile_centric()
    write_slot(
        "30",
        {
            "id": 30,
            "name": "late",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "chat",
            "port": 8099,
            "workers": 1,
            "model": {"default": "untuned", "context_size": 16000},
        },
    )
    report = pca.migrate_profile_centric()
    assert report.plan is not None and report.plan.changed
    late = tomllib.loads((paths.slots_config_dir() / "30.toml").read_text())
    assert "workers" not in late and "context_size" not in late["model"]
    assert late["profile"] != "chat"  # a generated profile keeps its 16000 window


def test_mixed_profile_blocks_and_is_kept_verbatim() -> None:
    production_install()
    data = tomllib.loads(paths.profiles_toml().read_text())
    data["profile"]["half"] = {"flags": "-fa auto", "quant": "Q4", "ctx_size": 8192}
    write_toml(paths.profiles_toml(), data)
    report = pca.migrate_profile_centric()
    assert report.plan is not None and "profile:half" in report.plan.blockers
    after = tomllib.loads(paths.profiles_toml().read_text())
    assert after["profile"]["half"] == {"flags": "-fa auto", "quant": "Q4", "ctx_size": 8192}


def test_stack_embedded_profiles_are_updated_in_place() -> None:
    production_install()
    write_toml(
        paths.stacks_toml(),
        {
            "stack": {
                "st": {
                    "slots": [],
                    "profiles": {
                        "old": {"flags": "--jinja -fa auto -b 2", "mtp": False},
                        "new": {"flags": "-b 4", "ctx_size": 8192},
                    },
                }
            }
        },
    )
    pca.migrate_profile_centric()
    embedded = tomllib.loads(paths.stacks_toml().read_text())["stack"]["st"]["profiles"]
    assert embedded["new"] == {"flags": "-b 4", "ctx_size": 8192}
    assert embedded["old"] == {"flags": "-fa auto -b 2"}
    assert not pca.migrate_profile_centric().plan.changed  # type: ignore[union-attr]


def test_slot_on_an_unregistered_model_converts_and_verifies() -> None:
    """Stale slots (model deleted or never pulled) are common on real boxes."""
    production_install()
    write_slot(
        "31",
        {
            "id": 31,
            "name": "stale",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "chat",
            "port": 8098,
            "workers": 1,
            "model": {"default": "deleted-model"},
        },
    )
    report = pca.migrate_profile_centric()
    assert report.plan is not None and not report.plan.blockers, report.lines()
    stale = tomllib.loads((paths.slots_config_dir() / "31.toml").read_text())
    assert "workers" not in stale
