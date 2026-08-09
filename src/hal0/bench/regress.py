"""regress.py — regression detection (DESIGN §11).

Cheap and dumb on purpose (no ML, runs at session end): for each cell with
enough history, compare the newest ok record's governing metric against the
trailing median of the last few, and flag a drop past a threshold — but ONLY
when nothing is *known* to have changed. A provenance change is a different
cell_key (schema.py), so within a single cell_key's history the identity is
already constant by construction; the extra provenance-equality guard here
catches the environment axis (host/hal0 version/image) that is intentionally NOT
in the key but can still explain a step, so a hal0 upgrade or image rebuild is
annotated (dashboard vertical marker, §8/§11) rather than flagged as a
regression.

Governing metric: ``summary.decode_ts_med`` (higher is better). A regression is
a DECREASE, so we flag when the new value is worse (lower) than the trailing
median by more than ``THRESHOLD_PCT``.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Any

from hal0.activity import AuditStore
from hal0.board.store import BoardStore
from hal0.config.paths import activity_db

from .store import Store, _record_ts

THRESHOLD_PCT = 10.0  # DESIGN §11: flag if worse by >10%
TRAILING_N = 5  # DESIGN §11: trailing median of the last 5
MIN_HISTORY = 3  # DESIGN §11: needs ≥3 historical ok records


@dataclass
class Flag:
    """One flagged cell (DESIGN §11 output → ``bench.regression`` journal event
    + board task when >2 flag in a session)."""

    cell_key: str
    model_id: str
    delta_pct: float  # signed: negative = slower than trailing median
    newest_ts: str | None  # ISO stamp of the flagged (newest) record
    trailing_median: float | None
    run_ids: list[str]  # [previous_run_id, newest_run_id]


def _provenance_key(rec: dict[str, Any]) -> tuple:
    """The environment provenance that, if unchanged, makes a drop a genuine
    regression (DESIGN §11 "provenance equals the previous record's"). cell_key
    already pins model/engine/config/workload identity, so the only remaining
    axis is the host environment: a hal0/kernel/rocm/image change is an
    *explained* step, not a regression."""
    host = rec.get("host") or {}
    engine = (rec.get("identity") or {}).get("engine") or {}
    return (
        host.get("hal0_version"),
        host.get("kernel"),
        host.get("rocm"),
        engine.get("image_digest"),
    )


def _metric(rec: dict[str, Any]) -> float | None:
    return (rec.get("summary") or {}).get("decode_ts_med")


def _median(values: list[float]) -> float | None:
    import statistics

    vals = [v for v in values if isinstance(v, (int, float))]
    return statistics.median(vals) if vals else None


def check(store: Store) -> list[Flag]:
    """Scan every cell_key's ok history and return the flagged regressions
    (DESIGN §11). Runs at session end over the whole store — cheap enough that
    it does not need to be scoped to the session's cells."""
    # Group ok records by cell_key in chronological (append) order.
    by_cell: dict[str, list[dict[str, Any]]] = {}
    for rec in store.iter_records():
        if rec.get("outcome") != "ok" or not rec.get("cell_key"):
            continue
        by_cell.setdefault(rec["cell_key"], []).append(rec)

    flags: list[Flag] = []
    for key, history in by_cell.items():
        if len(history) < MIN_HISTORY:
            continue
        newest = history[-1]
        previous = history[-2]
        new_val = _metric(newest)
        if new_val is None:
            continue

        # Trailing median of the last TRAILING_N records *before* the newest.
        prior = history[-(TRAILING_N + 1) : -1]
        baseline = _median([m for m in (_metric(r) for r in prior) if m is not None])
        if baseline is None or baseline <= 0:
            continue

        delta_pct = 100.0 * (new_val - baseline) / baseline
        if delta_pct >= -THRESHOLD_PCT:
            continue  # not worse by more than the threshold

        # Only a regression if nothing known changed vs the previous record.
        if _provenance_key(newest) != _provenance_key(previous):
            continue  # explained by an env/provenance step — annotate, don't flag

        model_id = (newest.get("identity") or {}).get("model", {}).get("id") or ""
        flags.append(
            Flag(
                cell_key=key,
                model_id=model_id,
                delta_pct=round(delta_pct, 1),
                newest_ts=_record_ts(newest) or None,
                trailing_median=round(baseline, 2),
                run_ids=[previous.get("run_id"), newest.get("run_id")],
            )
        )
    return flags


#: Board task fires only when a session's flag count exceeds this — a lone
#: flag is noise-tolerant (DESIGN §11), a cluster is worth an operator's eye.
BOARD_TASK_THRESHOLD = 2


def journal_flags(flags: list[Flag], suite_id: str) -> None:
    """Emit ONE durable ``bench.regression`` event for a session's flags, and
    (when more than :data:`BOARD_TASK_THRESHOLD` flag) an Operator Board task.

    Uses hal0's OWN established durable-journal mechanism —
    :class:`hal0.activity.AuditStore`, the same SQLite audit trail every other
    subsystem's structural events land in (``slot.state``, ``pull.*``,
    ``system.*`` — see that module + ``hal0.events.EventBus``'s ``sink``
    hook). This module runs CLI-side (``hal0 bench run``/``worker``), off
    hal0-api's FastAPI event loop, so it cannot reach ``app.state.events``;
    ``AuditStore``/``BoardStore`` are both plain, dependency-free classes a
    standalone process can open directly against the SAME on-disk store
    (``hal0.config.paths.activity_db`` / the default board db — both
    HAL0_HOME-aware, so tests never touch a real box's state; module-level
    names, not lazy imports, so the test suite can inject a fake journal the
    same way ``harness.BENCHCTL`` is monkeypatched elsewhere in this package).

    Journaling is diagnostic, never load-bearing: any failure here is logged
    to stderr and swallowed, never raised — a broken audit/board write must
    not turn an otherwise-successful bench session into a failure.
    """
    if not flags:
        return

    detail = [
        {
            "cell_key": f.cell_key,
            "model_id": f.model_id,
            "delta_pct": f.delta_pct,
            "trailing_median": f.trailing_median,
            "run_ids": f.run_ids,
        }
        for f in flags
    ]
    message = f"{len(flags)} cell(s) regressed in suite {suite_id!r}"

    try:
        store = AuditStore(activity_db())
        store.init_schema()
        asyncio.run(
            store.record(
                kind="event",
                category="bench",
                action="bench.regression",
                target=suite_id,
                actor="system",
                severity="warn",
                message=message,
                after={"suite": suite_id, "flags": detail},
            )
        )
    except Exception as exc:  # journaling must never break the session
        print(f"[regress] journal write failed: {exc!r}", file=sys.stderr)

    if len(flags) <= BOARD_TASK_THRESHOLD:
        return
    try:
        BoardStore().create_task(
            {
                "title": f"bench regression: {len(flags)} cell(s) in {suite_id!r}",
                "body": "\n".join(
                    f"- {f.model_id} {f.cell_key[:16]} {f.delta_pct}% vs {f.trailing_median}"
                    for f in flags
                ),
                "status": "triage",
                "created_by": "bench",
            }
        )
    except Exception as exc:  # a board write must never break the session either
        print(f"[regress] board task creation failed: {exc!r}", file=sys.stderr)
