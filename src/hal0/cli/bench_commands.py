#!/usr/bin/env python3
"""
`hal0 bench` CLI — the single operator/agent surface for benchmarking.

Everything below the CLI is scripts (deterministic, testable).
"""

import argparse
import json
import sys
from pathlib import Path

# Add installer/bench to path
BENCH_DIR = Path(__file__).parent.parent.parent.parent / "installer" / "bench"
sys.path.insert(0, str(BENCH_DIR))

from planner import plan  # noqa: E402
from runner import run_worklist  # noqa: E402
from v2_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    DEFAULT_RECORDS_PATH,
    DEFAULT_V2_DIR,
    count_records,
    ensure_v2_dir,
    get_trend,
    search_records,
)


def cmd_plan(args):
    """Show what's stale and needs running."""
    worklist = plan(suite_id=args.suite)
    if args.json:
        print(json.dumps(worklist, indent=2, default=str))
    else:
        print(f"Planned {len(worklist)} cells")
        for item in worklist[:10]:
            print(
                f"  {item['cell']['model']} / {item['cell']['lane']} / "
                f"{item['cell']['kind']} / {item['cell']['depth']} "
                f"({item['stale_reason']})"
            )
        if len(worklist) > 10:
            print(f"  ... and {len(worklist) - 10} more")


def cmd_run(args):
    """Execute the plan (or a slice of it)."""
    worklist = plan(suite_id=args.suite)
    if not worklist:
        print("No stale cells to run.")
        return

    results = run_worklist(
        worklist=worklist,
        exclusive=not args.no_exclusive,
        budget_min=args.budget_min,
        dry_run=args.dry_run,
    )

    print(f"\n{'=' * 60}")
    print(
        f"Results: {results['ok']} ok, {results['failed']} failed, "
        f"{results['skipped']} skipped, {results['total']} total"
    )


def cmd_status(args):
    """Show live status: current cell, queue, ETA."""
    ensure_v2_dir()
    if not DEFAULT_RECORDS_PATH.exists():
        print("No records yet.")
        return

    # Count records
    total = count_records()
    print(f"Total records: {total}")
    print(f"Store: {DEFAULT_V2_DIR}")
    print(f"Records: {DEFAULT_RECORDS_PATH}")
    print(f"DB: {DEFAULT_DB_PATH}")

    # Show recent records
    records = search_records(limit=5)
    if records:
        print("\nRecent records:")
        for row in records:
            print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]}")


def cmd_results(args):
    """Query bench.db for results."""
    ensure_v2_dir()
    records = search_records(
        cell_key=args.cell_key,
        suite=args.suite,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(records, indent=2))
    else:
        for row in records:
            print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]}")


def cmd_history(args):
    """Show trend for a cell/model over time."""
    ensure_v2_dir()
    trend = get_trend(cell_key=args.cell_key, limit=args.limit)
    if args.json:
        print(json.dumps(trend, indent=2, default=str))
    else:
        for row in trend:
            print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]}")


def cmd_reindex(args):
    """Rebuild bench.db from records.jsonl."""
    ensure_v2_dir()
    # Rebuild by reading all records
    import json
    import sqlite3

    from v2_store import DEFAULT_DB_PATH, DEFAULT_RECORDS_PATH

    db_path = DEFAULT_DB_PATH
    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TABLE IF EXISTS records")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            run_id TEXT PRIMARY KEY,
            suite TEXT,
            trigger TEXT,
            cell_key TEXT,
            outcome TEXT,
            summary TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_records_cell_key ON records(cell_key)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_records_suite ON records(suite)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_records_trigger ON records(trigger)
    """)

    if DEFAULT_RECORDS_PATH.exists():
        with open(DEFAULT_RECORDS_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    if row.get("schema") == 2:
                        conn.execute(
                            "INSERT INTO records (run_id, suite, trigger, cell_key, "
                            "outcome, summary) VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                row.get("run_id"),
                                row.get("suite"),
                                row.get("trigger"),
                                row.get("cell_key"),
                                row.get("outcome"),
                                json.dumps(row.get("summary", {})),
                            ),
                        )
                except (json.JSONDecodeError, KeyError):
                    pass
    conn.commit()
    conn.close()
    # Count from SQLite now
    import sqlite3

    conn2 = sqlite3.connect(str(db_path))
    count = conn2.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    conn2.close()
    print(f"Reindexed {count} records into {db_path}")


def cmd_publish(args):
    """Regenerate roster.json (+ docs data file)."""
    ensure_v2_dir()
    roster_path = DEFAULT_V2_DIR / "roster.json"

    # Build roster from latest ok records
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
        run_id, suite, _trigger, cell_key, outcome, summary = row
        if outcome != "ok":
            continue
        try:
            summary_data = json.loads(summary) if summary else {}
            roster["models"].append(
                {
                    "run_id": run_id,
                    "suite": suite,
                    "cell_key": cell_key,
                    "summary": summary_data,
                }
            )
        except (json.JSONDecodeError, TypeError):
            pass

    with open(roster_path, "w") as f:
        json.dump(roster, f, indent=2, default=str)

    print(f"Published roster to {roster_path}")
    print(f"  {len(roster['models'])} models")


def cmd_import_v1(args):
    """One-time: import v1 index.json + server-ab/*.json → v2 records."""
    ensure_v2_dir()
    v1_index = DEFAULT_V2_DIR.parent / "index.json"
    if not v1_index.exists():
        print(f"v1 index not found: {v1_index}")
        return

    import json

    with open(v1_index) as f:
        index = json.load(f)

    records_written = 0
    for record in index.get("records", []):
        # Convert v1 record to v2 format
        v2_record = {
            "schema": 2,
            "run_id": f"imported-{record.get('model', {}).get('name', 'unknown')}-{record.get('backend', 'unknown')}",
            "suite": "imported",
            "trigger": "import-v1",
            "cell_key": record.get("cell_key", ""),
            "model": record.get("model", {}),
            "engine": record.get("engine", {}),
            "lane": record.get("lane", ""),
            "config": record.get("config", {}),
            "workload": record.get("workload", {}),
            "host": record.get("host", {}),
            "reps": record.get("reps", []),
            "summary": record.get("summary", {}),
            "outcome": record.get("outcome", "ok"),
            "artifacts": "",
        }
        from v2_store import write_record

        write_record(v2_record)
        records_written += 1

    print(f"Imported {records_written} v1 records into v2 store")


def main():
    parser = argparse.ArgumentParser(prog="hal0 bench", description="Benchmarks CLI for hal0")
    subparsers = parser.add_subparsers(dest="command")

    # plan
    plan_parser = subparsers.add_parser("plan", help="Show what's stale and needs running")
    plan_parser.add_argument("--suite", help="Suite ID to plan for")
    plan_parser.add_argument("--json", action="store_true", help="JSON output")
    plan_parser.set_defaults(func=cmd_plan)

    # run
    run_parser = subparsers.add_parser("run", help="Execute the plan")
    run_parser.add_argument("--suite", help="Suite ID to run")
    run_parser.add_argument("--no-exclusive", action="store_true")
    run_parser.add_argument("--budget-min", type=int, default=240)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(func=cmd_run)

    # status
    status_parser = subparsers.add_parser("status", help="Show live status")
    status_parser.set_defaults(func=cmd_status)

    # results
    results_parser = subparsers.add_parser("results", help="Query bench.db")
    results_parser.add_argument("--cell-key", dest="cell_key", help="Cell key to filter")
    results_parser.add_argument("--suite", help="Suite to filter")
    results_parser.add_argument("--limit", type=int, default=10)
    results_parser.add_argument("--json", action="store_true")
    results_parser.set_defaults(func=cmd_results)

    # history
    history_parser = subparsers.add_parser("history", help="Trend for a cell/model")
    history_parser.add_argument("--cell-key", dest="cell_key", required=True)
    history_parser.add_argument("--limit", type=int, default=5)
    history_parser.add_argument("--json", action="store_true")
    history_parser.set_defaults(func=cmd_history)

    # reindex
    reindex_parser = subparsers.add_parser("reindex", help="Rebuild bench.db from records.jsonl")
    reindex_parser.set_defaults(func=cmd_reindex)

    # publish
    publish_parser = subparsers.add_parser("publish", help="Regenerate roster.json")
    publish_parser.add_argument("--check", action="store_true", help="Diff without writing")
    publish_parser.set_defaults(func=cmd_publish)

    # import-v1
    import_parser = subparsers.add_parser("import-v1", help="Import v1 records into v2")
    import_parser.set_defaults(func=cmd_import_v1)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
