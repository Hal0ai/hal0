"""test_bundle.py — bundle = a faithful, selective package of ok records.

Selection contract: only outcome=="ok" records; filterable by run_id set,
suite, and ISO `since` lower bound (record ts derives from run_id, same rule
as store._record_ts). Redaction contract: host.name never leaves the box
unless explicitly requested.
"""

from __future__ import annotations

from hal0.bench.bundle import BundleSpec, select_records
from hal0.bench.store import Store


def _rec(run_id: str, outcome: str = "ok", suite: str = "roster", model: str = "m1") -> dict:
    return {
        "run_id": run_id,
        "cell_key": f"sha256:{model}-{run_id}",
        "suite": suite,
        "trigger": "manual",
        "identity": {
            "model": {"id": model},
            "lane": "rocm",
            "workload": {"kind": "tg", "depth": 2048},
        },
        "host": {"name": "my-secret-hostname", "gpu": "Strix Halo", "hal0_version": "1.0"},
        "outcome": outcome,
        "summary": {"decode_ts_med": 42.0},
        "schema": 2,
    }


def _store(tmp_path, monkeypatch, records) -> Store:
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    store = Store()
    for r in records:
        store.append_record(r)
    return store


def test_select_only_ok_records(tmp_path, monkeypatch):
    store = _store(
        tmp_path,
        monkeypatch,
        [
            _rec("2026-08-01T00:00:00Z-aaa111"),
            _rec("2026-08-01T00:00:01Z-bbb222", outcome="failed"),
            _rec("2026-08-01T00:00:02Z-ccc333", outcome="oom"),
        ],
    )
    got = select_records(store, BundleSpec())
    assert [r["run_id"] for r in got] == ["2026-08-01T00:00:00Z-aaa111"]


def test_select_by_run_ids(tmp_path, monkeypatch):
    store = _store(
        tmp_path,
        monkeypatch,
        [_rec("2026-08-01T00:00:00Z-aaa111"), _rec("2026-08-02T00:00:00Z-bbb222")],
    )
    spec = BundleSpec(run_ids=["2026-08-02T00:00:00Z-bbb222"])
    got = select_records(store, spec)
    assert [r["run_id"] for r in got] == ["2026-08-02T00:00:00Z-bbb222"]


def test_select_by_suite_and_since(tmp_path, monkeypatch):
    store = _store(
        tmp_path,
        monkeypatch,
        [
            _rec("2026-07-01T00:00:00Z-old111", suite="roster"),
            _rec("2026-08-05T00:00:00Z-new222", suite="roster"),
            _rec("2026-08-05T00:00:00Z-new333", suite="smoke"),
        ],
    )
    got = select_records(store, BundleSpec(suite="roster", since="2026-08-01"))
    assert [r["run_id"] for r in got] == ["2026-08-05T00:00:00Z-new222"]
