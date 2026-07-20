"""``hal0 slot migrate-id-keying`` — the operator-run M5 id-flip CLI (P3-runtime-db
inc4). Offline / filesystem-direct (unlike the rest of ``slot_commands.py``,
which is an HTTP client to the hal0 API) — this migration flips the on-disk
layout the *stopped* API reads next boot, so it deliberately never touches the
API.

Covers the three seams the command wires together:

  * ``_backup_slot_state``   — pre-flight tar backup (the only rollback path).
  * ``_active_hal0_units``   — the live-runtime safety gate (halo143 split-brain
                                lesson: never flip under a running API/slot).
  * ``_migrate_id_keying_dry_run_plan`` — the plan a ``--dry-run`` prints
                                without invoking the real (file-moving) migrator.

and one end-to-end run of the typer command itself against a real fixture tree.
"""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

import pytest

from hal0.cli.slot_commands import (
    _active_hal0_units,
    _backup_slot_state,
    _migrate_id_keying_dry_run_plan,
    slot_migrate_id_keying,
)
from hal0.slots.identity import SlotIdentityStore

# ── _backup_slot_state ───────────────────────────────────────────────────────


def test_backup_tars_config_data_and_db(tmp_path: Path) -> None:
    config_dir = tmp_path / "etc" / "slots"
    data_dir = tmp_path / "var" / "slots"
    db_file = tmp_path / "var" / "hal0.db"
    config_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    (config_dir / "chat.toml").write_text('[slot]\nname = "chat"\n')
    (data_dir / "chat").mkdir()
    (data_dir / "chat" / "state.json").write_text("{}")
    db_file.write_text("sqlite-bytes")

    backup_root = tmp_path / "backups"
    tar_path = _backup_slot_state(
        config_dir=config_dir, data_dir=data_dir, db_file=db_file, backup_root=backup_root
    )

    assert tar_path.exists()
    assert tar_path.parent == backup_root
    with tarfile.open(tar_path, "r:gz") as tf:
        names = tf.getnames()
    assert any("chat.toml" in n for n in names)
    assert any("state.json" in n for n in names)
    assert any("hal0.db" in n for n in names)


def test_backup_tolerates_missing_data_dir(tmp_path: Path) -> None:
    # A fresh box may have no data_dir yet (no slot has ever loaded) — the
    # backup must not blow up, just skip what's absent.
    config_dir = tmp_path / "etc" / "slots"
    config_dir.mkdir(parents=True)
    (config_dir / "chat.toml").write_text('[slot]\nname = "chat"\n')
    data_dir = tmp_path / "var" / "slots"  # never created
    db_file = tmp_path / "var" / "hal0.db"  # never created

    tar_path = _backup_slot_state(
        config_dir=config_dir,
        data_dir=data_dir,
        db_file=db_file,
        backup_root=tmp_path / "backups",
    )
    with tarfile.open(tar_path, "r:gz") as tf:
        names = tf.getnames()
    assert any("chat.toml" in n for n in names)


# ── _active_hal0_units ───────────────────────────────────────────────────────


def _fake_run(active_units: set[str]):
    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["systemctl", "is-active"]:
            unit = argv[-1]
            rc = 0 if unit in active_units else 3
            return subprocess.CompletedProcess(
                argv, rc, stdout=("active\n" if rc == 0 else "inactive\n")
            )
        if argv[:2] == ["systemctl", "list-units"]:
            lines = [
                f"{u} loaded active running dummy"
                for u in sorted(active_units)
                if u != "hal0-api.service"
            ]
            return subprocess.CompletedProcess(
                argv, 0, stdout="\n".join(lines) + ("\n" if lines else "")
            )
        raise AssertionError(f"unexpected argv: {argv}")

    return run


def test_active_units_empty_when_nothing_running() -> None:
    assert _active_hal0_units(run=_fake_run(set())) == []


def test_active_units_reports_api_and_slots() -> None:
    active = _active_hal0_units(run=_fake_run({"hal0-api.service", "hal0-slot@143.service"}))
    assert "hal0-api.service" in active
    assert "hal0-slot@143.service" in active


def test_active_units_tolerates_missing_systemctl() -> None:
    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("systemctl not found")

    assert _active_hal0_units(run=run) == []


# ── _migrate_id_keying_dry_run_plan ──────────────────────────────────────────


def test_dry_run_plan_never_touches_disk(tmp_path: Path) -> None:
    config_dir = tmp_path / "etc" / "slots"
    config_dir.mkdir(parents=True)
    (config_dir / "chat.toml").write_text('[slot]\nname = "chat"\nport = 8081\n')
    (config_dir / "143.toml").write_text('[slot]\nid = 143\nname = "brain"\nport = 8082\n')

    identity = SlotIdentityStore(db_path=tmp_path / "hal0.db")
    plan = _migrate_id_keying_dry_run_plan(config_dir=config_dir, identity=identity)

    assert any("chat" in line and "new id" in line for line in plan)
    assert any("143.toml" in line and "already id-keyed" in line for line in plan)
    # No file was touched, no identity row was minted.
    assert (config_dir / "chat.toml").exists()
    assert identity.get_by_name("chat") is None


def test_dry_run_plan_reuses_existing_identity_row(tmp_path: Path) -> None:
    config_dir = tmp_path / "etc" / "slots"
    config_dir.mkdir(parents=True)
    (config_dir / "chat.toml").write_text('[slot]\nname = "chat"\nport = 8081\n')

    identity = SlotIdentityStore(db_path=tmp_path / "hal0.db")
    row = identity.create(name="chat", slot_type="llm", device="gpu-rocm")

    plan = _migrate_id_keying_dry_run_plan(config_dir=config_dir, identity=identity)
    assert any(f"-> {row.id}.toml" in line for line in plan)


# ── end-to-end: the typer command ────────────────────────────────────────────


def test_command_migrates_tree_and_writes_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))

    # The real SubprocessSlotArtifactOps shells out to podman/systemctl —
    # deploy-only, "HELD FOR ON-HARDWARE SMOKE" per its own docstring, same
    # as the rest of the migrator's test suite. Swap in the recording double
    # (patched at the source module the command's LOCAL import re-resolves
    # from on every call) so this test exercises the command's own wiring —
    # backup, safety gate, report — without touching a real systemd/podman.
    from hal0.slots.migrate_id_keying import RecordingSlotArtifactOps

    monkeypatch.setattr(
        "hal0.slots.migrate_id_keying.SubprocessSlotArtifactOps", RecordingSlotArtifactOps
    )

    from hal0.config import paths

    config_dir = paths.slots_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "chat.toml").write_text('[slot]\nname = "chat"\nport = 8081\n')

    # No live systemd in the test env — _active_hal0_units naturally reports
    # nothing running (FileNotFoundError path), so the safety gate passes.
    slot_migrate_id_keying(yes=True, stop_services=False, dry_run=False)

    remaining = sorted(p.name for p in config_dir.glob("*.toml"))
    assert remaining != ["chat.toml"]  # migrated to <id>.toml
    assert len(remaining) == 1

    backups = list((paths.var_lib() / "backups").glob("*.tar.gz"))
    assert len(backups) == 1
