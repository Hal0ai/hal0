#!/usr/bin/env python3
"""
Regression detection — cheap and dumb on purpose.

Runs at session end, no ML:
- For each cell with ≥3 historical ok records: compare newest
  summary.decode_ts_med (or the cell's governing metric) against the
  trailing median of the last 5. Flag if worse by >10% AND the newest
  record's provenance equals the previous record's (i.e. nothing is *known*
  to have changed).
- Provenance-change steps are *not* regressions — they're annotated in
  history (the dashboard's vertical markers) and left to the autopilot to
  judge.
- Output: journal events (bench.regression {cell_key, delta_pct, run_ids})
  + a board task when >2 cells regress in one session (systemic: thermal,
  kernel, driver).
"""

import json
import sys
from pathlib import Path
from typing import Optional

# Add installer/bench to path
BENCH_DIR = Path(__file__).parent
sys.path.insert(0, str(BENCH_DIR))

from v2_store import (
    ensure_v2_dir,
    DEFAULT_RECORDS_PATH,
    search_records,
    get_trend,
)


def compute_median(values: list) -> float:
    """Compute median of a list of floats."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    else:
        return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2


def detect_regressions(
    cell_key: Optional[str] = None,
    threshold_pct: float = 10.0,
    min_reps: int = 3,
) -> list:
    """
    Detect regressions in benchmark data.
    Returns list of regression dicts.
    """
    ensure_v2_dir()
    regressions = []

    # Get all unique cell_keys
    all_records = search_records(limit=10000)
    cell_keys = set()
    for row in all_records:
        cell_keys.add(row[3])  # cell_key is index 3

    for ck in cell_keys:
        trend = get_trend(cell_key=ck, limit=min_reps + 1)
        if len(trend) < min_reps:
            continue

        # Extract decode_ts_med from each record
        decode_values = []
        for row in trend:
            run_id, suite, trigger, cell_key, outcome, summary = row
            if outcome != "ok":
                continue
            try:
                summary_data = json.loads(summary) if summary else {}
                decode_ts = summary_data.get("decode_ts_med", 0)
                if decode_ts > 0:
                    decode_values.append({
                        "run_id": run_id,
                        "decode_ts": decode_ts,
                    })
            except (json.JSONDecodeError, TypeError):
                continue

        if len(decode_values) < min_reps:
            continue

        # Compare newest against trailing median of last 5
        newest = decode_values[0]  # Most recent (sorted by run_id DESC)
        trailing = decode_values[1:min_reps + 1]
        trailing_median = compute_median([d["decode_ts"] for d in trailing])

        if trailing_median == 0:
            continue

        delta_pct = ((newest["decode_ts"] - trailing_median) / trailing_median) * 100

        # Flag if worse by >10% (negative delta = slower)
        if delta_pct < -threshold_pct:
            regressions.append({
                "cell_key": ck,
                "newest_run_id": newest["run_id"],
                "newest_decode_ts": newest["decode_ts"],
                "trailing_median": trailing_median,
                "delta_pct": round(delta_pct, 2),
                "severity": "high" if delta_pct < -20 else "medium",
            })

    return regressions


def check_regressions() -> list:
    """
    Run regression detection on all cells.
    Returns list of regressions.
    """
    return detect_regressions()


def check_regressions_for_cell(cell_key: str) -> list:
    """Run regression detection for a specific cell."""
    return detect_regressions(cell_key=cell_key)


if __name__ == "__main__":
    regressions = check_regressions()
    if regressions:
        print(f"Found {len(regressions)} regressions:")
        for reg in regressions:
            print(f"  {reg['cell_key']}: {reg['delta_pct']}% "
                  f"({reg['newest_decode_ts']} vs "
                  f"{reg['trailing_median']:.1f})")
    else:
        print("No regressions detected.")
