"""``kanban_db_init`` phase contract (O20).

The Hermes gateway's kanban-board WATCHER opens ``$HERMES_HOME/kanban.db``
via a raw sqlite path — never through ``hermes_cli.kanban_db.connect()``.
``connect()``'s first-call auto-init was, until this step existed, the ONLY
thing that ever created the schema, and it only ran when hal0's HP-executor
registered (``HERMES_DASHBOARD_BASE_URL`` set). A box whose executor never
registers therefore had a watcher hitting ``no such table: tasks`` /
``kanban_notify_subs`` every tick (docs/rework/r4-stage-validation.md O20).

These tests pin :func:`hp._phase_kanban_db_init`:

* tables missing → hermes's own ``init_db`` is invoked via the venv python,
  never a hal0-side schema replica;
* tables already present → convergent skip, no subprocess invoked;
* hermes venv absent (fresh/partial install) → an honest ``skip``, never a
  bootstrap failure;
* pipeline placement — the step runs after ``install``/``home_init`` (both
  the venv and ``$HERMES_HOME`` must exist first) and before
  ``install_artifacts``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hal0.agents import hermes_provision as hp


@pytest.fixture
def kanban_state(tmp_path: Path) -> hp.BootstrapState:
    """A BootstrapState with a real (but empty) venv/bin/python stub."""
    hermes_home = tmp_path / "hermes_home"
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    python_stub = venv / "bin" / "python"
    python_stub.write_text("#!/bin/sh\nexit 0\n")
    python_stub.chmod(0o755)
    return hp.BootstrapState(hermes_home=str(hermes_home), venv=str(venv), agent_id="hermes-agent")


def _seed_tables(db_path: Path, tables: frozenset[str] = hp.KANBAN_DB_EXPECTED_TABLES) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        for table in tables:
            conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()


# ── hermes-venv-absent: honest skip, never a failure ─────────────────────────


def test_skips_when_hermes_venv_absent(tmp_path: Path) -> None:
    """A fresh/partial install with no venv yet must SKIP, not FAIL."""
    state = hp.BootstrapState(
        hermes_home=str(tmp_path / "hermes_home"),
        venv=str(tmp_path / "no-such-venv"),
    )
    result = hp._phase_kanban_db_init(hp._StepCtx(state=state))
    assert result.status is hp.PhaseStatus.SKIP
    assert "venv absent" in (result.reason or "")
    assert not (Path(state.hermes_home) / hp.KANBAN_DB_NAME).exists()


# ── tables missing: init_db is invoked, never a hal0-side schema replica ─────


def test_invokes_hermes_init_db_when_tables_missing(kanban_state: hp.BootstrapState) -> None:
    record: list[list[str]] = []

    def _run(argv, *_a, **_kw):
        record.append(list(argv))

        class _Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Completed()

    io = hp.InstallIO(run=_run)
    result = hp._phase_kanban_db_init(hp._StepCtx(state=kanban_state, io=io))

    assert result.status is hp.PhaseStatus.OK
    assert result.details["changed"] is True
    assert result.details["missing_before"] == sorted(hp.KANBAN_DB_EXPECTED_TABLES)

    assert len(record) == 1
    argv = record[0]
    hermes_python = str(Path(kanban_state.venv) / "bin" / "python")
    db_path = str(Path(kanban_state.hermes_home) / hp.KANBAN_DB_NAME)
    assert argv[0] == hermes_python
    assert argv[1] == "-c"
    # Calls hermes's own kanban_db.init_db — never a hal0-side CREATE TABLE.
    assert "hermes_cli.kanban_db" in argv[2]
    assert "init_db" in argv[2]
    assert db_path in argv[2]
    # HERMES_HOME must exist so the venv python can write the db under it.
    assert Path(kanban_state.hermes_home).is_dir()


def test_init_db_failure_surfaces_as_fail(kanban_state: hp.BootstrapState) -> None:
    """A genuine subprocess error (bad venv, crash) fails the step — this is
    NOT a case to swallow, unlike the venv-absent skip."""
    import subprocess

    def _run(*_a, **_kw):
        raise subprocess.CalledProcessError(1, ["python", "-c", "..."])

    io = hp.InstallIO(run=_run)
    result = hp._phase_kanban_db_init(hp._StepCtx(state=kanban_state, io=io))
    assert result.status is hp.PhaseStatus.FAIL
    assert "kanban init_db failed" in (result.reason or "")


# ── tables already present: convergent skip, no subprocess invoked ──────────


def test_converges_when_tables_already_present(kanban_state: hp.BootstrapState) -> None:
    db_path = Path(kanban_state.hermes_home) / hp.KANBAN_DB_NAME
    _seed_tables(db_path)

    def _run(*_a, **_kw):
        raise AssertionError("init_db must not be invoked when tables already exist")

    io = hp.InstallIO(run=_run)
    result = hp._phase_kanban_db_init(hp._StepCtx(state=kanban_state, io=io))
    assert result.status is hp.PhaseStatus.OK
    assert result.details["changed"] is False
    assert result.details["tables_before"] == sorted(hp.KANBAN_DB_EXPECTED_TABLES)


def test_reinvokes_when_some_tables_missing(kanban_state: hp.BootstrapState) -> None:
    """A partially-initialized DB (e.g. an interrupted prior run) still counts
    as needing init — any missing expected table triggers a re-run."""
    db_path = Path(kanban_state.hermes_home) / hp.KANBAN_DB_NAME
    partial = frozenset(hp.KANBAN_DB_EXPECTED_TABLES - {"kanban_notify_subs"})
    _seed_tables(db_path, partial)

    record: list[list[str]] = []

    def _run(argv, *_a, **_kw):
        record.append(list(argv))

        class _Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Completed()

    io = hp.InstallIO(run=_run)
    result = hp._phase_kanban_db_init(hp._StepCtx(state=kanban_state, io=io))
    assert result.status is hp.PhaseStatus.OK
    assert result.details["changed"] is True
    assert result.details["missing_before"] == ["kanban_notify_subs"]
    assert len(record) == 1


# ── pipeline placement ────────────────────────────────────────────────────────


def test_kanban_db_init_runs_after_install_and_home_init() -> None:
    names = [name for name, _fn in hp._INSTALL_STEPS]
    assert names.index("install") < names.index("kanban_db_init")
    assert names.index("home_init") < names.index("kanban_db_init")
    assert names.index("kanban_db_init") < names.index("install_artifacts")
