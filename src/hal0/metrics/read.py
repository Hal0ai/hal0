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

# window -> (bucket count, bucket width in seconds); count * width == window length.
_WINDOW_TO_BUCKETS = {"1h": (24, 150), "24h": (24, 3600), "7d": (21, 28800)}


# window -> (bucket count, bucket width in seconds) for slot_activity(); the
# activity charts on the NPU band and the Overview draw these 60 buckets.
_ACTIVITY_WINDOWS = {"5m": (60, 5), "15m": (60, 15), "1h": (60, 60)}
_ACTIVITY_DEFAULT_WINDOW = "15m"


def _parse_ts(raw: Any) -> datetime | None:
    """Parse a stored ISO timestamp; naive values are taken as UTC."""
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
    # Unknown window names fall back to 1h everywhere (delta, bucket layout).
    window_key = window if window in _WINDOW_TO_TIMEDELTA else "1h"
    delta = _WINDOW_TO_TIMEDELTA[window_key]
    bucket_count, bucket_s = _WINDOW_TO_BUCKETS[window_key]
    now = datetime.now(UTC)
    start = now - delta
    out: dict[str, Any] = {
        "window": {"from": start.isoformat(), "to": now.isoformat(), "bucket": window},
        "totals": {"requests": 0, "ok": 0, "errors": 0, "tokens_completed": 0},
        "by_model": [],
        "bench_baseline": {},
        # Zero-filled, oldest first; the last bucket ends at window.to.
        "buckets": [
            {
                "ts": (start + timedelta(seconds=i * bucket_s)).isoformat(),
                "requests": 0,
                "tokens_completed": 0,
            }
            for i in range(bucket_count)
        ],
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
            f"SELECT ts, model_id, runner, device, modality, ok, ttft_ms, prefill_tps, decode_tps, "
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

            for row in rows:
                row_ts = _parse_ts(row["ts"])
                if row_ts is None:
                    continue
                idx = int((row_ts - start).total_seconds() // bucket_s)
                bucket = out["buckets"][min(max(idx, 0), bucket_count - 1)]
                bucket["requests"] += 1
                bucket["tokens_completed"] += row["completion_tokens"] or 0

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


def slot_activity(
    db_path: Path | str | None = None,
    *,
    window: str = _ACTIVITY_DEFAULT_WINDOW,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``GET /api/stats/activity`` payload: per-slot load and fleet duty in equal buckets.

    ``window`` is ``5m`` | ``15m`` | ``1h`` (anything else reads as ``15m``);
    each is 60 buckets. Per slot, from ``request_metric``:

      - ``tps``: token rate per bucket. Each request's completion tokens are
        spread evenly over its generation time (``total_ms`` minus
        ``ttft_ms``, ending at the row's ``ts``) and only the part inside the
        window is charted. A request still streaming appears once it ends.
      - ``requests`` / ``errors`` / ``ttft_p50_ms``: over requests that ended
        in the window; ``tokens`` is their completion total.

    ``fleet.gpu_util`` / ``fleet.npu_util`` are the mean of the sampler's
    ``__fleet__`` rows per bucket, ``None`` where no reading landed.
    Unmigrated or empty DB: empty buckets, never a 500.
    """
    if window not in _ACTIVITY_WINDOWS:
        window = _ACTIVITY_DEFAULT_WINDOW
    buckets, bucket_s = _ACTIVITY_WINDOWS[window]
    span_s = buckets * bucket_s
    end_dt = now if now is not None else datetime.now(UTC)
    start_dt = end_dt - timedelta(seconds=span_s)
    w0 = start_dt.timestamp()

    def _bucket(t: float) -> int:
        return min(buckets - 1, max(0, int((t - w0) // bucket_s)))

    with connect(db_path) as conn:
        request_rows = _safe_query(
            conn,
            "SELECT ts, slot_id, completion_tokens, ttft_ms, total_ms, ok FROM request_metric "
            "WHERE ts >= ? AND slot_id IS NOT NULL",
            (start_dt.isoformat(),),
        )
        fleet_rows = _safe_query(
            conn,
            "SELECT ts, gpu_util, npu_util FROM slot_sample "
            "WHERE slot_id = '__fleet__' AND ts >= ?",
            (start_dt.isoformat(),),
        )

    token_bins: dict[str, list[float]] = {}
    stats: dict[str, dict[str, Any]] = {}
    for row in request_rows:
        ended = _parse_ts(row["ts"])
        if ended is None:
            continue
        end = ended.timestamp()
        if end < w0:
            continue
        slot = row["slot_id"]
        bins = token_bins.setdefault(slot, [0.0] * buckets)
        st = stats.setdefault(slot, {"requests": 0, "errors": 0, "tokens": 0, "ttft": []})
        st["requests"] += 1
        if not row["ok"]:
            st["errors"] += 1
        if row["ttft_ms"] is not None:
            st["ttft"].append(float(row["ttft_ms"]))
        tokens = row["completion_tokens"] or 0
        if tokens <= 0:
            continue
        st["tokens"] += tokens
        total_s = (row["total_ms"] or 0.0) / 1000.0
        ttft_s = (row["ttft_ms"] or 0.0) / 1000.0
        gen_s = total_s - ttft_s if 0.0 < ttft_s < total_s else total_s
        if gen_s <= 0.0:
            bins[_bucket(end)] += tokens
            continue
        gen_start = end - gen_s
        per_s = tokens / gen_s
        for b in range(_bucket(gen_start), _bucket(end) + 1):
            lo = max(gen_start, w0 + b * bucket_s)
            hi = min(end, w0 + (b + 1) * bucket_s)
            if hi > lo:
                bins[b] += per_s * (hi - lo)

    slots_out: dict[str, Any] = {}
    for slot, bins in token_bins.items():
        st = stats[slot]
        tps = [round(v / bucket_s, 2) for v in bins]
        slots_out[slot] = {
            "tps": tps,
            "peak_tps": max(tps),
            "tokens": st["tokens"],
            "requests": st["requests"],
            "errors": st["errors"],
            "ttft_p50_ms": _percentile(st["ttft"], 0.50),
        }

    sums: dict[str, list[float]] = {"gpu_util": [0.0] * buckets, "npu_util": [0.0] * buckets}
    counts: dict[str, list[int]] = {"gpu_util": [0] * buckets, "npu_util": [0] * buckets}
    for row in fleet_rows:
        sampled = _parse_ts(row["ts"])
        if sampled is None or sampled.timestamp() < w0:
            continue
        b = _bucket(sampled.timestamp())
        for key in ("gpu_util", "npu_util"):
            if row[key] is not None:
                sums[key][b] += float(row[key])
                counts[key][b] += 1
    fleet = {
        key: [
            round(sums[key][b] / counts[key][b], 3) if counts[key][b] else None
            for b in range(buckets)
        ]
        for key in sums
    }

    return {
        "window": window,
        "window_s": span_s,
        "bucket_s": bucket_s,
        "buckets": buckets,
        "end": end_dt.isoformat(),
        "fleet": fleet,
        "slots": slots_out,
    }
