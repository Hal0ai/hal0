"""``--stop-services`` on the deploy-window ``hal0 slot migrate-id-keying`` verb (#2325).

``--stop-services`` stops ``hal0-api`` and every active ``hal0-slot@*`` unit
so the migration can rewrite config the running process would otherwise
resolve. Before #2325 nothing ever started them again: a refusal, a declined
prompt, or an exception after the stop left the API and every slot down.

``migrate-id-keying`` is the one deploy-window verb left (ADR-0029 deleted
``migrate-hw``/``migrate-caps``/``migrate-flags``), so it carries the whole
``_refuse_if_live`` / ``_stopped_units`` machinery under test.

These tests fake systemd (``active_hal0_units`` + ``subprocess.run``) and the
migration seam, record every side effect in one ordered log, and assert:

* checks that can refuse the run (live units without the flag, the confirm
  prompt) happen before anything is stopped;
* every unit ``--stop-services`` stopped is started again on every
  non-success exit: an exception, Ctrl-C, a failed backup, and a unit that
  would not stop;
* a successful run leaves them stopped and names them next to the existing
  "Restart hal0-api" hint. Whether a success should restart them is a
  separate decision, out of scope for #2325 (see its triage comment).
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
import typer

from hal0.cli import slot_commands

API = "hal0-api.service"
SLOT = "hal0-slot@chat.service"


class FakeSystemd:
    """A tiny systemd: tracks active units and logs every stop/start."""

    def __init__(self, log: list[tuple[str, ...]], active: list[str]) -> None:
        self.log = log
        self.active = list(active)
        self.stubborn: set[str] = set()
        self.start_rc = 0

    def active_units(self) -> list[str]:
        return list(self.active)

    def run(self, argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "systemctl", argv
        verb, unit = argv[1], argv[2]
        self.log.append((verb, unit))
        if verb == "stop" and unit not in self.stubborn and unit in self.active:
            self.active.remove(unit)
        elif verb == "start" and self.start_rc == 0 and unit not in self.active:
            self.active.append(unit)
        return subprocess.CompletedProcess(argv, self.start_rc if verb == "start" else 0)


@pytest.fixture
def log() -> list[tuple[str, ...]]:
    return []


@pytest.fixture
def systemd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, log: list[tuple[str, ...]]
) -> FakeSystemd:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    fake = FakeSystemd(log, [API, SLOT])
    monkeypatch.setattr(slot_commands, "active_hal0_units", fake.active_units)
    monkeypatch.setattr(slot_commands.subprocess, "run", fake.run)

    def _backup(**_kwargs: Any) -> Any:
        log.append(("backup",))
        return tmp_path / "backup.tar.gz"

    monkeypatch.setattr(slot_commands, "_backup_slot_state", _backup)
    return fake


def _install_id_keying(
    monkeypatch: pytest.MonkeyPatch,
    log: list[tuple[str, ...]],
    *,
    apply_raises: BaseException | None = None,
) -> None:
    from hal0.slots import migrate_id_keying

    def _migrate(**_kwargs: Any) -> Any:
        log.append(("migrate",))
        if apply_raises is not None:
            raise apply_raises
        return migrate_id_keying.MigrationReport(
            migrations=[migrate_id_keying.SlotMigration(name="chat", slot_id=1)],
            skipped_ids=[],
        )

    monkeypatch.setattr(migrate_id_keying, "migrate_slot_id_keying", _migrate)
    monkeypatch.setattr(migrate_id_keying, "SubprocessSlotArtifactOps", lambda: object())


STOPPED = [("stop", API), ("stop", SLOT)]
RESTARTED = [("start", SLOT), ("start", API)]


def _migrate(**kwargs: Any) -> None:
    slot_commands.slot_migrate_id_keying(**kwargs)


# ── success: units stay stopped, and the output names them ───────────────────


def test_success_leaves_units_stopped_and_names_them(
    monkeypatch: pytest.MonkeyPatch,
    systemd: FakeSystemd,
    log: list[tuple[str, ...]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_id_keying(monkeypatch, log)

    _migrate(apply=True, yes=True, stop_services=True)

    assert log == [*STOPPED, ("backup",), ("migrate",)]
    assert systemd.active == []
    out = " ".join(capsys.readouterr().out.split())  # undo Rich line-wrapping
    assert "Restart hal0-api" in out
    assert f"still stopped by --stop-services (restart them when ready): {API}, {SLOT}" in out


def test_no_active_units_runs_without_stopping_anything(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    _install_id_keying(monkeypatch, log)
    systemd.active = []

    _migrate(apply=True, yes=True, stop_services=False)

    assert log == [("backup",), ("migrate",)]


# ── refusals before the stop: nothing is stopped ─────────────────────────────


def test_declined_prompt_stops_nothing(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    _install_id_keying(monkeypatch, log)

    def _decline(*_args: Any, **_kwargs: Any) -> bool:
        raise typer.Abort()

    monkeypatch.setattr(slot_commands.typer, "confirm", _decline)

    with pytest.raises(typer.Abort):
        _migrate(apply=True, yes=False, stop_services=True)

    assert log == []
    assert systemd.active == [API, SLOT]


def test_without_stop_services_live_units_refuse_and_nothing_is_touched(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    _install_id_keying(monkeypatch, log)

    with pytest.raises(typer.Exit) as excinfo:
        _migrate(apply=True, yes=True, stop_services=False)

    assert excinfo.value.exit_code == 1
    assert log == []
    assert systemd.active == [API, SLOT]


# ── failures after the stop: every stopped unit is started again ─────────────


def test_exception_mid_run_restarts_stopped_units(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    _install_id_keying(monkeypatch, log, apply_raises=OSError("disk full"))

    with pytest.raises(OSError, match="disk full"):
        _migrate(apply=True, yes=True, stop_services=True)

    assert log == [*STOPPED, ("backup",), ("migrate",), *RESTARTED]
    assert sorted(systemd.active) == sorted([API, SLOT])


def test_backup_failure_restarts_stopped_units(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    _install_id_keying(monkeypatch, log)

    def _backup(**_kwargs: Any) -> Any:
        log.append(("backup",))
        raise PermissionError("backups dir not writable")

    monkeypatch.setattr(slot_commands, "_backup_slot_state", _backup)

    with pytest.raises(PermissionError):
        _migrate(apply=True, yes=True, stop_services=True)

    assert log == [*STOPPED, ("backup",), *RESTARTED]
    assert sorted(systemd.active) == sorted([API, SLOT])


def test_unit_that_will_not_stop_refuses_and_restarts_the_rest(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    _install_id_keying(monkeypatch, log)
    systemd.stubborn.add(SLOT)

    with pytest.raises(typer.Exit) as excinfo:
        _migrate(apply=True, yes=True, stop_services=True)

    assert excinfo.value.exit_code == 1
    assert ("migrate",) not in log
    assert ("backup",) not in log
    assert log == [*STOPPED, *RESTARTED]
    assert sorted(systemd.active) == sorted([API, SLOT])


def test_restart_failure_names_the_unit_and_keeps_the_original_error(
    monkeypatch: pytest.MonkeyPatch,
    systemd: FakeSystemd,
    log: list[tuple[str, ...]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_id_keying(monkeypatch, log, apply_raises=OSError("boom"))
    systemd.start_rc = 1

    with pytest.raises(OSError, match="boom"):
        _migrate(apply=True, yes=True, stop_services=True)

    out = " ".join(capsys.readouterr().out.split())  # undo Rich line-wrapping
    assert f"systemctl start {API}" in out
    assert f"systemctl start {SLOT}" in out


def test_ctrl_c_mid_run_restarts_stopped_units(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    _install_id_keying(monkeypatch, log, apply_raises=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        _migrate(apply=True, yes=True, stop_services=True)

    assert log == [*STOPPED, ("backup",), ("migrate",), *RESTARTED]
    assert sorted(systemd.active) == sorted([API, SLOT])
