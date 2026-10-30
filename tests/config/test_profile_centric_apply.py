"""Applying the profile-centric conversion (ADR-0029): backup, write, verify,
roll back, rerun and restore, on an install in the old shapes."""

from __future__ import annotations

import json
import os
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
    ledger = json.loads((paths.var_lib() / pc.LEDGER_FILE).read_text())
    assert ledger["legacy_seeds_migrated"] is True  # moved, not lost
    assert profiles["profile"]["coding-custom"]["flags"]
    # The registry: SQL migration 012 ran, the holding table is gone once
    # every slot converted, and the old columns are left for a rollback.
    assert 12 in _migrations()
    assert "profile" in model_columns() and _row("small-chat")["profile"] == "coding"
    assert pc.HOLDING_TABLE not in tables()
    # The state file is an audit record of the run, never read back.
    state_path = paths.var_lib() / pc.STATE_FILE
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o640
    assert _state() == {
        "complete": True,
        "converted": sorted(s.file for s in plan.slots if s.status == "convert"),
        "blocked": [],
        "backup": str(backup),
        "backups": [str(backup)],
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
    assert "legacy_seeds_migrated" not in profiles  # strict shape even while blocked
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


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses the directory write check")
def test_unwritable_backups_dir_names_the_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    """#2657: the profile conversion shares the runner migration's backups
    directory, so a root-owned 2755 one fails here too, with the remedy."""
    production_install()
    backups = paths.var_lib() / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backups.chmod(0o2555)
    real_uid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: real_uid + 1)
    before = _snapshot()
    with pytest.raises(pca.ProfileConversionError) as info:
        pca.migrate_profile_centric()
    msg = str(info.value)
    assert "nothing was changed" in msg
    assert f"chmod 2775 {backups}" in msg
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
    assert 12 in _migrations()

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
    assert 12 in _migrations()


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


def test_retired_slot_keys_are_stripped_so_the_strict_slot_loads() -> None:
    """``SlotConfig`` forbids unknown keys (ADR-0029 §5), so every key hal0
    retired from a slot (``backend``, ``enabled``, ``lru``, ``runtime`` and a
    bare ``image`` ref) must leave with the conversion, and the converted file
    must pass the strict typed load."""
    from hal0.config.loader import load_slot_config

    production_install()
    pca.migrate_profile_centric()
    write_slot(
        "31",
        {
            "id": 31,
            "name": "retired",
            "type": "llm",
            "backend": "vulkan",
            "enabled": True,
            "lru": True,
            "runtime": "container",
            "image": "ghcr.io/foo:v1",
            "profile": "chat",
            "port": 8098,
            "model": {"default": "untuned"},
        },
    )
    with pytest.raises(Exception, match="unknown key"):
        load_slot_config("31")
    report = pca.migrate_profile_centric()
    assert report.plan is not None and report.plan.changed, report.lines()
    retired = tomllib.loads((paths.slots_config_dir() / "31.toml").read_text())
    for key in ("backend", "enabled", "lru", "runtime", "image"):
        assert key not in retired, key
    assert retired["device"] == "gpu-vulkan"  # backend became device
    assert load_slot_config("31").name == "retired"
    for stem in (p.stem for p in paths.slots_config_dir().glob("*.toml")):
        load_slot_config(stem)  # nothing the conversion wrote is rejected


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


# ── a blocker never takes the rest of the box down (review P0-1) ─────────────


def _launches(stem: str) -> list[str]:
    """What the slot launches through the strict launch-path render."""
    from hal0.config.migrations import profile_centric_frozen as frozen
    from hal0.providers.container import strict_slot_argv

    raw = tomllib.loads((paths.slots_config_dir() / f"{stem}.toml").read_text())
    return list(strict_slot_argv(frozen.hoist_slot(raw, stem)).argv)


def test_blocked_slot_leaves_the_clean_slots_launching() -> None:
    write_toml(paths.profiles_toml(), {"legacy_seeds_migrated": True, "profile": {}})
    for stem, port, model in (("1", 8081, "m"), ("2", 8082, "n")):
        write_slot(
            stem,
            {
                "id": int(stem),
                "name": f"s{stem}",
                "type": "llm",
                "device": "gpu-rocm",
                "profile": "chat",
                "port": port,
                "workers": 1,
                "model": {"default": model},
            },
        )
    write_db(
        [
            old_model("m", defaults={"extra_args": "--mmproj /x"}),
            old_model("n", metadata={"context_length": 131072}),
        ]
    )
    report = pca.migrate_profile_centric()
    assert report.plan is not None and report.plan.blockers == ["1.toml"]
    # profiles.toml is in the strict new shape and loads; the other slot launches.
    from hal0.config.loader import load_profiles_config

    assert "chat" in load_profiles_config().profile
    argv = _launches("2")
    assert argv[argv.index("--model") + 1] == "/models/n/model.gguf"


def test_registry_error_during_verify_rolls_everything_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hal0.providers.container as container

    production_install()
    pca._ensure_db_migrated(paths.db_path())
    before = _snapshot()

    def _locked(_slot: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(container, "strict_slot_argv", _locked)
    with pytest.raises(pca.ProfileConversionError, match="rolled back"):
        pca.migrate_profile_centric()
    assert _snapshot() == before  # a transient error is never a block
    assert not (paths.var_lib() / pc.STATE_FILE).exists()


def test_mixed_shape_profile_blocks_only_its_slots() -> None:
    from hal0.errors import UnprocessableEntity
    from hal0.profiles import ProfileCatalog

    production_install()
    data = tomllib.loads(paths.profiles_toml().read_text())
    data["profile"]["half"] = {"flags": "-fa auto", "quant": "Q4", "ctx_size": 8192}
    write_toml(paths.profiles_toml(), data)
    write_slot(
        "40",
        {
            "id": 40,
            "name": "halfslot",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "half",
            "port": 8140,
            "workers": 1,
            "model": {"default": "untuned"},
        },
    )
    report = pca.migrate_profile_centric()
    assert report.plan is not None
    assert set(report.plan.blockers) == {"40.toml", "profile:half"}
    # The catalog loads; the bad entry is set aside, not fatal.
    with pytest.raises(UnprocessableEntity) as exc:
        ProfileCatalog().resolve("half")
    assert exc.value.code == "profiles.invalid"
    assert "--model" in _launches("16")  # a converted slot still launches
    stored = tomllib.loads(paths.profiles_toml().read_text())["profile"]["half"]
    assert stored == {"flags": "-fa auto", "quant": "Q4", "ctx_size": 8192}


def test_verify_block_reverts_the_stack_row_that_followed_the_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_install()
    write_toml(
        paths.stacks_toml(),
        {"stack": {"st": {"slots": [{"slot": "agent", "profile": "thinking", "mtp": True}]}}},
    )
    monkeypatch.setattr(pca, "_verify", lambda plan: {"1.toml": "--ctx-size: plan 1, launch 2"})
    pca.migrate_profile_centric()
    (row,) = tomllib.loads(paths.stacks_toml().read_text())["stack"]["st"]["slots"]
    # The profile is kept; the retired tuning key is not (StackSlotEntry forbids it).
    assert row == {"slot": "agent", "profile": "thinking"}


def test_blocked_slot_stack_row_still_drops_retired_keys_and_file_validates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#2649: a v1.3.0 row (``vision = false``) for a blocked slot must not leave
    stacks.toml failing validation, and the report must not claim a reverted drop."""
    from hal0.config.loader import load_stacks_config

    production_install()
    write_toml(
        paths.stacks_toml(),
        {"stack": {"st": {"slots": [{"slot": "agent", "profile": "thinking", "vision": False}]}}},
    )
    monkeypatch.setattr(pca, "_verify", lambda plan: {"1.toml": "--ctx-size: plan 1, launch 2"})
    report = pca.migrate_profile_centric()
    (row,) = tomllib.loads(paths.stacks_toml().read_text())["stack"]["st"]["slots"]
    assert row == {"slot": "agent", "profile": "thinking"}
    assert "st" in load_stacks_config().stack  # no ConfigParseError
    assert report.plan is not None
    lines = [ln for ln in report.plan.lines() if ln.startswith("stack st row agent")]
    assert lines == ["stack st row agent: drop vision"]  # only what actually happens


def test_shipped_installer_config_converts_nothing() -> None:
    """A fresh install of this release is already in the new shape."""
    import shutil

    repo = Path(__file__).resolve().parents[2]
    shutil.copytree(repo / "installer" / "etc-hal0", paths.etc(), dirs_exist_ok=True)
    write_db([], upgraded=True)
    plan = pc.plan_conversion()
    assert not plan.changed, plan.lines()


# ── rollback to a release before ADR-0029 (review P1-1) ──────────────────────


def test_rollback_restore_undoes_every_run(restorable: None) -> None:
    production_install()
    before = _snapshot()
    small_before = _row("small-chat")
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
    late = (paths.slots_config_dir() / "30.toml").read_bytes()
    pca.migrate_profile_centric()
    assert len(pca.conversion_backups()) == 2

    restored = pca.restore_for_rollback()

    assert restored
    after = _snapshot()
    for path, data in before.items():
        assert after.get(path) == data, path
    assert (paths.slots_config_dir() / "30.toml").read_bytes() == late
    assert _row("small-chat")["profile"] == small_before["profile"]
    assert 12 not in _migrations() and pc.HOLDING_TABLE not in tables()
    assert not (paths.var_lib() / pc.STATE_FILE).exists()
    assert pca.restore_for_rollback() == []  # nothing left to undo


def test_updater_rollback_restores_only_for_an_older_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.updater import updater

    calls: list[str] = []
    monkeypatch.setattr(pca, "restore_for_rollback", lambda **_k: calls.append("r") or ["x"])
    newer = tmp_path / "hal0-1.5.0"
    (newer / updater._PROFILE_CENTRIC_MARKER).parent.mkdir(parents=True)
    (newer / updater._PROFILE_CENTRIC_MARKER).write_text("")
    assert updater._restore_profile_conversion_for(newer, job_id=None) == []
    older = tmp_path / "hal0-1.4.0"
    older.mkdir()
    assert updater._restore_profile_conversion_for(older, job_id=None) == ["x"]
    assert calls == ["r"]


def test_updater_rollback_refuses_with_manual_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.updater import updater

    def _fail(**_k: Any) -> list[str]:
        raise pca.ProfileConversionError("conversion backup(s) missing: /x")

    monkeypatch.setattr(pca, "restore_for_rollback", _fail)
    older = tmp_path / "hal0-1.4.0"
    older.mkdir()
    with pytest.raises(updater.UpdateRollbackUnavailable, match="profile migrate --restore"):
        updater._restore_profile_conversion_for(older, job_id=None)


# ── FLM windows: anchor and trio shadows (review P2) ──────────────────────────


def _flm_trio(anchor: dict[str, Any]) -> None:
    write_slot(
        "8", {"id": 8, "name": "flm", "type": "llm", "device": "npu", "port": 8088, **anchor}
    )
    for sid, name, kind, model in (
        (6, "flm-embed", "embedding", "embed-gemma:300m"),
        (7, "flm-stt", "transcription", "whisper-v3:turbo"),
    ):
        write_slot(
            str(sid),
            {
                "id": sid,
                "name": name,
                "type": kind,
                "device": "npu",
                "port": 8088,
                "served_by": "flm",
                "profile": "flm",
                "model": {"default": model},
            },
        )


def _window(stem: str) -> int | None:
    from hal0.config.loader import _flatten_slot_toml, _read_toml
    from hal0.providers.container import slot_effective_context

    raw = _read_toml(paths.slots_config_dir() / f"{stem}.toml")
    return slot_effective_context(_flatten_slot_toml(raw, slot_name=stem))[0]


def test_flm_shadows_keep_the_window_they_had() -> None:
    """The box's shape: an anchor an older hal0 wrote (no window: 8192 today)
    and two clean shadows on the flm seed. Nothing changes at restart."""
    write_toml(paths.profiles_toml(), {"profile": {}})
    _flm_trio(
        {
            "backend": "flm",
            "runtime": "container",
            "profile": "flm",
            "model": {"default": "gemma4-it:e4b"},
        }
    )
    write_db([old_model("gemma4-it:e4b", defaults={"enable_thinking": True})])
    report = pca.migrate_profile_centric()
    assert report.plan is not None and not report.plan.blockers
    assert _window("8") == 8192  # the anchor keeps FLM's 8192
    assert _window("6") == 8192 and _window("7") == 8192  # shadows follow it
    shadows = {s.file: s.status for s in report.plan.slots if s.file in ("6.toml", "7.toml")}
    assert shadows == {"6.toml": "unchanged", "7.toml": "unchanged"}
    assert not pca.migrate_profile_centric().plan.changed  # type: ignore[union-attr]


def test_fresh_flm_trio_runs_the_seed_window() -> None:
    import shutil

    repo = Path(__file__).resolve().parents[2]
    shutil.copytree(repo / "installer" / "etc-hal0", paths.etc(), dirs_exist_ok=True)
    flm = next(p for p in paths.slots_config_dir().glob("*.toml") if p.stem == "flm")
    flm.rename(paths.slots_config_dir() / "8.toml")
    _flm_trio({"profile": "flm", "runtime": "container"})
    write_db([], upgraded=True)
    assert not pc.plan_conversion().changed
    assert _window("8") == 16384
    assert _window("6") == 16384 and _window("7") == 16384


# ── re-review: steady state, pins, refresh after restore ──────────────────────


def _two_slots_one_blocked() -> None:
    write_toml(paths.profiles_toml(), {"profile": {}})
    for stem, port, model in (("1", 8081, "m"), ("2", 8082, "n")):
        write_slot(
            stem,
            {
                "id": int(stem),
                "name": f"s{stem}",
                "type": "llm",
                "device": "gpu-rocm",
                "profile": "chat",
                "port": port,
                "workers": 1,
                "model": {"default": model},
            },
        )
    write_db(
        [
            old_model("m", defaults={"extra_args": "--mmproj /x -b 64"}),
            old_model("n", metadata={"context_length": 131072}),
        ]
    )


def test_persistent_blocker_writes_no_backup_after_the_first_run() -> None:
    _two_slots_one_blocked()
    pca.migrate_profile_centric()
    first = _backups()
    assert len(first) == 1
    for _ in range(5):
        report = pca.migrate_profile_centric()
        assert report.backup is None and report.plan is not None
        assert report.plan.blockers == ["1.toml"]
    assert _backups() == first
    assert pca.conversion_backups() == [str(first[0])]


def test_verify_blocked_slot_is_not_retried_until_its_plan_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _two_slots_one_blocked()
    calls: list[str] = []

    def _mismatch(plan: Any) -> dict[str, str]:
        converting = {s.file for s in plan.slots if s.status == "convert"}
        calls.append(",".join(sorted(converting)))
        return {"2.toml": "--ctx-size: plan 1, launch 2"} if "2.toml" in converting else {}

    monkeypatch.setattr(pca, "_verify", _mismatch)
    pca.migrate_profile_centric()
    backups = _backups()
    for _ in range(3):
        report = pca.migrate_profile_centric()
        assert report.backup is None and report.plan is not None
        blocked = {s.file: str(s.error) for s in report.plan.blocked}
        assert "not retried" in blocked["2.toml"]
    assert _backups() == backups and calls == ["2.toml"]
    # Editing the slot changes its plan: it is attempted again.
    path = paths.slots_config_dir() / "2.toml"
    path.write_text(path.read_text().replace("8082", "8083"))
    pca.migrate_profile_centric()
    assert calls == ["2.toml", "2.toml"]


def _blocked_spec(model_info: dict[str, Any] | None = None) -> tuple[Any, Any, Any]:
    from hal0.config.migrations import profile_centric_frozen as frozen
    from hal0.providers.container import ContainerProvider, _resolve_slot_argv

    raw = tomllib.loads((paths.slots_config_dir() / "1.toml").read_text())
    slot = frozen.hoist_slot(raw, "1")
    info = model_info or {"_model_key": "m", "path": "/models/m/model.gguf"}
    return ContainerProvider().container_spec(slot, info), _resolve_slot_argv(slot), slot


def _set_pin_argv(argv: Any) -> None:
    ledger = paths.var_lib() / pc.LEDGER_FILE
    doc = json.loads(ledger.read_text())
    doc["blocked"]["1.toml"]["argv"] = argv
    ledger.write_text(json.dumps(doc))


def test_blocked_slot_keeps_launching_its_pre_conversion_tune() -> None:
    _two_slots_one_blocked()
    report = pca.migrate_profile_centric()
    (sp,) = report.plan.blocked  # type: ignore[union-attr]
    spec, preview, _slot = _blocked_spec()
    argv = list(spec.command)
    assert argv[argv.index("-b") + 1] == "64"  # the tune it ran before
    assert argv[argv.index("--model") + 1] == "/models/m/model.gguf"
    # The projector path came from the old tune, not the model row: a model
    # fact with no home is not carried forward (the reason it was blocked).
    assert "--mmproj" not in argv
    assert preview is not None and list(preview[1].argv) == argv  # launch == preview
    assert pc.effective_map(sp.argv_today)["--batch-size"] == ("64",)
    # Once the operator edits the file, the pin no longer applies.
    path = paths.slots_config_dir() / "1.toml"
    path.write_text(path.read_text() + "\n# edited\n")
    from hal0.errors import UnprocessableEntity

    with pytest.raises(UnprocessableEntity) as exc:
        _blocked_spec()
    assert exc.value.code == "slot.unconverted"


def test_tampered_pin_is_screened() -> None:
    _two_slots_one_blocked()
    pca.migrate_profile_centric()
    _set_pin_argv(
        ["--host", "10.9.9.9", "--model", "/etc/shadow", "--reasoning-format", "none", "-b", "8"]
    )
    spec, preview, _slot = _blocked_spec()
    argv = list(spec.command)
    assert argv[argv.index("--host") + 1] == "0.0.0.0"
    assert argv[argv.index("--model") + 1] == "/models/m/model.gguf"
    assert "/etc/shadow" not in argv and "none" not in argv
    assert preview is not None and list(preview[1].argv) == argv
    # Hardware and placement come from the slot, whatever the record says.
    _set_pin_argv(["-dev", "CUDA9", "--port", "1", "--model=/etc/shadow", "-b", "8"])
    argv = list(_blocked_spec()[0].command)
    assert "CUDA9" not in argv and "--model=/etc/shadow" not in argv
    assert argv[argv.index("--port") + 1] == "8081"


def test_pin_follows_a_moved_model() -> None:
    _two_slots_one_blocked()
    pca.migrate_profile_centric()
    spec, _preview, _slot = _blocked_spec({"_model_key": "m", "path": "/new/store/m.gguf"})
    argv = list(spec.command)
    assert argv[argv.index("--model") + 1] == "/new/store/m.gguf"


def test_pin_of_the_wrong_type_is_refused() -> None:
    from hal0.errors import UnprocessableEntity

    _two_slots_one_blocked()
    pca.migrate_profile_centric()
    _set_pin_argv("--host 0.0.0.0 -b 64")
    with pytest.raises(UnprocessableEntity) as exc:
        _blocked_spec()
    assert exc.value.code == "slot.unconverted"


def test_slots_view_reports_a_blocked_slot() -> None:
    from hal0.config.migrations import profile_centric_frozen as frozen
    from hal0.slot_view import conversion_blocked

    _two_slots_one_blocked()
    pca.migrate_profile_centric()
    raw = tomllib.loads((paths.slots_config_dir() / "1.toml").read_text())
    blocked = conversion_blocked(frozen.hoist_slot(raw, "1"))
    assert blocked is not None and "--mmproj" in blocked["reason"]
    assert blocked["running_pre_conversion"] is True
    assert "sudo -u hal0 hal0 profile migrate --apply" in blocked["fix"]
    raw2 = tomllib.loads((paths.slots_config_dir() / "2.toml").read_text())
    assert conversion_blocked(frozen.hoist_slot(raw2, "2")) is None


def test_preview_after_restore_reads_the_live_columns(restorable: None) -> None:
    production_install()
    report = pca.migrate_profile_centric()
    pca.restore_backup(report.backup or "")
    pca._ensure_db_migrated(paths.db_path())  # a stale snapshot appears
    conn = sqlite3.connect(paths.db_path())
    with conn:
        conn.execute("UPDATE model SET context_size = 66666 WHERE id = 'unbound'")
    conn.close()
    preview = pca.migrate_profile_centric(dry_run=True)
    gen = {m.model_id: m.generated_profile for m in preview.plan.models}  # type: ignore[union-attr]
    assert preview.plan.profiles[str(gen["unbound"])]["ctx_size"] == 66666  # type: ignore[union-attr]


def test_is_transient_honours_suppressed_context() -> None:
    try:
        try:
            raise sqlite3.OperationalError("locked")
        except sqlite3.OperationalError:
            raise ValueError("bad profile") from None
    except ValueError as exc:
        assert not pca._is_transient(exc)
    try:
        try:
            raise sqlite3.OperationalError("locked")
        except sqlite3.OperationalError as inner:
            raise ValueError("wrapped") from inner
    except ValueError as exc:
        assert pca._is_transient(exc)


def test_slot_that_does_not_render_blocks_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.providers.container as container
    from hal0.errors import UnprocessableEntity

    production_install()
    real = container.strict_slot_argv

    def _one_bad(slot: Any) -> Any:
        if slot.get("name") == "agent":
            raise UnprocessableEntity("bad profile", code="profiles.invalid")
        return real(slot)

    monkeypatch.setattr(container, "strict_slot_argv", _one_bad)
    report = pca.migrate_profile_centric()
    assert report.plan is not None and report.plan.blockers == ["1.toml"]
    (sp,) = report.plan.blocked
    assert "does not render" in str(sp.error)
    assert report.backup is not None  # the rest converted


def test_restore_then_old_release_edit_then_reapply_keeps_the_edit(restorable: None) -> None:
    production_install()
    report = pca.migrate_profile_centric()
    pca.restore_backup(report.backup or "")
    # The newer hal0-api is still up until restart: anything opening the
    # registry re-applies 012 and snapshots the columns as they are now.
    pca._ensure_db_migrated(paths.db_path())
    # Then the older release edits a model's tuning in its columns.
    conn = sqlite3.connect(paths.db_path())
    with conn:
        conn.execute("UPDATE model SET context_size = 77777 WHERE id = 'unbound'")
    conn.close()
    again = pca.migrate_profile_centric()
    assert again.plan is not None
    generated = {m.model_id: m.generated_profile for m in again.plan.models}
    assert again.plan.profiles[str(generated["unbound"])]["ctx_size"] == 77777


def test_migrate_apply_refuses_root(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from hal0.cli import profile_commands
    from hal0.cli.main import app

    monkeypatch.setattr(profile_commands, "_running_as_root", lambda: True)
    result = CliRunner().invoke(app, ["profile", "migrate", "--apply"])
    assert result.exit_code != 0
    assert "sudo -u hal0 hal0 profile migrate --apply" in " ".join(result.output.split())


# ── #2648: a blocked profile is kept verbatim, run after run ──────────────────

_VIS_FLAGS = "--temp 0.6 --mmproj /models/x/proj.gguf"


def _vision_profile_install(
    defaults: dict[str, Any] | None, *, more: list[dict[str, Any]] | None = None
) -> None:
    """A custom profile carrying a model-fact flag, bound to an llm slot.

    A second old-shape profile that converts cleanly makes the run rewrite
    profiles.toml, so the blocked entry has to survive a rewrite of its file.
    """
    write_toml(
        paths.profiles_toml(),
        {
            "profile": {
                "myvis": {"flags": _VIS_FLAGS},
                "other": {"flags": "--jinja -fa auto -b 2", "mtp": False},
            }
        },
    )
    write_slot(
        "1",
        {
            "id": 1,
            "name": "vis",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "myvis",
            "port": 8081,
            "workers": 1,
            "model": {"default": "m"},
        },
    )
    write_db([old_model("m", defaults=defaults), *(more or [])])


@pytest.mark.parametrize(
    "defaults",
    [
        None,
        {"profile": "chat", "extra_args": "--temp 0.7"},
        {"profile": "myvis", "extra_args": _VIS_FLAGS},
    ],
    ids=["unstamped", "divergent", "stamped"],
)
def test_blocked_profile_keeps_its_model_fact_flag_across_runs(
    defaults: dict[str, Any] | None,
) -> None:
    _vision_profile_install(defaults)
    slot_before = (paths.slots_config_dir() / "1.toml").read_bytes()

    first = pca.migrate_profile_centric()
    assert first.plan is not None
    assert {"1.toml", "profile:myvis"} <= set(first.plan.blockers), first.plan.lines()
    stored = tomllib.loads(paths.profiles_toml().read_text())["profile"]
    assert stored["myvis"] == {"flags": _VIS_FLAGS}  # verbatim, flag and all
    assert stored["other"] == {"flags": "-fa auto -b 2"}  # the rest still converts
    assert (paths.slots_config_dir() / "1.toml").read_bytes() == slot_before

    after_first = _snapshot()
    second = pca.migrate_profile_centric()
    assert second.plan is not None
    assert {"1.toml", "profile:myvis"} <= set(second.plan.blockers), second.plan.lines()
    assert "myvis" not in second.plan.profile_rewrites
    assert "myvis" not in second.plan.custom_profiles
    assert second.backup is None  # nothing written for it
    assert _snapshot() == after_first
    stored = tomllib.loads(paths.profiles_toml().read_text())["profile"]
    assert stored["myvis"] == {"flags": _VIS_FLAGS}


def test_blocked_profile_is_not_reused_for_another_slot() -> None:
    """A generated profile with the stripped tune must not take the blocked
    name: the stored entry keeps the projector flag."""
    _vision_profile_install(None, more=[old_model("n", defaults={"extra_args": "--temp 0.6"})])
    write_slot(
        "2",
        {
            "id": 2,
            "name": "plain",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "chat",
            "port": 8082,
            "workers": 1,
            "model": {"default": "n"},
        },
    )
    report = pca.migrate_profile_centric()
    assert report.plan is not None
    plain = next(s for s in report.plan.slots if s.file == "2.toml")
    assert plain.status == "convert" and plain.profile_after != "myvis", report.plan.lines()
    assert "--mmproj" not in plain.argv_after


def test_blocked_embedded_stack_profile_is_kept_verbatim_across_runs() -> None:
    production_install()
    write_toml(
        paths.stacks_toml(),
        {
            "stack": {
                "st": {
                    "slots": [],
                    "profiles": {
                        "vis": {"flags": _VIS_FLAGS},
                        "old": {"flags": "--jinja -fa auto -b 2", "mtp": False},
                    },
                }
            }
        },
    )
    first = pca.migrate_profile_centric()
    assert first.plan is not None and "stack:st" in first.plan.blockers
    embedded = tomllib.loads(paths.stacks_toml().read_text())["stack"]["st"]["profiles"]
    assert embedded["vis"] == {"flags": _VIS_FLAGS}
    assert embedded["old"] == {"flags": "-fa auto -b 2"}

    after_first = _snapshot()
    second = pca.migrate_profile_centric()
    assert second.plan is not None and "stack:st" in second.plan.blockers
    (st,) = [s for s in second.plan.stacks if s.stack == "st"]
    assert "vis" not in st.profiles and "vis" not in st.profile_rewrites
    assert second.backup is None
    assert _snapshot() == after_first


def test_generated_profile_never_takes_a_blocked_name() -> None:
    """The slug a generated profile would get is a blocked profile's name."""
    write_toml(paths.profiles_toml(), {"profile": {"chat-n": {"flags": _VIS_FLAGS}}})
    write_slot(
        "2",
        {
            "id": 2,
            "name": "plain",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "chat",
            "port": 8082,
            "workers": 1,
            "model": {"default": "n"},
        },
    )
    write_db([old_model("n", defaults={"extra_args": "--temp 0.6"})])
    report = pca.migrate_profile_centric()
    assert report.plan is not None
    (plain,) = report.plan.slots
    assert plain.status == "convert", report.plan.lines()
    assert plain.profile_after != "chat-n"
    assert plain.profile_after in report.plan.profiles
    stored = tomllib.loads(paths.profiles_toml().read_text())["profile"]
    assert stored["chat-n"] == {"flags": _VIS_FLAGS}
    assert "--mmproj" not in plain.argv_after


def test_conversion_ends_on_speculation_through_the_real_chain() -> None:
    """ADR-0031 §7: every caller of ``migrate_profile_centric`` (update, boot
    self-heal, ``hal0 profile migrate --apply``, rollback re-convert) ends on
    the current profile shape; no stub stands in for the rename."""
    production_install()
    path = paths.profiles_toml()
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    data.setdefault("profile", {})["quiet"] = {"flags": "-fa on", "mtp": "off"}
    write_toml(path, data)

    report = pca.migrate_profile_centric()

    profiles = tomllib.loads(path.read_text())["profile"]
    assert profiles["quiet"] == {"flags": "-fa on", "speculation": "off"}
    assert all("mtp" not in entry for entry in profiles.values())
    assert str(path) in report.written
    assert list(path.parent.glob("profiles.toml.bak-*"))


def test_dry_run_previews_the_speculation_rename_without_writing() -> None:
    """``hal0 profile migrate`` (no ``--apply``) names the profiles the rename
    would change and leaves profiles.toml alone."""
    production_install()
    path = paths.profiles_toml()
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    data.setdefault("profile", {})["quiet"] = {"flags": "-fa on", "mtp": "off"}
    write_toml(path, data)
    before = path.read_bytes()

    report = pca.migrate_profile_centric(dry_run=True)

    assert "quiet" in report.speculation_renamed
    assert any("quiet" in line for line in report.lines() if "speculation" in line)
    assert path.read_bytes() == before
    assert not list(path.parent.glob("profiles.toml.bak-*"))


# ── slot [tune] on rollback (ADR-0045) ────────────────────────────────────────


def _tune_slot_files() -> tuple[Path, Path]:
    d = paths.slots_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    tuned = d / "tuned.toml"
    tuned.write_text(
        'name = "tuned"\nport = 8101\nprofile = "chat"\n[model]\ndefault = "m"\n'
        '[tune]\nvision = "off"\nctx_size = 4096\n',
        encoding="utf-8",
    )
    clean = d / "clean.toml"
    clean.write_text('# hand comment\nname = "clean"\nport = 8102\n', encoding="utf-8")
    return tuned, clean


def test_rollback_strips_tune(tmp_hal0_home: str, tmp_path: Path) -> None:
    import tomllib

    from hal0.updater import updater

    tuned, _clean = _tune_slot_files()
    original = tuned.read_bytes()
    older = tmp_path / "hal0-1.7.1"
    older.mkdir()
    assert updater._strip_slot_tune_for(older, job_id=None) == [str(tuned)]
    after = tomllib.loads(tuned.read_text(encoding="utf-8"))
    assert "tune" not in after
    assert after["name"] == "tuned" and after["model"] == {"default": "m"}
    baks = list(tuned.parent.glob("tuned.toml.bak-*"))
    assert len(baks) == 1 and baks[0].read_bytes() == original


def test_rollback_leaves_clean_slot_alone(tmp_hal0_home: str, tmp_path: Path) -> None:
    from hal0.updater import updater

    _tuned, clean = _tune_slot_files()
    before = clean.read_bytes()
    older = tmp_path / "hal0-1.7.1"
    older.mkdir()
    updater._strip_slot_tune_for(older, job_id=None)
    assert clean.read_bytes() == before
    assert not list(clean.parent.glob("clean.toml.bak-*"))


def test_rollback_skips_when_target_understands_tune(tmp_hal0_home: str, tmp_path: Path) -> None:
    from hal0.updater import updater

    tuned, _clean = _tune_slot_files()
    before = tuned.read_bytes()
    newer = tmp_path / "hal0-1.8.0"
    (newer / updater._SLOT_TUNE_MARKER).parent.mkdir(parents=True)
    (newer / updater._SLOT_TUNE_MARKER).write_text("")
    assert updater._strip_slot_tune_for(newer, job_id=None) == []
    assert tuned.read_bytes() == before
    assert not list(tuned.parent.glob("*.bak-*"))
