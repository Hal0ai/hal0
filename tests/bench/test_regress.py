"""test_regress.py — regression detection math (DESIGN §11) + the
``bench.regression`` journal/board wiring (Phase 4), against FAKED
AuditStore and a faked ``api_post`` (no real audit db or API touched)."""

from __future__ import annotations

from typing import ClassVar

from hal0.bench import regress
from hal0.bench.regress import BOARD_TASK_THRESHOLD, Flag, journal_flags
from hal0.bench.schema import (
    Config,
    Engine,
    Host,
    Identity,
    Model,
    Outcome,
    Record,
    Summary,
    Workload,
)
from hal0.bench.store import Store


def _flag(i: int = 0) -> Flag:
    return Flag(
        cell_key=f"sha256:cell{i}",
        model_id=f"m{i}",
        delta_pct=-15.0,
        newest_ts="2026-08-01T00:00:00Z",
        trailing_median=100.0,
        run_ids=["prev", "newest"],
    )


class FakeAuditStore:
    """Records every constructor call + every ``record()`` call — the module-
    level ``regress.AuditStore`` name is monkeypatched to this class, mirroring
    how the rest of this package fakes seams (``harness.BENCHCTL`` etc)."""

    instances: ClassVar[list[FakeAuditStore]] = []

    def __init__(self, path):
        self.path = path
        self.schema_inited = False
        self.records: list[dict] = []
        FakeAuditStore.instances.append(self)

    def init_schema(self) -> None:
        self.schema_inited = True

    async def record(self, **kwargs) -> int:
        self.records.append(kwargs)
        return len(self.records)


class ExplodingAuditStore(FakeAuditStore):
    def init_schema(self) -> None:
        raise RuntimeError("db locked")


class FakeBoardApi:
    """Stands in for ``hal0.cli._shared.api_post``: records each board POST."""

    calls: ClassVar[list[tuple[str, dict]]] = []

    def __call__(self, path: str, *, json: dict | None = None, **_: object) -> dict:
        FakeBoardApi.calls.append((path, json or {}))
        return {"task": json}


class ExplodingBoardApi(FakeBoardApi):
    def __call__(self, path: str, **_: object) -> dict:
        raise RuntimeError("api down")


def _patch_board(monkeypatch, api) -> None:
    monkeypatch.setattr("hal0.cli._shared.api_post", api)


def _reset():
    FakeAuditStore.instances = []
    FakeBoardApi.calls = []


class TestJournalFlags:
    def test_no_flags_writes_nothing(self, monkeypatch):
        _reset()
        monkeypatch.setattr(regress, "AuditStore", FakeAuditStore)
        _patch_board(monkeypatch, FakeBoardApi())

        journal_flags([], "roster")

        assert FakeAuditStore.instances == []
        assert FakeBoardApi.calls == []

    def test_emits_one_audit_event_summarising_every_flag(self, monkeypatch):
        _reset()
        monkeypatch.setattr(regress, "AuditStore", FakeAuditStore)
        _patch_board(monkeypatch, FakeBoardApi())
        flags = [_flag(0), _flag(1)]

        journal_flags(flags, "roster")

        [audit] = FakeAuditStore.instances
        assert audit.schema_inited
        [rec] = audit.records
        assert rec["action"] == "bench.regression"
        assert rec["category"] == "bench"
        assert rec["target"] == "roster"
        assert rec["severity"] == "warn"
        assert len(rec["after"]["flags"]) == 2
        assert rec["after"]["flags"][0]["cell_key"] == "sha256:cell0"

    def test_board_task_only_above_threshold(self, monkeypatch):
        _reset()
        monkeypatch.setattr(regress, "AuditStore", FakeAuditStore)
        _patch_board(monkeypatch, FakeBoardApi())

        at_threshold = [_flag(i) for i in range(BOARD_TASK_THRESHOLD)]
        journal_flags(at_threshold, "roster")
        assert FakeBoardApi.calls == []  # <= threshold: no task

        _reset()
        monkeypatch.setattr(regress, "AuditStore", FakeAuditStore)
        _patch_board(monkeypatch, FakeBoardApi())
        above_threshold = [_flag(i) for i in range(BOARD_TASK_THRESHOLD + 1)]
        journal_flags(above_threshold, "roster")
        [(path, task)] = FakeBoardApi.calls
        assert path == "/api/board/tasks"
        assert set(task) == {"title", "body", "triage"}
        assert task["triage"] is True
        assert str(BOARD_TASK_THRESHOLD + 1) in task["title"]

    def test_audit_failure_never_raises_and_board_task_still_attempted(self, monkeypatch):
        _reset()
        monkeypatch.setattr(regress, "AuditStore", ExplodingAuditStore)
        _patch_board(monkeypatch, FakeBoardApi())
        flags = [_flag(i) for i in range(BOARD_TASK_THRESHOLD + 1)]

        journal_flags(flags, "roster")  # must not raise

        assert FakeBoardApi.calls  # board path still ran despite the audit failure

    def test_board_failure_never_raises(self, monkeypatch):
        _reset()
        monkeypatch.setattr(regress, "AuditStore", FakeAuditStore)
        _patch_board(monkeypatch, ExplodingBoardApi())
        flags = [_flag(i) for i in range(BOARD_TASK_THRESHOLD + 1)]

        journal_flags(flags, "roster")  # must not raise

        [audit] = FakeAuditStore.instances
        assert audit.records  # the audit path still completed


class TestRegressionCheck:
    """A couple of DESIGN §11 sanity checks that predate Phase 4 but had no
    dedicated test module yet."""

    def _ok_record(self, decode_ts, run_id, hal0_version="1.0.0"):
        return Record(
            run_id=run_id,
            suite="t",
            trigger="manual",
            identity=Identity(
                model=Model(id="m1"),
                engine=Engine(),
                lane="rocm",
                config=Config(),
                workload=Workload(),
            ),
            host=Host(hal0_version=hal0_version),
            outcome=Outcome.OK,
            summary=Summary(decode_ts_med=decode_ts),
        ).to_dict()

    def test_flags_a_drop_past_threshold_with_stable_provenance(self, tmp_path):
        store = Store(tmp_path)
        for i, val in enumerate([100.0, 100.0, 100.0, 100.0]):
            store.append_record(self._ok_record(val, f"r{i}"))
        store.append_record(self._ok_record(50.0, "r-new"))  # >10% worse

        flags = regress.check(store)

        assert len(flags) == 1
        assert flags[0].model_id == "m1"
        assert flags[0].delta_pct < 0

    def test_no_flag_when_provenance_changed(self, tmp_path):
        store = Store(tmp_path)
        for i, val in enumerate([100.0, 100.0, 100.0, 100.0]):
            store.append_record(self._ok_record(val, f"r{i}"))
        # hal0_version bumped on the newest record — an explained step, not a regression.
        store.append_record(self._ok_record(50.0, "r-new", hal0_version="1.0.1"))

        flags = regress.check(store)

        assert flags == []
