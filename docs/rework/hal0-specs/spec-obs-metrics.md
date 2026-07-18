# OBS-1 — Observability / §13 metrics core: implementation spec

**Repo:** `/home/mint/hal0` (branch `rework/descar`, verified). Plan: `/home/mint/hal0-rework-plan.md` §13 (1–7), §7.5 (SQLite pilot), §7.6 (request seam), §20 (BENCH), §21.3 (introspection), §23.2 seams **S8** (`db/` foundation) + **S12** (request seam), §23.3 (bench column ownership), §23.4 (sequencing), tracker rows `OBS-1..4` (`hal0-rework-tracker.md:123–126`).

Everything below is verified against the code as it stands today.

---

## PART 0 — current-state map of scattered metrics (file:line)

The plan calls metrics "scattered"; here is the line-by-line census of every existing measurement site, grouped by what it actually measures. The OBS-1 unification deletes all of these except the ones marked **KEEP** and routes them through one SQLite-backed seam.

### 0.1 Streaming throughput — TTFT + tok/s (per-slot deques)

- **`app.state.tps_events`** — `collections.defaultdict(deque(maxlen=4096))` keyed by slot name (`api/__init__.py:1112-1115`).
- **`app.state.ttft_events`** — `collections.defaultdict(deque(maxlen=128))` keyed by slot name (`api/__init__.py:1117-1120`).
- **`_instrument_streaming_throughput(response, app_state, slot_name, dispatch_started)`** — wraps a `StreamingResponse` body iterator; counts `b'"delta":'` markers in SSE chunks → appends `(mono_ts, tokens)` to `tps_events[slot_name]`; on first content delta appends `(mono_ts, max(0, now - dispatch_started))` to `ttft_events[slot_name]` (`api/routes/v1.py:100-153`). One-shot TTFT per response (`ttft_pending = ttft_events is not None`, `:128`).
- **`_record_nonstreaming_throughput(body_bytes, app_state, slot_name)`** — parses non-streaming JSON, reads `usage.completion_tokens`, appends `(now, completion)` to `tps_events`; pulls FLM-specific `usage.decoding_speed_tps` + `usage.kv_token_occupancy_rate_percentage` to `app.state.slot_throughput` / `app.state.slot_kv_occupancy` (`api/routes/v1.py:156-198`). Also bumps `app.state.slot_request_count[slot_name]` + `slot_last_used[slot_name]`.
- **`_dispatch_and_forward`** — the **only** call site that threads `dispatch_started=time.monotonic()` into the streaming wrapper + invokes `_record_nonstreaming_throughput` (`api/routes/v1.py:629-670`). This is the **single hook point** for §7.6's request seam on the v1 path; **S12**.

### 0.2 Throughput history read endpoint

- **`GET /api/stats/throughput/history`** (`api/routes/throughput.py:33-154`) — reads `app.state.tps_events`, bins (mono→epoch), returns `{window_s, bucket_s, samples[], per_slot{}}`. In-memory only; **no retention past the deque maxlen**, no SQL.
- Per-slot local-tps accessor `_per_slot_local_tps` (`api/routes/slots.py:613`+) reads the same deques.

### 0.3 Per-slot scrape — llama.cpp native + docker cgroup + systemd uptime

- **`_scrape_llama_metrics(port)`** (`api/routes/slots.py:673-797`) — `httpx.AsyncClient` against `http://127.0.0.1:{port}/metrics` + `/slots`. Parses `llamacpp:requests_processing` / `requests_deferred` / `kv_cache_usage_ratio` (latter synthesised from `/slots` `max(n_prompt_tokens)/n_ctx` because upstream's kv gauge was removed post-b9279). 0.5 s `httpx.Timeout`; degrades silently per endpoint. **Read pattern, not storage.**
- **`_docker_container_mem_bytes(container_name)`** (`api/routes/slots.py:800-844`) — `docker inspect → cgroupv2 → memory.current`. Returns 0 on failure.
- **`_local_slot_metrics(request)`** (`api/routes/slots.py:847-933`) — fans `_systemd_show` + `_docker_container_mem_bytes` + `_scrape_llama_metrics` out via `asyncio.gather`; merges into `{name: {mem_rss_mb, uptime_seconds, requests_processing, requests_deferred, kv_cache_usage, ctx}}`.
- **`GET /api/metrics`** (`api/routes/slots.py:936-1025`) — merges upstream-proxied `/api/slots/metrics` (multi-host fan-out via `stats_slots`) with local tps/ttft/mem/uptime/request_count/last_used; **returns live dict on every call, no persistence**.

### 0.4 FLM / NPU throughput + KV

- `app.state.slot_throughput: dict[str,float]` + `slot_kv_occupancy: dict[str,float]` + `slot_request_count: dict[str,int]` + `slot_last_used: dict[str,float]` (`api/__init__.py:1125-1128`). All per-slot mutable globals, written from `_record_nonstreaming_throughput` + `_touch_npu_shadow_count` (`:215-230`).

### 0.5 Per-slot TTFT samples (typed)

- **`SlotSamples`** (`slots/ttft_samples.py:35-88`) — `ttft_samples: deque(maxlen=128)`, `inflight: dict[req_id, mono_ts]`, `throughput_tps`, `kv_occupancy_pct`. `first_chunk(req_id)` records TTFT once, returns it; pops from `inflight`. `samples_from_events(events, window_s)` adapts raw `app.state.ttft_events[slot]` to a `SlotSamples` view for read-only aggregation (`:117-130`). **Reuses the raw deque** rather than carrying its own — read-only.
- `avg_ttft_across(slots)` + `avg_kv_cache_across(kv_cache)` — fleet aggregations (`:91-114`).

### 0.6 Prometheus exposition + JSON stub

- **`slots/metrics.py::render_slot_metrics(slots)`** (`slots/metrics.py:38-80`) — Prometheus text-format 0.0.4 with `hal0_slot_up{slot=}` / `hal0_slot_state{slot=,state=}` / `hal0_slots_ready_total`. Honest about scope: header comment at `:16-18` says per-slot llama metrics are "a follow-up: scrape each container's own /metrics when the toolbox images enable it." **Reuse the renderer** for the OBS T2 slot gauges.
- **`GET /api/metrics`** (`api/routes/health.py:224-226`) — returns literal `{"slots": {}, "hardware": {}, "dispatcher": {}}`. **Stub; never populated.** Delete or replace (see §3.2).
- **`GET /api/metrics/prometheus`** (`api/routes/health.py:229-257`) — calls `render_slot_metrics(await sm.list())`; SlotManager-missing returns empty body; `text/plain; version=0.0.4; charset=utf-8`.

### 0.7 Hardware probe + per-sample counters

- **`hardware/probe.py`** — **one-time, install-time** write of `/etc/hal0/hardware.json` (`HardwareInfo`). Detection paths: `_detect_amd_gpus` enumerates `/sys/class/drm/card*/device/mem_info_{vram,gtt}_{total,used}` (`:407-460`), NVML via `nvidia-smi`, vulkaninfo + lspci fallbacks. **Not a sampler** — runs at install / `hal0 probe`.
- **`hardware/gpu_view.py::sample()`** (`:178-217`) — the typed point-in-time sampler: returns frozen `GPUMemorySample{vendor, is_uma, vram_total_mb, gtt_total_mb, total_mb (max-pool), vram_used_mb, gtt_used_mb, used_mb (max-pool), gpu_busy (raw), util_is_forced_high}`. AMD reads `mem_info_*` sysfs + `power_dpm_force_performance_level` + `gpu_busy_percent` (no subprocess, `:130-157`); NVIDIA `nvidia-smi --query-gpu={utilization.gpu,memory.used,memory.total}` (`:160-175`).
- **`hardware/stats.py::HardwareStats.snapshot()`** (`:244-283`) — typed projection of the same view for the API route: `ram_used_gb`, `ram_available_gb`, `gpu_util`, `gpu_vram_used_mb`, `gpu_vram_total_mb`, `gtt_used_mb`, `vram_used_mb`, `util_is_forced_high`, `gpu_clock_mhz`, `gpu_temp_c`. `slot_port_occupancy` separately (not polled — issue #427).

### 0.8 Power / thermal (hwmon, one-shot)

- **`api/routes/power.py::_probe_power()`** (`:83-125`) — finds `amdgpu` hwmon dir by name → reads `power1_average` (µW → W), `temp1_input` (m°C → °C), `freq1_input` (Hz → MHz, falls back to `pp_dpm_sclk`); k10temp `temp1_input`. Runs in `asyncio.to_thread`.
- **`GET /api/stats/power`** (`:131-138`) — wraps it. No history.

### 0.9 Stats route surface today (what `/api/stats` returns)

| Route | Source | Persistence |
|---|---|---|
| `GET /api/stats/hardware` (`hardware.py:563-655`) | `HardwareStats.snapshot()` + upstream proxy + Proxmox host | in-process only |
| `GET /api/stats/slots` (`hardware.py:658-670`) | proxy upstream + local merge | in-process |
| `GET /api/stats/power` (`power.py:131-138`) | hwmon probe | in-process |
| `GET /api/stats/throughput/history` (`throughput.py:33`) | `tps_events` deques | in-process (bounded) |

### 0.10 Slot lifecycle events

- `SlotState` enum + `LEGAL_TRANSITIONS` map (`slots/state.py:98-119`). `SlotManager.set_state(name, to_state)` moves via `_current_state` + writes `state.json` (`slots/manager.py:627-679`). **No event log** beyond the journald log line (no row). **Reuse the transition edge** for OBS `slot_event`.

### 0.11 Slot memory snapshot (capacity)

- `slots/capacity.py::build_per_slot(slots, registry)` (`:222-339`) — per-slot `vram_mb`/`ram_mb`/`mem_mb`/`state`/`model_id`. Three attribution paths (FLM catalog → container cgroup → registry file-size+KV), max-pooled to defeat the UMA/Strix Halo GTT-not-in-cgroup under-report bug (`:319-330`).
- `_container_cgroup_mem_bytes` (`:149-219`) — podman/docker `inspect → cgroupv2 → memory.current`, **returns 0 on absent/unknown runtime**.
- `CapacitySnapshot.probe()` (`:405-462`) — system-wide `free_vram_mb / free_ram_mb / total_*` + slot-budget.

### 0.12 Bench system — completely separate DB + identity

- **State root** `/var/lib/hal0-bench` (env `HAL0_BENCH_STATE`/`BENCHLAB_STATE`, `bench/store.py:34-42`). Deliberately out-of-tree from `/var/lib/hal0`.
- **Append-only `records.jsonl`** (`:74-82`) + **derived `bench.db`** rebuilt from scratch by `reindex()` (`:102-193`). Schema-2 `Record` (`bench/schema.py:196-235`) with `Identity{Model, Engine, lane, Config, Workload}` + `Host` (NOT in cell_key) + `Rep[]` (raw llama timings) + `Summary` (medians) + `Telemetry{vram_peak_mb, gtt_peak_mb, gpu_edge_temp_max_c, gpu_power_avg_w, throttled}`.
- **`cell_key(identity)`** (`bench/schema.py:251-271`) = `sha256:hex(canonical_json(identity))`. **Excludes device + host** by design (`:262-264` comment: "we'd rather under-classify than fork a cell on a kernel bump"). Spec-bench.final §2.1 fixes this by adding `device` to identity (verified ROCm ordinal via `llama-bench --list-devices`).
- **Hardcoded `-dev ROCm0`** in `installer/bench/config.sh:57-60` + 4× `schema.py` profile strings (954, 968, 1012, 1036, 1049). Single-card bench even on multi-GPU. **Spec-bench.final fixes this; OBS-1 owns the schema landing.**
- Bench CLI 12 modules; total 3,984 LOC. `bench/store.py::reindex()` writes to a **second, private** SQLite DB (`$HAL0_BENCH_STATE/bench.db`). §23.3(d) says: delete this; the `bench_run` table lands in `/var/lib/hal0/hal0.db` instead.

### 0.13 Tool-loop / request seam (the §7.6 hand-off)

- `toolloop/engine.py::run_tool_loop(llm_fn, tools, dispatch_fn, *, max_rounds, on_event)` (S1, `:322`) — exists in skeleton form; brain + board_chat + omni_router all still have their own loops. Per-request metrics are recorded at the **route layer** today, not inside the loop. **OBS-1 reads the loop's `on_event`** as an optional second hook (thinking-block frame for §13.2's "hal0 differentiator" reasoning length).

### 0.14 Summary — every site the OBS-1 spec unifies

| Today | Source-of-truth storage | OBS target |
|---|---|---|
| `tps_events[slot]` / `ttft_events[slot]` deques | `app.state` (lost on restart) | `request_metric` rows (async off hot path) + rollup |
| `slot_throughput` / `slot_kv_occupancy` dicts | `app.state` | `request_metric.spec_accept_rate` / derived aggregates |
| `_scrape_llama_metrics` parsed per-request | live read at `/api/slots/metrics` | prefill_tps / decode_tps / ctx_used columns (llama `timings`) |
| `_docker_container_mem_bytes` | live cgroup read | `slot_sample.vram_bytes/gtt_bytes` (T2 sampler) |
| `_systemd_show` memory + uptime | live systemd read | `slot_sample` (T2 sampler) + `slot_event` (T2 transition log) |
| `HardwareStats.snapshot()` polled every 2.5 s | live, no history | `slot_sample` for slot GTT/VRAM + rollup for fleet-wide |
| `power.py::_probe_power` hwmon | live, no history | `slot_sample.power_w / temp_c` (or split fleet row) |
| `bench/store.py` + `bench.db` | separate file | `bench_run` rows in `hal0.db` (§23.3(d)); delete out-of-tree state root |
| `bench/schema.py::cell_key` (no device) | content-address | `bench_run` row + `cell_key` extended with device (`spec-bench.final §2.1`) |
| Prometheus `hal0_slot_up/state/ready_total` | live render | same renderer, fed by `slot_event`/`slot_sample` state view |
| `GET /api/metrics` JSON stub (empty) | nothing | native Performance summary (OBS-3) reads `request_metric`/`slot_sample` |

---

## PART 1 — architecture (the one seam + SQLite core + bundled opt-in stack)

### 1.1 Through-line

> **One measurement seam (§7.6, S12)** → **one SQLite (`hal0.db`)** → **in-process aggregator + native dashboard**. Prometheus/Grafana + Langfuse/OTLP are **bundled opt-in companion containers**, never a dependency of the shipped box. Plan §13.1 verbatim; sequenced after ML-1 (§13.7, §23.4).

### 1.2 Components

```
┌───────────── the §7.6 request seam ──────────────┐
│                                                  │
│  api/routes/v1._dispatch_and_forward             │
│  + toolloop/engine.run_tool_loop (on_event)      │
│  + api/routes/board_chat._chat_stream             │
│                                                  │
│  T1 capture → request_metric row (async, off hot)│
└────────────────────────┬─────────────────────────┘
                         │
┌───────────── T2 per-slot sampler ───────────────┐
│                                                  │
│  asyncio.create_task on lifespan startup        │
│  interval = 5s (configurable; UI knob = OBS-3)  │
│  reads hardware/gpu_view.sample + hwmon         │
│  → slot_sample row + slot_event on transition   │
└────────────────────────┬─────────────────────────┘
                         │
┌───────────── T3 bench ───────────────────────────┐
│                                                  │
│  bench/runner.py writes bench_run rows           │
│  (cell_key extended with device per spec-bench) │
│  → hal0.db; DELETE /var/lib/hal0-bench + bench.db│
└────────────────────────┬─────────────────────────┘
                         │
┌────────── src/hal0/db/hal0.db (one SQLite, ML-1) ┐
│                                                  │
│  schema_migrations (ML-1)                       │
│  model / model_file / model_backend (ML-1)      │
│  port_claim (ML-2 PortAuthority)                │
│  metric_rollup (OBS, hourly/daily aggregates)   │
│  request_metric   (OBS T1, OBS-1)               │
│  slot_sample      (OBS T2, OBS-2)               │
│  slot_event       (OBS T2, OBS-2)               │
│  bench_run        (OBS T3, OBS-4 + spec-bench)  │
└────────────────────────┬─────────────────────────┘
                         │
┌───────────── read API (§21.3) ──────────────────┐
│                                                  │
│  GET /api/stats            (OBS-3: rollups)     │
│  GET /api/system-stats     (OBS-3: latest)      │
│  GET /api/models/health    (per-slot snapshot)  │
│  GET /api/metrics/prometheus (extend slot gauges)│
│  Native Performance dashboard view (Benchmarks  │
│  pane + OBS-3 baseline/regression coloring)     │
└──────────────────────────────────────────────────┘

       (optional, off by default — `--with-observability`)
┌───────────── companion stack ───────────────────┐
│  prometheus + grafana as podman containers       │
│  scrape /api/metrics/prometheus (slot gauges)    │
│  + /api/stats (rollups, T1 NOT — T1 lives in SQLite only)│
│  Grafana ships pre-provisioned (hal0 datasource) │
│  Ports via §11.2 PortAuthority; LAN-bound        │
└──────────────────────────────────────────────────┘
```

### 1.3 Constraint hierarchy (zero-dep core)

| Surface | Always-on? | Dep? | Where |
|---|---|---|---|
| Request-seam capture (T1) | YES | none | in-process; writes `request_metric` async |
| Slot sampler (T2) | YES | none | in-process background task; writes `slot_sample`/`slot_event` |
| Bench recorder (T3) | YES | none | in-process; writes `bench_run` |
| In-process aggregator + `metric_rollup` | YES | none | background task |
| `/api/stats`, `/api/system-stats`, `/api/models/health` | YES | none | thin read over the tables |
| Native Performance dashboard pane (Benchmarks extension) | YES | none | UI reads §21.3 endpoints |
| Prometheus `/api/metrics/prometheus` (slot gauges + counters) | YES | none | extends existing renderer; off-stack compatible |
| Prometheus companion container | NO (opt-in) | podman + image | `--with-observability` install flag |
| Grafana companion container | NO (opt-in) | podman + image | same; pre-provisioned dashboards |
| Langfuse / OTLP export | NO (opt-in) | podman + image | future; off-stack |

### 1.4 What's NOT built in OBS-1 (out of scope, lands later)

- Langfuse / OTLP / OpenTelemetry exporters — fold in once a real use case appears; the §7.6 seam + `request_metric` schema are the surface they bind to (Decision D2 in the plan defers the auth gate).
- `hal0 doctor bundle` (§21.4) — uses `request_metric`/`slot_sample` as evidence source but is its own lane; sequenced after §21.2 + OBS-1.
- Multi-host aggregation — `/api/stats/*` already proxies upstreams for the multi-host case (`hardware.py:242-260` `_proxy_upstream_endpoint`); OBS-1 keeps that seam; per-host local DB stays the source of truth.
- `kb-1` auth gating on `/api/metrics/prometheus` — Decision D2 (plan §21.D2); OBS-1 leaves the route at "unauthenticated by convention" pending §1.

---

## PART 2 — SQLite schema (refines §13.3, lands in `db/migrations/003_metrics.sql`)

**This is the schema OBS-1 owns.** Migration number = `003` because ML-1 takes `001_registry.sql` and ML-2 PortAuthority takes `002_port_authority.sql`. Migration lands **after** ML-1 (S8 dependency, plan §13.7 + §23.4). Bench T3 columns (`tps_prefill`, `tps_decode`, `ttft_ms`, `spec_accept`, `quality`, `baseline`) ride along with this migration per §23.3(d); spec-bench.final §2 owns the *bench-internal* use of those columns.

```sql
-- 003_metrics.sql  (schema version 3)
-- Owner: OBS-1 (§13). Depends on ML-1 db/ foundation (PRAGMA foreign_keys=ON,
-- BEGIN IMMEDIATE transactions, schema_migrations runner).
--
-- Cross-references:
--   * §7.6 / S12 — request_metric populated from the request seam
--   * §7.5 / S8   — db/ connection layer (ML-1)
--   * §13.1       — zero-dep core; Prometheus companion is opt-in
--   §13.2 T1      — request_metric  (per-request)
--   §13.2 T2      — slot_sample     (per-slot timeseries)
--   §13.2 T2      — slot_event      (per-slot lifecycle)
--   §13.2 T3      — bench_run       (per bench cell-run; §23.3(d))
--   §13.6         — metric_rollup   (long-retention aggregates)
--
-- Cardinality discipline (plan §13.6): per-request detail lives in SQLite
-- only. Prometheus label cardinality = {slot, model, runner, device,
-- modality} — never per-request (avoids TSDB explosion).

-- ── T1 per-request metric ─────────────────────────────────────────────────
-- One row per request served through the §7.6 request seam. Async-written
-- off the inference hot path (see Part 4.1 hook). Retained for
-- `metrics.retention.request_days` (default 7) then pruned.
CREATE TABLE request_metric (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              TEXT    NOT NULL,            -- ISO-8601 UTC
  request_id      TEXT    NOT NULL,            -- x-request-id header (S12)
  slot_id         TEXT,                        -- slot name OR upstream id (nullable for dispatcher errors)
  model_id        TEXT,                        -- resolved model id (from registry)
  runner          TEXT,                        -- rocm|vulkan|cpu|flm|kokoro|comfyui (S6 lookup, NOT sniff)
  device          TEXT,                        -- runtime device token (S7): gpu-rocm|gpu-vulkan|cpu|npu
  modality        TEXT,                        -- chat|vision|embed|rerank|asr|tts|image|video (S6/§7.1d)
  prompt_tokens   INTEGER,
  completion_tokens INTEGER,
  ctx_used        INTEGER,                     -- n_ctx_used at completion (llama timings n_past or FLM equivalent)
  ttft_ms         REAL,                        -- first-content-delta latency (§13.2 T1)
  prefill_tps     REAL,                        -- prompt_tokens / prefill_seconds (llama timings prompt_seconds)
  decode_tps      REAL,                        -- completion_tokens / decode_seconds (llama timings predicted_per_second)
  queue_ms        REAL,                        -- now − dispatch_started (in-process wait inside dispatcher)
  total_ms        REAL,                        -- now − request_start
  cache_hit       INTEGER,                     -- 0|1 (llama timings cache_n or n_cache_hit)
  spec_accept_rate REAL,                       -- MTP / spec-decode accept (llama timings draft_n_accepted/draft_n)
  stop_reason     TEXT,                        -- stop|length|tool_calls|error
  ok              INTEGER NOT NULL,            -- 0|1
  error_code      TEXT,                        -- typed error code (Hal0Error.code) when ok=0
  client          TEXT                         -- ip prefix or user-agent hint (truncated for privacy)
);
CREATE INDEX idx_request_metric_ts      ON request_metric(ts);
CREATE INDEX idx_request_metric_slot_ts ON request_metric(slot_id, ts);
CREATE INDEX idx_request_metric_model_ts ON request_metric(model_id, ts);
CREATE INDEX idx_request_metric_ok_ts    ON request_metric(ok, ts);

-- ── T2 per-slot sample (timeseries) ────────────────────────────────────────
-- Sampler writes one row per (slot, tick). 5s default interval (configurable
-- via [metrics].sample_interval_s). Graceful-missing-sensor: any NULL field
-- is the sensor being absent, not "actually zero" (per plan §13.5 / #791
-- discipline). Downsample to `metric_rollup` for long retention.
CREATE TABLE slot_sample (
  ts              TEXT    NOT NULL,            -- ISO-8601 UTC
  slot_id         TEXT    NOT NULL,
  state           TEXT    NOT NULL,            -- SlotState enum value
  vram_bytes      INTEGER,                     -- resident VRAM (or UMA: max(VRAM, GTT))
  gtt_bytes       INTEGER,                     -- amdgpu GTT pool bytes (Strix Halo UMA-aware)
  ram_bytes       INTEGER,                     -- host RAM used by container cgroup
  gpu_util        REAL,                        -- 0..1 (raw; util_is_forced_high flagged separately if needed)
  npu_util        REAL,                        -- 0..1 (NPU/FLM only; NULL on non-NPU)
  power_w         REAL,                        -- hwmon power1_average → W
  temp_c          REAL,                        -- hwmon temp1_input → °C
  inflight        INTEGER,                     -- requests_processing scraped from llama-server
  kv_used         INTEGER,                     -- llama-server KV occupancy bytes (or FLM column occupancy %)
  PRIMARY KEY (ts, slot_id)
);
CREATE INDEX idx_slot_sample_slot_ts ON slot_sample(slot_id, ts);

-- ── T2 slot lifecycle event ────────────────────────────────────────────────
-- One row per SlotManager state transition (legal edge per
-- slots/state.py:LEGAL_TRANSITIONS). Cold→warm load time = the
-- (offline → ready) row's duration_ms. Plus gpu-arbiter-wait timing when
-- arbiter.guard_dispatch blocks (callers stamp the wait).
CREATE TABLE slot_event (
  ts              TEXT    NOT NULL,
  slot_id         TEXT    NOT NULL,
  event           TEXT    NOT NULL,            -- transition | load_started | load_done | arbiter_wait
  from_state      TEXT,
  to_state        TEXT,
  duration_ms     REAL,                        -- wall-clock cost of this event
  reason          TEXT                         -- human note (e.g. "backend-aware lazy load")
);
CREATE INDEX idx_slot_event_slot_ts ON slot_event(slot_id, ts);

-- ── T3 bench_run (replaces out-of-tree bench/bench.db; §23.3(d)) ────────────
-- One row per cell-run from bench. cell_key = sha256 of canonical-JSON
-- Identity block, EXTENDED with device (spec-bench.final §2.1 fixes the
-- bench-internal identity; this table accepts the extended key verbatim).
-- Writes happen ONLY from bench/runner.py (replaces the current
-- bench/store.py append + reindex path; the out-of-tree state root +
-- bench.db go away).
CREATE TABLE bench_run (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              TEXT    NOT NULL,            -- ISO-8601 UTC (derived from run_id prefix)
  run_id          TEXT    NOT NULL,            -- UTC stamp + suffix (matches records.jsonl shape)
  cell_key        TEXT    NOT NULL,            -- sha256 of canonical-JSON identity (device-extended)
  suite           TEXT,
  trigger         TEXT,                        -- manual|scheduled|on_install|on_pull
  model_id        TEXT    NOT NULL,
  runner          TEXT    NOT NULL,            -- S6 key
  profile         TEXT,                        -- profile id at bench time
  hw_hash         TEXT,                        -- sha256 of canonical HW fingerprint (CPU model, GPU list, ram, ROCm ver)
  device          TEXT,                        -- resolved device token (S7) — was missing from cell_key pre-OBS-1
  tps_prefill     REAL,
  tps_decode      REAL,
  ttft_ms         REAL,
  spec_accept     REAL,                        -- MTP accept rate
  quality         REAL,                        -- eval-run score (when applicable)
  power_avg_w     REAL,
  vram_peak_mb    INTEGER,
  gtt_peak_mb     INTEGER,
  gpu_temp_max_c  INTEGER,
  throttled       INTEGER,                     -- 0|1 (NULL when telemetry missing)
  baseline        INTEGER,                     -- 0|1 — is THIS row the (model × runner × hw) baseline?
  outcome         TEXT    NOT NULL,            -- ok|failed|skipped-contended|oom|hang
  raw             TEXT                         -- JSON: full record (mirrors records.jsonl line, kept for debug)
);
CREATE INDEX idx_bench_run_cell_ts ON bench_run(cell_key, ts);
CREATE INDEX idx_bench_run_model_ts ON bench_run(model_id, ts);
CREATE INDEX idx_bench_run_baseline ON bench_run(model_id, runner, hw_hash, baseline);

-- ── Long-retention aggregates ──────────────────────────────────────────────
-- Background aggregator downsamples request_metric (T1) hourly and
-- slot_sample (T2) per (slot × hour). bench_run stays row-level (rare
-- events). Plan §13.5: bounded storage; raw rows pruned at retention,
-- rollup kept long.
CREATE TABLE metric_rollup (
  bucket          TEXT    NOT NULL,            -- 'YYYY-MM-DDTHH:00:00Z' (hourly) | 'YYYY-MM-DD' (daily)
  dim_kind        TEXT    NOT NULL,            -- request_hourly | slot_sample_hourly | bench_daily
  dim_key         TEXT    NOT NULL,            -- JSON-encoded dimension object (e.g. {"model_id":"qwen3-4b","runner":"rocm"} or {"slot_id":"primary"})
  -- Common aggregates (any field unused for a dim_kind stays NULL):
  count           INTEGER,
  ok_count        INTEGER,
  ttft_ms_p50     REAL,
  ttft_ms_p95     REAL,
  tps_prefill_avg REAL,
  tps_decode_avg  REAL,
  tps_decode_p50  REAL,
  spec_accept_avg REAL,
  vram_bytes_avg  INTEGER,
  gtt_bytes_avg   INTEGER,
  power_w_avg     REAL,
  PRIMARY KEY (bucket, dim_kind, dim_key)
);
CREATE INDEX idx_metric_rollup_kind_bucket ON metric_rollup(dim_kind, bucket);
```

**Notes for the implementer:**

1. `PRAGMA foreign_keys=ON` is set in `db/connection.py::connect()` (ML-1); re-assert on every connect (it's per-connection in SQLite). The CASCADE pattern from `model_file`/`model_backend` (spec-ml1-sqlite §(b)) is the reference for `bench_run.raw` cleanup if a model row is deleted (currently `bench_run` keeps `model_id` as TEXT, not FK — deletion of a model row does NOT delete bench history; that's correct: bench results survive model removal for forensics).
2. `runner`/`device`/`modality` columns read from the **resolved** model record (§7.1d modalities + S6 runner registry). For request_metric, the seam reads these **after** dispatcher.dispatch resolves `call.resolved_model`, so partial-routing failures still get a populated row (`ok=0`, `error_code` set, `slot_id` may be null).
3. Cardinality discipline (plan §13.6): Prometheus export labels = `{slot_id, model_id, runner, device, modality}` — derived from aggregate queries, never per-request. Per-request rows live in SQLite only.
4. The `bench_run` row count grows slowly (≤ runs/day/suite); the JSON in `raw` is what `bench/publish.build_roster()` + planner consume today (kept 1:1 for round-trip).
5. `client` truncation: `prefix[:24]` of the IP + optional user-agent hint; never store full PII. Document this in the row-schema comment.

---

## PART 3 — files to add / touch (exact edit plan)

### 3.1 Files to ADD

```
src/hal0/db/migrations/003_metrics.sql                  # Part 2 DDL

src/hal0/metrics/                                        # new package — "obs" rejected; "metrics" matches slot/metrics.py + the seam name
├── __init__.py
├── seam.py                # RequestSeam — the §7.6 measurement point (S12)
├── capture.py             # T1 capture: build_request_metric_row() + async writer
├── sampler.py             # T2 sampler: background task + slot_sample + slot_event writes
├── aggregator.py          # background rollup task (hourly + daily)
├── retention.py           # background pruner (request_metric @ N days)
├── hooks.py               # hardware-graceful probes (reuse gpu_view.sample + hwmon)
├── read.py                # /api/stats / /api/system-stats / /api/models/health query helpers
├── schema.py              # request_metric / slot_sample / slot_event / bench_run dataclasses (pydantic v2, mirrors Model pattern)
└── prom.py                # extended Prometheus exporter (slot gauges + counters from slot_sample/slot_event)

tests/metrics/
├── test_seam.py            # RequestSeam: streaming + non-streaming + cancel; off hot path timing
├── test_capture.py         # async writer, batch flushing, backpressure
├── test_sampler.py         # slot_sample rows; transition → slot_event; missing-sensor degradation
├── test_aggregator.py      # hourly/daily rollup math; idempotent re-run
├── test_retention.py       # prune at N days; rollup survives
├── test_read_api.py        # /api/stats / /api/system-stats / /api/models/health contract
├── test_prom.py            # expanded /api/metrics/prometheus body
└── test_bench_owns_run.py  # bench/runner writes bench_run rows; no /var/lib/hal0-bench on disk; bench.db deleted
```

### 3.2 Files to TOUCH

| File | Change | Why |
|---|---|---|
| `src/hal0/api/__init__.py` (lifespan, ~line 945+) | Add `app.state.metrics_seam`, `app.state.metrics_sampler`, `app.state.metrics_aggregator`, `app.state.metrics_retention`; start/stop the background tasks in lifespan | OBS hooks live as app-state services |
| `src/hal0/api/routes/v1.py` (`_dispatch_and_forward`, ~line 629) | Wrap the call+forward with `RequestSeam.record(...)` → `record_response(...)` async context manager; `dispatch_started` already present | S12 is the single hook for T1 on the v1 path |
| `src/hal0/api/routes/board_chat.py` (`_chat_stream`) | Same S12 wrap; the board path emits to the same seam | §7.6 is shared by board_chat + brain + omni_router (plan §7.6) |
| `src/hal0/api/routes/health.py` (`GET /api/metrics`, ~line 224) | Replace the empty stub with `metrics.read.system_stats(app.state)` | §21.3 read API |
| `src/hal0/api/routes/hardware.py` (end, ~line 670) | Add `GET /api/stats`, `GET /api/system-stats`, `GET /api/models/health`; extend `/api/metrics/prometheus` to use `metrics/prom.py` | §21.3 read API |
| `src/hal0/slots/metrics.py` | **KEEP** `render_slot_metrics`; OBS-1 calls it from `metrics/prom.py` after extending the body with slot gauges from `slot_sample` (the per-slot llama scrape goes away from the route layer) | Single Prometheus renderer, OBS-fed |
| `src/hal0/slots/manager.py` (`set_state` ~line 640) | Emit `slot_event` row on every legal transition via the metrics seam (no behaviour change to FSM) | T2 lifecycle log |
| `src/hal0/slots/arbiter.py` (`guard_dispatch`, `enter_dispatch`) | Stamp `slot_event(event='arbiter_wait', duration_ms=...)` when a dispatch is gated | T2 contention metric (hal0 differentiator) |
| `src/hal0/bench/runner.py` | Replace `bench.store.Store.append_record` path with `metrics.capture.write_bench_run(row)` (writes `bench_run` + keeps `records.jsonl` as a debug mirror, optional); **delete** `bench.store.Store.reindex` writes to `bench.db` | §23.3(d) — one DB |
| `src/hal0/bench/store.py` | Reduce to `records.jsonl` reader + ROSTER file emitter; delete the `reindex()` private-DB write path | Out-of-tree DB goes away |
| `installer/bench/config.sh` | No structural change (this is the runner-image seam; spec-bench.final §2.1 owns the device-target fix) | Spec-bench owns this |
| `installer/systemd/hal0-bench.service` | Add `--metrics-backend sqlite` flag (or env var) so bench-worker writes to `hal0.db`, not the out-of-tree state root | §23.3(d) |
| `src/hal0/cli/registry_commands.py` (group already wired) | Add `hal0 metrics status`, `hal0 metrics prune [--days N]`, `hal0 metrics export --out path` (TOML/JSONL rollup dump) | Operator surface |
| `ui/src/dash/Benchmarks.tsx` (the existing perf pane) | Extend with the **native Performance** view: TTFT/TPS by model over rolling window (reads `/api/system-stats`), baseline-vs-current regression coloring using `bench_run.baseline` rows | OBS-3 native dashboard |
| `installer/install.sh` (or `hal0 setup --with-observability`) | Add `--with-observability` flag that provisions Prometheus + Grafana companion containers (port-authority'd, LAN-bound) | §13.1 opt-in |
| `installer/manifests/` (or `bundles/`) | Ship `prometheus.yml` + Grafana provisioning JSON (datasource + dashboards) for the companion stack | §13.1 zero-setup dashboards |
| `pyproject.toml` | **No new dep.** Stdlib `sqlite3` (already used), `httpx` (already), `asyncio` (already). | De-scar ethos; matches ML-1 |

### 3.3 What STAYS behind the interface (unchanged)

- `slots/capacity.py` (`build_per_slot`, `CapacitySnapshot`) — OBS-2's sampler **reads** from this; capacity stays a single-source view. Don't re-implement its memory math.
- `hardware/probe.py` + `hardware/gpu_view.py` + `hardware/stats.py` — install-time probe + typed sample stay; OBS-2's `hooks.py` wraps `gpu_view.sample()` + the hwmon reader.
- `bench/schema.py::Record` + `Identity` dataclasses — keep as the bench-internal source of truth; OBS-1 accepts the row via `metrics.capture.write_bench_run(record)` and serialises into `bench_run`.
- `bench/store.py::iter_records` + `newest_ok_by_cell` (read-only JSONL scan) — planner keeps using the JSONL directly; OBS-1 **does not** migrate `records.jsonl` data into SQLite. New bench rows go to `bench_run`; old `records.jsonl` lines remain readable for the JSONL tooling until an explicit migration script (out of scope for OBS-1, document in `bench/store.py` docstring).
- `toolloop/engine.py` — the optional `on_event` consumer for `request_metric.reasoning_*` is a follow-up; OBS-1 ships the v1+board seam hooks first.
- All 60+ call sites of `ModelRegistry`, `SlotManager`, `Dispatcher` — zero edits. The seam is a **wrapper**, not a fork.

### 3.4 What DELETES (none of this lands in main until verification)

| Symbol | Where | Deletion rule |
|---|---|---|
| `GET /api/metrics` empty JSON stub | `health.py:224-226` | replace with `metrics.read.system_stats` |
| `bench/store.py::reindex()` private DB write | `bench/store.py:102-193` | delete after `bench_run` writes verified |
| `/var/lib/hal0-bench/bench.db` | runtime | deleted by `hal0 metrics migrate --from-bench-store` CLI (one-shot, idempotent) **only after bench_run rows written successfully for one full bench cycle** |
| `bench/store.py::Store.append_record` (records.jsonl write) | `bench/store.py:74-82` | KEEP for the debug mirror; `metrics.capture.write_bench_run` calls it on a config flag (`bench.records_jsonl_mirror=true`) so an operator can grep the JSONL |
| `_scrape_llama_metrics` per-request scrape on `GET /api/slots/metrics` | `slots.py:673-797` | **move** to the slot sampler (T2 inflight + KV), not the per-request route — llama `timings` give exact TTFT/prefill/decode per request, the scrape is for queue depth only |
| `_local_slot_metrics` MEM + UP scrape | `slots.py:847-933` | **move** to the slot sampler; the route reads from `slot_sample` instead |

---

## PART 4 — the three tiers (T1 per-request, T2 per-slot, T3 bench)

### 4.1 T1 per-request capture

**Hook site:** `src/hal0/api/routes/v1.py::_dispatch_and_forward` (`:629-670`) is the canonical wrapper for the v1 path. `dispatch_started = time.monotonic()` (`:659`) is **already recorded**. Wrap the body in a `RequestSeam.record(...)` async context manager:

```python
# sketch (v1.py, edit around line 648)
from hal0.metrics.seam import RequestSeam, RequestEvent

seam: RequestSeam = request.app.state.metrics_seam
with seam.record(
    request=request,
    body=body,
    call=None,                              # populated after dispatch
) as ev:
    call = await dispatcher.dispatch(request, body=body)
    ev.bind(call)                           # slot_id / model_id / runner / device / modality
    dispatch_started = time.monotonic()
    response = await dispatcher.forward(call)
    if isinstance(response, StreamingResponse):
        response = _instrument_streaming_throughput(response, request.app.state,
                                                    call.upstream_name, dispatch_started=dispatch_started)
        ev.attach_streaming(response)       # wraps the body_iterator to capture ttft_ms + decode_tps at completion
        return response
    if isinstance(response, Response) and getattr(response, "body", None):
        _record_nonstreaming_throughput(response.body, request.app.state, call.upstream_name)
        ev.attach_nonstreaming(response)    # reads usage.{prompt,completion}_tokens + stop_reason
    return response
# on exit: ev.commit() enqueues the row via the async writer (backpressure-safe)
```

**Async + off hot path (plan §13.5):** the seam maintains a bounded `asyncio.Queue` (default 1024 rows) drained by a single background task that batches up to 64 rows into one `BEGIN IMMEDIATE` write. On overflow (sustained >1024 inflight rows), the writer drops the oldest with a metric + log warning — never blocks the request handler. Latency contribution = a `queue.put_nowait` and a dict copy ≈ <50 µs.

**Exact llama timings (not estimated):** the streaming wrapper's first content delta records `ttft_ms = (now - dispatch_started) * 1000`. Decode TPS **must come from llama `timings.predicted_per_second`** when the non-streaming body carries them (or from the SSE `usage` final chunk when streaming). The existing `tps_events` delta-counting is an **approximation** (counts `b'"delta":'` markers in SSE bytes — close, but not byte-exact). OBS-1 replaces this with a **preferred path**: when the upstream emits `usage` in the final SSE chunk (llama-server does), the seam captures `prompt_tokens / completion_tokens / timings.predicted_per_second / timings.prompt_per_second / timings.cache_n / timings.draft_n / timings.draft_n_accepted` from that chunk. When the upstream **does not** (FLM, comfyui), fall back to the existing delta-counting approximation and stamp `tps_source='approx'` on the row (transparency, not a silent bug).

**FLM / comfyui / NPU:** same seam, different fields. FLM carries `usage.decoding_speed_tps` (already extracted at `v1.py:193-198`); comfyui doesn't have a token throughput metric and gets `ok` + `total_ms` + `error_code` only.

**Board_chat path:** `api/routes/board_chat.py::_chat_stream` (per §7.6's "extract once" intent) wraps with the same `RequestSeam.record` — keeps the seam as the single hook.

### 4.2 T2 per-slot sampler

**Topology:** a single asyncio task started in `api/__init__.py` lifespan, default interval `[metrics].sample_interval_s = 5` (configurable; UI knob = OBS-3). One iteration = one `slot_sample` row per **dispatchable** slot + one `slot_event` row per transition since the last tick.

**What it samples (graceful-missing per §13.5):**

| Field | Source | When missing |
|---|---|---|
| `state` | `slot_manager.state(name)` | never (FSM) |
| `vram_bytes`, `gtt_bytes` | `hardware.gpu_view.sample()` (typed; already GTT-aware on Strix Halo, gpu_view.py:130-157) | `None` when DRM sysfs absent; **never** 0 |
| `ram_bytes` | `_container_cgroup_mem_bytes(slot_name)` from `slots/capacity.py` | 0 when container down |
| `gpu_util` | `hardware.gpu_view.sample().gpu_busy` (raw; flag `util_is_forced_high` separately if needed) | `None` when no counter |
| `npu_util` | FLM `/usage` block, polled at sample interval | `None` for non-NPU slots |
| `power_w`, `temp_c` | `api/routes/power.py::_probe_power` shape (run in `asyncio.to_thread`; never blocks loop) | `None` when no `amdgpu`/`k10temp` hwmon |
| `inflight` | `_scrape_llama_metrics(port).requests_processing` (moved from `slots.py:673`) | 0 when llama-server not reachable |
| `kv_used` | `_scrape_llama_metrics(port).kv_cache_usage` * n_ctx (or FLM column occupancy %) | `None` when not scrapeable |

**Slot events (transitions):** hook `SlotManager.set_state` (`slots/manager.py:640+`) to call `metrics_seam.emit_slot_event(...)` on every legal transition. Cold→warm load time = the `(offline → ready)` event's `duration_ms`. **No FSM behaviour change** — the seam records, the FSM runs.

**Arbiter contention (hal0 differentiator, §13.2):** when `slots/arbiter.py::guard_dispatch` blocks a dispatch (exclusive GPU image mode), the arbiter stamps `slot_event(event='arbiter_wait', duration_ms=...)` on release. Plan §13.2 names this as first-class.

**Interval + cost:** 5s × N slots × ~1 ms probe = trivial on a single-LXC box; for a 4-slot Strix Halo with FLM the row budget is ~70k rows/day raw (5s × 4 slots × 3600 × 24), ~1 MB at ~14 B/row after compression. Downsamples to `metric_rollup` hourly (per-slot aggregates) so the raw `slot_sample` can be pruned at `[metrics].retention.slot_sample_days = 3` (default; §13.5).

### 4.3 T3 bench (extends §20 + §23.3(d))

**`bench_run` rows live in `hal0.db`, not `/var/lib/hal0-bench/bench.db`.** Spec-bench.final owns the bench-internal changes (device in cell_key, image_digest, argv parity with runtime); OBS-1 owns the schema landing + the delete-the-out-of-tree-DB migration. The two specs share the table and the row format.

**One-shot migration (CLI, NOT in OBS-1 boot path):** `hal0 metrics migrate --from-bench-store [--dry-run]` reads `bench/store.py::iter_records()` lines, inserts into `bench_run`, then leaves the JSONL in place (operator rm's the dir after verification). Idempotent on `(run_id)`.

**Delete rules:**
- `/var/lib/hal0-bench/bench.db` — deleted only after a successful `migrate --from-bench-store` + one full bench cycle verified on the new schema.
- `/var/lib/hal0-bench/records.jsonl` — kept as the grep/debug mirror. `bench/store.py::Store.append_record` stays as a sink gated by `[bench].records_jsonl_mirror=true` (default ON, so the bench community can keep grepping).
- The new `HAL0_BENCH_STATE` env (and the legacy `BENCHLAB_STATE`) is **deprecated** but not removed in OBS-1 — plan §23.3(d) says "delete the out-of-tree state root" but the operator's workflow still wants the JSONL; OBS-1 deletes `bench.db` and the reindex write path, **not** the JSONL state root. Document in `bench/store.py` docstring that `/var/lib/hal0-bench/records.jsonl` remains the grep surface.

**Bench owns T3 rows (plan §13.3 + §23.3(d)):** only `bench/runner.py` writes `bench_run`. The CLI/UI surfaces (Benchmarks.tsx, the API bench routes) **read** `bench_run` via the metrics read API (§5).

### 4.4 Aggregator + retention (long-retention aggregates)

**Aggregator** (`metrics/aggregator.py`): background task that runs every hour (configurable; default 1h). Reads the last hour of `request_metric` per `(model_id, runner, device, modality)` and `slot_sample` per `slot_id`, computes the rollup columns in §2 (`ttft_ms_p50/p95`, `tps_*`, `spec_accept_avg`, etc.), upserts into `metric_rollup`. Idempotent (uses `INSERT OR REPLACE` keyed on `(bucket, dim_kind, dim_key)`). Daily rollups computed from hourly after 24h.

**Retention** (`metrics/retention.py`): background task that runs every 6h, prunes:
- `request_metric` rows older than `[metrics].retention.request_days` (default 7).
- `slot_sample` rows older than `[metrics].retention.slot_sample_days` (default 3) — replaced by the hourly rollup.
- `metric_rollup` rows older than `[metrics].retention.rollup_days` (default 90) — daily rollups replace hourly.

**Storage bound (plan §13.5):** at default settings on a typical 4-slot box: ~50 MB raw `request_metric` (7d) + ~5 MB `slot_sample` (3d) + ~20 MB `metric_rollup` (90d) + bench rows (negligible). **Bounded by config, never unbounded.**

---

## PART 5 — read API (§21.3)

### 5.1 `GET /api/stats` (thin read over `request_metric` rollup)

**Path:** `/api/stats` (replaces the empty stub at `health.py:224`). **Auth:** unauthenticated by convention (Decision D2); the KB-1 auth gate lands separately and gates `/api/metrics/prometheus` + mutating routes, not this read.

**Response shape:**
```json
{
  "window": {"from": "2026-07-18T00:00:00Z", "to": "2026-07-18T01:00:00Z", "bucket": "hour"},
  "totals": {"requests": 412, "ok": 408, "errors": 4, "tokens_completed": 184211},
  "by_model": [
    {
      "model_id": "qwen3-4b", "runner": "rocm", "device": "gpu-rocm", "modality": "chat",
      "ttft_ms": {"p50": 142, "p95": 311},
      "tps_decode": {"avg": 48.3, "p50": 47.1},
      "tps_prefill": {"avg": 1820},
      "spec_accept_rate": 0.62,
      "ok": 311, "errors": 2
    }
  ],
  "bench_baseline": {
    "qwen3-4b × rocm × hw:abc123": {"tps_decode": 49.1, "ttft_ms": 138, "captured": "2026-07-12T03:00:00Z"}
  }
}
```

**Query params:** `?window=1h|24h|7d` (default 1h), `?model_id=...`, `?runner=...`, `?bucket=hour|day`.

### 5.2 `GET /api/system-stats` (latest snapshot + fleet rollup)

**Response shape (latest sample + last 24h rollup):**
```json
{
  "ts": "2026-07-18T01:23:45Z",
  "fleet": {
    "ram_used_gb": 14.2, "ram_available_gb": 49.8,
    "gpu_util": 0.34, "gpu_vram_used_mb": 18234, "gpu_vram_total_mb": 96000,
    "gtt_used_mb": 22011,
    "gpu_temp_c": 64, "gpu_power_w": 87,
    "util_is_forced_high": false
  },
  "slots": [
    {"slot_id": "primary", "state": "serving", "vram_bytes": 8912345678,
     "gtt_bytes": 22011000000, "inflight": 3, "kv_used": 0.42,
     "last_used_ts": 1752800000.123}
  ]
}
```

**Replaces the existing per-route live scrapes** that `GET /api/stats/hardware` and `GET /api/stats/power` do today — they keep working for back-compat, but `/api/system-stats` is the one canonical surface (§21.3 verbatim).

### 5.3 `GET /api/models/health` (per-model per-slot snapshot)

**Response shape (plan §21.3 verbatim):**
```json
{
  "models": [
    {
      "checkpoint": "qwen3-4b",
      "last_use": "2026-07-18T01:22:18Z",
      "type": "chat",
      "device": "gpu-rocm",
      "pinned": true,
      "recipe": "qwen3-4b-rocm-fp4",
      "pid": 12345,
      "recipe_options": {"ngl": 99, "ctx_size": 32768, "parallel": 4},
      "backend_url": "http://127.0.0.1:8081",
      "health_ok": true,
      "ttft_ms_p50_24h": 142, "tps_decode_p50_24h": 47.1
    }
  ]
}
```

**Reads from:** `slot_event` (last transition = last_use) + `slot_sample` (latest VRAM/GTT/inflight) + `request_metric` (24h rollup for ttft/tps) + `slot_manager.snapshot()` (state, pinned, recipe, pid). Plus the §7.1b `RUNNER_IMAGES[runner].public_api_port` for `backend_url`.

### 5.4 `GET /api/metrics/prometheus` (extend, don't replace)

**What it adds on top of the existing `hal0_slot_up / hal0_slot_state / hal0_slots_ready_total`:**
- `hal0_slot_sample_vram_bytes{slot=}` (gauge, from `slot_sample` latest)
- `hal0_slot_sample_gtt_bytes{slot=}` (gauge)
- `hal0_slot_sample_inflight{slot=}` (gauge)
- `hal0_slot_sample_kv_used{slot=}` (gauge)
- `hal0_fleet_gpu_util`, `hal0_fleet_gpu_power_w`, `hal0_fleet_gpu_temp_c` (gauges)
- `hal0_request_total{runner=,model_id=,modality=,ok=}` (counter, from `request_metric` last hour)
- `hal0_request_ttft_ms_bucket{runner=,model_id=,bucket=...}` (histogram, from rollup)

**No per-request labels** (plan §13.6). Cardinality ceiling ≈ 4 labels × ~10 models × 4 runners × 4 devices × 8 modalities = ~5k series — bounded.

### 5.5 Native Performance dashboard view (OBS-3, extends Benchmarks.tsx)

**Reads:** `/api/system-stats` (latest) + `/api/stats?window=24h` (rollup) + `bench_run` rows tagged `baseline=1` (per-`(model × runner × hw)` baseline).

**Panes (extend the existing Benchmarks.tsx, don't fork):**
- **Headline KPIs** — TTFT p50/p95, decode tps p50, error rate (24h window).
- **Per-model table** — `model × runner × device` with current rollup + baseline tps + **regression coloring** (red if `current < baseline × 0.85`).
- **Per-slot sparkline** — VRAM/GTT/inflight over the last hour (reads `slot_sample` via the `/api/system-stats` payload, extended with a `slot_history` field for the past hour).
- **Bench baseline panel** — the `bench_run.baseline=1` rows surfaced as "the reference" with a one-click "re-benchmark" CTA that hits `POST /api/benchmarks/run` (existing bench route; OBS-4 owns baseline-on-install, OBS-3 just shows it).

**No new dashboard root.** Folds into the existing nav `["Performance","Benchmarks"]` (`ui/src/dash/main.jsx:30,322`; breadcrumb `chrome.jsx:403`) — Performance tab is the OBS-3 pane; Benchmarks tab stays as the deep-dive per-model matrix (spec-bench.final §2.4).

---

## PART 6 — bundled opt-in Prometheus/Grafana (companion containers)

**Off by default. On = `--with-observability` install flag.**

**Companion containers** (same mechanism as OpenWebUI/ComfyUI): `prom/prometheus` + `grafana/grafana`, pinned in `installer/manifests/` digest table (§7.1b's `RUNNER_IMAGES` shape — `runtime_family=companion`, `device_class=lan`).

**Ports via §11.2 PortAuthority** (`port_claim` table in `hal0.db`, ML-2):
- Prometheus: `9090/tcp` (or next free from `prometheus_start..prometheus_end`)
- Grafana: `3000/tcp` (or next free from `grafana_start..grafana_end`)
- LAN-bound (not loopback only); firewall through hal0's network-exposure posture (§21.11).

**Provisioning shipped in-tree** (`installer/observability/`):
- `prometheus.yml` — scrape `http://127.0.0.1:8080/api/metrics/prometheus` every 15s.
- `grafana/provisioning/datasources/hal0.yml` — the hal0 Prometheus datasource.
- `grafana/provisioning/dashboards/hal0.yml` — folder provider pointing at `grafana/dashboards/`.
- `grafana/dashboards/hal0-overview.json`, `hal0-slots.json`, `hal0-fleet.json`, `hal0-bench-baseline.json` — pre-built.

**Auth:** admin key (KB-1's `HAL0_ADMIN_KEY`) minted at install; Grafana ships with the admin password set via the companion env. **/api/metrics/prometheus unauthenticated by convention** until Decision D2 lands (plan §21.D2).

**Updates:** through the same companion-service lifecycle as OpenWebUI/ComfyUI; image digests pinned in `installer/manifests/digests.toml`.

---

## PART 7 — sequencing + dependency edges

**Strict order (matches §23.4 build DAG + plan §13.7):**

```
ML-1 (db/ foundation + 001_registry.sql)
  ├─ 002_port_authority.sql         (ML-2 PortAuthority, §11.2)
  │     └─ BLOCKS → 003_metrics.sql (this spec)
  ├─ §7.1d modalities split         (Model.modalities, normalize_modality)
  │     └─ BLOCKS → 003_metrics.sql request_metric.modality column populated
  └─ ML-4 runners registry           (RUNNER_IMAGES key surface)
        └─ BLOCKS → 003_metrics.sql request_metric.runner / device columns populated

003_metrics.sql (this spec, OBS-1 schema)
  ├─ OBS-1 T1 capture seam        (§7.6 / S12, v1 + board_chat hooks)
  ├─ OBS-2 sampler                (T2 slot_sample + slot_event)
  ├─ OBS-3 read API + dashboard   (extends Benchmarks.tsx; /api/stats, /api/system-stats, /api/models/health)
  ├─ OBS-4 bench runner lands cols+ deletes out-of-tree bench.db (§23.3(d))
  ├─ KB-1/§21.11 metrics auth     (Decision D2 — separate lane, blocked by KB-1)
  ├─ §21.4 doctor rework          (uses metrics as evidence source)
  └─ Optional export stack        (Prometheus/Grafana companion containers)
```

**OBS-1 owns:** 003 schema + T1 capture + T2 sampler + T3 bench wiring + §21.3 read API + native Performance dashboard pane + retention/aggregator background tasks. **Bundled companion stack = same PR or follow-up PR — implementer's call; one PR preferred to keep "shipped-box" coherent.**

**Deps OBS-1 needs before the schema migration can be written:**
1. `db/` foundation (ML-1) merged — gives `connect()`, `tx()`, `migrate()`, `PRAGMA foreign_keys=ON`.
2. `§7.1d` modalities — gives `normalize_modality()` so `request_metric.modality` is populated from a closed enum, not free text.
3. ML-4 runners registry — gives `resolve_runner_image()` so `request_metric.runner` is the S6 key, not a sniff.
4. `slot.id` (plan §11.1) lands **before** `slot_event` `slot_id` becomes stable — `id` is the immutable key; `name` is the mutable label.

If any of these aren't merged when OBS-1 lands, OBS-1 writes the column as NULL and the post-OBS-1 PR populates it; this is the "deferred column" pattern from spec-ml1-sqlite §(e) "ModelCapabilities mtp/jinja."

---

## PART 8 — risks (capped verification)

### 8.1 Top risks (ranked by blast radius)

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | The async writer queue overflows under burst (e.g. parallel agent loop) → metric loss | Med | bounded queue + drop-oldest + warning log; never block the request handler; document in metrics/seam.py docstring |
| R2 | `_scrape_llama_metrics` moves to the sampler — per-request `inflight` becomes 5s-stale, not real-time | Low | sampler interval 5s default; the dashboard's `inflight` indicator becomes "approximate"; per-request route reading falls back to the latest `slot_sample` (acceptable — the value never needs ms precision) |
| R3 | `bench_run` row write contention with `request_metric`/`slot_sample` writes | Low | one connection per writer; WAL + `BEGIN IMMEDIATE` serialises; bench runs are rare (≤ hourly) vs request writes (continuous); write latency contribution <1ms |
| R4 | GTT bytes is 0 when no amdgpu (NVIDIA / Intel / CPU-only box) | Low | `None`, never 0 — graceful degradation per §13.5; UI shows em-dash; rollup excludes the field when all rows have NULL |
| R5 | Prometheus label cardinality exceeds operator expectations | Low | bounded by the documented ceiling (~5k series); documented in the renderer; if a future operator adds per-user labels, the `security/exposure.py` S9 classification must require admin-key first |
| R6 | Bench records.jsonl → bench_run migration loses rows on schema mismatch | Low | `--dry-run` first; idempotent on `(run_id)`; the JSONL stays on disk until operator rm's |
| R7 | `ttft_ms` from the SSE delta-counting approximation disagrees with the llama `timings.predicted_per_second` exact value | Med | prefer `usage` chunk when present; stamp `tps_source='exact'\|'approx'` on the row; baseline regression uses `tps_source='exact'` only |
| R8 | The decision seam (T1 capture) inside `_dispatch_and_forward` is missed on a future route added outside v1.py | Med | expose `RequestSeam` as a FastAPI dependency + a `request.app.state.metrics_seam` accessor; document in the seam module's docstring + an integration test that asserts every `dispatch` call goes through the seam |
| R9 | `/api/metrics/prometheus` unauthenticated by convention — Decision D2 will gate it; OBS-1 ships it open | Low | D2 is a follow-up; KB-1 owns; OBS-1 documents the current posture in the route docstring + an `arch-decisions/` ADR stub |
| R10 | Companion Prometheus+Grafana containers survive uninstall of hal0 | Low | documented in `installer/install.sh --with-observability` uninstall path: companion containers are owned by their companion label and removed together |

### 8.2 Capped verification (matches the ways-of-working DoD)

**Per-PR (one PR per sub-step below):**
1. Compiles + types + tests + CI green.
2. Scar baseline same-or-lower (no regrowth).
3. Touched-area docs reconciled.
4. Branch merged to `rework/descar`; worktree pruned.
5. Tracker row flipped (OBS-1 sub-step).

**Adversarial checks (Opus reviews):**
- **T1 hot-path budget:** run the bench harness (spec-bench T1 lane) + a 100-concurrent `client.run_chat_completion` soak for 5 min, assert p99 request latency does not regress by >2 ms (queue.put_nowait + dict copy must be sub-50 µs).
- **Async writer backpressure:** monkey-patch the queue to never drain; assert request handlers still respond (drop-oldest path); assert the warning log fires.
- **GTT/VRAM graceful:** mock `gpu_view.sample()` to return all-None on an Intel/CPU-only path; assert no exception, `slot_sample` rows have all-NULL hardware fields, dashboard renders em-dashes.
- **Bench migration idempotency:** run `hal0 metrics migrate --from-bench-store` twice on the same JSONL; assert row count in `bench_run` is the same after both runs.
- **Read API contract:** golden response fixtures for `/api/stats`, `/api/system-stats`, `/api/models/health`; assert against pre-canned SQLite.
- **Prometheus cardinality:** a fuzz test that mutates `model_id`/`slot_id`/`runner` randomly; assert the renderer caps cardinality at the documented ceiling.

**No new external test infra.** All checks run on the existing TestClient harness (700+ tests) + the bench γ-suite on real Strix Halo (spec-bench.final §1.6).

---

## PART 9 — DoD checklist (per PR)

Per `/home/mint/hal0-rework-ways-of-working.md` DoD (plan §9):

- [ ] `003_metrics.sql` migration applies cleanly on a fresh DB and idempotently on an existing DB.
- [ ] `request_metric` row count matches `tps_events` event count within ±1 per slot per hour (sanity).
- [ ] `_scrape_llama_metrics` removed from `GET /api/slots/metrics`; `inflight` reads from `slot_sample` latest.
- [ ] `GET /api/stats` + `/api/system-stats` + `/api/models/health` return the documented JSON shapes (golden tests).
- [ ] `GET /api/metrics/prometheus` body extends the existing series (golden test).
- [ ] Benchmarks.tsx Performance tab renders without 4xx/5xx; baseline regression coloring works against a seeded `bench_run.baseline=1` row.
- [ ] `--with-observability` install provisions the companion containers; `prometheus.yml` + Grafana provisioning ship from `installer/observability/`.
- [ ] `hal0 metrics status` + `hal0 metrics prune` + `hal0 metrics export` verbs work.
- [ ] Existing 700+ TestClient tests still pass (loopback + no-auth posture preserved).
- [ ] `scripts/scar_baseline.txt` same-or-lower; touched-area docs reconciled; tracker row flipped.

---

## PART 10 — explicit non-goals (carried forward from §13.1 + §21.3)

- **No remote telemetry / phone-home.** 100% local; any future aggregate opt-in telemetry requires an explicit, separate opt-in (out of scope for v1).
- **No Langfuse/OTLP export.** Companion stack covers observability; Langfuse/OTLP lands as a follow-up with its own ADR (the request seam + `request_metric` schema are the binding surface).
- **No multi-host aggregation in OBS-1.** Per-host DB stays the source of truth; upstream proxy in `/api/stats/hardware` already handles fan-out for multi-host (out-of-scope rewrite).
- **No KB-1 auth gate on `/api/metrics/prometheus` in OBS-1.** Decision D2; KB-1 owns.
- **No write of `telemetry_history.jsonl` mirror.** `records.jsonl` mirror stays because grep-ability is its value; no new mirror file.

---

## PART 11 — handoff

- **Spec owner:** OBS-1 lane; tracker row `OBS-1` (primary) + `OBS-2`/`OBS-3`/`OBS-4` (sub-rows).
- **Cross-lane consumers (this spec unblocks):**
  - §21.4 `hal0 doctor rework` (uses `request_metric`/`slot_sample` as evidence).
  - §21.10 reaper GTT probe (reads `slot_sample.gtt_bytes` for pressure eviction; plan §21.10 already calls for the GTT-aware switch — OBS-1 makes the data live).
  - §15.6 reasoning-normalization (toolloop `on_event` → `request_metric` reasoning-length column; out of scope for v1, but the seam exposes the hook).
  - `feat/brain-tool-use-hardening`'s port-claim harvest (§11.2; ML-2 PortAuthority ships first, OBS-1 reads `port_claim` for the companion-stack port allocation).
- **Follow-ups (not in OBS-1):**
  - OBS-5: Langfuse/OTLP export (post-Decision D2 + real use case).
  - OBS-6: `hal0 doctor bundle` redaction + `--json` (plan §21.4).
  - OBS-7: KPI alerting hooks (event-bus emit on TTFT p95 regression; out of scope until operator asks).

🤖 Generated with [Claude Code](https://claude.com/claude-code)