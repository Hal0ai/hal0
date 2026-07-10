"""cli.py — the single operator/agent surface: ``benchlab <verb>`` (DESIGN §5).

argparse (not typer) so the CLI is stdlib-only like the rest of the critical
path (server_ab.py precedent). Every verb is a thin wrapper over the library
modules — the CLI holds no logic of its own beyond wiring + output formatting:

    plan       what's stale and why; no GPU, no writes (planner.plan)
    run        execute the plan or a slice (runner.run_session)
    status     last session log / live state
    results    query current values (store.results)
    history    trend for a cell/model (store.history)
    reindex    rebuild bench.db from records.jsonl (store.reindex)
    publish    regenerate roster.json (+ --check diff) (publish.*)
    import-v1  one-time: hal0 v1 index.json + server-ab/*.json → v2 records

Runs unprivileged; Tier A cells go through the seam inside ``run``.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .planner import fetch_registry_models, plan
from .publish import build_roster, emit_site_ts, write_roster
from .regress import check as regress_check
from .runner import DEFAULT_API, describe_worklist, fetch_host, run_session
from .runner import _traffic_in_flight as traffic_in_flight
from .schema import (
    Config,
    Engine,
    Host,
    Identity,
    Model,
    Outcome,
    Record,
    Rep,
    Summary,
    Workload,
)
from .store import Store
from .suites import Suite, load_suite_file, load_suites

# Where operator suite TOMLs live (DESIGN §4). Virtual seed suites would be
# merged in here in a fuller build; for now the dir is the source.
SUITE_DIR = Path("/etc/hal0/bench/suites")

# hal0 v1 result locations (DESIGN §3.1, §5 import-v1).
V1_BENCH_DIR = Path("/var/lib/hal0/benchmarks")

# Politeness policy for --scheduled (DESIGN §6): only proceed inside the
# maintenance window and when the GPU is quiet. Window default is Sun 03:00-07:00
# LOCAL, overridable via /etc/hal0/bench/window.toml.
WINDOW_FILE = Path("/etc/hal0/bench/window.toml")
_DAY_IDX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _parse_hm(v: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in str(v).split(":"))
        return (h, m)
    except (ValueError, TypeError):
        return default


def _load_window() -> tuple[set[int], tuple[int, int], tuple[int, int], int]:
    """Return (weekday-set, (start_h,start_m), (end_h,end_m), min_idle_min).

    Parses the real /etc/hal0/bench/window.toml schema (DESIGN §6, verified
    on-box): ``[window]`` with ``day`` (a single "Sun") OR ``days`` (a list),
    ``start``/``end`` "HH:MM", and ``[politeness].min_idle_min``. Defaults to
    Sun 03:00-07:00 / 10 min; a malformed file falls back to the safe default."""
    days = {6}
    start, end, min_idle = (3, 0), (7, 0), 10
    if WINDOW_FILE.exists():
        try:
            import tomllib

            doc = tomllib.loads(WINDOW_FILE.read_text())
            w = doc.get("window", {})
            raw_days = w.get("days") or ([w["day"]] if w.get("day") else [])
            parsed = {_DAY_IDX[str(d)[:3].lower()] for d in raw_days if str(d)[:3].lower() in _DAY_IDX}
            if parsed:
                days = parsed
            start = _parse_hm(w.get("start"), start)
            end = _parse_hm(w.get("end"), end)
            min_idle = int(doc.get("politeness", {}).get("min_idle_min", min_idle) or min_idle)
        except (OSError, ValueError, KeyError, ImportError):
            pass  # a malformed override falls back to the safe default, never crashes
    return days, start, end, min_idle


def _scheduled_politeness(api: str, now: datetime | None = None) -> tuple[bool, str]:
    """DESIGN §6 --scheduled gate: (a) inside the maintenance window, (b) no
    active /v1 traffic in the trailing ``min_idle_min`` minutes. Returns
    (ok, reason). A skipped week is fine; a corrupted-numbers week is not — so
    anything uncertain declines."""
    now = now or datetime.now().astimezone()
    days, (sh, sm), (eh, em), min_idle = _load_window()
    day_name = {v: k.title() for k, v in _DAY_IDX.items()}
    win = f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d} on {'/'.join(day_name[d] for d in sorted(days))}"
    if now.weekday() not in days:
        return False, f"outside maintenance window (now {now:%a %H:%M}; window {win})"
    if not ((sh, sm) <= (now.hour, now.minute) < (eh, em)):
        return False, f"outside maintenance window (now {now:%a %H:%M}; window {win})"
    if traffic_in_flight(api, quiet_min=min_idle):
        return False, f"active /v1 traffic in the last {min_idle} min — deferring to the next tick"
    return True, f"inside window {win}, GPU quiet"


def _load_suite(ref: str) -> Suite:
    """Resolve a --suite argument: a filesystem path to a .toml, else an id
    looked up in SUITE_DIR."""
    p = Path(ref)
    if p.suffix == ".toml" and p.exists():
        return load_suite_file(p)
    suites = load_suites(SUITE_DIR)
    if ref not in suites:
        sys.exit(f"unknown suite {ref!r} (looked in {SUITE_DIR} and as a path)")
    return suites[ref]


def _session_host(api: str, exclusive: bool) -> Host:
    """The host block for records written this session: real gpu/platform/kernel/
    mem + hal0 version read live from the box (runner.fetch_host), with env
    overrides so CI/tests can pin them."""
    import os

    host = fetch_host(api, exclusive=exclusive)
    host.name = os.environ.get("HAL0_BENCH_HOST", host.name)
    host.platform = os.environ.get("HAL0_BENCH_PLATFORM", host.platform)
    host.gpu = os.environ.get("HAL0_BENCH_GPU", host.gpu)
    host.hal0_version = os.environ.get("HAL0_BENCH_HAL0_VERSION", host.hal0_version)
    return host


# --------------------------------------------------------------------------- #
# verbs
# --------------------------------------------------------------------------- #


def _fetch_models(args: argparse.Namespace) -> list:
    """Registry fetch with a clean error instead of a urllib traceback when
    hal0-api is briefly down (it cycles on this box). ``--no-registry`` plans
    against an empty set."""
    if args.no_registry:
        return []
    try:
        return fetch_registry_models(args.api)
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(
            f"cannot reach the hal0-api registry at {args.api}/api/models ({exc}). "
            f"hal0-api may be restarting — retry shortly, or pass --no-registry."
        )


def cmd_plan(args: argparse.Namespace) -> int:
    suite = _load_suite(args.suite)
    store = Store()
    models = _fetch_models(args)
    cells = plan(suite, models, store)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "cell_key": c.cell_key,
                        "model": c.model_id,
                        "lane": c.lane,
                        "kind": c.kind,
                        "depth": c.depth,
                        "reason": c.reason,
                    }
                    for c in cells
                ],
                indent=2,
            )
        )
    else:
        print(f"suite {suite.id}: {len(cells)} stale cell(s)")
        for c in cells:
            print(f"  {c.model_id:40s} {c.lane:12s} {c.kind:6s} d{c.depth:<7d} {c.reason}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    suite = _load_suite(args.suite)
    store = Store()

    # --scheduled politeness (DESIGN §6): decline (exit 0) outside the window or
    # over live traffic, so the next timer tick simply retries. Checked BEFORE the
    # registry fetch so an out-of-window decline needs no API (which cycles here).
    if args.scheduled:
        ok, reason = _scheduled_politeness(args.api)
        if not ok:
            print(
                json.dumps(
                    {"event": "bench.session.declined", "suite": suite.id, "reason": reason},
                    indent=2,
                )
            )
            return 0

    models = _fetch_models(args)
    cells = plan(suite, models, store)

    # --dry-run: print the ordered worklist + the exact commands, then stop.
    if args.dry_run:
        budget = args.budget_min or suite.budget_min
        print(
            f"[dry-run] suite {suite.id}: {len(cells)} stale cell(s), "
            f"budget {budget} min, exclusive={suite.exclusive}"
        )
        if not cells:
            print("  (nothing stale — nothing to run)")
            return 0
        print("ordered worklist (cheap-before-expensive within a model):\n")
        for line in describe_worklist(cells, suite.exclusive, args.api):
            print(line)
        return 0

    result = run_session(
        cells,
        store,
        _session_host(args.api, suite.exclusive),
        suite_id=suite.id,
        api=args.api,
        budget_min=args.budget_min or suite.budget_min,
        exclusive=suite.exclusive,
        dry_run=args.dry_run,
    )
    # DESIGN §5.5: end-of-session — reindex + regression check + journal event.
    if not args.dry_run and result.run_ids:
        store.reindex()
        flags = regress_check(store)
        if flags:
            print(f"[regress] {len(flags)} cell(s) flagged:")
            for f in flags:
                print(f"  {f.model_id} {f.cell_key[:16]} {f.delta_pct}% vs {f.trailing_median}")
    event = {
        "event": "bench.session.completed",
        "suite": result.suite_id,
        "cells_ok": result.cells_ok,
        "cells_failed": result.cells_failed,
        "duration_s": result.duration_s,
        "aborted": result.aborted,
    }
    print(json.dumps(event, indent=2))
    return 0 if result.aborted is None else 1


def _resolve_model_id(model: str, registry: list[dict]) -> str:
    """Map a queued model reference to a registry id the planner can select. The
    dashboard's per-row "+" queues the roster id, which for v1-imported models is a
    path-like ``dir/File.gguf`` that doesn't match a registry id — so also match
    on the gguf path/basename. Returns the registry id, or the input unchanged."""
    base = model.rsplit("/", 1)[-1]
    for m in registry:
        if m.get("id") == model:
            return model
        path = m.get("path") or ""
        if path == model or path.rsplit("/", 1)[-1] == base:
            return m.get("id") or model
    return model


def _worklist_suite(item: dict, base: Suite | None) -> Suite:
    """Build the suite for one queued item: a named suite id, or a single model
    (an ad-hoc roster-shaped suite scoped to that model via the include list).

    A model item may carry ``lanes`` (e.g. ["rocm","vulkan_radv"] to compare
    backends) and ``configs`` (a flag-tuning grid) from the dashboard — otherwise
    it defaults to the model's preferred lane and a single default config."""
    if item.get("suite") and base is not None:
        return base
    from .suites import Cells, Matrix, Selector, Staleness, _normalize_configs

    return Suite(
        id=f"queue:{item.get('model', '?').rsplit('/', 1)[-1]}",
        description="single-model run queued from the dashboard",
        exclusive=True,  # the worker overrides run_session's exclusive from control
        selector=Selector(include=[item["model"]]),
        matrix=Matrix(
            lanes=list(item.get("lanes") or ["default"]),
            depths=[2048],
            samplers=["greedy"],
            reps=3,
            configs=_normalize_configs(item.get("configs")),
        ),
        cells=Cells(kinds=["pp", "tg"]),
        staleness=Staleness(max_age_days=0),  # always (re)run a queued item
    )


def cmd_worker(args: argparse.Namespace) -> int:
    """Drain the run queue (DESIGN §7 POST /api/run, wired out-of-request).

    Loops forever: while control.state=="running" and the queue is non-empty and
    the GPU is idle, run the head item (exclusive per the control toggle), then
    dequeue it. SAFE BY DEFAULT — control.state defaults to "stopped", so this
    service is inert (never touches the GPU / competing slots) until an operator
    hits Start in the UI. Pause/Stop are honoured between cells."""
    import time

    from . import control

    store = Store()
    print(f"[worker] started; polling every {args.poll}s (state defaults to 'stopped' — idle until Start)")
    while True:
        if not control.worker_should_run():
            control.write_status(None, _now_stamp())
            time.sleep(args.poll)
            continue
        queue = control.read_queue()
        if not queue:
            control.write_status(None, _now_stamp())
            time.sleep(args.poll)
            continue
        item = queue[0]
        ctrl = control.read_control()
        try:
            models = fetch_registry_models(args.api)
            if item.get("suite"):
                try:
                    suite = _load_suite(item["suite"])
                except SystemExit:
                    print(f"[worker] unknown suite {item.get('suite')!r} — dropping item")
                    control.dequeue(item.get("id"))
                    continue
            elif item.get("model"):
                resolved = _resolve_model_id(item["model"], models)
                suite = _worklist_suite(
                    {"model": resolved, "lanes": item.get("lanes"), "configs": item.get("configs")},
                    None,
                )
            else:
                control.dequeue(item.get("id"))
                continue

            cells = plan(suite, models, store)
            control.write_status(
                {"item": item, "suite": suite.id, "cells": len(cells), "started": _now_stamp(),
                 "exclusive": ctrl["exclusive"]},
                _now_stamp(),
            )
            print(f"[worker] running {suite.id}: {len(cells)} cell(s), exclusive={ctrl['exclusive']}")
            result = run_session(
                cells, store, _session_host(args.api, ctrl["exclusive"]),
                suite_id=suite.id, api=args.api, exclusive=ctrl["exclusive"],
                should_continue=control.worker_should_run,
            )
            if result.aborted == "stopped":
                print(f"[worker] {suite.id} stopped after {result.cells_ok} cell(s) — item stays queued")
                control.write_status(None, _now_stamp())
                continue  # leave the item queued; resumes on next Start
            if result.aborted == "gpu-contended":
                print("[worker] GPU busy (traffic in flight) — retrying item next tick")
                control.write_status(None, _now_stamp())
                time.sleep(args.poll)
                continue  # leave queued; never bench a busy GPU
            store.reindex()
            control.dequeue(item.get("id"))
            print(f"[worker] {suite.id} done: ok={result.cells_ok} failed={result.cells_failed}")
            control.write_status(None, _now_stamp())
        except Exception as exc:  # a bad item must never crash-loop the worker
            print(f"[worker] error on item {item.get('id')}: {exc!r} — dropping it and continuing")
            control.dequeue(item.get("id"))
            control.write_status(None, _now_stamp())
            time.sleep(args.poll)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = Store()
    recent = sorted(store.iter_records(), key=lambda r: r.get("run_id") or "")[-10:]
    if args.json:
        print(json.dumps(recent, indent=2))
        return 0
    print(f"state root: {store.root}")
    print(
        f"records: {store.records_path} ({'exists' if store.records_path.exists() else 'absent'})"
    )
    for r in recent:
        wl = (r.get("identity") or {}).get("workload") or {}
        print(f"  {r.get('run_id')}  {r.get('outcome'):18s} {wl.get('kind')}  {r.get('suite')}")
    return 0


def cmd_results(args: argparse.Namespace) -> int:
    store = Store()
    rows = store.results(model=args.model, since=args.since, limit=args.limit)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(
                f"  {r['model_id']:40s} {r['lane']:12s} {r['kind']:6s} "
                f"d{r['depth']:<7} {r['decode_ts_med']}  {r['ts']}"
            )
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    store = Store()
    rows = store.history(cell_key=args.cell, model=args.model, limit=args.limit)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"  {r['ts']}  {r['model_id']:40s} {r['decode_ts_med']}")
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    store = Store()
    n = store.reindex()
    print(f"reindexed {n} record(s) -> {store.db_path}")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    store = Store()
    roster = build_roster(store)
    if args.check:
        existing = store.root / "roster.json"
        old = json.loads(existing.read_text()) if existing.exists() else None
        changed = old != roster
        # Ignore the volatile generated-date when diffing content.
        if old is not None:
            a, b = dict(old), dict(roster)
            a.pop("generated", None)
            b.pop("generated", None)
            changed = a != b
        print(f"roster {'CHANGED' if changed else 'unchanged'}")
        return 1 if changed else 0
    path = write_roster(store, roster)
    print(f"wrote {path} ({len(roster['models'])} model(s))")
    if args.site_ts:
        out = emit_site_ts(roster, args.site_ts)
        print(f"wrote {out}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Agentic task eval (the quality tier): drive each model as a real Hermes
    agent through verifiable-value tasks and score correctness + speed.

    Unlike ``run`` (which takes EXCLUSIVE GPU via the seam), eval sends requests
    to the LIVE hal0 inference endpoint through Hermes — so the target model must
    already be serveable, and eval competes with production traffic. Same
    politeness rule as scheduled benchmarks: unless --force, decline over live
    traffic (re-checked before each task) so we never pile onto production."""
    import tempfile

    from . import evalrun

    tasks = evalrun.TASKS if not args.task else [t for t in evalrun.TASKS if t.id in args.task]
    if not tasks:
        print(f"no such task(s): {args.task}; known: {[t.id for t in evalrun.TASKS]}")
        return 2
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        print("--models is required (comma-separated model ids)")
        return 2

    if args.dry_run:
        print(f"[dry-run] {len(models)} model(s) x {len(tasks)} task(s); no GPU, no writes\n")
        for model in models:
            for task in tasks:
                cmd = evalrun.hermes_cmd(task, model, args.api)
                print(f"  {model} :: {task.id} ({task.kind}, {task.timeout_s}s)")
                print("    " + " ".join(_shquote(c) for c in cmd) + "\n")
        return 0

    run_id = _now_stamp()
    rows: list[evalrun.EvalRecord] = []
    with tempfile.TemporaryDirectory(prefix="hal0-bench-eval-") as tmp:
        workroot = Path(tmp)
        for model in models:
            for task in tasks:
                if not args.force and traffic_in_flight(args.api):
                    print(json.dumps({"event": "bench.eval.declined", "task": task.id,
                                      "model": model, "reason": "live-traffic"}))
                    continue
                print(f"[eval] {model} :: {task.id} …", flush=True)
                rec = evalrun.run_task(task, model, run_id, args.api, workroot)
                evalrun.append_eval(rec)
                rows.append(rec)
                mark = "OK " if rec.correct else ("~  " if rec.score > 0 else "X  ")
                m = rec.metrics
                print(f"  {mark} score={rec.score} got={rec.answer!r} "
                      f"want={rec.expected!r} wall={m.get('wall_s')}s "
                      f"tools={m.get('tool_calls')} tok_out={m.get('tokens_out')} "
                      f"steps={len(rec.checkpoints_hit)}/{rec.checkpoints_total}")

    if rows:
        print("\n" + _eval_table(rows))
    return 0


def _eval_table(rows: list) -> str:
    """A compact model x task comparison of the just-run eval records."""
    models = sorted({r.model for r in rows})
    tasks = sorted({r.task_id for r in rows})
    by = {(r.model, r.task_id): r for r in rows}
    w = max([len(m) for m in models] + [12])
    head = "model".ljust(w) + "  " + "  ".join(t[:14].ljust(14) for t in tasks) + "  avg"
    out = [head, "-" * len(head)]
    for m in models:
        cells, scores = [], []
        for t in tasks:
            r = by.get((m, t))
            if r is None:
                cells.append("-".ljust(14))
                continue
            scores.append(r.score)
            tag = "ok" if r.correct else f"{r.score:.2f}"
            cells.append(f"{tag} {r.metrics.get('wall_s', '?')}s".ljust(14))
        avg = f"{sum(scores) / len(scores):.2f}" if scores else "-"
        out.append(m.ljust(w) + "  " + "  ".join(cells) + f"  {avg}")
    return "\n".join(out)


def _shquote(s: str) -> str:
    return s if s and all(c.isalnum() or c in "-_./:=" for c in s) else "'" + s.replace("'", "'\\''") + "'"


def cmd_import_v1(args: argparse.Namespace) -> int:
    """One-time import of hal0 v1 results into v2 records (DESIGN §5, §3.1).

    Implements the ``index.json`` path (the llama-bench aggregate written by
    generate_results_json.py). Each normalized v1 record becomes one schema-2
    record with outcome=ok, identity mapped from the v1 fields, and a single
    synthetic rep carrying the v1 avg/stddev so history is not lost.
    """
    store = Store()
    root = Path(args.bench_dir)
    n = _import_v1_index(root / "index.json", store)
    print(f"imported {n} v1 llama-bench record(s) from {root / 'index.json'}")
    m = _import_v1_server_ab(root / "server-ab", store)
    print(f"imported {m} v1 server-ab record(s) from {root / 'server-ab'}")
    store.reindex()
    return 0


def _import_v1_index(index_path: Path, store: Store) -> int:
    """Convert generate_results_json.py's index.json records to schema-2."""
    if not index_path.exists():
        print(f"[import-v1] no index.json at {index_path}; nothing to import")
        return 0
    doc = json.loads(index_path.read_text())
    n = 0
    for rec in doc.get("records", []):
        cfg = rec.get("config", {})
        model = rec.get("model", {})
        metric = rec.get("metric", {})
        kind = "pp" if rec.get("test") == "pp" else "tg"
        ts = rec.get("timestamp") or _now_stamp()
        identity = Identity(
            model=Model(
                id=_norm_v1_model_id(model.get("name") or model.get("path")),
                gguf=model.get("path", ""),
                size_bytes=int(model.get("size", 0) or 0),
            ),
            engine=Engine(
                kind="llama-bench",
                image=rec.get("runtime_image", ""),
                llamacpp_build=str((rec.get("llamacpp_build") or {}).get("commit") or ""),
            ),
            lane=_v1_lane(rec.get("backend")),
            config=Config(
                kv={"main_k": cfg.get("type_k", ""), "main_v": cfg.get("type_v", "")},
                parallel=1,
                ctx=int(cfg.get("n_depth", 0) or 0) or 32768,
            ),
            workload=Workload(
                kind=kind,
                depth=int(cfg.get("n_depth", 0) or 0),
                n_prompt=int(cfg.get("n_prompt", 0) or 0),
                n_gen=int(cfg.get("n_gen", 0) or 0),
            ),
        )
        summary = Summary(
            decode_ts_med=metric.get("avg_ts") if kind == "tg" else None,
            decode_ts_stddev=metric.get("stddev_ts") if kind == "tg" else None,
            prefill_ts_med=metric.get("avg_ts") if kind == "pp" else None,
        )
        record = Record(
            run_id=_stamp_to_run_id(ts),
            suite="import-v1",
            trigger="manual",
            identity=identity,
            host=Host(name=rec.get("host") or "hal0", gpu=rec.get("gpu") or "", hal0_version=""),
            outcome=Outcome.OK,
            summary=summary,
            note="imported from v1 index.json",
        )
        store.append_record(record)
        n += 1
    return n


def _norm_v1_model_id(name: str | None) -> str:
    """Canonicalise a v1 model id so the same model measured under inconsistent
    names doesn't split into duplicate roster rows. hal0's index.json records the
    same gguf sometimes as ``dir/File.gguf`` and sometimes as the absolute
    ``/mnt/ai-models/dir/File.gguf`` (verified on-box) — strip the model-root
    prefix + leading slash so both collapse to one id."""
    n = (name or "unknown").lstrip("/")
    return n[len("mnt/ai-models/"):] if n.startswith("mnt/ai-models/") else n


def _v1_lane(backend: str | None) -> str:
    if not backend:
        return "rocm"
    b = backend.lower()
    if "vulkan" in b:
        return "vulkan_radv"
    return "rocm"


# --------------------------------------------------------------------------- #
# import-v1: server-ab/*.json  (DESIGN §5, §3.1)
# --------------------------------------------------------------------------- #

# server_ab mode -> the v2 workload.kind it maps to (DESIGN §3.2 workload.kind).
_SA_MODE_TO_KIND = {"ab": "chat", "reuse": "reuse", "embed": "embed", "rerank": "rerank"}


def _import_v1_server_ab(sa_dir: Path, store: Store) -> int:
    """Import hal0's standalone ``server-ab/*.json`` sessions into schema-2.

    Each file is one server_ab.py session (verified on-box 2026-07-05):
    top-level provenance header ``{timestamp, mode, slot, n, max_tokens}`` +
    ``results`` whose shape depends on ``mode``:

      * ``ab``    — ``{<variant>: {extra_args, runs:[<run>...], median}}``  → one
        v2 record per VARIANT, one rep per timed run.
      * ``reuse`` — ``{<variant>: {extra_args, second_call:<run>}}``        → one
        record per variant, a single rep from the cache-warmed call.
      * ``embed`` / ``rerank`` — ``{dims|score_spread, latency_s:[...],
        median_latency_s}`` → one record, one rep per latency sample.

    Provenance mapping (DESIGN task note "unknown fields → nulls, never guesses"):
    the header only knows the SLOT name and the variant's ``extra_args`` — not the
    gguf, image, build, or lane — so those identity fields are left empty/0, never
    invented. ``extra_args`` becomes the resolved argv so cell_key still separates
    variants. Files whose shape we don't recognise (e.g. the bespoke
    ``mtp-sweep-*.json`` matrix) are skipped with a note rather than guessed."""
    if not sa_dir.is_dir():
        print(f"[import-v1] no server-ab dir at {sa_dir}; nothing to import")
        return 0
    n = 0
    for path in sorted(sa_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[import-v1] skip {path.name}: {exc}")
            continue
        if not isinstance(doc, dict) or "mode" not in doc or "results" not in doc:
            print(f"[import-v1] skip {path.name}: unrecognised shape (not a server_ab session)")
            continue
        mode = doc.get("mode")
        kind = _SA_MODE_TO_KIND.get(mode)
        if kind is None:
            print(f"[import-v1] skip {path.name}: unsupported mode {mode!r}")
            continue
        results = doc.get("results") or {}
        stamp = doc.get("timestamp") or ""
        slot = doc.get("slot") or "unknown"
        max_tokens = int(doc.get("max_tokens", 0) or 0)

        if mode in ("embed", "rerank"):
            rec = _sa_latency_record(mode, kind, slot, results, max_tokens, stamp, path.name)
            if rec is not None:
                store.append_record(rec)
                n += 1
            continue

        # ab / reuse: one record per variant
        for variant, block in results.items():
            if not isinstance(block, dict):
                continue
            runs = block.get("runs")
            if runs is None and "second_call" in block:
                runs = [block["second_call"]]
            rec = _sa_generative_record(
                mode, kind, slot, variant, block.get("extra_args", ""),
                runs or [], max_tokens, stamp, path.name,
            )
            if rec is not None:
                store.append_record(rec)
                n += 1
    return n


def _sa_run_to_rep(run: dict) -> Rep | None:
    """One server_ab timed run → a schema-2 Rep. Returns None for an errored run
    (no throughput numbers), so a failed call is dropped, never counted as ok."""
    if not isinstance(run, dict) or run.get("error") or run.get("predicted_per_second") is None:
        return None
    draft_n = run.get("draft_n")
    accepted = run.get("draft_n_accepted")
    accept = (accepted / draft_n) if draft_n else None
    return Rep(
        t_s=run.get("wall_s"),
        prefill_ts=run.get("prompt_per_second"),
        decode_ts=run.get("predicted_per_second"),
        ttft_ms=run.get("prompt_ms"),  # prompt-processing time ≈ TTFT proxy
        accept_rate=accept,
        drafted=draft_n,
        accepted=accepted,
        timings_raw=run,
    )


def _sa_generative_record(
    mode, kind, slot, variant, extra_args, runs, max_tokens, stamp, fname
) -> Record | None:
    reps = [r for r in (_sa_run_to_rep(run) for run in runs) if r is not None]
    if not reps:
        return None
    import shlex

    identity = Identity(
        model=Model(id=slot),  # v1 server-ab only knows the slot name
        engine=Engine(kind="llama-server"),
        lane="",  # unknown from the session file — never guessed
        config=Config(argv=shlex.split(extra_args or ""), ctx=0),
        workload=Workload(kind=kind, n_gen=max_tokens, sampler={"mode": "production"}),
    )
    return Record(
        run_id=_stamp_to_run_id(_compact_ts_to_iso(stamp)),
        suite="import-v1",
        trigger="manual",
        identity=identity,
        host=Host(name="hal0"),
        outcome=Outcome.OK,
        reps=reps,
        summary=Summary(
            decode_ts_med=_median([r.decode_ts for r in reps]),
            prefill_ts_med=_median([r.prefill_ts for r in reps]),
            accept_med=_median([r.accept_rate for r in reps]),
        ),
        note=f"imported from v1 server-ab {mode}/{variant} ({fname})",
    )


def _sa_latency_record(mode, kind, slot, results, max_tokens, stamp, fname) -> Record | None:
    lat = [x for x in (results.get("latency_s") or []) if isinstance(x, (int, float))]
    if not lat:
        return None
    reps = [Rep(t_s=x, timings_raw={"latency_s": x}) for x in lat]
    identity = Identity(
        model=Model(id=slot),
        engine=Engine(kind="llama-server"),
        lane="",
        config=Config(ctx=0),
        workload=Workload(kind=kind, n_gen=max_tokens),
    )
    return Record(
        run_id=_stamp_to_run_id(_compact_ts_to_iso(stamp)),
        suite="import-v1",
        trigger="manual",
        identity=identity,
        host=Host(name="hal0"),
        outcome=Outcome.OK,
        reps=reps,
        # embed/rerank have no decode t/s; leave summary throughput null (never
        # guessed). Median latency stays inspectable in reps[].
        summary=Summary(),
        note=f"imported from v1 server-ab {mode} ({fname})",
    )


def _median(values: list) -> float | None:
    import statistics

    vals = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(vals), 4) if vals else None


def _compact_ts_to_iso(ts: str) -> str:
    """server-ab stamps are compact UTC (``20260704T212506Z``); normalise to the
    ISO form ``_stamp_to_run_id`` understands. Pass ISO/other through unchanged."""
    try:
        dt = datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return ts


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp_to_run_id(ts: str) -> str:
    """Normalize a v1 timestamp to a run_id (UTC stamp + suffix). Keeps chrono
    sort; the suffix ``v1`` marks the provenance."""
    import secrets

    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        stamp = dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        stamp = _now_stamp()
    return f"{stamp}-v1{secrets.token_hex(2)}"


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hal0 bench", description=__doc__.split("\n")[0])
    ap.add_argument("--version", action="version", version=f"hal0 bench {__version__}")
    ap.add_argument("--api", default=DEFAULT_API, help=f"hal0-api base (default {DEFAULT_API})")
    ap.add_argument(
        "--no-registry",
        action="store_true",
        help="skip the registry fetch (plan/run against an empty model set)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="what's stale and why (no GPU, no writes)")
    p.add_argument("--suite", required=True, help="suite id or path to a .toml")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("run", help="execute the plan (or a slice)")
    p.add_argument("--suite", required=True)
    p.add_argument("--budget-min", type=int, default=0, help="override suite budget")
    p.add_argument("--dry-run", action="store_true", help="print the worklist + commands, run nothing")
    p.add_argument(
        "--scheduled",
        action="store_true",
        help="timer mode: apply the DESIGN §6 window/traffic politeness policy",
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="recent records / session state")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("worker", help="drain the dashboard run queue (idle until Start)")
    p.add_argument("--poll", type=int, default=10, help="seconds between queue polls")
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("results", help="current values from bench.db")
    p.add_argument("--model", default=None)
    p.add_argument("--since", default=None, help="ISO lower bound on ts")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_results)

    p = sub.add_parser("history", help="trend for a cell/model over time")
    p.add_argument("--cell", default=None, help="cell_key")
    p.add_argument("--model", default=None)
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("reindex", help="rebuild bench.db from records.jsonl")
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("publish", help="regenerate roster.json")
    p.add_argument("--check", action="store_true", help="diff without writing")
    p.add_argument("--site-ts", default=None, help="also emit the site data .ts to this path")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("eval", help="agentic task eval (quality tier): score a model as a real agent")
    p.add_argument("--models", required=True, help="comma-separated model ids to eval/compare")
    p.add_argument("--task", action="append", help="limit to task id(s); repeatable (default: all)")
    p.add_argument("--api", default=DEFAULT_API, help=f"hal0 API base (default {DEFAULT_API})")
    p.add_argument("--dry-run", action="store_true", help="print the exact hermes commands; no GPU")
    p.add_argument("--force", action="store_true", help="run even over live traffic (skip politeness)")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("import-v1", help="import hal0 v1 results into v2 records")
    p.add_argument(
        "--bench-dir",
        default=str(V1_BENCH_DIR),
        help=f"hal0 v1 benchmarks dir (default {V1_BENCH_DIR})",
    )
    p.set_defaults(func=cmd_import_v1)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
