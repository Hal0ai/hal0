"""``--stop-services`` on the deploy-window ``hal0 slot migrate-*`` verbs (#2325).

``--stop-services`` stops ``hal0-api`` and every active ``hal0-slot@*`` unit
so the migration can rewrite config the running process would otherwise
resolve. Before #2325 nothing ever started them again: a refusal, a declined
prompt, or an exception after the stop left the API and every slot down.

These tests fake systemd (``active_hal0_units`` + ``subprocess.run``) and the
migration seams, record every side effect in one ordered log, and assert:

* checks that can refuse the run (the ``migrate-flags`` divergent-share
  preflight, the confirm prompt) happen before anything is stopped;
* every unit ``--stop-services`` stopped is started again on every
  non-success exit: a partial run, an exception, and a unit that would not
  stop;
* a successful run leaves them stopped and names them next to the existing
  "Restart hal0-api" hint. Whether a success should restart them is a
  separate decision, out of scope for #2325 (see its triage comment).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
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


def _fake_fold(
    log: list[tuple[str, ...]], *, apply_raises: BaseException | None = None
) -> Callable[..., list[str]]:
    def run_migration(*, deploy_window: bool, dry_run: bool) -> list[str]:
        log.append(("dry-run",) if dry_run else ("migrate",))
        if not dry_run and apply_raises is not None:
            raise apply_raises
        return ["fold something"]

    return run_migration


def _install_fold(
    monkeypatch: pytest.MonkeyPatch, module: str, run_migration: Callable[..., list[str]]
) -> None:
    monkeypatch.setattr(f"hal0.config.migrations.{module}.run_migration", run_migration)


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


FOLDS = [
    (slot_commands.slot_migrate_hw, "hw_slot_ownership"),
    (slot_commands.slot_migrate_caps, "model_owned_caps"),
    (slot_commands.slot_migrate_flags, "slot_flags_fold"),
]
ALL = [*FOLDS, (slot_commands.slot_migrate_id_keying, None)]

STOPPED = [("stop", API), ("stop", SLOT)]
RESTARTED = [("start", SLOT), ("start", API)]


def _without_preflight(log: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [e for e in log if e != ("dry-run",)]


def _invoke(command: Any, module: str | None, **kwargs: Any) -> None:
    if module is None:
        kwargs["dry_run"] = False
    command(**kwargs)


# ── success: units stay stopped, and the output names them ───────────────────


@pytest.mark.parametrize(("command", "module"), ALL)
def test_success_leaves_units_stopped_and_names_them(
    command: Any,
    module: str | None,
    monkeypatch: pytest.MonkeyPatch,
    systemd: FakeSystemd,
    log: list[tuple[str, ...]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    if module is None:
        _install_id_keying(monkeypatch, log)
    else:
        _install_fold(monkeypatch, module, _fake_fold(log))

    _invoke(command, module, apply=True, yes=True, stop_services=True)

    assert _without_preflight(log) == [*STOPPED, ("backup",), ("migrate",)]
    assert systemd.active == []
    out = " ".join(capsys.readouterr().out.split())  # undo Rich line-wrapping
    assert "Restart hal0-api" in out
    assert f"still stopped by --stop-services (restart them when ready): {API}, {SLOT}" in out


def test_flags_preflight_runs_before_anything_is_stopped(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    _install_fold(monkeypatch, "slot_flags_fold", _fake_fold(log))

    slot_commands.slot_migrate_flags(apply=True, yes=True, stop_services=True)

    assert log == [("dry-run",), *STOPPED, ("backup",), ("migrate",)]


# ── refusals before the stop: nothing is stopped ─────────────────────────────


def test_flags_divergent_share_refusal_stops_nothing(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    def _refuse(*, deploy_window: bool, dry_run: bool) -> list[str]:
        log.append(("dry-run",) if dry_run else ("migrate",))
        raise RuntimeError("model 'm': slots a, b fold divergent tunes")

    _install_fold(monkeypatch, "slot_flags_fold", _refuse)

    with pytest.raises(typer.Exit) as excinfo:
        slot_commands.slot_migrate_flags(apply=True, yes=True, stop_services=True)

    assert excinfo.value.exit_code == 1
    assert log == [("dry-run",)]
    assert systemd.active == [API, SLOT]


@pytest.mark.parametrize(("command", "module"), ALL)
def test_declined_prompt_stops_nothing(
    command: Any,
    module: str | None,
    monkeypatch: pytest.MonkeyPatch,
    systemd: FakeSystemd,
    log: list[tuple[str, ...]],
) -> None:
    if module is None:
        _install_id_keying(monkeypatch, log)
    else:
        _install_fold(monkeypatch, module, _fake_fold(log))

    def _decline(*_args: Any, **_kwargs: Any) -> bool:
        raise typer.Abort()

    monkeypatch.setattr(slot_commands.typer, "confirm", _decline)

    kwargs: dict[str, Any] = {"apply": True, "yes": False, "stop_services": True}
    if module is None:
        kwargs["dry_run"] = False
    with pytest.raises(typer.Abort):
        command(**kwargs)

    assert _without_preflight(log) == []
    assert systemd.active == [API, SLOT]


# ── failures after the stop: every stopped unit is started again ─────────────


@pytest.mark.parametrize(("command", "module"), ALL)
def test_exception_mid_run_restarts_stopped_units(
    command: Any,
    module: str | None,
    monkeypatch: pytest.MonkeyPatch,
    systemd: FakeSystemd,
    log: list[tuple[str, ...]],
) -> None:
    boom = OSError("disk full")
    kwargs: dict[str, Any] = {"apply": True, "yes": True, "stop_services": True}
    if module is None:
        _install_id_keying(monkeypatch, log, apply_raises=boom)
        kwargs["dry_run"] = False
    else:
        _install_fold(monkeypatch, module, _fake_fold(log, apply_raises=boom))

    with pytest.raises(OSError, match="disk full"):
        command(**kwargs)

    assert _without_preflight(log) == [*STOPPED, ("backup",), ("migrate",), *RESTARTED]
    assert sorted(systemd.active) == sorted([API, SLOT])


def test_backup_failure_restarts_stopped_units(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    _install_fold(monkeypatch, "hw_slot_ownership", _fake_fold(log))

    def _backup(**_kwargs: Any) -> Any:
        log.append(("backup",))
        raise PermissionError("backups dir not writable")

    monkeypatch.setattr(slot_commands, "_backup_slot_state", _backup)

    with pytest.raises(PermissionError):
        slot_commands.slot_migrate_hw(apply=True, yes=True, stop_services=True)

    assert log == [*STOPPED, ("backup",), *RESTARTED]


def test_flags_partial_run_restarts_stopped_units(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    from hal0.config.migrations.slot_flags_fold import FoldPartiallyApplied, SkippedFold

    partial = FoldPartiallyApplied(
        ["SKIP model 'ghost': not in registry"],
        [SkippedFold(model_id="ghost", slot_names=("two",), reason="not in registry")],
    )
    _install_fold(monkeypatch, "slot_flags_fold", _fake_fold(log, apply_raises=partial))

    with pytest.raises(typer.Exit) as excinfo:
        slot_commands.slot_migrate_flags(apply=True, yes=True, stop_services=True)

    assert excinfo.value.exit_code == 2
    assert log == [("dry-run",), *STOPPED, ("backup",), ("migrate",), *RESTARTED]


@pytest.mark.parametrize(("command", "module"), FOLDS)
def test_unit_that_will_not_stop_refuses_and_restarts_the_rest(
    command: Any,
    module: str,
    monkeypatch: pytest.MonkeyPatch,
    systemd: FakeSystemd,
    log: list[tuple[str, ...]],
) -> None:
    _install_fold(monkeypatch, module, _fake_fold(log))
    systemd.stubborn.add(SLOT)

    with pytest.raises(typer.Exit) as excinfo:
        command(apply=True, yes=True, stop_services=True)

    assert excinfo.value.exit_code == 1
    assert ("migrate",) not in log
    assert ("backup",) not in log
    assert _without_preflight(log) == [*STOPPED, *RESTARTED]
    assert sorted(systemd.active) == sorted([API, SLOT])


def test_restart_failure_names_the_unit_and_keeps_the_original_error(
    monkeypatch: pytest.MonkeyPatch,
    systemd: FakeSystemd,
    log: list[tuple[str, ...]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_fold(monkeypatch, "model_owned_caps", _fake_fold(log, apply_raises=OSError("boom")))
    systemd.start_rc = 1

    with pytest.raises(OSError, match="boom"):
        slot_commands.slot_migrate_caps(apply=True, yes=True, stop_services=True)

    out = " ".join(capsys.readouterr().out.split())  # undo Rich line-wrapping
    assert f"systemctl start {API}" in out
    assert f"systemctl start {SLOT}" in out


def test_without_stop_services_live_units_refuse_and_nothing_is_touched(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    _install_fold(monkeypatch, "hw_slot_ownership", _fake_fold(log))

    with pytest.raises(typer.Exit) as excinfo:
        slot_commands.slot_migrate_hw(apply=True, yes=True, stop_services=False)

    assert excinfo.value.exit_code == 1
    assert log == []
    assert systemd.active == [API, SLOT]


def test_ctrl_c_mid_run_restarts_stopped_units(
    monkeypatch: pytest.MonkeyPatch, systemd: FakeSystemd, log: list[tuple[str, ...]]
) -> None:
    _install_fold(
        monkeypatch, "hw_slot_ownership", _fake_fold(log, apply_raises=KeyboardInterrupt())
    )

    with pytest.raises(KeyboardInterrupt):
        slot_commands.slot_migrate_hw(apply=True, yes=True, stop_services=True)

    assert log == [*STOPPED, ("backup",), ("migrate",), *RESTARTED]
    assert sorted(systemd.active) == sorted([API, SLOT])
