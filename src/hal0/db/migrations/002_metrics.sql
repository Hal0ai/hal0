-- 002_metrics.sql  (schema version 2)
-- OBS-1 (§13). Depends on ML-1's db/ foundation (PRAGMA foreign_keys=ON,
-- BEGIN IMMEDIATE transactions, schema_migrations runner, 001_registry.sql).
--
-- NOTE ON NUMBERING: hal0-specs/spec-obs-metrics.md names this file
-- ``003_metrics.sql`` (written before the sequencing was locked). At the
-- time OBS-1 landed, only 001_registry.sql (ML-1) existed on
-- rework/descar -- PortAuthority's 002_port_authority.sql had not merged
-- yet -- so this migration takes version 002. A future db lane (e.g.
-- PortAuthority) takes 003+.
--
-- Cross-references:
--   * §7.6 / S12  -- request_metric populated from the request seam
--   * §7.5 / S8   -- db/ connection layer (ML-1)
--   * §13.1       -- zero-dep core; Prometheus companion is opt-in
--   §13.2 T1      -- request_metric  (per-request)
--   §13.2 T2      -- slot_sample     (per-slot timeseries)
--   §13.2 T2      -- slot_event      (per-slot lifecycle)
--   §13.2 T3      -- bench_run       (per bench cell-run; landing spot only,
--                    the bench-internal writer lands in a follow-up lane)
--   §13.6         -- metric_rollup   (long-retention aggregates)
--
-- Cardinality discipline (plan §13.6): per-request detail lives in SQLite
-- only. Prometheus label cardinality = {slot, model, runner, device,
-- modality} -- never per-request (avoids TSDB explosion).
--
-- Deferred columns (spec-ml1-sqlite §(e) pattern, "ModelCapabilities
-- mtp/jinja"): ``runner``/``device``/``modality`` land NULL until the
-- §7.1d modalities split + ML-4 runners registry lanes populate them from
-- a closed enum instead of a sniff. A NULL here is "not yet resolvable",
-- never a synthesized guess.

-- ── T1 per-request metric ─────────────────────────────────────────────────
-- One row per request served through the request seam (S12,
-- api/routes/v1.py::_dispatch_and_forward). Written asynchronously off the
-- inference hot path (see hal0/metrics/writer.py). Retained for
-- [metrics].retention.request_days (default 7) then pruned.
CREATE TABLE request_metric (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  ts                TEXT    NOT NULL,            -- ISO-8601 UTC
  request_id        TEXT    NOT NULL,            -- x-request-id header (S12)
  slot_id           TEXT,                        -- slot name OR upstream id (nullable for dispatcher errors)
  model_id          TEXT,                        -- resolved model id (call.resolved_model)
  runner            TEXT,                        -- rocm|vulkan|cpu|flm|kokoro|comfyui (deferred column)
  device            TEXT,                        -- gpu-rocm|gpu-vulkan|cpu|npu (deferred column)
  modality          TEXT,                        -- chat|vision|embed|rerank|asr|tts|image|video (deferred column)
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  ctx_used          INTEGER,                     -- n_ctx_used at completion (llama timings prompt_n+predicted_n)
  ttft_ms           REAL,                        -- first-content-delta latency (streaming) / prompt_ms (non-streaming)
  prefill_tps       REAL,                        -- llama timings prompt_per_second (exact) or NULL
  decode_tps        REAL,                        -- llama timings predicted_per_second (exact) or wall-clock estimate (approx)
  tps_source        TEXT,                        -- 'exact' (llama timings / FLM usage) | 'approx' (delta-count / wall-clock)
  queue_ms          REAL,                        -- dispatch_started - request entry (local routing overhead)
  total_ms          REAL,                        -- now - request entry
  cache_hit         INTEGER,                      -- 0|1 (llama timings cache_n > 0)
  spec_accept_rate  REAL,                        -- MTP / spec-decode accept (llama timings draft_n_accepted/draft_n)
  stop_reason       TEXT,                        -- stop|length|tool_calls|error
  ok                INTEGER NOT NULL,            -- 0|1
  error_code        TEXT,                        -- typed error code (Hal0Error.code) when ok=0
  client            TEXT                         -- ip prefix, truncated to 24 chars (never full PII)
);
CREATE INDEX idx_request_metric_ts       ON request_metric(ts);
CREATE INDEX idx_request_metric_slot_ts  ON request_metric(slot_id, ts);
CREATE INDEX idx_request_metric_model_ts ON request_metric(model_id, ts);
CREATE INDEX idx_request_metric_ok_ts    ON request_metric(ok, ts);

-- ── T2 per-slot sample (timeseries) ────────────────────────────────────────
-- Sampler writes one row per (slot, tick) plus one synthetic
-- slot_id='__fleet__' row per tick carrying box-wide GPU/power/thermal
-- readings that are not currently attributable to an individual slot on
-- a shared-GPU (UMA) box. Any NULL field is the sensor/attribution being
-- absent, not "actually zero" (plan §13.5 / #791 discipline). Downsampled
-- to metric_rollup for long retention.
CREATE TABLE slot_sample (
  ts              TEXT    NOT NULL,            -- ISO-8601 UTC
  slot_id         TEXT    NOT NULL,            -- slot name, or '__fleet__' for the box-wide row
  state           TEXT    NOT NULL,            -- SlotState enum value ('n/a' for the fleet row)
  vram_bytes      INTEGER,                     -- resident VRAM (or UMA: max(VRAM, GTT)) -- per-slot estimate
  gtt_bytes       INTEGER,                     -- amdgpu GTT pool bytes (Strix Halo UMA-aware) -- fleet row only
  ram_bytes       INTEGER,                     -- host RAM used by container cgroup (per-slot) or total (fleet)
  gpu_util        REAL,                        -- 0..1 (raw; fleet row only on shared-GPU boxes)
  npu_util        REAL,                        -- 0..1 (NPU/FLM only; NULL on non-NPU)
  power_w         REAL,                        -- hwmon power1_average -> W (fleet row only)
  temp_c          REAL,                        -- hwmon temp1_input -> degC (fleet row only)
  inflight        INTEGER,                     -- requests_processing scraped from llama-server (per-slot)
  kv_used         INTEGER,                     -- llama-server KV occupancy (0..1 scaled *1000000, or FLM column occupancy %)
  PRIMARY KEY (ts, slot_id)
);
CREATE INDEX idx_slot_sample_slot_ts ON slot_sample(slot_id, ts);

-- ── T2 slot lifecycle event ────────────────────────────────────────────────
-- One row per observed SlotManager state transition (legal edge per
-- slots/state.py:LEGAL_TRANSITIONS). Observed at sampler-tick granularity
-- (default 5s) in OBS-1 -- a direct hook into SlotManager.set_state for
-- exact transition timestamps is a follow-up (documented in
-- hal0/metrics/sampler.py). Cold->warm load time = the (offline -> ready)
-- row's tick-granularity duration_ms.
CREATE TABLE slot_event (
  ts              TEXT    NOT NULL,
  slot_id         TEXT    NOT NULL,
  event           TEXT    NOT NULL,            -- transition | load_started | load_done | arbiter_wait
  from_state      TEXT,
  to_state        TEXT,
  duration_ms     REAL,                        -- wall-clock cost of this event (NULL when not measured)
  reason          TEXT                         -- human note (e.g. "sampler-observed transition")
);
CREATE INDEX idx_slot_event_slot_ts ON slot_event(slot_id, ts);

-- ── T3 bench_run (schema landing spot; §23.3(d)) ────────────────────────────
-- One row per cell-run from bench. cell_key = sha256 of canonical-JSON
-- Identity block. OBS-1 lands the table only -- the bench-internal writer
-- (bench/runner.py migrating off bench/store.py's out-of-tree bench.db) is
-- a follow-up lane (OBS-4 / spec-bench.final).
CREATE TABLE bench_run (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              TEXT    NOT NULL,            -- ISO-8601 UTC (derived from run_id prefix)
  run_id          TEXT    NOT NULL,            -- UTC stamp + suffix (matches records.jsonl shape)
  cell_key        TEXT    NOT NULL,            -- sha256 of canonical-JSON identity (device-extended)
  suite           TEXT,
  trigger         TEXT,                        -- manual|scheduled|on_install|on_pull
  model_id        TEXT    NOT NULL,
  runner          TEXT    NOT NULL,
  profile         TEXT,                        -- profile id at bench time
  hw_hash         TEXT,                        -- sha256 of canonical HW fingerprint
  device          TEXT,                        -- resolved device token
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
  baseline        INTEGER,                     -- 0|1 -- is THIS row the (model x runner x hw) baseline?
  outcome         TEXT    NOT NULL,            -- ok|failed|skipped-contended|oom|hang
  raw             TEXT                         -- JSON: full record (mirrors records.jsonl line, kept for debug)
);
CREATE INDEX idx_bench_run_cell_ts ON bench_run(cell_key, ts);
CREATE INDEX idx_bench_run_model_ts ON bench_run(model_id, ts);
CREATE INDEX idx_bench_run_baseline ON bench_run(model_id, runner, hw_hash, baseline);

-- ── Long-retention aggregates ──────────────────────────────────────────────
-- Background aggregator downsamples request_metric (T1) hourly and
-- slot_sample (T2) per (slot x hour). bench_run stays row-level (rare
-- events). Plan §13.5: bounded storage; raw rows pruned at retention,
-- rollup kept long. tokens/watt (a hal0 differentiator, plan §13.2) is a
-- derived read-time ratio of tps_decode_avg / power_w_avg -- not its own
-- stored column, since both source columns already round-trip here.
CREATE TABLE metric_rollup (
  bucket          TEXT    NOT NULL,            -- 'YYYY-MM-DDTHH:00:00Z' (hourly) | 'YYYY-MM-DD' (daily)
  dim_kind        TEXT    NOT NULL,            -- request_hourly | slot_sample_hourly | bench_daily
  dim_key         TEXT    NOT NULL,            -- JSON-encoded dimension object
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
