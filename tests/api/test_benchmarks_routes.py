"""test_benchmarks_routes.py — GET /api/benchmarks/runs row shape.

The dashboard's Benchmarks accordion plots decode AND prefill trend lines
from this run-list row (RunSummary on the frontend) — it must carry
``prefill_ts_med`` alongside ``decode_ts_med``, sourced from the same
``record["summary"]`` the way decode already is. Missing prefill here
silently dropped the accordion's prefill series (#benchmarks lane-color
follow-up).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hal0.bench.store import Store


def _ok_rec(run_id: str, model_id: str, decode: float, prefill: float | None) -> dict:
    return {
        "run_id": run_id,
        "cell_key": f"{model_id}|rocm|tg|2048|default",
        "suite": "roster",
        "trigger": "manual",
        "config": "default",
        "identity": {
            "model": {"id": model_id},
            "lane": "rocm",
            "workload": {"kind": "tg", "depth": 2048},
        },
        "host": {"hal0_version": "1.0.0-rc.3"},
        "outcome": "ok",
        "summary": {"decode_ts_med": decode, "prefill_ts_med": prefill},
        "reps": [{"decode_ts": decode}],
    }


def test_run_summary_row_includes_prefill_ts_med(isolated_client: TestClient) -> None:
    store = Store()
    store.append_record(_ok_rec("2026-08-07T09:00:00Z-abc123", "qwen3.6-35b-a3b", 71.4, 812.3))

    resp = isolated_client.get("/api/benchmarks/runs", params={"model": "qwen3.6-35b-a3b"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    row = body["runs"][0]
    assert row["decode_ts_med"] == 71.4
    # The field this test guards — must mirror decode_ts_med's sourcing
    # (record["summary"]["prefill_ts_med"]), not be silently dropped.
    assert row["prefill_ts_med"] == 812.3


def test_run_summary_row_prefill_ts_med_is_none_when_absent(isolated_client: TestClient) -> None:
    """A record with no prefill measurement (e.g. a pp-only or failed sweep)
    must serialize prefill_ts_med as null, not omit the key or raise."""
    store = Store()
    store.append_record(_ok_rec("2026-08-07T09:05:00Z-def456", "qwen3.6-35b-a3b", 70.0, None))

    resp = isolated_client.get("/api/benchmarks/runs", params={"model": "qwen3.6-35b-a3b"})
    assert resp.status_code == 200
    row = resp.json()["runs"][0]
    assert row["prefill_ts_med"] is None
