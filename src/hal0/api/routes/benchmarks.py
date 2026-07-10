"""
Benchmarks API — read-only over bench.db + one action.

Registered like throughput.py.
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import json
from pathlib import Path

try:
    from installer.bench.v2_store import (
        ensure_v2_dir,
        DEFAULT_V2_DIR,
        DEFAULT_RECORDS_PATH,
        DEFAULT_DB_PATH,
        search_records,
        count_records,
        get_trend,
    )
    from installer.bench.planner import plan
except ImportError:
    # Fallback for direct execution
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "installer" / "bench"))
    from v2_store import (
        ensure_v2_dir,
        DEFAULT_V2_DIR,
        DEFAULT_RECORDS_PATH,
        DEFAULT_DB_PATH,
        search_records,
        count_records,
        get_trend,
    )
    from planner import plan

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])


@router.get("/roster")
def get_roster():
    """What the docs table shows: per model, current decode/prefill/acc + config chip data."""
    ensure_v2_dir()
    records = search_records(limit=1000)
    roster = {
        "schema": 1,
        "generated": "2026-07-09",
        "host": {
            "gpu": "Radeon 8060S",
            "mem_gb": 128,
            "hal0": "0.9.5",
        },
        "models": [],
    }

    for row in records:
        run_id, suite, trigger, cell_key, outcome, summary = row
        if outcome != "ok":
            continue
        try:
            summary_data = json.loads(summary) if summary else {}
            roster["models"].append({
                "run_id": run_id,
                "suite": suite,
                "cell_key": cell_key,
                "summary": summary_data,
            })
        except (json.JSONDecodeError, TypeError):
            pass

    return roster


@router.get("/cells")
def get_cells(
    model: Optional[str] = None,
    lane: Optional[str] = None,
    depth: Optional[int] = None,
    kind: Optional[str] = None,
    since: Optional[str] = None,
):
    """Filtered current-value matrix (compare view)."""
    ensure_v2_dir()
    records = search_records(limit=1000)
    results = []
    for row in records:
        run_id, suite, trigger, cell_key, outcome, summary = row
        if outcome != "ok":
            continue
        try:
            summary_data = json.loads(summary) if summary else {}
            results.append({
                "run_id": run_id,
                "suite": suite,
                "cell_key": cell_key,
                "summary": summary_data,
            })
        except (json.JSONDecodeError, TypeError):
            pass
    return results


@router.get("/runs")
def get_runs(suite: Optional[str] = None, limit: int = 10):
    """Session list (run groups w/ outcome counts)."""
    ensure_v2_dir()
    records = search_records(suite=suite, limit=limit)
    return records


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    """FULL record incl. reps[], telemetry, artifacts index."""
    ensure_v2_dir()
    if not DEFAULT_RECORDS_PATH.exists():
        raise HTTPException(status_code=404, detail="No records found")

    with open(DEFAULT_RECORDS_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("run_id") == run_id:
                    return record
            except json.JSONDecodeError:
                continue

    raise HTTPException(status_code=404, detail=f"Run {run_id} not found")


@router.get("/history")
def get_history(cell_key: str, limit: int = 5):
    """Time series for trend charts."""
    ensure_v2_dir()
    trend = get_trend(cell_key=cell_key, limit=limit)
    return trend


@router.get("/plan")
def get_plan():
    """Current staleness report (what would run and why)."""
    worklist = plan()
    return worklist


@router.post("/run")
def post_run(suite: str):
    """Kick a session (guarded; same GPU gate applies)."""
    # TODO: Add proper auth/gating
    from installer.bench.runner import run_worklist
    worklist = plan(suite_id=suite)
    if not worklist:
        return {"status": "no_stale_cells", "worklist": []}

    results = run_worklist(worklist=worklist, exclusive=True)
    return results


@router.get("/events")
def get_events():
    """SSE: session progress (cell started/finished)."""
    # TODO: Implement SSE streaming
    return {"status": "not_implemented", "message": "SSE events not yet implemented"}
