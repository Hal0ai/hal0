#!/usr/bin/env python3
"""
v2 result store — append-only records.jsonl + SQLite index + artifacts.

This is the NEW result store that the new runner writes to. The v1 store
(/var/lib/hal0/benchmarks/runs/*.json) is kept for backward compatibility
and imported into v2 once via `hal0 bench import-v1`.
"""

import json
import os
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_RESULT_DIR = Path("/var/lib/hal0/benchmarks")
DEFAULT_V2_DIR = DEFAULT_RESULT_DIR / "v2"
DEFAULT_DB_PATH = DEFAULT_V2_DIR / "bench.db"
DEFAULT_RECORDS_PATH = DEFAULT_V2_DIR / "records.jsonl"


def ensure_v2_dir():
    """Create the v2 store directory if it doesn't exist."""
    DEFAULT_V2_DIR.mkdir(parents=True, exist_ok=True)


def make_run_id() -> str:
    """Generate a unique run_id: timestamp + random suffix."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    suffix = hashlib.sha256(os.urandom(16)).hexdigest()[:6]
    return f"{ts}-{suffix}"


def compute_cell_key(record: dict) -> str:
    """
    Compute a deterministic hash of the identity block.
    Two runs with the same cell_key measured the same thing.
    """
    identity = {
        "model_id": record.get("model", {}).get("id"),
        "model_sha": record.get("model", {}).get("gguf_sha256"),
        "engine_kind": record.get("engine", {}).get("kind"),
        "engine_image": record.get("engine", {}).get("image"),
        "engine_image_digest": record.get("engine", {}).get("image_digest"),
        "llamacpp_build": record.get("engine", {}).get("llamacpp_build"),
        "decode_tune": record.get("engine", {}).get("decode_tune"),
        "lane": record.get("lane"),
        "config_argv": sorted(record.get("config", {}).get("argv", [])),
        "config_env": sorted(record.get("config", {}).get("env", {}).items()),
        "config_kv": record.get("config", {}).get("kv", {}),
        "config_spec": record.get("config", {}).get("spec"),
        "config_parallel": record.get("config", {}).get("parallel"),
        "workload_kind": record.get("workload", {}).get("kind"),
        "workload_depth": record.get("workload", {}).get("depth"),
        "workload_n_prompt": record.get("workload", {}).get("n_prompt"),
        "workload_n_gen": record.get("workload", {}).get("n_gen"),
        "workload_sampler": record.get("workload", {}).get("sampler"),
        "workload_concurrency": record.get("workload", {}).get("concurrency"),
    }
    raw = json.dumps(identity, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def write_record(record: dict, records_path: Optional[Path] = None) -> str:
    """Append a single record to records.jsonl and return run_id."""
    if records_path is None:
        records_path = DEFAULT_RECORDS_PATH
    ensure_v2_dir()
    records_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = make_run_id()
    record["run_id"] = run_id
    record["schema"] = 2
    with open(records_path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return run_id


def append_record(record: dict, run_id: Optional[str] = None) -> dict:
    """
    Append a record and optionally update the SQLite index.
    Returns the written record.
    """
    if run_id is None:
        run_id = make_run_id()
    record["run_id"] = run_id
    record["schema"] = 2
    write_record(record)
    _refresh_sqlite(record)
    return record


def _refresh_sqlite(record: dict):
    """Update the SQLite index with a new record."""
    records_path = DEFAULT_RECORDS_PATH
    if not records_path.exists():
        return
    db_path = DEFAULT_DB_PATH
    ensure_v2_dir()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    _init_sqlite(conn)
    conn.execute("DELETE FROM records")
    with open(records_path) as f:
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
                        )
                    )
            except (json.JSONDecodeError, KeyError):
                pass
    conn.commit()
    conn.close()


def _init_sqlite(conn: sqlite3.Connection):
    """Create the SQLite schema."""
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
    conn.commit()


def search_records(
    cell_key: Optional[str] = None,
    suite: Optional[str] = None,
    trigger: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
) -> list:
    """Query the SQLite index for matching records."""
    ensure_v2_dir()
    db_path = DEFAULT_DB_PATH
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        query = "SELECT * FROM records"
        params = []
        filters = []
        if cell_key:
            filters.append("cell_key = ?")
            params.append(cell_key)
        if suite:
            filters.append("suite = ?")
            params.append(suite)
        if trigger:
            filters.append("trigger = ?")
            params.append(trigger)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY run_id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cur = conn.execute(query, params)
        return cur.fetchall()
    finally:
        conn.close()


def count_records(
    cell_key: Optional[str] = None,
    suite: Optional[str] = None,
) -> int:
    """Count matching records."""
    records = search_records(cell_key=cell_key, suite=suite, limit=0, offset=0)
    return len(records)


def get_latest_record(cell_key: str) -> Optional[dict]:
    """Get the most recent ok record for a cell."""
    records = search_records(cell_key=cell_key, limit=1)
    if not records:
        return None
    row = records[0]
    # Reconstruct full record — SQLite stores only summary fields
    return row  # Note: full record not stored in SQLite, just index


def get_trend(
    cell_key: str,
    limit: int = 5,
) -> list:
    """Get the latest N records for a cell (for regression detection)."""
    records = search_records(cell_key=cell_key, limit=limit, offset=0)
    return records


if __name__ == "__main__":
    # Self-test
    ensure_v2_dir()
    print("v2 store ready:", DEFAULT_V2_DIR)
    print("Records path:", DEFAULT_RECORDS_PATH)
    print("DB path:", DEFAULT_DB_PATH)
