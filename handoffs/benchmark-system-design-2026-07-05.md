# hal0 benchmarking system — full design (batch runs, rich run detail, display, auto-update)

**Status:** DESIGN — implementation lives **out-of-tree** in a standalone
lab repo deployed to the box (not an official hal0 feature). It consumes
only hal0's stable public surfaces: the `hal0-benchctl` seam, `hal0-api` on
`:8080`, the installed `server_ab.py`, and the registry API. Its state lives
under `/var/lib/hal0-bench/`, its display on its own port, its units under
its own names — so §5's CLI, §7–8's API/dashboard, and §12's seam verbs are
implemented lab-side (or skipped), not as hal0 PRs. This handoff stays in
hal0 as the design record and the definition of that integration contract.
Ready to implement in phases (§13, re-homed accordingly). Companion to the
ROCmFPX runner bench runbook (2026-07-05) and the profile-matrix handoff
(2026-07-04). Reference point: [llm.ciru.ai](https://llm.ciru.ai/) is the
"overboard" version of this; we build the ~20% of it that matters for one
box (or a small fleet later), reusing hal0's existing seam, agent, and
docs-site plumbing instead of standing up a separate service.

**Goal in one sentence:** an operator (or the Hermes agent) declares *suites*
once; the system then keeps the benchmark dataset **current by itself** —
re-running exactly the cells whose inputs changed (new model, new runner
image, new flags, new hal0 release), storing **full per-run detail**, serving
it on the dashboard, and refreshing the public
[model-roster table](https://hal0.dev/docs/reference/model-roster-benchmark/)
without hand-editing.

---

## 0. Goals / non-goals

**Goals**
1. **Set-and-forget batch benchmarking** — declarative suites + a planner
   that knows what is stale, run in scheduled GPU windows, resumable across
   failures/reboots.
2. **Rich per-run detail** — every run stores full provenance (image digest,
   llama.cpp build, resolved argv/env, KV quant, depth, sampler, `-np`,
   model SHA) and raw per-repetition samples, not just a median. The display
   exposes all of it (user requirement: "more detail on the runs").
3. **Display** — a dashboard **Benchmarks** page (roster board → run detail
   drawer → history), plus the existing docs-site roster table upgraded with
   expandable per-model run detail.
4. **Auto-update** — the docs roster and dashboard data refresh from the
   dataset with zero hand-edited numbers; regressions get surfaced, not
   silently averaged away.
5. **Autonomy via skills + scripts** — the deterministic 95% is scripts
   (planner/runner/aggregator/publisher); the judgment 5% (interpret, accept/
   reject flag changes, write prose) is an agent skill with pre-registered
   acceptance criteria.

**Non-goals (now)**
- Multi-box fleet aggregation (schema reserves `host` for it; no UI).
- Quality/eval benchmarking (MMLU-style) — this is a *throughput/latency*
  system; pi-bench (coding-agent eval) stays deferred as per
  `installer/bench/README.md`.
- A public standalone leaderboard site à la llm.ciru.ai — the docs page is
  the public face.

---

## 1. What exists today (inventory — all of this is kept and reused)

| Piece | Where | Role |
|---|---|---|
| `hal0-benchctl` sudo seam | `installer/wrappers/hal0-benchctl` | THE privileged surface: `run`, `run-model`, `sweep` (whitelisted flags), `aggregate`, `list` |
| Tier A engine (llama-bench) | `installer/bench/run_benchmarks.sh` + `config.sh` | rootful podman sweeps, resumable cells, `--exclusive` GPU rule |
| Tier B/C engine (live server) | `installer/bench/server_ab.py` | modes `ab / reuse / embed / rerank / batch / mtp`; provenance header (`--runner-image`, `--decode-tune`, `--note`); restores slot config |
| Matrix scripting | `installer/bench/profile-matrix.sh` | named cells (`fpx-batch`, `fpx-kv27`, …) as seam calls |
| Aggregator | `installer/bench/generate_results_json.py` | flattens llama-bench JSON + `.meta.json` → `index.json` + `SUMMARY.md` |
| Results | `/var/lib/hal0/benchmarks/` | `runs/`, `logs/`, `server-ab/`, `index.json`, `SUMMARY.md` |
| Skills | `installer/agent-skills/hal0-bench`, `hal0-tune` | agent-facing method + seam etiquette |
| Agent runtime | `hal0-agent@hermes.service` | unprivileged autonomous agent on the box |
| Public display | `docs/reference/model-roster-benchmark.mdx` → site repo's `ModelRoster.astro` + `data/model-roster.ts` | the existing table; data file currently produced by hand-run sessions |
| Roadmap hooks | `installer/bench/README.md` §Scope | "Upstream end-state: a `hal0 bench` CLI + `/api/benchmarks` route reading `index.json`" — this design is that end-state, fleshed out |

**Gaps this design fills:** no suite/planner layer (every batch is a hand-run
session), no unified schema across Tier A and Tier B results, no run-level
provenance in the roster data, no scheduler, no API/dashboard surface, no
automated publish of `model-roster.ts`, no regression detection.

---

## 2. Architecture overview

```
/etc/hal0/bench/suites/*.toml        (operator/agent declares ONCE)
        │
        ▼
┌─ hal0 bench plan ─────────────────────────────────────────────┐
│ PLANNER: suite × registry × provenance → stale-cell worklist  │
│ (pure function, no GPU; runs anywhere, any time)              │
└──────────────┬────────────────────────────────────────────────┘
               ▼
┌─ hal0 bench run ──────────────────────────────────────────────┐
│ RUNNER: takes worklist → GPU-window gate → drives             │
│   Tier A cells via `sudo -n hal0-benchctl …`                  │
│   Tier B/C cells via server_ab.py                             │
│ one cell at a time · resumable · watchdog · budget-aware      │
└──────────────┬────────────────────────────────────────────────┘
               ▼
/var/lib/hal0/benchmarks/v2/…        RESULT STORE (records.jsonl + bench.db)
               │
      ┌────────┼───────────────┐
      ▼        ▼               ▼
 /api/benchmarks   hal0 bench publish     regression check
 (dashboard page)  (→ roster.json →       (journal event +
                    site-repo PR)          board task)

Triggers: hal0-bench.timer (weekly window) · model pull → smoke suite ·
runner-image/profile-flag change → provenance drift → planner marks stale
```

Everything left of the store is **scripts** (deterministic, testable).
Everything that requires judgment (accept a flag change, write the "what the
data shows" prose, decide a regression is real) is the **autopilot skill**
(§10) sitting on top of the same CLI verbs.

---

## 3. Result store & record schema v2 (the "more detail on runs" core)

### 3.1 Layout

```
/var/lib/hal0/benchmarks/
  runs/ logs/ server-ab/ index.json SUMMARY.md      # v1, untouched (existing tooling keeps writing here)
  v2/
    records.jsonl          # append-only, one JSON record per measured cell-run
    bench.db               # SQLite index rebuilt from records.jsonl (derived, disposable)
    artifacts/<run_id>/    # raw engine output per run: llama-bench JSON,
                           # server_ab result JSON, cell logs, sampled telemetry
    roster.json            # latest published roster snapshot (what the docs show)
```

`records.jsonl` is the source of truth: append-only, human-greppable,
trivially syncable off-box. `bench.db` is a derived index for the API
(rebuilt by `hal0 bench reindex`; never authoritative). v1 `index.json`
records are imported once by a shim so history isn't lost.

### 3.2 The record (one per cell × run)

```jsonc
{
  "schema": 2,
  "run_id": "2026-07-05T03:12:44Z-a1b2c3",       // stamp + short random
  "suite": "roster-weekly",                       // suite id, or "manual"/"skill:<name>"
  "trigger": "timer",                             // timer | model-pull | provenance-drift | manual | agent
  "cell_key": "sha256:…",                         // hash of the identity block below — the dedup/staleness key

  // ---- identity: WHAT was measured (all fields feed cell_key) ----
  "model": { "id": "chadrock3-6-35b-uncensored-mtp-strix-lean",
             "gguf": "…/model.gguf", "sha256": "…", "quant": "ROCmFPX",
             "size_bytes": 26843545600, "caps": ["mtp","vision","coder"] },
  "engine": { "kind": "llama-bench" | "llama-server",
              "image": "ghcr.io/hal0ai/…:rocm-7.2.4-rocmfp4-server",
              "image_digest": "sha256:…", "llamacpp_build": "b9438-22cadc194",
              "decode_tune": "rpb2-nwarps2" },
  "lane": "rocm" | "vulkan_radv",
  "config": { "argv": ["-b","512","-ub","512","-fa","on","-ngl","999", "…"],   // RESOLVED, post-dedup
              "env": {"HSA_OVERRIDE_GFX_VERSION":"11.5.1", "…":"…"},
              "kv": {"main_k":"q8_0","main_v":"q8_0","draft_k":"q4_0","draft_v":"q4_0"},
              "spec": {"type":"draft-mtp","n_max":3,"p_min":0.25,"backend_sampling":false} | null,
              "parallel": 1, "ctx": 32768 },
  "workload": { "kind": "pp" | "tg" | "chat" | "batch" | "embed" | "rerank" | "reuse",
                "depth": 32768,                    // ctx-fill at measurement (2k/32k/128k axis)
                "n_prompt": 2048, "n_gen": 256,
                "sampler": {"mode":"greedy"} | {"mode":"production","temp":0.6,"top_p":0.95,"top_k":20},
                "concurrency": 1 },

  // ---- environment: WHERE it was measured ----
  "host": { "name": "hal0", "platform": "strix-halo", "gpu": "Radeon 8060S (gfx1151)",
            "kernel": "6.15.4", "rocm": "7.2.4", "mem_gb": 128,
            "hal0_version": "0.9.0", "exclusive": true },

  // ---- results: full detail, not just the median ----
  "reps": [                                        // one entry per repetition
    { "t_s": 101.2, "prefill_ts": 812.4, "decode_ts": 99.1, "ttft_ms": 412,
      "accept_rate": 0.71, "drafted": 812, "accepted": 577,
      "timings_raw": {"…": "verbatim llama-server timings block"} }
  ],
  "summary": { "decode_ts_med": 99.0, "decode_ts_stddev": 0.8,
               "prefill_ts_med": 810.0, "ttft_ms_p50": 415, "ttft_ms_p95": 468,
               "accept_med": 0.71,
               "aggregate_ts": null, "per_stream_ts_med": null },   // batch mode fills these
  "telemetry": { "vram_peak_mb": 24810, "gtt_peak_mb": 31200,
                 "gpu_edge_temp_max_c": 78, "gpu_power_avg_w": 92,
                 "throttled": false },              // sampled from amdgpu sysfs during the run
  "outcome": "ok" | "failed" | "skipped-contended" | "oom" | "hang",
  "artifacts": "v2/artifacts/2026-07-05T03…/",     // raw engine JSON + logs live here
  "note": "free text (server_ab --note passthrough)"
}
```

Design points:

- **`cell_key` = hash(identity block).** Two runs with the same key measure
  *the same thing*; the newest `ok` record is the current value, older ones
  are history (trend line). Any provenance change — model digest, image
  digest, resolved argv, KV, depth, sampler — changes the key, which is
  exactly what makes auto-update (§6) a set-difference rather than a policy
  file.
- **`reps[]` keeps every repetition raw** (plus the verbatim server timings
  block). Medians are derived in `summary` for display, but scatter/stddev
  is inspectable per run — the runbook's "reproduced across ≥3 runs" gate
  becomes checkable from stored data.
- **`telemetry`** is new: a 1 Hz sampler (amdgpu hwmon + `/sys/kernel/debug`
  GTT/VRAM counters where readable, else `rocm-smi`) run by the harness
  around each cell. It answers "did that number come from a throttled run"
  and gives the C-GTT starvation cell hard data. `throttled` flags any rep
  where the sustained clock dropped >10% below the run's own p95 clock.
- **v1 compatibility:** `generate_results_json.py` grows a `--emit-v2` flag
  that additionally appends schema-2 records (identity from `.meta.json` +
  llama-bench row; `reps` from the per-rep samples llama-bench already
  reports). `server_ab.py` writes v2 natively (its provenance header §
  already carries most of the identity block). Nothing existing breaks.

---

## 4. Suites — the declarative layer (`/etc/hal0/bench/suites/*.toml`)

A suite = *what to measure* × *under what config* × *when it's stale*.
Shipped as **virtual seeds** (code-defined, self-healing on upgrade — same
pattern as seed profiles) with operator TOML overrides.

```toml
# /etc/hal0/bench/suites/roster.toml  (the suite behind the public table)
[suite]
id          = "roster"
description = "Uniform head-to-head across the chat+coding roster"
schedule    = "weekly"           # consumed by the timer policy (§6)
budget_min  = 240                # hard wall-clock cap per session; planner orders cells by value
exclusive   = true               # uses --exclusive windows; never publishes contended numbers
priority    = 50

[selector]                        # which models — resolved against the registry at plan time
caps_any    = ["chat", "coder"]  # registry capability tags
installed   = true               # only models present under /mnt/ai-models
# explicit include/exclude lists also supported

[matrix]                          # the axes; identity fields not listed are pinned to model defaults
lanes    = ["default"]           # "default" = the model's preferred profile lane; or ["rocm","vulkan_radv"]
depths   = [2048]                # roster is the uniform 2k board; tune suites use [2048,32768,131072]
samplers = ["greedy"]
reps     = 3

[cells]                           # which measurement kinds
kinds = ["pp", "tg"]             # roster: prefill + decode (+ MTP acceptance implied when model has MTP)

[staleness]                       # when is a cell's newest record too old anyway?
max_age_days = 30                # even with no provenance drift, refresh monthly
```

Other seed suites:

- **`smoke`** — trigger `model-pull`; one model, default lane, 2k depth,
  1 rep, ~3 min. Purpose: a freshly pulled model gets a number (and an OOM/
  breakage signal) within minutes, automatically. Non-exclusive is allowed
  but the record is marked `skipped-contended` for publishing purposes if
  the GPU wasn't idle.
- **`lane-matrix`** — both lanes × 3 depths on the profile-class
  representatives; feeds `hal0-tune`. Schedule `monthly`.
- **`concurrency`** — `server_ab --mode batch` np{1,2,4,8} on the agent-class
  slots; the Tier C cells. Schedule `on-demand` (agent/operator only).
- Named runbook matrices (like the FPX one) are just suite files checked
  into `handoffs/` alongside the doc that motivates them — runnable with
  `hal0 bench run --suite ./fpx-retune.toml`.

---

## 5. The `hal0 bench` CLI (new `src/hal0/cli/bench_commands.py`)

The single operator/agent surface; everything below it already exists or is
§3/§4 machinery. Runs unprivileged; Tier A cells go through the seam.

```
hal0 bench plan   [--suite ID|PATH] [--json]   # what's stale and why; no GPU, no writes
hal0 bench run    [--suite ID|PATH] [--budget-min N] [--dry-run]
                                               # execute the plan (or a slice of it)
hal0 bench status                              # live: current cell, queue, ETA, last session log
hal0 bench results [--model M] [--since D] [--json]   # query bench.db
hal0 bench history --cell KEY|--model M        # trend for a cell/model over time
hal0 bench reindex                             # rebuild bench.db from records.jsonl
hal0 bench publish [--check]                   # regenerate roster.json (+ docs data file, §9.2)
hal0 bench import-v1                           # one-time: index.json + server-ab/*.json → v2 records
```

**Runner behavior (`run`):**
1. Planner produces the worklist, ordered by (suite priority, staleness
   severity, cheap-before-expensive within a model so partial sessions still
   publish something coherent).
2. **GPU-window gate:** refuses if GPU slots are active, exactly like the
   harness today. With `exclusive=true` the runner brackets the *whole
   session* with one stop/restart of GPU slots (not per cell — restarts are
   the slow part), via a new seam verb (§12). It re-checks between cells
   that no slot was manually started; if one was, it aborts cleanly and the
   remaining cells stay queued.
3. Executes each cell → appends the v2 record → moves on. A cell failure
   (`oom`/`hang`/timeout, per-cell watchdog = 3× expected duration) records
   `outcome:"failed"` with the log in `artifacts/` and continues; it does
   NOT kill the session.
4. **Resumable by construction:** the plan is a set-difference against the
   store, so re-running after a crash/reboot recomputes the remaining cells.
   No queue state to persist or corrupt.
5. On session end: `aggregate` (v1) + reindex (v2) + regression check (§11)
   + journal event `bench.session.completed {suite, cells_ok, cells_failed,
   duration}`.

---

## 6. Auto-update semantics (the "set and forget" core)

**A cell is stale iff:**
1. No `ok` record exists for its `cell_key` (never measured — includes the
   case where *any identity input changed*: new model digest after re-pull,
   new runner image digest, changed resolved argv because a seed profile or
   registry `extra_args` changed, new llama.cpp build); **or**
2. Newest `ok` record is older than the suite's `max_age_days`.

Because resolved argv/env are part of the identity, **a merged profile-flag
PR automatically invalidates exactly the affected cells** and nothing else.
No "please re-bench" checklists.

**Triggers → planner:**

| Trigger | Mechanism | Suites consulted |
|---|---|---|
| Weekly window | `hal0-bench.timer` → `hal0-bench.service` (oneshot, `User=hal0`, runs `hal0 bench run --scheduled`) | all suites whose `schedule` window matches |
| Model pulled/registered | pull completion hook emits journal event → a lightweight dispatcher (the same service, `--trigger model-pull <id>`) | `smoke` |
| Provenance drift | nothing to do eagerly — drift is *discovered* by the next `plan` because the cell_key changed | all |
| Operator/agent | `hal0 bench run …` | named suite |

`--scheduled` adds the politeness policy: only proceed if (a) inside the
configured maintenance window (`/etc/hal0/bench/window.toml`, default
Sun 03:00–07:00 local), (b) no active `/v1/*` traffic in the last 10 min
(check hal0-api's throughput history — the route exists), (c) not on
battery/thermal alarm. Otherwise exit 0 and let the next timer tick retry —
a skipped week is fine, a corrupted-numbers week is not.

**Timer unit (installer/systemd/):**
```ini
# hal0-bench.timer
[Timer]
OnCalendar=Sun *-*-* 03:00
Persistent=true
RandomizedDelaySec=15m
```

---

## 7. API — `/api/benchmarks` (new `src/hal0/api/routes/benchmarks.py`)

Read-only over `bench.db` (+ one action). Registered like `throughput.py`.

```
GET  /api/benchmarks/roster                 # what the docs table shows: per model,
                                            # current decode/prefill/acc + config chip data
GET  /api/benchmarks/cells?model=&lane=&depth=&kind=&since=
                                            # filtered current-value matrix (compare view)
GET  /api/benchmarks/runs?suite=&limit=     # session list (run groups w/ outcome counts)
GET  /api/benchmarks/runs/{run_id}          # FULL record incl. reps[], telemetry, artifacts index
GET  /api/benchmarks/history?cell_key=      # time series for trend charts
GET  /api/benchmarks/plan                   # current staleness report (what would run and why)
POST /api/benchmarks/run {suite}            # kick a session (guarded; same GPU gate applies)
GET  /api/benchmarks/events                 # SSE: session progress (cell started/finished)
```

`GET /runs/{run_id}` is the "more detail" contract: everything in §3.2 plus
a pointer list into `artifacts/` (raw llama-bench JSON, logs) served with
sane size limits.

---

## 8. Dashboard — the **Benchmarks** page (ui/)

PLAN.md already reserves a Benchmarks UI; this is its scope. Three levels of
zoom, mirroring llm.ciru.ai's useful parts:

1. **Roster board** (default view) — the docs table, live: model × decode/
   prefill/acc%/caps/spec/KV/size, sortable, filterable by cap/lane/quant
   family. Staleness badge per row ("measured 3d ago on current image" vs
   amber "provenance drifted — pending re-run"). A "Plan" pill shows
   `/api/benchmarks/plan` count and a Run button (POST, gated).
2. **Model detail** (row click) — per-lane × per-depth mini-matrix; history
   sparkline (decode t/s over time with provenance-change markers — vertical
   lines where image/flags changed, so a step in the trend has a visible
   cause); MTP acceptance trend; links to every run.
3. **Run detail drawer** (the deep zoom — the user's explicit ask) —
   for a `run_id`: full identity block rendered as chips (image@digest,
   build, lane, KV main/draft, spec params, sampler, depth, np, ctx) plus
   the environment chips (hal0 version, kernel, ROCm, exclusive flag),
   resolved argv/env in a copyable block, **per-rep table + scatter** (not
   just the median), TTFT p50/p95, telemetry strip (VRAM/GTT peak, temp,
   power, throttled flag), outcome + log link for failed cells, and a
   raw-JSON download.

Charts follow the existing dashboard patterns (`ui/src/dash`, live telemetry
header conventions); SSE from `/api/benchmarks/events` animates a session in
progress (nice-to-have, phase-late).

---

## 9. Docs-site publish — the public table, auto-updated

### 9.1 Data contract

`hal0 bench publish` renders **`roster.json`** (versioned schema) from the
store: one entry per roster-suite model with current summary numbers **plus
the run detail the docs will now show**:

```jsonc
{ "schema": 1, "generated": "2026-07-05", "host": {"gpu":"Radeon 8060S","mem_gb":128,"hal0":"0.9.0"},
  "models": [ { "id": "…", "decode_ts": 99.0, "prefill_ts": 810.0, "accept": 0.71,
                "caps": ["mtp","vision"], "spec": "draft-mtp", "kv": "q8/q8", "size_gb": 25.0,
                "detail": { "run_id": "…", "measured": "2026-07-05", "lane": "vulkan_radv",
                            "image": "…:rocm-7.2.4", "llamacpp_build": "b9438", "hal0": "0.9.0",
                            "depth": 2048, "sampler": "greedy", "reps": 3, "stddev": 0.8,
                            "ttft_ms_p50": 415, "argv_digest": "…", 
                            "history": [ {"date":"2026-06-01","decode_ts":97.2}, … ] } } ] }
```

### 9.2 Pipeline (two stages, both automated)

1. **On-box:** `hal0 bench publish` writes `v2/roster.json` and (if the repo
   checkout is present) regenerates the site data file
   (`data/model-roster.ts` stays the interface; the generator emits it from
   `roster.json` — `ROSTER_DATE` included). `--check` diffs without writing.
2. **To the site:** the autopilot skill (§10) — or the operator — opens a PR
   to the website repo with the regenerated data file whenever `publish
   --check` reports a change after a green weekly session. Publishing stays
   a PR (human-mergeable, diffable, revertible) rather than a live endpoint;
   that's the deliberate scale-down from llm.ciru.ai.

### 9.3 Docs table upgrade (run detail on the public page)

`ModelRoster.astro` gains **expandable rows** fed by `detail`: measured-on
date, lane, image/build, depth/sampler/reps/stddev, TTFT, and a 6-month
decode sparkline. The page's methodology aside starts rendering from data
(`generated`, host block) instead of prose that can drift. The
`model-roster-benchmark.mdx` in *this* repo needs only a short addition
documenting the expanded columns; the component work lands in the site repo.

---

## 10. Skills — the autonomy layer

### 10.1 `hal0-bench` (existing — update)
Add the v2 surface: `hal0 bench plan/run/results/history` as the preferred
verbs; seam etiquette unchanged. Document the record schema and where
artifacts live so any agent session can answer "why is this number what it
is" from the store.

### 10.2 `hal0-bench-autopilot` (new skill)
The weekly judgment pass, run by Hermes after the timer session (or invoked
manually). Procedure encoded in SKILL.md:

1. `hal0 bench status` — confirm last session outcome; for failed cells read
   the artifact log, classify (OOM → mark model/depth combination as
   excluded in the suite override; hang → retry once; else file board task).
2. Regression review (§11 output): for each flagged cell, check the trend
   against provenance markers. Drift explained by an image/flag change →
   annotate; unexplained >10% drop → re-run the cell once, and if it
   reproduces, open a board task with the two run_ids.
3. `hal0 bench publish --check` → if changed, regenerate, update the "what
   the data shows" prose ONLY if a headline fact changed (pre-registered
   claims live in the mdx; the skill edits them with the same
   accept/reject discipline as the runbook §2), open the site-repo PR.
4. Never publish contended/non-exclusive numbers; never lower `reps` to make
   budget — drop whole cells instead (planner already orders by value).

### 10.3 `hal0-tune` (existing — unchanged relationship)
Keeps consuming the same store; lane-matrix and concurrency suites replace
its ad-hoc sweeps as the data source.

---

## 11. Regression detection

Cheap and dumb on purpose (runs at session end, no ML):

- For each cell with ≥3 historical `ok` records: compare newest
  `summary.decode_ts_med` (or the cell's governing metric) against the
  **trailing median of the last 5**. Flag if worse by >10% AND the newest
  record's provenance equals the previous record's (i.e. nothing is *known*
  to have changed).
- Provenance-change steps are *not* regressions — they're annotated in
  history (the dashboard's vertical markers) and left to the autopilot to
  judge.
- Output: journal events (`bench.regression {cell_key, delta_pct, run_ids}`)
  + a board task when >2 cells regress in one session (systemic: thermal,
  kernel, driver).

---

## 12. Privilege / seam changes (minimal, same pattern)

The seam stays the entire privileged surface. Additions to `hal0-benchctl`:

```
gpu-quiesce start|end     # bracket an exclusive SESSION (stop/restart GPU slots once,
                          # with a systemd-run scoped watchdog that auto-restores after
                          # a max window even if the runner dies)
telemetry start|end <run_id>   # root-side 1 Hz amdgpu sampler → artifacts/<run_id>/telemetry.jsonl
```

Both validate a `run_id`/token so the agent can't hold slots down
indefinitely: `gpu-quiesce start` writes a bounded systemd timer that force-
restores slots after `window_max` (default 5 h) regardless of the caller.
Everything else in this design — planner, runner, store, API, publish — runs
as the unprivileged `hal0` user.

Sudoers file `packaging/sudoers/hal0-benchctl` is unchanged (same single
binary grant).

---

## 13. Implementation plan (each phase = one PR, independently shippable)

| Phase | Deliverable | Touches | Est. |
|---|---|---|---|
| **P1 — store + CLI read side** | schema v2, `records.jsonl` writers (`server_ab.py` native; `generate_results_json.py --emit-v2`), `bench.db` reindex, `hal0 bench results/history/import-v1` | `installer/bench/`, `src/hal0/cli/bench_commands.py`, tests in `tests/bench/` | M |
| **P2 — suites + planner + runner** | suite TOML loader + seed suites, `plan`/`run`/`status`, GPU-window gate, watchdog, seam `gpu-quiesce` | + `src/hal0/bench/` (new pkg), `installer/wrappers/hal0-benchctl` | L |
| **P3 — scheduling + triggers** | `hal0-bench.timer`/`.service`, `--scheduled` politeness policy, model-pull → smoke hook, journal events | `installer/systemd/`, pull hook, `manifest.json` | S |
| **P4 — API + dashboard** | `/api/benchmarks/*`, Benchmarks page (roster board → model detail → run drawer) | `src/hal0/api/routes/benchmarks.py`, `ui/` | L |
| **P5 — publish pipeline** | `hal0 bench publish`, `roster.json` contract, site data-file generator; site-repo: expandable-row `ModelRoster` | this repo + site repo | M |
| **P6 — autonomy + regression** | regression checker, `hal0-bench-autopilot` skill, telemetry sampler (`telemetry` seam verb) | `installer/agent-skills/`, seam | M |

Ordering rationale: P1/P2 make every *manual* bench session richer
immediately (the FPX runbook can already write v2 records); P3 turns on
set-and-forget; P4/P5 are the display; P6 closes the loop. P4 can start in
parallel after P1 (the API only needs the store).

---

## 14. Open questions

1. **SQLite vs JSON-scan for the API** — bench.db assumed above; if record
   count stays <10k, a startup JSONL scan with an in-memory index would cut
   a dependency surface. Decide in P1 by measuring reindex cost.
2. **Site-repo publish authorization** — the autopilot PR path needs a
   scoped token on the box (or the operator merges a locally-generated
   patch). Default to "generate + notify, operator pushes" until a scoped
   deploy key is set up.
3. **Telemetry sampler privileges** — hwmon is world-readable; GTT counters
   may need the root-side seam verb. Confirm on-box in P6 (ship without
   telemetry if debugfs is locked down; the field is nullable).
4. **Multi-box future** — `host.name` is in the identity's *environment*, not
   the cell_key. When a second box appears, cell_key gains a host dimension
   and the roster grows a host picker. Nothing else changes; deliberately
   not built now.
5. **Docs-side interactivity budget** — expandable rows + sparkline is
   Astro-island territory; keep it zero-JS-fallback (details/summary) so
   the docs stay fast.
