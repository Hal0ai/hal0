"""Tests for the FLM (NPU) store audit helpers behind ``hal0 doctor models``.

These are the pure classifiers + repair seam that harden the single most
reboot-fragile surface on the box (the FLM store the NPU slot bind-mounts).
They are unit-tested directly — no live API, no real privileged filesystem —
because each maps to a documented install/reboot incident:

  * ``flm_store_divergence`` — env var silently overriding the TOML field.
  * ``flm_mount_guard``      — store under an unmounted /mnt path (exit 125).
  * ``flm_store_writability``— store not writable by the container uid (1000).
  * ``repair_flm_store``     — the ``--fix`` chown/chmod, first-failure-wins.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hal0.cli.doctor_commands import (
    _FLM_CONTAINER_UID,
    flm_mount_guard,
    flm_store_divergence,
    flm_store_writability,
    repair_flm_store,
)


class _Stat:
    """Minimal os.stat_result stand-in — only st_uid / st_mode are read."""

    def __init__(self, uid: int, mode: int) -> None:
        self.st_uid = uid
        self.st_mode = mode


# ── flm_store_divergence ──────────────────────────────────────────────────────


def test_divergence_flags_conflicting_env_and_toml() -> None:
    row = flm_store_divergence("/mnt/ai-models/flm/models", "/var/lib/hal0/.config/flm/models")
    assert row is not None
    assert row["status"] == "warn"
    assert "HAL0_FLM_MODELS_DIR" in row["detail"]


def test_divergence_ignores_trailing_slash_only_difference() -> None:
    assert flm_store_divergence("/mnt/store/", "/mnt/store") is None


def test_divergence_none_when_either_side_unset() -> None:
    assert flm_store_divergence(None, "/mnt/store") is None
    assert flm_store_divergence("/mnt/store", None) is None
    assert flm_store_divergence("", "") is None


# ── flm_mount_guard ───────────────────────────────────────────────────────────


def test_mount_guard_warns_when_external_path_not_mounted() -> None:
    # Nothing along /mnt/ai-models/... is a mountpoint → deepest is "/".
    row = flm_mount_guard(Path("/mnt/ai-models/flm/models"), ismount=lambda _p: False)
    assert row is not None
    assert row["status"] == "warn"
    assert "exit 125" in row["detail"]


def test_mount_guard_ok_when_external_path_is_mounted() -> None:
    # The mount root is live → no warning.
    def ismount(p: str) -> bool:
        return p == "/mnt/ai-models"

    assert flm_mount_guard(Path("/mnt/ai-models/flm/models"), ismount=ismount) is None


def test_mount_guard_ignores_on_root_store() -> None:
    # A store on the root fs is not an external-mount concern.
    assert (
        flm_mount_guard(Path("/var/lib/hal0/.config/flm/models"), ismount=lambda _p: False) is None
    )


# ── flm_store_writability ─────────────────────────────────────────────────────


def test_writability_ok_when_owned_by_container_uid() -> None:
    assert (
        flm_store_writability(Path("/x"), stat_of=lambda _p: _Stat(_FLM_CONTAINER_UID, 0o700))
        is None
    )


def test_writability_ok_when_group_writable() -> None:
    # Owned by a different uid but group-writable (2775) is fine for the group member.
    assert flm_store_writability(Path("/x"), stat_of=lambda _p: _Stat(0, 0o2775)) is None


def test_writability_fails_when_not_writable_and_carries_repair_target() -> None:
    row = flm_store_writability(Path("/x"), stat_of=lambda _p: _Stat(0, 0o755))
    assert row is not None
    assert row["status"] == "fail"
    assert row["uid"] == 0
    assert row["mode"] == 0o755


# ── repair_flm_store ──────────────────────────────────────────────────────────


def test_repair_runs_chown_then_chmod_and_reports_ok() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    ok, msg = repair_flm_store(Path("/srv/flm"), run=run)
    assert ok
    assert calls[0][:2] == ["chown", f"{_FLM_CONTAINER_UID}:hal0"]
    assert calls[1][:2] == ["chmod", "2775"]
    assert "/srv/flm" in msg


def test_repair_short_circuits_on_first_failure() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "chown: operation not permitted")

    ok, msg = repair_flm_store(Path("/srv/flm"), run=run)
    assert not ok
    assert "chown failed" in msg
    assert "operation not permitted" in msg
    assert len(calls) == 1  # chmod never attempted


# ── doctor migrations (pending_layout_migration) ──────────────────────────────


def test_pending_migration_reports_create_counts(monkeypatch) -> None:
    import hal0.cli.migrate_commands as mig
    from hal0.cli.doctor_commands import pending_layout_migration

    report = mig.MigrationReport(
        actions=[
            mig.SymlinkAction("create", Path("/a"), Path("/t1"), "registry:x"),
            mig.SymlinkAction("create", Path("/b"), Path("/t2"), "registry:y"),
            mig.SymlinkAction("would-overwrite", Path("/c"), Path("/t3"), "registry:z"),
            mig.SymlinkAction("skip-exists", Path("/d"), Path("/t4"), "registry:w"),
        ]
    )
    monkeypatch.setattr(mig, "plan_migration", lambda **_kw: report)
    assert pending_layout_migration() == (2, 1)


def test_pending_migration_zero_on_current_layout(monkeypatch) -> None:
    import hal0.cli.migrate_commands as mig
    from hal0.cli.doctor_commands import pending_layout_migration

    monkeypatch.setattr(mig, "plan_migration", lambda **_kw: mig.MigrationReport())
    assert pending_layout_migration() == (0, 0)


def test_pending_migration_none_when_planner_raises(monkeypatch) -> None:
    import hal0.cli.migrate_commands as mig
    from hal0.cli.doctor_commands import pending_layout_migration

    def boom(**_kw: object) -> object:
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr(mig, "plan_migration", boom)
    assert pending_layout_migration() is None
