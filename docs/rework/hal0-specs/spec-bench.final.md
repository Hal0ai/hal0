I now have a complete, verified picture. Here is the implementation-ready spec.

---

# hal0 BENCH rework — implementation-ready spec

Verified against code at `/home/mint/hal0` @ `rework/descar` (HEAD `1131df7b`). Coordinates with `hal0-rework-plan.md` §20 (BENCH), §13 (OBS), §7.1a/b (model-owned config + runner registry), §7.5/§8 (SQLite), §11.2 (ports), P3-quadlet/P3-perms. Docstrings in the code reference an out-of-tree "DESIGN" doc (`docs/archive/handoffs/benchmark-system-design-2026-07-05.md`) — that is the stale origin; this spec supersedes it in-tree.

---

## PART 1 — CURRENT-STATE MAP (file:line)

### 1.1 The bench package `src/hal0/bench/` (3,984 LOC, 12 modules)

The "v2 benchmark system" **is merged into `rework/descar`** — there is no stash or `worktree-bench-integration` branch present (`git stash list` empty; `git worktree list` shows only the main checkout). The package is the current in-tree implementation.

| Module | LOC | Role |
|---|---|---|
| `cli.py` | 1034 | argparse verbs: `plan`/`run`/`worker`/`status`/`results`/`history`/`reindex`/`publish`/`eval`/`import-v1`. |
| `runner.py` | 587 | session driver: worklist→records; Tier-A via seam, Tier-B/C via `server_ab.py`. |
| `evalrun.py` | 516 | agentic tool-calling eval (separate quality track). |
| `planner.py` | 409 | pure staleness set-difference; expands suite×matrix→stale `Cell`s. |
| `store.py` | 275 | append-only `records.jsonl` + **derived** `bench.db` SQLite index. |
| `schema.py` | 271 | schema-2 `Record` dataclasses + `cell_key()` content-address. |
| `parsers.py` | 250 | parse llama-bench + server_ab output. |
| `publish.py` | 197 | `build_roster()` → roster.json. |
| `suites.py` | 181 | TOML suite loader (`[suite]/[selector]/[matrix]/[cells]/[staleness]`). |
| `control.py` | 127 | web control/queue state as JSON files under state root. |
| `regress.py` | 117 | trailing-median regression check. |

Key data-flow facts:
- **State root is out-of-tree:** `store.py:34` `DEFAULT_STATE_ROOT = "/var/lib/hal0-bench"`, resolved via `$HAL0_BENCH_STATE`/legacy `$BENCLAB_STATE` (`store.py:37-42`). Deliberately separate from hal0's `/var/lib/hal0/benchmarks` — this is the "built outside hal0" seam.
- **Store shape:** `records.jsonl` is source-of-truth (`store.py:74-82`); `bench.db` is a disposable index rebuilt from scratch each `reindex()` (`store.py:102-193`) with table `records` + view `current_cells` (newest `ok` per `cell_key`). **This is a *second, private* SQLite DB, not §13's `bench_run` in `/var/lib/hal0/hal0.db`.**
- **cell_key** (`schema.py:251-271`) = sha256 of canonical-JSON `Identity` (model+engine+lane+config+workload). Host/GPU is deliberately **excluded** (`schema.py:125-145`, 262-264) — so today two different physical GPUs collapse to the same cell (the core multi-device gap).
- **Resume-no-dup (#1261-style):** append-as-you-go (`runner.py:411`) + re-plan-recomputes-missing (`planner.py:334-409`, docstring 1-8) + Tier-A sweep memoisation per `(model,lane,depth,config)` (`runner.py:362,423-427,457-475`). `_clear_stale_sweep` (`runner.py:211-226`) forces a fresh seam run because `run_benchmarks.sh` is idempotent-skip. **Preserve all of this.**

### 1.2 The device-targeting bug (card0/card1 ↔ ROCm0/ROCm1)

There are **two independent device-selection mechanisms with no shared source of truth**:

**A. Runtime/slot path — visibility-env based, multi-GPU-aware (correct):**
- `SlotConfig.gpu_index: int|None` (`config/schema.py:326-339`).
- `container.py:962-970,989` resolves `gpu_index`; `container.py:1082-1084` calls `gpu_visibility_env(device, gpu_index)`.
- `providers/_gpu.py:176-205` `gpu_visibility_env`: gpu-rocm→`HIP_VISIBLE_DEVICES`+`ROCR_VISIBLE_DEVICES`; gpu-vulkan→`GGML_VK_VISIBLE_DEVICES`; gpu-cuda→CDI ordinal-0. NVIDIA per-index via `nvidia_cdi_devices(gpu_index)` (`_gpu.py:159-170`).
- Device nodes: `container.py:496-498` emits `--device=<dev>` from `resolve_gpu_device_paths()` (`_gpu.py:42-78`, enumerates `/dev/kfd` + all `/dev/dri` char nodes).

**B. Bench + profile path — hardcoded `-dev ROCm0`, single-card (broken):**
- **`installer/bench/config.sh:57-60`** — the real seam registry:
  - `[rocm]="ghcr.io/hal0ai/hal0-rocmfpx:c077206|/opt/rocmfpx/bin/llama-bench|2048|GGML_HIP_ENABLE_UNIFIED_MEMORY=1|-dev ROCm0"`
  - `[vulkan_radv]="…|512||-dev Vulkan0"`
  - `config.sh:29-33` `COMMON_RUN_FLAGS` hardcodes `--device=/dev/kfd`, `--device=/dev/dri/renderD128` (single render node), `--group-add 993/44`.
- **`config/schema.py`** profile flag strings embed `-dev ROCm0` verbatim: `954, 968, 1012, 1036, 1049`; MTP draft device map `schema.py:802` `_MTP_DRAFT_DEVICE={"rocm":"ROCm0",...}`, default `ROCm0` at `:815`, emitted `--spec-draft-device` at `:818`.
- Bench Python never sets `gpu_index` or visibility env: `runner.fetch_host` (`runner.py:102-119`) reads `/api/hardware` as **one** GPU (`_friendly_gpu`, `runner.py:122-137`, only `gpus[0]`); `_tier_a_cmd` (`runner.py:270-281`) shells `hal0-benchctl sweep <gguf> <lane>` with **no device arg**. `HAL0_BENCH_GPU` (`cli.py:139`) is a **display label only**.

**Root cause:** the probe *does* enumerate every card with a usable index — `hardware/probe.py:447-460` `_detect_amd_gpus`, `GPUInfo.index` = DRM `card*` order, asserted (probe.py:450-452, docstring) to equal ROCm ordinal. `GPUInfo` (`config/schema.py:1863-1889`) carries `index`/`drm_path`/`compute_capable`/`vulkan_capable`. But `/api/hardware`'s flat projection (`api/routes/hardware.py:99-159`, esp. `:113,136`) surfaces the full `gpus[]` yet bench reads only the primary. So: **the topology exists and the runtime can target it, but bench throws it away and the seam pins index 0.** On a 2-GPU box, every bench lands on `ROCm0` and all results claim the same (unlabelled) device.

Latent secondary bug: `probe.py:450-452` *assumes* DRM `card<N>` == HIP `ROCm<N>`; unverified on multi-vendor/multi-GPU hosts. The rework must key device identity on a **verified** ROCm ordinal (via `llama-bench --list-devices` inside the runner image), not on the DRM assumption.

### 1.3 API route `api/routes/benchmarks.py` (481 LOC)

Endpoints (all `def`, threadpool — `:14-18`): `GET /roster` (`:96`), `GET /plan` (`:189`), `GET /cells` (`:243`), `GET /history` (`:270`), `GET /runs` (`:300`), `GET /runs/{run_id}` (`:324`), `GET /evals` (`:345`), `GET/POST /queue` (`:390`,`:403`), `DELETE /queue/{id}` (`:446`), `POST /control` (`:451`), `POST /run` (`:465`), `GET /events` SSE-stub (`:472`).

Stitched-in seams to fix:
- **`/run` alias** (`:465-469`): thin back-compat that just calls `post_queue({"suite": …|"roster"})`. Redundant with `POST /queue`.
- **Raw `HTTPException`** scattered (no shared error envelope): `:199, 274, 331, 412, 414, 416, 420, 461`. hal0's other routes use a common error shape; these are outliers (flagged in the API review).
- **External-origin assumptions:** module reaches into `hal0.bench` internals by importing **private** planner functions `_is_tier_a_incompatible`, `_model_caps` (`:34-40`); `SUITE_DIR` is re-declared from env with a "keep in sync" comment (`:47-54`); registry is fetched by an **HTTP call back into localhost `/api/models`** (`planner.fetch_registry_models`, `planner.py:78-94`) rather than an in-process call.
- **SSE not wired** (`:472-480`): heartbeat-only; UI polls `/queue` every 3s instead.

### 1.4 systemd units (`installer/systemd/`)

- `hal0-bench.service` — `Type=oneshot`, `ExecStart=… hal0 bench run --suite roster --scheduled`, `User=hal0`, `TimeoutStartSec=6h`, intentionally **not** sandboxed (needs the `hal0-benchctl` sudo seam). Driven by:
- `hal0-bench.timer` — `OnCalendar=Sun *-*-* 03:00`, `Persistent=true`, `RandomizedDelaySec=15m`.
- `hal0-bench-worker.service` — `Type=simple`, `ExecStart=… hal0 bench worker --poll 10`, `Restart=on-failure`, `User=hal0`. Long-poller draining the UI queue; **defaults to `stopped`** (inert until UI Start).
- **Privilege model:** the worker/runner are unprivileged; every GPU-touching op goes through **`installer/wrappers/hal0-benchctl`** (`packaging/sudoers/hal0-benchctl` grant). The seam verbs: `run`/`run-model`/`sweep`→`run_benchmarks.sh`; `gpu-quiesce start|end` (stops/starts `hal0-slot@{agent,brain,flm,rerank}` — a **hardcoded slot list**, wrapper ~98-116); `telemetry start|end`; `aggregate`; `list`. The seam runs **rootful podman** with the images from `config.sh`.
- **No dedicated bench container/runner image** today: the bench runs `llama-bench` inside the same `hal0-rocmfpx` image the slots use, via the seam. Device = whatever `config.sh` `dev_args` pins.

### 1.5 UI `ui/src/dash/Benchmarks.tsx` (1160 LOC)

Four tabs (`:285-376`): **Roster** (`RosterTab :380`, `ModelRow :443`, `ModelDetail :509` with lane×depth×config matrix `:517-530` + throughput sparkline `Sparkline :251` + run-sweep chips), **Runs** (`:647`, `RunDetail :754`), **Evals** (`:906`), **Run Queue** (`QueueTab :967` — Start/Pause/Stop `:1030-1032`, exclusive toggle `:1035`, plan table `:1141`). Plain `apiGet/apiPost/apiDelete` (no react-query); polls `/queue` every 3s (`:306-310`).

Field coverage: renders decode/prefill tps, ttft p50/p95, accept%, telemetry (vram/gtt/temp/power/throttled `:792-795`), config argv, engine image/build. **Does NOT render `hw_hash`** (absent everywhere) and **has no device/GPU axis** — the only "device" dimension is **lane** (`rocm`/`vulkan_radv`, `laneLabel :81`, hardcoded two lanes). Host is a single object `{gpu,mem_gb,hal0}` (`:49,329-333`). Matrix/run grouping keys on `(lane,depth,config)` only (`:522,536`) → **runs from different cards silently collapse into one cell.** No baseline-delta/regression coloring (only sparkline min/max).

Separate, disjoint live-perf surface (unification target for §13): `useThroughputHistory` (`ui/src/api/hooks/useThroughputHistory.ts:38-62`, react-query, `GET /api/stats/throughput/history`) consumed by `dashboard-redesign.jsx`, `metric-cards.jsx`, `npu-pane.jsx`. Nav: route `main.jsx:30,322`; breadcrumb `chrome.jsx:403` labels the page `["Performance","Benchmarks"]`. **No existing standalone Performance/observability trends view** — Benchmarks.tsx *is* the perf page.

### 1.6 Coordination-target inventory (what exists vs. what must be built)

- `src/hal0/runners/` (§7.1b registry) — **does not exist yet.** Runner images live in two hand-synced places: `config/schema.py:851` `DEFAULT_ROCMFPX_IMAGE`, `:886-887` fallbacks, `:890-912` `resolve_default_image`, `SEED_PROFILES :915-1194`; and `installer/bench/config.sh:57-60` `BACKENDS`.
- `src/hal0/db/` (§8.1 SQLite core) — **does not exist yet.** `bench_run` (§13.3) is unbuilt; only `bench/store.py`'s private `bench.db` exists.
- `Model.preferred_runner`, `ModelDefaults.mtp/jinja` (§7.1a/b) — **planned, unwired** (`hal0-rework-plan.md:283-313`; `_apply_preferred_profile` exists at `slots/manager.py:2454`, `_apply_preferred_runner` does not).
- Tracker tasks already carved: **OBS-1** (metrics seam + `bench_run` schema), **OBS-4** (bench baseline-on-install + regression), **P3-quadlet**, **P3-perms** (`hal0-rework-tracker.md:107,114-121`). This BENCH rework **owns the bench half of OBS-1/OBS-4** and must land *after* ML-1 (SQLite pilot, §13.7).

---

## PART 2 — FIRST-CLASS DESIGN

### 2.0 Guiding shape

Fold bench **into** hal0: one SQLite DB (`/var/lib/hal0/hal0.db`), one runner registry, one hardware probe, one metrics view. Kill the out-of-tree state root and the private `bench.db`. Keep the good bones — pure planner, append-once resumability, sweep memoisation, the `cell_key` content-address discipline — but **extend the identity to include device + hardware**, and **replace the shell seam's hardcoded device with a probe-driven, per-device target.**

### 2.1 (a) HW-aware device targeting

**Add a `Device` to the measurement identity.** New dataclass (extends `schema.py`):

```
Device: class ("gpu-rocm"|"gpu-vulkan"|"gpu-cuda"|"cpu"|"npu"),
        index (int, the probed GPUInfo.index),
        rocm_ordinal (int|None, the VERIFIED llama-bench device ordinal),
        gfx (str, e.g. "gfx1151"), drm_path, pci_id, name
```

- **Topology source:** a new `bench/topology.py` reads the full `gpus[]` from `/api/hardware` (already exposed, `hardware.py:136`) + NPU (`hardware.py:149-150`). For each GPU it resolves the **verified** device ordinal by running `llama-bench --list-devices` **once** inside the target runner image (via the seam) and matching PCI id / VRAM to the probe's `GPUInfo` — this closes the `probe.py:450-452` DRM-vs-ROCm assumption. Result: an ordered list of `Device`s to bench.
- **`cell_key` gains the device dimension** (`schema.py:251-271`): add `Device.class + index + gfx + pci_id` to the hashed identity. Consequence: per-(model×lane×**device**) cells; a 2-GPU box now stores 2 distinct cells instead of collapsing. Host block still excludes environment (kernel/hal0_version) per the existing rationale (`schema.py:125-145`).
- **Runner targets a specified device**, not `ROCm0`:
  - Tier-A seam call (`runner.py:270-281` `_tier_a_cmd`) gains a `--device <rocm_ordinal>` (or `--card <index>`) argument; the seam (`config.sh`/`run_benchmarks.sh`) is parameterized to (1) set `HIP_VISIBLE_DEVICES=<index>`+`ROCR_VISIBLE_DEVICES=<index>` (or `GGML_VK_VISIBLE_DEVICES` for vulkan) and (2) substitute `-dev ROCm<n>`/`-dev Vulkan<n>` instead of the hardcoded `ROCm0`. Reuse the exact env mapping from `_gpu.py:gpu_visibility_env` — do **not** re-implement it; export it as the shared source of truth (see §2.7).
  - Tier-B/C (`server_ab.py`) already targets a *slot*; pin the slot's `gpu_index` for the bench (or spin a bench slot on the target card).
  - Device-node passthrough: replace `config.sh:33` hardcoded `renderD128` with the target card's render node from `resolve_gpu_device_paths()` / probe `render_path`.
- **NPU:** where a model has an NPU runner (`_detect_npu`, `probe.py:789-826`; `/dev/accel/accel*`), a `Device(class="npu")` cell measures the NPU path (FLM/npu provider). Degrade gracefully when absent (§13.5 hardware-graceful).
- **Per-device results:** every `bench_run` row (below) stores `device` + `hw_hash`; the matrix (2.4) has device as a first-class axis.

### 2.2 (b) Runner-aware image + flags

Bench consumes the **§7.1b runner registry** (`hal0/runners/RUNNER_IMAGES`) as the single source of the correct image + bench-tuned flags per runner — replacing `installer/bench/config.sh:57-60` and the `SEED_PROFILES` image pins.

- `RUNNER_IMAGES: {key → {image, image_digest, runtime_family, supports:{mtp,jinja,mmproj}, device_class, bench_bin, bench_flags}}`. `bench_bin` (`/opt/rocmfpx/bin/llama-bench`) and lane-specific `bench_flags` (ubatch defaults etc.) move here from `config.sh`.
- Bench resolves the image from the **model's `preferred_runner`** (2.3), then reads `image_digest` from the registry — feeding the record's `Engine.image_digest` (`schema.py:73-83`) *at plan time* instead of the current parse-it-back-from-output-for-display-only workaround (`runner.py:548-552`, `planner.py:272-281`). This makes engine provenance part of `cell_key` deterministically (a rebuilt image re-benches exactly the affected cells).
- The seam's `config.sh BACKENDS` map is **deleted**; `run_benchmarks.sh` receives image + bench_bin + dev_args as parameters from the Python runner (which reads `RUNNER_IMAGES`). One registry, no hand-sync (`config.sh:44-45` "keep in sync" comment goes away).

### 2.3 Model-config-driven baseline + write-back (coordinator additions #1, #3)

**Baseline config = the model's §7.1 record, not ad-hoc args.** Bench measures a model "as it will actually run":
- The planner's `_resolve_profile` (`planner.py:201-234`) is replaced by a resolver that reads the **model record** (§7.1: `preferred_runner`, `ModelDefaults{mtp,jinja,extra_args,n_gpu_layers,context_size}`, `profile`) via the **in-process** registry (SQLite `SqliteModelRegistry`, §8.3) — not the localhost HTTP hop. The resolved argv is exactly the launch argv the runtime would produce (reuse `container.py:561 resolve_argv` precedence, §7.1a low→high: runner image → profile tune → arch defaults → model metadata → slot overrides). This guarantees plan↔run↔runtime parity.
- **Write-back (auto-tuner loop):** after a **tuning matrix** run (2.4), a recommender picks the best config per objective (`tps` | `tps/watt` | `ttft`) and offers to write it back to the **model record**: set `preferred_runner` + `ModelDefaults.mtp/jinja/extra_args`. This is a write to the §7.1 model record (SQLite `model` table, §8.2) via the registry interface — **coordinate with ML-runner/ML-1**; bench must not write model config through any path other than the registry API. Gate behind explicit operator confirmation (dashboard "Apply best config" action → `POST /api/benchmarks/apply`).

### 2.4 TUNING MATRIX mode (the headline new feature; coordinator #2, #4)

Declarative **`TuningPlan`** — a list of config variants expanded into cells and run through the *same* planner→runner→telemetry orchestration (so dedup/resume/memoisation come for free).

**Axes (all optional; cartesian product, each point = one `bench_run` row):**
- **runner/backend:** `{rocm, vulkan, cpu}` — the vulkan↔rocm compare the user wants.
- **device:** `{card0, card1, …, npu}` from probed topology (2.1).
- **flags:** `mtp {on,off}`, `-b/-ub {…}`, `-ngl {…}`, `ctx {…}`, `-fa {on,off}`, KV-quant `-ctk/-ctv {…}`. This extends the existing `[matrix].configs` mechanism (`suites.py:41-59`, `planner._apply_flags:237-249`) — which already A/Bs flags into `cell_key` — with the runner+device axes.

**Shape:** `TuningPlan` is a suite variant: `[tuning]` table in the suite TOML (or an API body) listing `runners=[…]`, `devices=[…]`, and `configs=[[…]]`. The planner expands `model × runner × device × config × {pp,tg,…}` into `Cell`s exactly as today (`planner.plan:334-409`), each carrying its `Device` + `runner` in the identity. Because every variant lands in `cell_key`, re-running is a set-difference (no duplicate rows), and a budget-truncated matrix still publishes coherent partial results (existing value-ordering `planner.py:394-408`).

**Output:** a comparison matrix (rows = configs/runners/devices, cols = metrics: prefill/decode tps, ttft, mtp_accept, power_w, vram/gtt) with the **best cell highlighted** per objective, and a **vulkan-vs-rocm side-by-side** view.

Note: the existing `installer/bench/profile-matrix.sh` is a shell prototype of this — supersede it with the Python `TuningPlan` (it becomes dead once the seam is parameterized).

### 2.5 (c) Tracking → SQLite `bench_run` (§13.3 — the SAME table, extended)

**Delete the private out-of-tree store** (`store.py` `records.jsonl` + `bench.db` at `/var/lib/hal0-bench`). Bench writes to **`/var/lib/hal0/hal0.db`** via `src/hal0/db/` (§8.1) — the same DB and connection policy (WAL, `foreign_keys=ON`, `busy_timeout`) as the registry pilot and OBS metrics.

Extend §13.3's `bench_run` from `(id, ts, model_id, runner, profile, hw_hash, tps, ttft_ms, spec_accept, quality, baseline)` to carry the full bench signal (a superset that keeps §13's columns):

```
bench_run(
  id, ts, run_id, session_id, suite, trigger,
  model_id, runner, profile, config_label,       -- what
  device_class, device_index, gfx, hw_hash,      -- where (NEW: per-device)
  lane, kind, depth,                              -- workload
  prefill_tps, decode_tps, ttft_ms, mtp_accept,  -- perf (tps split per §13.2)
  power_w, tps_per_watt,                          -- efficiency (hal0 differentiator §13.2)
  vram_peak_mb, gtt_peak_mb, temp_c, throttled,  -- telemetry (schema.py Telemetry)
  quality,                                        -- from evalrun (§2.8)
  cell_key,                                       -- content-address (dedup/history)
  baseline INTEGER,                               -- is-this-row-the-baseline flag
  outcome, note, artifacts_path
)
```

- `hw_hash` = stable hash of `(platform, gfx, device_class, device_index, vram, npu_present)` — the per-hardware key §13.3/§13.4 needs and the UI must render (it currently can't, §1.5).
- **Current value / history:** replace the `current_cells` view (`store.py:179-189`) with an equivalent SQL view keyed on `cell_key` (newest `ok`) — now naturally per-device because device is in `cell_key`.
- **Artifacts** (raw llama-bench/server_ab JSON, telemetry.jsonl, logs) stay on disk under `/var/lib/hal0/benchmarks/artifacts/<run_id>/`; the row stores the relative path (as today, `store.py:64-70`).
- **Schema migration:** ships as a `db/migrations/NNN_bench.sql` (§8.1 forward-only runner). One-shot import of any existing `/var/lib/hal0-bench/records.jsonl` → `bench_run` on first boot (idempotent, mirrors §8.3 step 3; reuse the `import-v1` logic in `cli.py:652-905`).

### 2.6 (d) Display — unified Performance view

Extend `Benchmarks.tsx` into the §13 "Performance" surface (nav already labels it `["Performance","Benchmarks"]`, `chrome.jsx:403`):

- **Matrix view (new top-level tab):** per `(model × runner × device × hw)` — rows = configs/runners/devices, cols = metrics; **best-config highlight** per objective; **vulkan-vs-rocm side-by-side**; **"Apply best config"** → write-back (2.3).
- **Device axis first-class:** replace the two-value `laneLabel/laneColor` (`Benchmarks.tsx:81,92`) with a device+lane selector driven by `bench_run.device_*`/`hw_hash`. Fix the silent-collapse bug: matrix/run grouping key becomes `(runner, device_index, hw_hash, depth, config)` (was `(lane,depth,config)`, `:522,536`).
- **Regression-vs-baseline:** color cells by delta vs the `baseline`-flagged row (§13.4 "qwen3 was 45 tps, now 30"); the plan `reason` column stays.
- **Live progress:** wire the real SSE publisher into `GET /events` (`benchmarks.py:472-480`) — emit per-cell start/finish from the worker — replacing the 3s `/queue` poll (`Benchmarks.tsx:306`). Coordinate with §13's request-seam events.
- **Unify with live perf:** the `useThroughputHistory` live tps (`useThroughputHistory.ts`) and the stored `bench_run` baselines become two panels of one Performance view over the **one** SQLite metrics DB (§13.1 built-in summary reads the same tables).

### 2.7 (e) Run orchestration: planner → runner → telemetry → quiesce

Formalize the pipeline the code already implements ad-hoc, and preserve the resume/no-dup guarantees:

1. **planner** (`planner.py`) — pure staleness set-difference over `bench_run` (was `store.newest_ok_by_cell`); now includes device+runner axes and reads the model record in-process. Unchanged resumability contract (`planner.py:1-8`).
2. **runner** (`runner.py`) — per-cell execute+append; **preserve** append-as-you-go (`:411`), sweep memoisation per group (`:362,457-475`), `_clear_stale_sweep` fresh-run guard (`:211-226`), per-cell 3× watchdog (`:384`), budget wall (`:366`), between-cell Pause/Stop (`:372-380`). Now targets `Device` (2.1).
3. **telemetry** — the seam already has `telemetry start|end` (wrapper) writing 1 Hz amdgpu counters to `artifacts/<run_id>/telemetry.jsonl`; parse into `bench_run.{power_w,vram_peak_mb,gtt_peak_mb,temp_c,throttled}` (parsers already populate `schema.Telemetry`). Sample **the target card**, not card0. Reuse `hardware/probe` sensors (§13.5, hardware-graceful).
4. **quiesce** — replace the wrapper's **hardcoded** slot list (`hal0-slot@{agent,brain,flm,rerank}`) with the live slot set from `/api/slots`, and quiesce only slots on the **target device** (multi-GPU: benching card1 need not stop card0's slots). Keep the seam's `--exclusive` per-sweep model (`runner.py:14-17`) and the fail-safe traffic gate (`runner.py:155-165,354-357`). Coordinate quiesce with P3-quadlet: once slots are Quadlet `.container` units, stop/start via the same unit names.
5. **queue + worker lifecycle** — keep `control.py` state machine (stopped/running/paused) and the safe-by-default `stopped` (`control.py:39,84-86`); **migrate the JSON control/queue/status files to `bench_*` runtime tables** (§8.4 runtime-state-in-SQLite) so there's one DB. Worker (`cli.py:352-482`) unchanged in behavior.

### 2.8 (f) Fix the stitched-in seams

- **`/run` alias** (`benchmarks.py:465`): remove; callers use `POST /queue` + `POST /control`. (Keep one release of 308→ if any external caller depends on it, else delete.)
- **Raw `HTTPException`** (`:199,274,331,412,414,416,420,461`): route through hal0's standard error-envelope helper (align with the other routers; §P3-routers thin-router work).
- **External-origin assumptions:** stop importing planner privates (`_is_tier_a_incompatible`, `_model_caps`) — promote them to a public bench API; replace the localhost `/api/models` HTTP hop (`planner.py:78-94`) with the in-process `SqliteModelRegistry`; drop the duplicated `SUITE_DIR` env re-declaration (`:47-54`) in favor of one config source.
- **Hardcoded device:** everything in 2.1 (`config.sh:57-60`, `schema.py:802,954,968,1012,1036,1049`) becomes probe-driven / registry-driven.
- **Out-of-tree state root** (`store.py:34`): deleted; one DB under `/var/lib/hal0/`.

---

## PART 3 — COORDINATION NOTES

- **§13 `bench_run` table (OBS-1/OBS-4):** this is the *same* table; §2.5 extends it (device/hw_hash/tps-split/power/vram-gtt/quality). Land the bench columns as part of OBS-1's schema so nothing is written twice. Bench owns the **T3** rows; OBS owns **T1/T2**. The built-in Performance summary (§13.1/OBS-3) reads bench baselines from this table. **Baseline-on-install + regression (§13.4/OBS-4)** = the existing `regress.py` (`THRESHOLD_PCT=10`, trailing-median, provenance-guard `regress.py:44-57`) rehosted onto `bench_run`; `baseline` flag set on the first `ok` row per `(cell_key, hw_hash)` at install/first-load.
- **§7.1b runners:** bench is a **consumer** of `hal0/runners/RUNNER_IMAGES` (image+digest+bench_bin+bench_flags+device_class+supports). Do not build a parallel image map — delete `config.sh BACKENDS` and read the registry. Blocks on §7.1b landing.
- **§7.1a model record / write-back:** baseline config and write-back both go through the §7.1 model record (`preferred_runner`, `ModelDefaults.mtp/jinja/extra_args`). Writes via the registry interface only (ML-runner + ML-1 SQLite). Bench becomes the auto-tuner that closes the model-owns-config loop.
- **hardware/probe:** the topology source of truth. `GPUInfo.index`/`drm_path`/`compute_capable`/`vulkan_capable` (`config/schema.py:1863-1889`) + NPU (`probe.py:789-826`). **Fix or verify** the DRM-order-==-ROCm-ordinal assumption (`probe.py:450-452`) by cross-checking `llama-bench --list-devices`; reuse `_gpu.gpu_visibility_env` as the one env-mapping source (export it; don't duplicate in the seam).
- **§7.5/§8 SQLite:** bench state lands in `/var/lib/hal0/hal0.db` via `src/hal0/db/` (connection + migrate + `db/migrations/`). Sequenced **after ML-1** (§13.7). The bench control/queue/status JSON files migrate under §8.4 runtime-state tables.
- **P3-quadlet / P3-perms:** the quiesce mechanism (§2.7 step 4) and the `hal0-bench{,-worker}.service` units must survive the Quadlet + one-`hal0`-user changes. Once slots are Quadlet `.container` units, quiesce stop/starts them by unit name; the bench worker itself is a candidate for a Quadlet-managed unit (P3-quadlet task) — but keep it **unprivileged + seam-mediated** (the units are explicitly not sandboxed for the sudo seam; preserve that constraint or move the privileged sweep into a rootful Quadlet bench-runner `.container`).

### Suggested tracker task breakdown (BENCH-*)
1. **BENCH-1** — device identity: `Device` dataclass + `cell_key` extension + `bench/topology.py` (probe→verified ordinal). Depends on hardware/probe (exists).
2. **BENCH-2** — `bench_run` in `hal0.db` (extend OBS-1 schema); delete out-of-tree store; import-v1 migration. Depends on ML-1, OBS-1.
3. **BENCH-3** — runner-registry consumption + seam parameterization (`run_benchmarks.sh`/`config.sh` take image+device); delete `BACKENDS`. Depends on §7.1b.
4. **BENCH-4** — model-config-driven baseline resolver (in-process registry). Depends on §7.1a, ML-1.
5. **BENCH-5** — `TuningPlan` matrix mode + recommender + write-back. Depends on BENCH-1/3/4.
6. **BENCH-6** — API cleanup (drop `/run`, error envelope, kill privates/HTTP-hop) + SSE publisher.
7. **BENCH-7** — UI: matrix/device axis, regression-vs-baseline, vulkan-vs-rocm, apply-best, live SSE; unify with `useThroughputHistory` Performance view.
8. **BENCH-8** — orchestration/quiesce: live slot set + per-device quiesce; migrate control/queue/status to SQLite; Quadlet alignment. Depends on P3-quadlet/P3-perms.

**Key files to touch:** `src/hal0/bench/{schema,store,planner,runner,control,suites,cli}.py`, new `src/hal0/bench/topology.py`, `src/hal0/api/routes/benchmarks.py`, new `src/hal0/db/` + `db/migrations/`, new `src/hal0/runners/`, `src/hal0/providers/_gpu.py` (export mapping), `installer/bench/{config.sh,run_benchmarks.sh}` (parameterize/retire), `installer/wrappers/hal0-benchctl` (device arg + live quiesce), `installer/systemd/hal0-bench*.{service,timer}`, `ui/src/dash/Benchmarks.tsx` + `ui/src/api/hooks/`.