"""test_store.py — the result store's "current value" contract (DESIGN §3.1).

The store defines a cell's *current value* as its newest ok record. Two code
paths compute this and they MUST agree:

  * ``newest_ok_by_cell`` (the planner's staleness input) reads records.jsonl in
    append order — "later ok records overwrite earlier ones" — so the newest is
    the last-appended ok record for a key.
  * the ``current_cells`` SQL view (what ``results()`` / the dashboard show).

They diverged when two ok records for one cell_key shared a wall-clock second:
run_id is ``<UTC-stamp>-<random hex>`` and the view picked ``MAX(run_id)``, whose
tie-break is the RANDOM hex suffix — so the view could surface an OLDER record as
"current" while the planner (append order) correctly saw the newer one. Same-second
ties are real for v1 imports (many rows share one source timestamp) and fast
re-measures.
"""

from __future__ import annotations

from hal0.bench.store import Store


def _ok_rec(run_id: str, decode: float) -> dict:
    return {
        "run_id": run_id,
        "cell_key": "sha256:abc",
        "suite": "s",
        "trigger": "t",
        "identity": {
            "model": {"id": "m"},
            "lane": "rocm",
            "workload": {"kind": "tg", "depth": 0},
        },
        "host": {"hal0_version": "1"},
        "outcome": "ok",
        "summary": {"decode_ts_med": decode},
    }


def test_current_cells_newest_is_append_order_not_run_id_lexicographic(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    store = Store()

    # Same wall-clock second. Appended A (older) THEN B (newer), but A's random
    # run_id suffix sorts AFTER B's — so MAX(run_id) would wrongly pick A.
    store.append_record(_ok_rec("2026-07-05T00:00:00Z-zzzzzz", 10.0))  # older
    store.append_record(_ok_rec("2026-07-05T00:00:00Z-aaaaaa", 99.0))  # newest

    store.reindex()
    rows = store.results()
    assert len(rows) == 1
    # The current value must be the newest (last-appended) ok record...
    assert rows[0]["decode_ts_med"] == 99.0
    # ...and it must AGREE with the planner's own source-of-truth notion.
    planner_current = store.newest_ok_by_cell()["sha256:abc"]["summary"]["decode_ts_med"]
    assert planner_current == 99.0
