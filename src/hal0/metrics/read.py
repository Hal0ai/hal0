"""Read API (§21.3) -- thin queries over the OBS-1 tables.

Backs ``GET /api/stats`` + ``GET /api/system-stats`` + ``GET
/api/models/health`` (wired in ``api/routes/hardware.py``). Every
function here degrades gracefully rather than 500ing: a fresh install
whose migrations haven't run yet, or a box with ``[metrics].enabled =
false``, gets empty/zeroed shapes back -- this is the "native Performance
summary... works with the stack off" requirement (plan §13.1).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hal0.db.connection import connect
from hal0.slots.state import is_dispatchable_state

_WINDOW_TO_TIMEDELTA = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return ordered[idx]


def _safe_query(
    conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> list[sqlite3.Row]:
    """Run a SELECT, returning [] on any error (e.g. table not yet migrated)."""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def system_stats(db_path: Path | str | None = None) -> dict[str, Any]:
    """``GET /api/system-stats`` payload: latest fleet reading + per-slot latest samples."""
    out: dict[str, Any] = {"ts": None, "fleet": {}, "slots": []}
    with connect(db_path) as conn:
        fleet_rows = _safe_query(
            conn,
            "SELECT * FROM slot_sample WHERE slot_id = '__fleet__' ORDER BY ts DESC LIMIT 1",
        )
        if fleet_rows:
            row = fleet_rows[0]
            out["ts"] = row["ts"]
            out["fleet"] = {
                "gpu_util": row["gpu_util"],
                "vram_bytes": row["vram_bytes"],
                "gtt_bytes": row["gtt_bytes"],
                "power_w": row["power_w"],
                "temp_c": row["temp_c"],
            }

        # Latest sample per non-fleet slot -- SQLite has no DISTINCT ON, so
        # a correlated MAX(ts) subquery per slot_id does the job at the
        # (small) cardinality this table has.
        slot_rows = _safe_query(
            conn,
            "SELECT s.* FROM slot_sample s "
            "INNER JOIN ("
            "  SELECT slot_id, MAX(ts) AS max_ts FROM slot_sample "
            "  WHERE slot_id != '__fleet__' GROUP BY slot_id"
            ") latest ON s.slot_id = latest.slot_id AND s.ts = latest.max_ts",
        )
        out["slots"] = [
            {
                "slot_id": row["slot_id"],
                "state": row["state"],
                "vram_bytes": row["vram_bytes"],
                "gtt_bytes": row["gtt_bytes"],
                "ram_bytes": row["ram_bytes"],
                "inflight": row["inflight"],
                "kv_used": row["kv_used"],
                "ts": row["ts"],
            }
            for row in slot_rows
        ]
    return out


def stats_summary(
    db_path: Path | str | None = None,
    *,
    window: str = "1h",
    model_id: str | None = None,
    runner: str | None = None,
) -> dict[str, Any]:
    """``GET /api/stats`` payload: totals + per-(model,runner,device,modality) rollup."""
    delta = _WINDOW_TO_TIMEDELTA.get(window, _WINDOW_TO_TIMEDELTA["1h"])
    now = datetime.now(UTC)
    start = now - delta
    out: dict[str, Any] = {
        "window": {"from": start.isoformat(), "to": now.isoformat(), "bucket": window},
        "totals": {"requests": 0, "ok": 0, "errors": 0, "tokens_completed": 0},
        "by_model": [],
        "bench_baseline": {},
    }
    with connect(db_path) as conn:
        clauses = ["ts >= ?"]
        params: list[Any] = [start.isoformat()]
        if model_id:
            clauses.append("model_id = ?")
            params.append(model_id)
        if runner:
            clauses.append("runner = ?")
            params.append(runner)
        where = " AND ".join(clauses)

        rows = _safe_query(
            conn,
            f"SELECT model_id, runner, device, modality, ok, ttft_ms, prefill_tps, decode_tps, "
            f"spec_accept_rate, completion_tokens FROM request_metric WHERE {where}",
            tuple(params),
        )

        if rows:
            total_ok = sum(1 for r in rows if r["ok"])
            total_tokens = sum(r["completion_tokens"] or 0 for r in rows)
            out["totals"] = {
                "requests": len(rows),
                "ok": total_ok,
                "errors": len(rows) - total_ok,
                "tokens_completed": total_tokens,
            }

            grouped: dict[tuple, list[sqlite3.Row]] = {}
            for row in rows:
                key = (row["model_id"], row["runner"], row["device"], row["modality"])
                grouped.setdefault(key, []).append(row)

            by_model = []
            for (m_id, m_runner, m_device, m_modality), group in grouped.items():
                ttft = [r["ttft_ms"] for r in group if r["ttft_ms"] is not None]
                prefill = [r["prefill_tps"] for r in group if r["prefill_tps"] is not None]
                decode = [r["decode_tps"] for r in group if r["decode_tps"] is not None]
                spec_accept = [
                    r["spec_accept_rate"] for r in group if r["spec_accept_rate"] is not None
                ]
                ok_count = sum(1 for r in group if r["ok"])
                by_model.append(
                    {
                        "model_id": m_id,
                        "runner": m_runner,
                        "device": m_device,
                        "modality": m_modality,
                        "ttft_ms": {
                            "p50": _percentile(ttft, 0.50),
                            "p95": _percentile(ttft, 0.95),
                        },
                        "tps_decode": {
                            "avg": (sum(decode) / len(decode)) if decode else None,
                            "p50": _percentile(decode, 0.50),
                        },
                        "tps_prefill": {"avg": (sum(prefill) / len(prefill)) if prefill else None},
                        "spec_accept_rate": (
                            (sum(spec_accept) / len(spec_accept)) if spec_accept else None
                        ),
                        "ok": ok_count,
                        "errors": len(group) - ok_count,
                    }
                )
            out["by_model"] = by_model

        # Bench baselines are independent of live request traffic in the
        # window -- a freshly-installed box with no requests yet still has
        # a baseline from its on-install bench run (plan §13.4).
        baseline_rows = _safe_query(
            conn,
            "SELECT model_id, runner, hw_hash, tps_decode, ttft_ms, ts FROM bench_run "
            "WHERE baseline = 1",
        )
        out["bench_baseline"] = {
            f"{r['model_id']} x {r['runner']} x hw:{r['hw_hash']}": {
                "tps_decode": r["tps_decode"],
                "ttft_ms": r["ttft_ms"],
                "captured": r["ts"],
            }
            for r in baseline_rows
        }
    return out


def models_health(
    slots: list[Any],
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """``GET /api/models/health`` payload -- one row per dispatchable slot.

    ``slots`` is the live ``list[Slot]`` from ``SlotManager.list()`` (the
    caller supplies it so this module has no FastAPI/app-state coupling).
    24h TTFT/decode-tps come from ``request_metric``; everything else from
    the slot snapshot itself.
    """
    rows_out: list[dict[str, Any]] = []
    with connect(db_path) as conn:
        since = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
        for slot in slots:
            metric_rows = _safe_query(
                conn,
                "SELECT ttft_ms, decode_tps FROM request_metric WHERE slot_id = ? AND ts >= ?",
                (slot.name, since),
            )
            ttft = [r["ttft_ms"] for r in metric_rows if r["ttft_ms"] is not None]
            decode = [r["decode_tps"] for r in metric_rows if r["decode_tps"] is not None]

            metadata = getattr(slot, "metadata", None) or {}
            state_value = getattr(slot.state, "value", str(slot.state))
            last_use = None
            last_used_at = getattr(slot, "last_used_at", None)
            if last_used_at is not None:
                with_tz = datetime.fromtimestamp(last_used_at, tz=UTC)
                last_use = with_tz.isoformat()

            rows_out.append(
                {
                    "checkpoint": slot.model_id,
                    "last_use": last_use,
                    "type": metadata.get("type"),
                    "device": metadata.get("device"),
                    "pinned": metadata.get("pinned"),
                    "recipe": metadata.get("profile") or metadata.get("recipe"),
                    "pid": metadata.get("pid"),
                    "recipe_options": metadata.get("recipe_options"),
                    "backend_url": (
                        f"http://127.0.0.1:{slot.port}" if getattr(slot, "port", 0) else None
                    ),
                    "health_ok": is_dispatchable_state(state_value),
                    "ttft_ms_p50_24h": _percentile(ttft, 0.50),
                    "tps_decode_p50_24h": _percentile(decode, 0.50),
                }
            )
    return {"models": rows_out}


def requests_rollup(
    db_path: Path | str | None = None,
    *,
    window_s: int = 60,
) -> dict[str, Any]:
    """``GET /api/stats/requests`` payload: near-real-time throughput/latency.

    Short window (default 60s) -- the dashboard's live "requests" card, not
    the longer ``stats_summary()`` rollup. ``endpoints`` is keyed by resolved
    ``model_id``: ``request_metric`` carries no per-HTTP-route ``path``
    column (only ``model_id``/``runner``/``device``/``modality``, and the
    latter three land NULL until the modalities-split lane populates them --
    see ``002_metrics.sql``), so ``model_id`` is the one real per-request
    dimension available today. Never fabricated: with zero rows in the
    window, counts are honest zeros and percentiles are ``null`` (no samples
    to compute over).
    """
    now = datetime.now(UTC)
    start = now - timedelta(seconds=window_s)
    out: dict[str, Any] = {
        "window_s": window_s,
        "req_per_min": 0.0,
        "p50_ms": None,
        "p95_ms": None,
        "endpoints": [],
        "errors": 0,
        "dedupe": False,
    }
    with connect(db_path) as conn:
        rows = _safe_query(
            conn,
            "SELECT model_id, total_ms, ok FROM request_metric WHERE ts >= ?",
            (start.isoformat(),),
        )
    if rows:
        total = len(rows)
        out["req_per_min"] = round(total / (window_s / 60), 2)
        latencies = [r["total_ms"] for r in rows if r["total_ms"] is not None]
        out["p50_ms"] = _percentile(latencies, 0.50)
        out["p95_ms"] = _percentile(latencies, 0.95)
        out["errors"] = sum(1 for r in rows if not r["ok"])

        counts: dict[str, int] = {}
        for r in rows:
            key = r["model_id"] or "unknown"
            counts[key] = counts.get(key, 0) + 1
        out["endpoints"] = [
            {"path": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        ]
    return out


__all__ = ["models_health", "requests_rollup", "stats_summary", "system_stats"]
