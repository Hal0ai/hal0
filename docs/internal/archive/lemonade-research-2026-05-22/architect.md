# Architecture — hal0 ↔ Lemonade integration

**Status:** draft architect output for ADR-0008 (supersedes ADR-0006 §process-topology, refines ADR-0007).
**Date:** 2026-05-22
**Locked:** Path 4 (Lemonade serves every iGPU modality), kokoro:cpu accepted, `capabilities.toml` remains hal0's omni surface, FLM/NPU keeps existing toolbox.

## Context

hal0 v0.1.x ships six per-modality toolboxes + a `Provider` subclass each. ADR-0006 retires them for Lemonade. The 2026-05-22 spike confirmed Lemonade works on Strix Halo gfx1151 but the deep-dive surfaced two architecture-shaping facts ADR-0006 missed: (1) Lemonade's eviction has **two distinct layers** — per-type LRU (normal, isolated) and "nuclear evict-all" (only on `load_failure`, the documented escape valve); (2) `/v1/load` is **declarative** and per-type slots are **already concurrent within one process** — `/v1/health.max_models` reports `{llm, embedding, reranking, transcription, image, tts}` independently. The spike treated all evictions as nuclear; that error inverts the topology decision below.

## Decision 1: process topology

**Recommendation: single Lemonade process with `max_loaded_models = 2` per type for LLM/embed/rerank** (raised from default 1). Escalate to two-process split only if telemetry shows nuclear evict-all >1×/week despite ADR-0007 preflight.

Cost model: Strix Halo, 128 GB unified RAM, working set = hermes-14b + nomic-embed + bge-rerank + whisper + sdcpp + kokoro.

| Vector | (a) per-slot | (b) split | (c) single | (d) upstream patch |
|---|---|---|---|---|
| Processes | 6 | 2 | **1** | 1 |
| Idle RAM (lemond resident ~180 MB ea.) | ~1.1 GB | ~360 MB | **~180 MB** | ~180 MB |
| Ports | 6 | 2 | **1** | 1 |
| Log streams | 6 WS + 6 journals | 2 | **1** | 1 |
| LRU evict blast radius | 1 modality | 1 modality | **1 type** (already isolated) | 1 type |
| Nuclear blast radius | 1 modality | 4 modalities | **all 6** (ADR-0007 mitigates) | **none** |
| Glue LOC | ~600 | ~250 | **~80** | ~120 + ongoing rebase |
| Maint hours/month | high (6 update cadences) | medium | **low** | high (weekly upstream breakage) |
| tok/s impact | none steady-state | none | **none** | none |

**Why (c) wins decisively:**

1. **Per-type LRU is already concurrent.** Lemonade's router keeps an LRU per `ModelType`. Loading a new LLM evicts old LLMs only — not embeds. `Chat+Embed` and `Chat+Voice` coexist for free at default config. The spike misread this.
2. **NPU exclusivity is hardware-enforced.** Splitting NPU into its own process buys nothing — the FLM toolbox already isolates it (Decision 3).
3. **Nuclear blast radius is moot in practice.** ADR-0007 preflight steers ~99% of structural failures into the file-not-found-exempt branch. Per-process isolation costs ~900 MB RAM steady-state to defend against an event the mitigation already handles. Both spike-observed nuclear evictions fired on LLM load — putting primary in a "stable" group (option b) doesn't make it stable.
4. **`max_loaded_models = 2` enables swap-without-evict.** Loading a replacement primary while old one drains in-flight requests costs ~12 GB GTT for two 14B models — trivial against 128 GB.

### Concurrency knobs hal0 sets at install

- `max_loaded_models`: `llm=2, embedding=2, reranking=2, transcription=1, image=1, tts=1`
- `--no-broadcast` (suppress UDP 13305 LAN announce)
- `LEMONADE_API_KEY` from `/var/lib/hal0/secrets/lemonade.key` (defense-in-depth on loopback)
- Bind 127.0.0.1:9100

### Fallback trigger

If `lemonade.evict.nuclear_total` counter (Decision 5) fires >1×/week in prod, escalate to option (b): primary LLM in process A, ephemerals in process B. Don't pre-pay this cost.

## Decision 2: lifecycle mapping

Lemonade exposes four primitives: `POST /v1/load` (declarative, no-op if matching options already loaded), `POST /v1/unload`, `POST /v1/pull`, `GET /v1/health`.

| hal0 state | Lemonade reality | Misalignment |
|---|---|---|
| `OFFLINE` | not in `loaded[]` | none |
| `PULLING` | `/v1/pull` in flight, progress on `/logs/stream` WS | Lemonade's pull returns immediately + streams; hal0's PULLING expects blocking. SlotManager wraps with progress-poller. |
| `STARTING` | `/v1/load` request queued in router's serialized-load mutex | Lemonade has no separate "starting" phase. **Collapse STARTING→WARMING for Lemonade slots.** |
| `WARMING` | in `loaded[]`, `last_use` null, sentinel not yet completed | aligned via existing sentinel probe |
| `READY` | `loaded[]` + sentinel OK | aligned |
| `SERVING` | request in flight | hal0-side only; Lemonade tracks busy-protection internally (EVICTION_TIMEOUT=5s) but doesn't expose |
| `IDLE` (a) | `loaded[]` lacks slot's `model_name` | poll `/v1/health.loaded[].model_name` |
| `IDLE` (b) | `now - last_use > idle_after_s` | poll `last_use`; hal0 owns the 300s policy (Lemonade has no idle TTL) |
| `UNLOADING` | `/v1/unload` in flight | synchronous + cheap; dwell time ~0. Keep state for FLM-slot parity. |
| `ERROR` | preflight failed / `/v1/load` 4xx-5xx / sentinel timeout | aligned; error class distinguishes `lemonade.load_failed` vs `lemonade.preflight_failed` |

### What hal0 has to model that Lemonade doesn't expose

1. **Slot identity vs model identity.** Lemonade tracks loaded *models* by `model_name`. hal0 tracks *slots* (stable across swaps). `slot.model_id` carries the current `model_name`; swap = unload-old then load-new.
2. **Per-slot ports become informational.** Single-process Lemonade puts all slots on 9100; dispatcher routes by `model_name` in the body, not port. `Slot.port = 9100` for all Lemonade slots.
3. **Sentinel readiness.** Lemonade reports `loaded[]` immediately after `/v1/load` 2xx, but FLM warmup + llama-server first-token compile mean it isn't inference-ready yet. Sentinel pattern stays unchanged.
4. **Idle-unload policy.** Lemonade has no idle TTL. hal0's 300s idle driver retargets to `/v1/unload`.
5. **`SERVING` distinction.** Dispatcher increments a counter on request-start, decrements on response-end, maps to state transitions.

`LEGAL_TRANSITIONS` in `hal0/slots/state.py` stays identical. Only the side effects of transitions change (`LemonadeClient.load()` replaces `systemctl start`).

## Decision 3: FLM/NPU coexistence

NPU is the only modality where hal0 keeps its existing toolbox in v0.2 (Lemonade's FLM install silently failed in spike; libxrt-npu2 prereq absent; AMD's installer doesn't bootstrap it).

- **Two backend layers:** Lemonade single-process (127.0.0.1:9100, iGPU + CPU modalities) + hal0-toolbox-flm (existing podman, 127.0.0.1:8087, NPU). Both implement the `Provider` ABC — the only two providers left after v0.2.
- **`capabilities.toml` rollup spans both.** `device = "npu"` → FLM provider; `device ∈ {gpu-rocm, gpu-vulkan, cpu}` → Lemonade provider. Catalog (`hal0/capabilities/catalog.py`) is the routing point.
- **Shared model store.** `/mnt/ai-models/` mounted into FLM toolbox at `/var/lib/hal0/.config/flm/models` (per `hal0_flm_models_mount_path`); exposed to Lemonade as `extra_models_dir = /mnt/ai-models/local/`. Both backends read the same ZFS dataset.
- **No cross-backend evict.** Lemonade's evict-all can't blast FLM (different process); FLM's serialized load can't blast Lemonade. NPU exclusivity remains hardware-enforced.

### libxrt-npu2 prereq

Install via **host apt at install.sh time**, not via container image:

```bash
apt install -y unzip libxrt-npu2   # in install.sh, before backend install
```

libxrt is a kernel-driver-coupled userspace library (XRT must match the `amdxdna` kmod version). Baking it into a container ABI-mismatches when the host kernel updates (per `pve_kernel_migration_2026-05-22`). Host apt = pin via Ubuntu package versioning, follows kernel updates.

manifest.json schema v2 adds `system_deps: ["unzip", "libxrt-npu2"]`; install.sh iterates. Existing systems get the dep on v0.2 upgrade.

## Decision 4: registration architecture

Spike's core finding: `extra_models_dir` GGUFs receive `labels=["custom"]` → type defaults to LLM → `--reranking`/`--embedding` not passed → 501. Type classification is **label-driven** (deep-dive §3); labels live in `server_models.json`.

### hal0 owns a generated `server_models.json`

Source: `/var/lib/hal0/registry/registry.toml`. Artifact: `/opt/lemonade/resources/server_models.json`. Generator: `src/hal0/lemonade/server_models_gen.py`, run at install + on `hal0 capabilities apply`.

Per-entry shape:

```json
"hal0.bge-reranker-v2-m3-q4_k_m": {
  "checkpoint": "pqnet/bge-reranker-v2-m3-Q8_0-GGUF",
  "variant": "bge-reranker-v2-m3-q8.gguf",
  "recipe": "llamacpp",
  "labels": ["reranking"],
  "size": 540123456,
  "sha256": "..."
}
```

- `labels` is the type signal (Lemonade's `get_model_type_from_labels` resolves it).
- `hal0.*` namespace separates curated entries from Lemonade's ~50 bundled vendor picks.
- `user.*` namespace reserved for runtime `/v1/pull` adds.

### Why not `extra_models_dir` alone?

Spike proved it: type defaults to LLM → broken for embed/rerank. Per deep-dive §3, runtime adds use `/v1/pull` with `embedding: true` / `reranking: true` (auto-attaches the right label). v0.2 uses both:

- **Install time:** generated `server_models.json` for curated picks (declarative, survives restart).
- **Runtime user adds:** `/v1/pull` with explicit type flag → `user.*` namespace.

### Reserved-args validator

The reserved-args superset is hardcoded in lemond's router and **not extensible**. hal0's `SlotConfig.extra_args` validator rejects any of:

`-m, --model, --port, --ctx-size, -c, --device, -dev, --gpu-layers, --n-gpu-layers, -ngl, --embedding, --embeddings, --reranking, --rerank, --jinja, --no-jinja, --mmproj, --mmproj-*, --no-mmproj-*, -mm, -mmu`

before the request leaves hal0. Single source of truth in `src/hal0/config/schema.py::SlotConfig.validate_extra_args()`, shared by CLI/API/dashboard.

### `device` → recipe + backend mapping

```
gpu-rocm    →  recipe=llamacpp,    llamacpp_backend=rocm
gpu-vulkan  →  recipe=llamacpp,    llamacpp_backend=vulkan
cpu         →  recipe=llamacpp,    llamacpp_backend=cpu
                (or whispercpp|kokoro|sd-cpp for non-LLM)
npu         →  FLM toolbox (not Lemonade)
```

Maps live in `LemonadeProvider.load(slot)`. **Architectural commitment for API agent:** hal0 never sends explicit JSON null — omitted fields are omitted, never nulled (avoids the `is_empty_option` foot-gun in deep-dive §1).

## Decision 5: metrics + observability

### Pipeline

```
Lemonade /v1/stats   ───┐
Lemonade /v1/health  ───┤
Lemonade /logs/stream───┼──► hal0 aggregator (lemonade/metrics.py)
FLM /metrics (existing)─┤        + KV-cache shim (PR #124)
systemd journal tail ───┘        → /api/metrics (Prometheus)
```

### Per-source contract

| Source | Cadence | Surface |
|---|---|---|
| `/v1/stats` | 5s poll | TTFT, tok/s, last-request perf (not cumulative — hal0 ring-buffers) |
| `/v1/health` | 5s poll | `loaded[]` (model_name, backend_url, last_use); `max_models` per-type |
| `ws://.../logs/stream` | persistent | severity-tagged lines; `logs.subscribe` with `after_seq` for reconnect-safe stream |
| FLM `/metrics` | 5s scrape | unchanged from v0.1.x |
| `lemond.service` journal | tail (push) | crashes, OOM, kernel hiccups; gap-fill for WS |

### KV-cache: keep PR #124 shim

Lemonade's bundled llama-server is b9253 → still missing `/metrics`. PR #124's `/slots` strategy (compute KV% from `max(n_prompt_tokens) / n_ctx`) still applies. **Change vs v0.1.x:** v0.1.x hit `127.0.0.1:<slot_port>/slots` directly; v0.2 hits the `backend_url` Lemonade reports per loaded model. URL discovery moves from hardcoded → `/v1/health.loaded[].backend_url`. Parsing unchanged.

### New metrics hal0 emits

- `lemonade.load.attempts_total{slot, model}` — counter
- `lemonade.load.failures_total{slot, model, class}` — class ∈ {preflight, http_4xx, http_5xx, timeout, nuclear}
- **`lemonade.evict.nuclear_total`** — parsed from `/logs/stream` "Load failed with non-file-not-found error" lines; **triggers Decision 1 re-evaluation**
- `lemonade.load.queue_depth` — outstanding `/v1/load` calls minus completions (Lemonade serializes)
- `lemonade.health.last_use_age_s{model}` — feeds idle-unload driver + dashboard
- `hal0.slot.state{slot, state}` — unchanged from v0.1.x

### Multi-process aggregation (future)

If Decision 1 escalates to option (b): `lemonade/metrics.py` already accepts a list of base URLs (single-element today). Multi-process = scrape each, label series with `process="primary"|"ephemeral"`, sum cardinality-safe gauges, max() queue-depth-style. No re-architecting.

### Dashboard implications

- v0.2: KV% from PR #124 shim against discovered `backend_url`; tok/s + TTFT from `/v1/stats` ring buffer.
- v0.2.1: direct `/logs/stream` WS subscription for real-time load + evict events. v0.2 polls `/v1/health` every 5s; ~5s lag on state transitions, acceptable for alpha.

## Open questions for API + UI agents

### For API agent

1. **`/v1/load` request body — null vs omit.** Architecture mandates omit. Codify the per-`(device, modality)` JSON shape in `LemonadeClient.load()` keyword-argument contract.
2. **`/v1/pull` runtime-add format.** Confirm `{model_name, reranking: true}` shape for runtime rerank adds (deep-dive §3 implies yes; no end-to-end smoke yet).
3. **`/v1/unload` semantics on serving model.** EVICTION_TIMEOUT=5s busy-protection blocks unload mid-request. Confirm `LemonadeClient.unload()` timeout behavior — wait+complete is our assumption.
4. **Sentinel payloads.** Cheapest inference call per modality (embedding / reranking / transcription / tts / image) to establish WARMING→READY without burning meaningful compute.

### For UI agent

1. **State transition lag.** v0.2 ships 5s `/v1/health` polling — up to 5s lag on state changes. Confirm acceptable for alpha; v0.2.1 swaps to WS.
2. **Per-type slot count rendering.** `/v1/health.max_models` per-type (`llm=2, embedding=2, reranking=2, transcription=1, image=1, tts=1`). Dashboard should render "1/2 LLM slots" rather than just listing models.
3. **Evict-all event surfacing.** Nuclear path = all slots flip OFFLINE simultaneously. UI needs a visible banner tied to `lemonade.evict.nuclear_total` increment — not six independent slot-card transitions — so operators recognize the failure mode.
4. **NPU slot rendering.** FLM toolbox keeps its own port + provider in v0.2. NPU slots render identically to Lemonade slots; backend distinction stays an implementation detail. Capability-card grouping already handles this per `hal0_capability_slots_system`, but worth re-verifying with two backends live.

---

*Companion docs: `researcher.md`, `api.md`, `ui.md`.*
