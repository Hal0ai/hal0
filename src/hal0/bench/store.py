"""store.py — the result store (DESIGN §3.1): append-only records.jsonl (source
of truth) + a derived, disposable SQLite index.

WHY this shape: ``records.jsonl`` is the authoritative log — append-only,
human-greppable, trivially rsync-able off-box, and impossible to corrupt with a
partial write of an *earlier* record (we only ever append). ``bench.db`` is a
pure index rebuilt from the JSONL by ``reindex()``; it is never authoritative
and can be deleted at any time (DESIGN §14.1 explicitly leaves "SQLite vs
in-memory JSON scan" open — keeping the DB derived means that decision never
touches the source of truth).

The store owns its state root: ``$HAL0_BENCH_STATE`` (or legacy ``$BENCHLAB_STATE``; default
``/var/lib/hal0-bench``), separate from hal0's ``/var/lib/hal0/benchmarks``, per
the out-of-tree contract (DESIGN preamble). Layout mirrors DESIGN §3.1:

    $HAL0_BENCH_STATE/
      records.jsonl        # append-only schema-2 records
      bench.db             # derived SQLite index (reindex target)
      artifacts/<run_id>/  # raw engine output per run
      roster.json          # latest published roster snapshot (publish.py writes)
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .schema import Record

DEFAULT_STATE_ROOT = "/var/lib/hal0-bench"


def state_root() -> Path:
    """Resolve the state root: ``$BENCHLAB_STATE`` or the default. Read live (not
    a module constant) so tests can point it at a tmp dir per-call."""
    return Path(
        os.environ.get("HAL0_BENCH_STATE", os.environ.get("BENCHLAB_STATE", DEFAULT_STATE_ROOT))
    )


class Store:
    """Handle onto one state root. Cheap to construct; holds no open resources
    between calls (each DB op opens/closes its own connection) so it is safe to
    build one per CLI invocation."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else state_root()
        self.records_path = self.root / "records.jsonl"
        self.db_path = self.root / "bench.db"
        self.artifacts_root = self.root / "artifacts"

    # -- filesystem helpers -------------------------------------------------- #

    def ensure_dirs(self) -> None:
        """Create the state root + artifacts dir. Idempotent; called before any
        write so a fresh box (or fresh tmp dir in tests) just works."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)

    def artifacts_dir(self, run_id: str) -> Path:
        """The per-run artifacts dir (raw llama-bench JSON, server_ab JSON, cell
        logs, telemetry.jsonl). Created on demand; the record's ``artifacts``
        field stores the path relative to the state root."""
        d = self.artifacts_root / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- append (source of truth) ------------------------------------------- #

    def append_record(self, record: Record | dict[str, Any]) -> None:
        """Append one record as a single canonical JSON line. Accepts a Record
        or an already-plain dict (import-v1 hands us dicts). One line per
        record, newline-terminated — the append is the commit."""
        self.ensure_dirs()
        payload = record.to_dict() if isinstance(record, Record) else record
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self.records_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def iter_records(self) -> Iterator[dict[str, Any]]:
        """Stream every record as a plain dict, in append (chronological) order.
        Blank/corrupt lines are skipped rather than fatal — a torn last line
        from a killed process must not make the whole history unreadable."""
        if not self.records_path.exists():
            return
        with self.records_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    # -- reindex (derived) --------------------------------------------------- #

    def reindex(self) -> int:
        """Rebuild ``bench.db`` from records.jsonl from scratch and return the
        number of records indexed.

        Two objects:
          * table ``records`` — one row per record, with the columns the CLI/API
            filter on (cell_key, model, lane, kind, depth, outcome, ts, hal0
            version) plus the full record JSON in ``raw``.
          * view ``current_cells`` — per cell_key, the newest ``ok`` record. This
            is the "current value" contract (DESIGN §3: newest ok record per key
            is current, older ones are history). A VIEW, not a table, so it can
            never drift from ``records``.

        Rebuilt from scratch every time (drop + recreate) so the DB is always a
        pure function of the JSONL — never patched incrementally, never
        authoritative.
        """
        self.ensure_dirs()
        # Fresh DB every reindex: it is derived + disposable.
        if self.db_path.exists():
            self.db_path.unlink()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE records (
                    rowid_seq   INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id      TEXT,
                    cell_key    TEXT,
                    suite       TEXT,
                    trigger     TEXT,
                    model_id    TEXT,
                    lane        TEXT,
                    kind        TEXT,
                    depth       INTEGER,
                    outcome     TEXT,
                    decode_ts_med REAL,
                    hal0_version  TEXT,
                    ts          TEXT,
                    raw         TEXT
                );
                CREATE INDEX idx_cell_key ON records(cell_key);
                CREATE INDEX idx_model ON records(model_id);
                CREATE INDEX idx_outcome ON records(outcome);
                """
            )
            n = 0
            for rec in self.iter_records():
                identity = rec.get("identity", {})
                model = identity.get("model", {})
                workload = identity.get("workload", {})
                summary = rec.get("summary", {})
                host = rec.get("host", {})
                conn.execute(
                    "INSERT INTO records (run_id, cell_key, suite, trigger, model_id, "
                    "lane, kind, depth, outcome, decode_ts_med, hal0_version, ts, raw) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        rec.get("run_id"),
                        rec.get("cell_key"),
                        rec.get("suite"),
                        rec.get("trigger"),
                        model.get("id"),
                        identity.get("lane"),
                        workload.get("kind"),
                        workload.get("depth"),
                        rec.get("outcome"),
                        summary.get("decode_ts_med"),
                        host.get("hal0_version"),
                        _record_ts(rec),
                        json.dumps(rec, separators=(",", ":"), ensure_ascii=False),
                    ),
                )
                n += 1
            # current_cells: newest ok record per cell_key. "Newest" is APPEND
            # order (rowid_seq is AUTOINCREMENT in insertion = chronological
            # order), matching newest_ok_by_cell()'s source-of-truth definition
            # ("later ok records overwrite earlier ones"). MAX(run_id) is wrong:
            # run_id is "<UTC-stamp>-<random hex>", so two ok records that share a
            # wall-clock second (v1 imports, fast re-measures) tie-break on the
            # RANDOM suffix and can surface an OLDER record as current.
            conn.executescript(
                """
                CREATE VIEW current_cells AS
                SELECT r.* FROM records r
                JOIN (
                    SELECT cell_key, MAX(rowid_seq) AS newest_ok
                    FROM records WHERE outcome = 'ok'
                    GROUP BY cell_key
                ) w ON r.rowid_seq = w.newest_ok;
                """
            )
            conn.commit()
            return n
        finally:
            conn.close()

    # -- query helpers (used by the CLI) ------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def newest_ok_by_cell(self) -> dict[str, dict[str, Any]]:
        """Map cell_key -> the full current (newest ok) record, read straight
        from records.jsonl (no DB required).

        This is the planner's staleness input (§6), so it deliberately does NOT
        depend on bench.db being reindexed — a stale/missing DB must never make
        the planner think everything is stale. Later ok records overwrite
        earlier ones because iter_records yields in append order.
        """
        current: dict[str, dict[str, Any]] = {}
        for rec in self.iter_records():
            if rec.get("outcome") == "ok" and rec.get("cell_key"):
                current[rec["cell_key"]] = rec
        return current

    def results(
        self,
        model: str | None = None,
        since: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Current-value rows for `benchlab results`, from the current_cells view.
        Optional model filter (substring) and ISO ``since`` lower bound on ts."""
        if not self.db_path.exists():
            self.reindex()
        conn = self._connect()
        try:
            sql = "SELECT * FROM current_cells WHERE 1=1"
            params: list[Any] = []
            if model:
                sql += " AND model_id LIKE ?"
                params.append(f"%{model}%")
            if since:
                sql += " AND ts >= ?"
                params.append(since)
            sql += " ORDER BY ts DESC LIMIT ?"
            params.append(limit)
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def history(
        self,
        cell_key: str | None = None,
        model: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Time-ordered ok records for a cell (trend line) or a whole model, for
        `benchlab history`. Oldest-first so a caller can draw a sparkline."""
        if not self.db_path.exists():
            self.reindex()
        conn = self._connect()
        try:
            sql = "SELECT * FROM records WHERE outcome = 'ok'"
            params: list[Any] = []
            if cell_key:
                sql += " AND cell_key = ?"
                params.append(cell_key)
            if model:
                sql += " AND model_id LIKE ?"
                params.append(f"%{model}%")
            sql += " ORDER BY ts ASC LIMIT ?"
            params.append(limit)
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()


def _record_ts(rec: dict[str, Any]) -> str:
    """Best-effort ISO timestamp for a record. run_id is a UTC stamp + suffix
    (``2026-07-05T03:12:44Z-a1b2c3``), so its leading stamp is a sortable ts;
    fall back to the whole run_id if the shape is unexpected."""
    run_id = rec.get("run_id") or ""
    return run_id.split("Z-")[0] + "Z" if "Z-" in run_id else run_id
