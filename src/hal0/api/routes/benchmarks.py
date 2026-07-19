"""Benchmarks API — /api/benchmarks/* over the hal0.bench result store.

Endpoint shapes per the design (docs/archive/handoffs/benchmark-system-design-2026-07-05.md
§7), ported in-tree from the benchlab lab repo's web/app.py on 2026-07-10.

Zoom levels served (design §8):
  1. roster board      -> GET /roster, GET /plan
  2. model detail      -> GET /cells, GET /history
  3. run-detail drawer -> GET /runs, GET /runs/{run_id}
Quality tier:          -> GET /evals
Actions (queue-based — this process NEVER drives the GPU inline; the
`hal0 bench worker` service drains the queue under the GPU gate):
  GET/POST /queue, DELETE /queue/{id}, POST /control, POST /run

Handlers are deliberately ``def`` (not ``async def``) so FastAPI runs them in
the threadpool: they do blocking file IO and, for the registry, a localhost
HTTP call back into this same API — which must not run on the event loop.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from hal0.api.middleware.error_codes import BadRequest, NotFound
from hal0.bench import control, evalrun
from hal0.bench.planner import (
    DEFAULT_API,
    _is_tier_a_incompatible,
    _model_caps,
    fetch_registry_models,
    plan,
)
from hal0.bench.publish import build_roster
from hal0.bench.store import Store
from hal0.bench.suites import load_suites

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])

# Suite directory (design §4). Mirrors hal0.bench.cli.SUITE_DIR — kept as its
# own constant so this module doesn't import argparse-only code; keep in sync.
SUITE_DIR = Path(
    os.environ.get("HAL0_BENCH_SUITE_DIR")
    or os.environ.get("BENCHLAB_SUITE_DIR")
    or "/etc/hal0/bench/suites"
)


def _api_base() -> str:
    """The hal0-api base the bench library should call back into for the
    registry/hardware/slots. Env-overridable for non-default ports."""
    return os.environ.get("HAL0_BENCH_API") or DEFAULT_API


def _store() -> Store:
    """One Store per request. Cheap to construct (Store docstring) — no open
    resources held between calls."""
    return Store()


def _run_summary(rec: dict[str, Any]) -> dict[str, Any]:
    """Flatten a raw records.jsonl record to the run-list row shape. The full
    record (identity, reps[], telemetry, ...) is what GET /runs/{run_id}
    returns — this is deliberately the thin version for the list view."""
    identity = rec.get("identity") or {}
    model = identity.get("model") or {}
    workload = identity.get("workload") or {}
    summary = rec.get("summary") or {}
    return {
        "run_id": rec.get("run_id"),
        "suite": rec.get("suite"),
        "trigger": rec.get("trigger"),
        "model": model.get("id"),
        "lane": identity.get("lane"),
        "kind": workload.get("kind"),
        "depth": workload.get("depth"),
        "outcome": rec.get("outcome"),
        "decode_ts_med": summary.get("decode_ts_med"),
        "reps": len(rec.get("reps") or []),
        "config": rec.get("config") or "default",
    }


# --------------------------------------------------------------------------- #
# Zoom level 1: roster board (design §8.1)
# --------------------------------------------------------------------------- #


@router.get("/roster")
def get_roster() -> dict[str, Any]:
    """Per-model current decode/prefill/acc + config chip data.

    The core is the roster.json contract (design §9.1) via the same
    `publish.build_roster` the CLI uses. On top, the board (not the published
    file) wants a few DISPLAY-only fields, so they are merged here rather than
    bloating roster.json: per-model ``runs`` count + ``last_run`` date (from a
    store scan) and a friendly ``name`` + ``hf_repo`` (from the live registry,
    best-effort — a down registry just omits them). Every INSTALLED,
    benchmarkable registry model with no records yet is appended as an
    "unmeasured" row so the board is the full hal0 model roster."""
    api = _api_base()
    store = _store()
    roster = build_roster(store)

    # Count runs + newest run per PHYSICAL model (gguf basename), so a model
    # with records under both a registry id and a v1 path-like id tallies
    # together — matching how build_roster collapses the board (one row per file).
    def _canon(model: dict[str, Any]) -> str:
        gguf = model.get("gguf") or ""
        return gguf.rsplit("/", 1)[-1] or model.get("id") or ""

    counts: dict[str, int] = {}
    last: dict[str, str] = {}
    for rec in store.iter_records():
        canon = _canon((rec.get("identity") or {}).get("model") or {})
        if not canon:
            continue
        counts[canon] = counts.get(canon, 0) + 1
        rid = rec.get("run_id") or ""
        if rid > last.get(canon, ""):
            last[canon] = rid

    # Index the registry by id AND by gguf path/basename: v1-imported roster
    # ids are path-like and don't match registry ids, but their gguf DOES.
    reg_by_id: dict[str, Any] = {}
    reg_by_file: dict[str, Any] = {}
    try:
        for m in fetch_registry_models(api):
            if m.get("id"):
                reg_by_id[m["id"]] = m
            path = m.get("path") or ""
            if path:
                reg_by_file[path] = m
                reg_by_file[path.rsplit("/", 1)[-1]] = m
    except (URLError, OSError, ValueError):
        pass

    for m in roster["models"]:
        gguf = m.get("gguf") or ""
        canon = gguf.rsplit("/", 1)[-1] or m["id"]
        r = reg_by_id.get(m["id"]) or reg_by_file.get(gguf) or reg_by_file.get(canon) or {}
        m["name"] = r.get("name")
        m["hf_repo"] = r.get("hf_repo")
        m["runs"] = counts.get(canon, 0)
        m["last_run"] = (last.get(canon) or "")[:10] or (m.get("detail") or {}).get("measured")
        m["measured"] = True

    present = {(m.get("gguf") or m["id"]).rsplit("/", 1)[-1] for m in roster["models"]}
    for reg_model in reg_by_id.values():
        if not reg_model.get("installed"):
            continue
        if _is_tier_a_incompatible(reg_model):  # non-gguf / embed / rerank
            continue
        path = reg_model.get("path") or ""
        base = path.rsplit("/", 1)[-1]
        if base in present or reg_model["id"] in present:
            continue
        present.add(base)
        sz = int(reg_model.get("size_bytes", 0) or 0)
        roster["models"].append(
            {
                "id": reg_model["id"],
                "gguf": path,
                "decode_ts": None,
                "prefill_ts": None,
                "accept": None,
                "caps": sorted(_model_caps(reg_model)),
                "spec": None,
                "kv": None,
                "size_gb": round(sz / 1e9, 1) or None,
                "detail": None,
                "name": reg_model.get("name"),
                "hf_repo": reg_model.get("hf_repo"),
                "runs": 0,
                "last_run": None,
                "measured": False,
            }
        )
    return roster


@router.get("/plan")
def get_plan(suite: str | None = None) -> dict[str, Any]:
    """Current staleness report: what would run and why (design §6; the board's
    Plan pill). The registry fetch degrades to an empty candidate set rather
    than failing the whole dashboard load."""
    api = _api_base()
    store = _store()
    suites = load_suites(SUITE_DIR)
    if suite:
        if suite not in suites:
            raise NotFound(
                f"unknown suite {suite!r} (looked in {SUITE_DIR})",
                code="bench.unknown_suite",
            )
        target_suites = [suites[suite]]
    else:
        target_suites = list(suites.values())

    registry_error: str | None = None
    try:
        models = fetch_registry_models(api)
    except (URLError, OSError, ValueError) as exc:
        models = []
        registry_error = str(exc)

    cells: list[dict[str, Any]] = []
    for s in target_suites:
        for c in plan(s, models, store):
            cells.append(
                {
                    "cell_key": c.cell_key,
                    "suite": c.suite_id,
                    "model": c.model_id,
                    "lane": c.lane,
                    "kind": c.kind,
                    "depth": c.depth,
                    "reason": c.reason,
                    "priority": c.priority,
                    "exclusive": c.exclusive,
                }
            )
    return {
        "suite": suite,
        "suites_considered": [s.id for s in target_suites],
        "stale_count": len(cells),
        "cells": cells,
        "registry_error": registry_error,
    }


# --------------------------------------------------------------------------- #
# Zoom level 2: model detail (design §8.2)
# --------------------------------------------------------------------------- #


@router.get("/cells")
def get_cells(
    model: str | None = None,
    lane: str | None = None,
    depth: int | None = None,
    kind: str | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    """Filtered current-value matrix (compare view) — the per-lane x per-depth
    mini-matrix. `Store.results()` (the `current_cells` view: newest ok record
    per cell_key) only takes model/since/limit — lane/depth/kind are filtered
    here in Python; fine at today's record counts."""
    store = _store()
    rows = store.results(model=model, since=since, limit=1000)
    if lane:
        rows = [r for r in rows if r.get("lane") == lane]
    if depth is not None:
        rows = [r for r in rows if r.get("depth") == depth]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    for r in rows:
        raw = r.pop("raw", None)
        if raw:
            r["record"] = json.loads(raw)
    return {"count": len(rows), "cells": rows}


@router.get("/history")
def get_history(cell_key: str | None = None, model: str | None = None) -> dict[str, Any]:
    """Time series for trend charts (decode t/s over time)."""
    if not cell_key and not model:
        raise BadRequest("cell_key or model is required", code="bench.missing_filter")
    store = _store()
    rows = store.history(cell_key=cell_key, model=model)
    # Trend charts need ts/decode_ts_med (+ prefill) per point; drop the bulky
    # raw record JSON but pull prefill out of it first (the records table only
    # indexes decode; prefill lives in the record).
    points = []
    for r in rows:
        raw = r.get("raw")
        prefill = None
        if raw:
            try:
                prefill = (json.loads(raw).get("summary") or {}).get("prefill_ts_med")
            except (ValueError, TypeError):
                prefill = None
        p = {k: v for k, v in r.items() if k != "raw"}
        p["prefill_ts_med"] = prefill
        points.append(p)
    return {"cell_key": cell_key, "model": model, "points": points}


# --------------------------------------------------------------------------- #
# Zoom level 3: run detail drawer (design §8.3)
# --------------------------------------------------------------------------- #


@router.get("/runs")
def list_runs(
    suite: str | None = None, model: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Run records, newest first, optionally filtered by suite and/or model,
    with an outcome tally over the listed page. ``model`` filtering is what the
    model-detail panel's "Runs" list uses (design §8.2)."""
    store = _store()

    def _match(r: dict[str, Any]) -> bool:
        if suite and r.get("suite") != suite:
            return False
        return not (model and ((r.get("identity") or {}).get("model") or {}).get("id") != model)

    records = [r for r in store.iter_records() if _match(r)]
    records.sort(key=lambda r: r.get("run_id") or "", reverse=True)
    page = records[:limit]
    outcomes: dict[str, int] = {}
    for r in page:
        outcome = r.get("outcome") or "?"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {"count": len(page), "outcomes": outcomes, "runs": [_run_summary(r) for r in page]}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """The full design §3.2 record: identity, reps[], telemetry, and an
    artifacts index — everything the run-detail drawer renders from."""
    store = _store()
    record = next((r for r in store.iter_records() if r.get("run_id") == run_id), None)
    if record is None:
        raise NotFound(f"unknown run_id: {run_id}", code="bench.unknown_run")
    record = dict(record)
    artifacts_dir = store.artifacts_root / run_id
    record["artifacts_files"] = (
        sorted(p.name for p in artifacts_dir.iterdir()) if artifacts_dir.is_dir() else []
    )
    return record


# --------------------------------------------------------------------------- #
# Quality tier: agentic-eval leaderboard
# --------------------------------------------------------------------------- #


@router.get("/evals")
def list_evals() -> dict[str, Any]:
    """Agentic-eval leaderboard: the latest score per (model, task), a
    per-model average, and the task catalogue. Reads <state root>/evals.jsonl
    (written by ``hal0 bench eval``); newest record per (model, task) wins."""
    records = evalrun.read_evals()
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for r in records:  # newest-last -> later record overwrites
        latest[(r.get("model", "?"), r.get("task_id", "?"))] = r

    tasks = [{"id": t.id, "kind": t.kind, "checkpoints": len(t.checkpoints)} for t in evalrun.TASKS]
    known = {t.id for t in evalrun.TASKS}
    seen_tasks = {tid for (_m, tid) in latest} | known
    task_order = [t["id"] for t in tasks] + sorted(seen_tasks - known)

    models: dict[str, dict[str, Any]] = {}
    for (model, task_id), rec in latest.items():
        m = rec.get("metrics") or {}
        row = models.setdefault(model, {"model": model, "tasks": {}, "avg_score": 0.0, "n": 0})
        row["tasks"][task_id] = {
            "correct": rec.get("correct"),
            "score": rec.get("score"),
            "outcome": rec.get("outcome"),
            "answer": rec.get("answer"),
            "expected": rec.get("expected"),
            "checkpoints_hit": len(rec.get("checkpoints_hit") or []),
            "checkpoints_total": rec.get("checkpoints_total"),
            "wall_s": m.get("wall_s"),
            "tool_calls": m.get("tool_calls"),
            "tokens_out": m.get("tokens_out"),
            "run_id": rec.get("run_id"),
        }
    for row in models.values():
        scores = [t["score"] for t in row["tasks"].values() if t["score"] is not None]
        row["n"] = len(scores)
        row["avg_score"] = round(sum(scores) / len(scores), 3) if scores else 0.0
    leaderboard = sorted(models.values(), key=lambda r: (-r["avg_score"], r["model"]))
    return {"tasks": tasks, "task_order": task_order, "models": leaderboard, "count": len(records)}


# --------------------------------------------------------------------------- #
# Actions — queue + worker control (the web process never drives the GPU)
# --------------------------------------------------------------------------- #


@router.get("/queue")
def get_queue() -> dict[str, Any]:
    """The run-queue view: worker control state, the active run
    (worker-written), and the pending items."""
    status = control.read_status()
    return {
        "control": control.read_control(),
        "active": status.get("active"),
        "updated": status.get("updated"),
        "items": control.read_queue(),
    }


@router.post("/queue")
def post_queue(body: dict[str, Any]) -> dict[str, Any]:
    """Enqueue a run: ``{model: <id>}`` (single model) or ``{suite: <id>}``.
    Records intent only — the worker (if running + Started) drains it under the
    same GPU gate as `hal0 bench run`."""
    suite = body.get("suite")
    model = body.get("model")
    kind = body.get("kind")
    if not suite and not model:
        raise BadRequest("body.suite or body.model is required", code="bench.invalid_envelope")
    if kind not in (None, "eval"):
        raise BadRequest(
            f"unknown queue kind {kind!r} (only 'eval')", code="bench.invalid_envelope"
        )
    if kind == "eval" and not model:
        raise BadRequest("kind='eval' requires body.model", code="bench.invalid_envelope")
    if suite:
        suites = load_suites(SUITE_DIR)
        if suite not in suites:
            raise NotFound(f"unknown suite {suite!r}", code="bench.unknown_suite")
    # Optional per-model run shape: lanes (compare backends) + configs (flag grid)
    # + kind ("eval" routes the item to the agentic tool-calling eval instead of
    # a lane sweep — the dashboard's Tool Bench).
    lanes = body.get("lanes")
    configs = body.get("configs")
    label = suite or model
    if kind == "eval":
        label += " [tools]"
    if lanes and len(lanes) > 1:
        label += f" [{'+'.join(lanes)}]"
    if configs and len(configs) > 1:
        label += f" [{len(configs)} cfgs]"
    item = {
        "id": secrets.token_hex(4),
        "label": label,
        "suite": suite,
        "model": model,
        "kind": kind,
        "lanes": lanes,
        "configs": configs,
        "enqueued": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return {"queued": item, "items": control.enqueue(item)}


@router.delete("/queue/{item_id}")
def delete_queue(item_id: str) -> dict[str, Any]:
    return {"items": control.dequeue(item_id)}


@router.post("/control")
def post_control(body: dict[str, Any]) -> dict[str, Any]:
    """Worker control: ``{action: start|pause|stop}`` and/or ``{exclusive:
    bool}`` (the "shut down competing slots" toggle). Start arms the worker;
    Pause/Stop take effect between cells."""
    action = body.get("action")
    state = (
        {"start": "running", "pause": "paused", "stop": "stopped"}.get(action) if action else None
    )
    if action and state is None:
        raise BadRequest(f"bad action {action!r} (start|pause|stop)", code="bench.bad_action")
    return control.set_control(state=state, exclusive=body.get("exclusive"))


@router.post("/run")
def post_run(body: dict[str, Any]) -> dict[str, Any]:
    """Back-compat (design §7 POST /run): enqueue a suite (default roster) onto
    the run queue. Prefer POST /queue + /control."""
    return post_queue({"suite": body.get("suite") or "roster"})


@router.get("/events")
async def get_events(request: Request) -> StreamingResponse:
    """SSE stub: session progress (design §8 "nice-to-have, phase-late"). Emits
    only a heartbeat comment; the UI polls /queue for live state instead."""

    async def event_stream():
        yield ": benchmarks events stream — no publisher wired yet\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
