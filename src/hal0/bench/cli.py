"""The single operator/agent surface for the bench system: ``hal0 bench <verb>``.

argparse (not typer) so the CLI is stdlib-only like the rest of the critical
path (server_ab.py precedent). Every verb is a thin wrapper over the library
modules — the CLI holds no logic of its own beyond wiring + output formatting:

    plan       what's stale and why; no GPU, no writes (planner.plan)
    run        execute the plan or a slice (runner.run_session)
    status     last session log / live state
    results    query current values (store.results)
    history    trend for a cell/model (store.history)
    reindex    rebuild bench.db from records.jsonl (store.reindex)
    devices    GPU device nodes benchmark containers use (devices.resolve_bench_devices)
    publish    regenerate roster.json (+ --check diff) (publish.*)

Runs unprivileged; Tier A cells go through the seam inside ``run``. (The
one-time ``import-v1`` migration verb was removed after every box migrated —
records it produced are ordinary schema-2 rows and remain valid.)
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import hal0

from .planner import fetch_registry_models, plan
from .publish import build_roster, emit_site_ts, write_roster
from .regress import check as regress_check
from .runner import DEFAULT_API, describe_worklist, fetch_host, run_session
from .runner import _traffic_in_flight as traffic_in_flight
from .schema import Host
from .store import Store
from .suites import Suite, load_suite_file, load_suites, suite_dir, window_file

# Politeness policy for --scheduled (DESIGN §6): only proceed inside the
# maintenance window and when the GPU is quiet. Window default is Sun 03:00-07:00
# LOCAL, overridable via the window.toml under suites' etc dir (suites.window_file).
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
    wf = window_file()
    if wf.exists():
        try:
            import tomllib

            doc = tomllib.loads(wf.read_text())
            w = doc.get("window", {})
            raw_days = w.get("days") or ([w["day"]] if w.get("day") else [])
            parsed = {
                _DAY_IDX[str(d)[:3].lower()] for d in raw_days if str(d)[:3].lower() in _DAY_IDX
            }
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
    looked up in the suite dir (suites.suite_dir — shared with the API)."""
    p = Path(ref)
    if p.suffix == ".toml" and p.exists():
        return load_suite_file(p)
    d = suite_dir()
    suites = load_suites(d)
    if ref not in suites:
        sys.exit(f"unknown suite {ref!r} (looked in {d} and as a path)")
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
        trigger="scheduled" if args.scheduled else "manual",
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


def _eval_run_id(item: dict) -> str:
    """A STABLE run_id for a queued eval, derived from the queue item id.

    The suite path is resumable because it appends per-cell as it goes and, on a
    defer, leaves the item queued so the next tick re-plans only what's still
    stale — the same records never get rewritten. Eval has no planner, so we get
    the same property by deriving a run_id from the (unique, ``token_hex``) queue
    item id: an eval that defers (Stop/Pause or live traffic) resumes under the
    SAME run_id on the next tick instead of restarting under a fresh ``_now_stamp``
    and duplicating the records of tasks that already completed. A fresh enqueue
    gets a new item id → new run_id → a clean full re-run."""
    return f"eval-{item.get('id') or 'adhoc'}"


def _worker_eval(model: str, api: str, item: dict) -> bool:
    """Run the full agentic-eval scenario set for one queued model (the
    dashboard's Tool Bench). Unlike suite runs this drives the LIVE inference
    endpoint through tool-eval-bench, so it never takes the GPU seam. Same
    politeness rule as cmd_eval: re-check for live traffic before each
    scenario and back off (leave the item queued) rather than pile onto
    production. Returns True when every scenario ran; False when the
    operator hit Stop/Pause or traffic appeared mid-run.

    Resumable across defers (like the suite path): tasks already recorded under
    this item's stable run_id in a prior tick are skipped, so a resumed eval runs
    only the remainder and never double-writes a record."""
    import tempfile

    from . import control, evalrun

    evalrun.ensure_tasks()
    run_id = _eval_run_id(item)
    done = {r.get("task_id") for r in evalrun.read_evals() if r.get("run_id") == run_id}
    pending = [t for t in evalrun.TASKS if t.id not in done]
    if not pending:
        return True
    with tempfile.TemporaryDirectory(prefix="hal0-bench-eval-") as tmp:
        workroot = Path(tmp)
        for task in pending:
            if not control.worker_should_run():
                return False
            if traffic_in_flight(api):
                print(
                    f"[worker] live traffic — backing off eval {model} (resumes where it left off)"
                )
                return False
            print(f"[worker] eval {model} :: {task.id} …", flush=True)
            rec = evalrun.run_task(task, model, run_id, api, workroot)
            evalrun.append_eval(rec)
    return True


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
    print(
        f"[worker] started; polling every {args.poll}s (state defaults to 'stopped' — idle until Start)"
    )
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
            if item.get("kind") == "eval" and item.get("model"):
                resolved = _resolve_model_id(item["model"], models)
                from . import evalrun

                missing = evalrun.tool_eval_missing()
                if missing:
                    print(f"[worker] {missing} — dropping eval item {item.get('id')}")
                    control.dequeue(item.get("id"))
                    control.write_status(None, _now_stamp())
                    continue

                control.write_status(
                    {
                        "item": item,
                        "suite": f"eval:{resolved.rsplit('/', 1)[-1]}",
                        "cells": len(evalrun.TASKS),
                        "started": _now_stamp(),
                        "exclusive": False,  # eval drives the live endpoint, never the seam
                    },
                    _now_stamp(),
                )
                if not _worker_eval(resolved, args.api, item):
                    control.write_status(None, _now_stamp())
                    if not control.worker_should_run():
                        # Stop/Pause: leave the item at the queue head so Start
                        # resumes THIS eval (its completed tasks persist under the
                        # stable run_id) — mirrors the suite path's "stopped" branch.
                        print(
                            f"[worker] eval {resolved} paused — item stays queued (resumes on Start)"
                        )
                        continue
                    # Deferred over live traffic. Eval is polite by design (never
                    # piles onto production), but shouldn't stall the whole queue
                    # while it waits — yield head-of-line so non-eval items behind
                    # it can drain. The item keeps its id, so it resumes where it
                    # left off when it next reaches the head on a quiet box.
                    print(f"[worker] eval {resolved} deferred (live traffic) — yielding queue head")
                    control.dequeue(item.get("id"))
                    control.enqueue(item)
                    time.sleep(args.poll)
                    continue
                control.dequeue(item.get("id"))
                print(f"[worker] eval {resolved} done: {len(evalrun.TASKS)} task(s)")
                control.write_status(None, _now_stamp())
                continue
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
                {
                    "item": item,
                    "suite": suite.id,
                    "cells": len(cells),
                    "started": _now_stamp(),
                    "exclusive": ctrl["exclusive"],
                },
                _now_stamp(),
            )
            print(
                f"[worker] running {suite.id}: {len(cells)} cell(s), exclusive={ctrl['exclusive']}"
            )
            result = run_session(
                cells,
                store,
                _session_host(args.api, ctrl["exclusive"]),
                suite_id=suite.id,
                api=args.api,
                budget_min=suite.budget_min,  # queued items were unbounded
                exclusive=ctrl["exclusive"],
                should_continue=control.worker_should_run,
                trigger="queue",
            )
            if result.aborted == "stopped":
                print(
                    f"[worker] {suite.id} stopped after {result.cells_ok} cell(s) — item stays queued"
                )
                control.write_status(None, _now_stamp())
                continue  # leave the item queued; resumes on next Start
            if result.aborted == "gpu-contended":
                print("[worker] GPU busy (traffic in flight) — retrying item next tick")
                control.write_status(None, _now_stamp())
                time.sleep(args.poll)
                continue  # leave queued; never bench a busy GPU
            store.reindex()
            flags = regress_check(store)
            if flags:
                print(f"[worker] [regress] {len(flags)} cell(s) flagged:")
                for f in flags:
                    print(f"  {f.model_id} {f.cell_key[:16]} {f.delta_pct}% vs {f.trailing_median}")
            control.dequeue(item.get("id"))
            print(f"[worker] {suite.id} done: ok={result.cells_ok} failed={result.cells_failed}")
            control.write_status(None, _now_stamp())
        except Exception as exc:  # a bad item must never crash-loop the worker
            # …but a TRANSIENT failure (registry blip, API restart) must not
            # silently discard operator work either (the old behavior). Retry
            # up to 3 attempts, re-queued at the tail so it can't head-of-line
            # block; give up loudly after that.
            attempts = int(item.get("attempts") or 0) + 1
            control.dequeue(item.get("id"))
            if attempts < 3:
                control.enqueue({**item, "attempts": attempts})
                print(
                    f"[worker] error on item {item.get('id')} "
                    f"(attempt {attempts}/3): {exc!r} — re-queued at the tail"
                )
            else:
                print(
                    f"[worker] error on item {item.get('id')} "
                    f"(attempt {attempts}/3): {exc!r} — giving up on this item"
                )
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


def cmd_devices(args: argparse.Namespace) -> int:
    """Show the GPU device nodes benchmark containers will be launched with.

    The same resolution the installed harness uses (issue #1303): probe /
    live discovery through the production slot helpers, never a hardcoded
    node name. Exit 2 with an actionable message when an explicit operator
    override cannot be honoured, so a bad setting is caught BEFORE a queued
    run reaches the GPU.
    """
    from .devices import main as devices_main

    return devices_main(["--format", args.format])


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


def cmd_bundle(args: argparse.Namespace) -> int:
    from .bundle import BundleSpec, select_records, write_bundle

    store = Store()
    spec = BundleSpec(
        run_ids=args.runs.split(",") if args.runs else None,
        suite=args.bundle_suite,
        since=args.since,
        title=args.title,
        notes=args.notes,
        with_artifacts=args.with_artifacts,
        redact_hostname=not args.no_redact_hostname,
        profile_paths=args.profile or [],
    )
    if args.list:
        recs = select_records(store, spec)
        print(f"{len(recs)} bundle-eligible record(s):")
        for r in recs:
            ident = r.get("identity", {})
            print(
                f"  {r['run_id']}  {ident.get('model', {}).get('id', '?'):40s} "
                f"{ident.get('lane', '?'):12s} "
                f"{ident.get('workload', {}).get('kind', '?'):6s} "
                f"suite={r.get('suite', '?')}"
            )
        return 0
    try:
        path, manifest = write_bundle(store, spec, args.out)
    except ValueError as exc:
        print(f"bundle: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {path} ({len(manifest['records'])} record(s), {manifest['bundle_id']})")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    from . import upload as _upload

    path = Path(args.bundle)
    if not path.is_file():
        print(f"upload: {path} not found", file=sys.stderr)
        return 1
    try:
        resp = _upload.upload_bundle(path, api=args.upload_api, token=args.token)
    except _upload.UploadError as exc:
        print(f"upload: {exc}", file=sys.stderr)
        return 1
    url = resp.get("url") or resp.get("bundle_id") or "ok"
    print(f"uploaded: {url}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Agentic scenario eval (the quality tier): drive each model through
    tool-eval-bench's tool-calling scenarios and score correctness + safety.

    Unlike ``run`` (which takes EXCLUSIVE GPU via the seam), eval sends requests
    to the LIVE hal0 inference endpoint (hal0's own ``/v1``) — so the target
    model must already be serveable, and eval competes with production
    traffic. Same politeness rule as scheduled benchmarks: unless --force,
    decline over live traffic (re-checked before each scenario) so we never
    pile onto production."""
    import tempfile

    from . import evalrun

    missing = evalrun.tool_eval_missing()
    if missing and not args.dry_run:
        print(missing)
        return 2
    if not missing:
        evalrun.ensure_tasks()

    if args.task:
        known = {t.id for t in evalrun.TASKS}
        tasks = [evalrun.get_task(t) or evalrun.Task(id=t) for t in args.task]
        unknown = [t for t in args.task if t not in known] if known else []
        if unknown:
            print(f"no such task(s): {unknown}; known: {sorted(known)}")
            return 2
    else:
        tasks = evalrun.TASKS
    if not tasks:
        print("no tasks available (tool-eval-bench scenario listing returned nothing)")
        return 2
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        print("--models is required (comma-separated model ids)")
        return 2

    if args.dry_run:
        print(f"[dry-run] {len(models)} model(s) x {len(tasks)} task(s); no GPU, no writes\n")
        for model in models:
            for task in tasks:
                cmd = evalrun.tool_eval_cmd(task, model, args.api)
                print(f"  {model} :: {task.id} ({task.kind or 'unknown'})")
                print("    " + " ".join(_shquote(c) for c in cmd) + "\n")
        return 0

    run_id = _now_stamp()
    rows: list[evalrun.EvalRecord] = []
    with tempfile.TemporaryDirectory(prefix="hal0-bench-eval-") as tmp:
        workroot = Path(tmp)
        for model in models:
            for task in tasks:
                if not args.force and traffic_in_flight(args.api):
                    print(
                        json.dumps(
                            {
                                "event": "bench.eval.declined",
                                "task": task.id,
                                "model": model,
                                "reason": "live-traffic",
                            }
                        )
                    )
                    continue
                print(f"[eval] {model} :: {task.id} …", flush=True)
                rec = evalrun.run_task(task, model, run_id, args.api, workroot)
                evalrun.append_eval(rec)
                rows.append(rec)
                mark = "OK " if rec.correct else ("~  " if rec.score > 0 else "X  ")
                m = rec.metrics
                print(
                    f"  {mark} score={rec.score} got={rec.answer!r} "
                    f"want={rec.expected!r} wall={m.get('wall_s')}s "
                    f"tools={m.get('tool_calls')} tok_out={m.get('tokens_out')} "
                    f"steps={len(rec.checkpoints_hit)}/{rec.checkpoints_total}"
                )

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
    return (
        s
        if s and all(c.isalnum() or c in "-_./:=" for c in s)
        else "'" + s.replace("'", "'\\''") + "'"
    )


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    # Operator-facing copy, not the module docstring's first line (#1477):
    # that read "cli.py — the single operator/agent surface: ``benchlab
    # <verb>`` (DESIGN §5)", so `hal0 bench --help` led with a filename, an
    # internal design-doc section, and a command name (`benchlab`) that is not
    # how anyone invokes this.
    ap = argparse.ArgumentParser(
        prog="hal0 bench",
        description=(
            "Keep hal0's throughput/latency benchmark dataset current: plan what is "
            "stale, run those cells, and query or publish the results."
        ),
    )
    ap.add_argument(
        "--version",
        action="version",
        version=f"hal0 bench (hal0 {hal0.__version__})",
    )
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
    p.add_argument(
        "--dry-run", action="store_true", help="print the worklist + commands, run nothing"
    )
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

    p = sub.add_parser("devices", help="GPU device nodes benchmark containers will use")
    p.add_argument(
        "--format",
        default="text",
        choices=("text", "env", "json", "flags"),
        help="output shape (default: text; the harness consumes env)",
    )
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("publish", help="regenerate roster.json")
    p.add_argument("--check", action="store_true", help="diff without writing")
    p.add_argument("--site-ts", default=None, help="also emit the site data .ts to this path")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("bundle", help="package selected ok records into a shareable archive")
    p.add_argument("--list", action="store_true", help="print bundle-eligible records; no write")
    p.add_argument("--runs", default=None, help="comma-separated run_ids (default: all ok)")
    # --suite is taken at the top level; dest avoids colliding with other verbs' args.suite
    p.add_argument(
        "--suite", dest="bundle_suite", default=None, help="only records from this suite"
    )
    p.add_argument("--since", default=None, help="ISO lower bound on record timestamp")
    p.add_argument("--title", default="", help="human title stored in the manifest")
    p.add_argument("--notes", default="", help="free-form notes stored in the manifest")
    p.add_argument("--with-artifacts", action="store_true", help="include raw artifacts/")
    p.add_argument(
        "--no-redact-hostname", action="store_true", help="keep host.name in shared records"
    )
    p.add_argument("--profile", action="append", help="profile/suite TOML to include; repeatable")
    p.add_argument("-o", "--out", default="bench-bundle.hal0bench.tar.gz", help="output path")
    p.set_defaults(func=cmd_bundle)

    p = sub.add_parser(
        "upload", help="maintainer: publish a bundle to the hal0.dev roster (requires token)"
    )
    p.add_argument("bundle", help="path to a .hal0bench.tar.gz built by `hal0 bench bundle`")
    p.add_argument(
        "--api",
        dest="upload_api",
        default=None,
        help="bench API base (default $HAL0_BENCH_API_BASE or https://api.hal0.dev)",
    )
    p.add_argument("--token", default=None, help="bearer token (default $HAL0_BENCH_TOKEN)")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser(
        "eval", help="agentic task eval (quality tier): score a model as a real agent"
    )
    p.add_argument("--models", required=True, help="comma-separated model ids to eval/compare")
    p.add_argument("--task", action="append", help="limit to task id(s); repeatable (default: all)")
    p.add_argument("--api", default=DEFAULT_API, help=f"hal0 API base (default {DEFAULT_API})")
    p.add_argument(
        "--dry-run", action="store_true", help="print the exact tool-eval-bench commands; no GPU"
    )
    p.add_argument(
        "--force", action="store_true", help="run even over live traffic (skip politeness)"
    )
    p.set_defaults(func=cmd_eval)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
