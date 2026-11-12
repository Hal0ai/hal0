"""The engine-maintenance API under names other than the embedder switch's.

The switch's own tests (``test_embedder_switch.py``) drive this module end to
end under :data:`hal0.memory.embedder_switch.NAMES`. These pin what a second
command relies on: every on-disk name and every word that names the command
comes from its :class:`MaintenanceNames`, and the journal machinery reads a
command's journal through that command's own parser.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hal0.memory import embedder_switch as es
from hal0.memory import engine_maintenance as em
from hal0.memory import write_gate

OTHER = em.MaintenanceNames(
    command="hal0 memory migrate fold-in",
    actor="the fold-in",
    journal="memory-fold-in.json",
    lock="memory-maintenance.lock",
    archive_prefix="memory-fold-in-",
    drop_in="zzzz-hal0-memory-maintenance.conf",
    mirror="journal.json",
    gate_reason="fold_in",
    log_event="hal0.memory.fold_in",
)

HS = "/var/lib/hal0/memory/hindsight/.venv/bin/hindsight-api"
UNIT = f"""# /etc/systemd/system/hindsight-api.service
[Service]
ExecStart={HS} --host 127.0.0.1 --port 9177
"""


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))


@dataclass
class _Journal:
    run_id: str
    started_at: str
    archive_dir: str
    phase: str = "running"


def _parse(raw: str) -> _Journal:
    data = json.loads(raw)
    if data.get("kind") != "fold-in":
        raise ValueError("not a fold-in journal")
    return _Journal(data["run_id"], data["started_at"], data["archive_dir"], data["phase"])


def _finished(journal: _Journal) -> bool:
    return journal.phase == "done"


def _write(path: Path, **fields: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": "fold-in", **fields}))


def test_the_switch_keeps_the_names_an_interrupted_run_is_found_by() -> None:
    # Changing any of these strands a run interrupted on a box before an upgrade.
    assert es.NAMES.journal == "memory-embedder-switch.json"
    assert es.NAMES.lock == "memory-embedder-switch.lock"
    assert es.NAMES.archive_prefix == "memory-embedder-switch-"
    assert es.NAMES.drop_in == es.ISOLATION_DROP_IN_NAME == "zzzz-hal0-embedder-switch.conf"
    assert es.NAMES.mirror == "journal.json"
    assert es.NAMES.gate_reason == write_gate.REASON_EMBEDDER_SWITCH
    assert es.journal_path() == em.journal_path(es.NAMES)
    assert es.journal_path().name == "memory-embedder-switch.json"


def test_actor_title_capitalises_only_the_first_letter() -> None:
    assert OTHER.actor_title == "The fold-in"
    assert es.NAMES.actor_title == "The switch"


def test_paths_come_from_the_names(tmp_path: Path) -> None:
    state = tmp_path / "var-lib" / "hal0" / "state"
    assert em.journal_path(OTHER) == state / "memory-fold-in.json"
    assert em.lock_path(OTHER) == state / "memory-maintenance.lock"
    assert em.isolation_drop_in_path(OTHER).name == "zzzz-hal0-memory-maintenance.conf"


def test_locate_journal_reads_the_primary_through_the_commands_parser() -> None:
    _write(em.journal_path(OTHER), run_id="r1", started_at="1", archive_dir="/a", phase="running")
    found = em.locate_journal(OTHER, _parse, _finished)
    assert found["journal"] == _Journal("r1", "1", "/a", "running")
    assert found["source"] == str(em.journal_path(OTHER)) and found["error"] is None


def test_locate_journal_ignores_a_finished_primary() -> None:
    _write(em.journal_path(OTHER), run_id="r1", started_at="1", archive_dir="/a", phase="done")
    assert em.locate_journal(OTHER, _parse, _finished) == {
        "journal": None,
        "source": None,
        "error": None,
    }


def test_locate_journal_falls_back_to_the_newest_unfinished_mirror_of_this_command() -> None:
    root = em.archive_root()
    em.journal_path(OTHER).parent.mkdir(parents=True, exist_ok=True)
    em.journal_path(OTHER).write_text("{not json")
    _write(root / "memory-fold-in-old" / "journal.json", run_id="a", started_at="1",
           archive_dir="/old", phase="running")  # fmt: skip
    _write(root / "memory-fold-in-new" / "journal.json", run_id="b", started_at="2",
           archive_dir="/new", phase="running")  # fmt: skip
    _write(root / "memory-fold-in-done" / "journal.json", run_id="c", started_at="3",
           archive_dir="/done", phase="done")  # fmt: skip
    # Another command's mirror is never taken for this one's.
    _write(root / "memory-embedder-switch-x" / "journal.json", run_id="d", started_at="9",
           archive_dir="/switch", phase="running")  # fmt: skip

    found = em.locate_journal(OTHER, _parse, _finished)

    assert found["journal"].run_id == "b"
    assert found["source"] == str(root / "memory-fold-in-new" / "journal.json")
    assert "is not a usable journal" in found["error"]
    assert em.unfinished_mirrors(OTHER, _parse, _finished) == [
        root / "memory-fold-in-new" / "journal.json",
        root / "memory-fold-in-old" / "journal.json",
    ]


def test_read_journal_file_reports_a_journal_its_parser_refuses(tmp_path: Path) -> None:
    path = tmp_path / "j.json"
    assert em.read_journal_file(path, _parse) == (None, None)
    path.write_text(json.dumps({"kind": "switch"}))
    journal, why = em.read_journal_file(path, _parse)
    assert journal is None and why == f"{path} is not a usable journal (not a fold-in journal)"


def test_lock_held_reads_the_commands_own_lock() -> None:
    assert em.lock_held(OTHER) is False
    path = em.lock_path(OTHER)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert em.lock_held(OTHER) is True
        assert em.lock_held(es.NAMES) is False
    finally:
        os.close(fd)


def test_isolation_drop_in_and_messages_name_the_command(tmp_path: Path) -> None:
    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, UNIT, "")

    path = tmp_path / OTHER.drop_in
    assert em.write_isolation_dropin(OTHER, 45123, "r1", runner=runner, path=path) == {
        "error": None,
        "exec_start": f"{HS} --host 127.0.0.1 --port 9177",
    }
    body = path.read_text()
    assert "# Written by `hal0 memory migrate fold-in` (run r1)." in body
    assert "# the fold-in can reach it" in body and "The fold-in removes" in body
    assert "# `hal0 memory migrate fold-in --status`." in body
    assert "switch" not in body
    assert em.isolated_port(OTHER, path) == 45123

    later = UNIT + f"# /etc/x.d/zzzzz.conf\n[Service]\nExecStart=\nExecStart={HS} --port 1\n"
    refusal = em.unit_refusals(OTHER, later, 9177)
    assert [r["code"] for r in refusal] == ["exec_start_overridden"]
    assert OTHER.drop_in in refusal[0]["message"]
    assert "the fold-in's private command" in refusal[0]["message"]
    assert "switch" not in refusal[0]["message"]


def test_the_drop_in_of_one_command_is_not_taken_for_the_units_command() -> None:
    ours = UNIT + f"# /etc/x.d/{OTHER.drop_in}\n[Service]\nExecStart=\nExecStart=/x --port 4\n"
    assert em.effective_exec_start(OTHER, ours) == f"{HS} --host 127.0.0.1 --port 9177"
    # To another command it is just a later-sorting drop-in that sets ExecStart.
    assert em.effective_exec_start(es.NAMES, ours) == "/x --port 4"


def test_the_engine_queues_refreshes_and_consolidation_by_itself() -> None:
    # The operation_type strings hindsight-api 1.2.0 stores (#3604).
    assert em.ENGINE_OPERATION_TYPES == (
        "refresh_mental_model",
        "consolidation",
        "vector_index_maintenance",
        "graph_maintenance",
    )
    # Only these embed, so only these mark a rolled-back store as mixed.
    assert em.ENGINE_EMBEDDING_TYPES == ("refresh_mental_model", "consolidation")
    assert set(em.ENGINE_EMBEDDING_TYPES) < set(em.ENGINE_OPERATION_TYPES)
    assert em.Timeouts().engine_ops_s > em.Timeouts().quiet_s


def test_a_parked_operation_is_due_at_its_next_retry_at() -> None:
    # hindsight-api 1.2.0 lists next_retry_at as a TIMESTAMPTZ isoformat.
    row = {"status": "pending", "next_retry_at": "2026-11-12T10:30:00+00:00"}
    assert em.parked_until(row) == datetime(2026, 11, 12, 10, 30, tzinfo=UTC)
    # A time without a zone is UTC; a row that is not pending is not parked.
    assert em.parked_until(row | {"next_retry_at": "2026-11-12T10:30:00"}) == datetime(
        2026, 11, 12, 10, 30, tzinfo=UTC
    )
    assert em.parked_until(row | {"status": "processing"}) is None
    assert em.parked_until({"status": "pending", "next_retry_at": None}) is None
    assert em.parked_until({"status": "pending", "next_retry_at": "soon"}) is None


@pytest.mark.parametrize(
    ("row", "wrote"),
    [
        ({"status": "completed"}, True),
        ({"status": "processing"}, True),
        # Consolidation commits each sub-batch before it fails or sees a cancel.
        ({"status": "failed"}, True),
        ({"status": "cancelled"}, True),
        # Queued, or parked before it started: retry_count stays 0.
        ({"status": "pending", "retry_count": 0}, False),
        ({"status": "pending", "next_retry_at": "2026-11-12T10:30:00+00:00"}, False),
        # An attempt raised or its worker died: it may have written first.
        ({"status": "pending", "retry_count": 1}, True),
        ({"status": "pending", "retry_count": "x"}, True),
        ({"status": "superseded"}, True),
    ],
)
def test_which_engine_rows_may_have_written(row: dict[str, Any], wrote: bool) -> None:
    assert em.may_have_written(row) is wrote


def test_an_operation_is_named_by_type_bank_id_and_status() -> None:
    # The list endpoint carries both names for the type and the id.
    listed = {
        "id": "a1",
        "operation_id": "a1",
        "task_type": "refresh_mental_model",
        "operation_type": "refresh_mental_model",
        "status": "processing",
    }
    older = {"id": "b2", "task_type": "consolidation", "status": "pending"}
    assert em.operation_type(listed) == "refresh_mental_model"
    assert em.operation_type(older) == "consolidation"
    assert em.operation_type({}) == "unknown"
    assert em.operations_text("shared", [listed, older]) == (
        "refresh_mental_model on shared (operation a1, processing), "
        "consolidation on shared (operation b2, pending)"
    )
