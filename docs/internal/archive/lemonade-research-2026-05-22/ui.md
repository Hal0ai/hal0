# UI/UX — hal0 dashboard with Lemonade backend

Scope: dashboard (`ui/`) only. Capability cards (`embed`, `voice`, `img`),
flat `SlotCard`, footer journal panel, FirstRun wizard. No "lemonade omni"
element — `capabilities.toml` is the rollup. Visual language unchanged
(hal0-amber `#feaf00`, success/warning/danger dots, mono numerics).
Lemonade replaces process backends; cards keep their skeleton.

---

## 1. Slot card under Lemonade

### State sources

Three signals, ranked by trust:

1. **`/v1/health` poll (5 s)** — authoritative "loaded?". Lemonade returns
   `{models: [{model_name, backend_url, …}]}`; absent `model_name` means
   **evicted**. This is the dot.
2. **`/logs/stream` WS** — surface-only. Drives journal + evict banner
   (§5). Never derive health from it.
3. **`/v1/stats`** (5 s, per loaded `backend_url`) — KV%, RSS, uptime,
   tok/s. Replaces dead `/metrics`.

Only `/v1/health` flips the dot.

### Three card states under Lemonade

```
┌─────────────────────────────────────────────┐
│ ● primary               :8000   [SWAP ▾]    │   loaded (green)
│  hermes-4-14b-q5_k_m                        │
│  KV 23%   T/S 20.4   TTFT 87ms   MEM 11.1G  │
│  [iGPU] [rocm] [ctx 32K]                    │
└─────────────────────────────────────────────┘
┌─────────────────────────────────────────────┐
│ ◐ primary               :8000   [SWAP ▾]    │   loading (amber pulse)
│  hermes-4-14b-q5_k_m                        │
│  loading… queue pos 2/3 · waits on `embed`  │  ← NEW row
│  [iGPU] [rocm]                              │
└─────────────────────────────────────────────┘
┌─────────────────────────────────────────────┐
│ ○ primary               —       [SWAP ▾]    │   evicted (faint)
│  hermes-4-14b-q5_k_m  ⚠ evicted             │  ← NEW chip
│  reason: load failure on `embed-rerank`     │
│  KV —   T/S —   TTFT —   MEM —              │
│  [iGPU] [rocm]      [▶ reload]              │
└─────────────────────────────────────────────┘
```

Three new affordances:

- **Queue-position row** (between models and stats) appears only while
  `state == loading` and another slot holds the load lock. Hides on
  serving/evicted.
- **`⚠ evicted` inline chip** in the model line when health says
  unloaded *and* last lifecycle action wasn't explicit unload. Click →
  drawer with last-known error excerpt + reload button.
- **Soft reload button** under stats row, evicted-only. Existing
  Start/Stop/Restart row in the footer untouched.

### Nuclear-evict notification

When `/v1/health` polls show ≥2 slots flip to evicted within a single
2 s window, OR `/logs/stream` emits the verbatim string
`evicting all models and retrying`, fire a **single** toast + a
persistent dashboard banner:

```
╔══════════════════════════════════════════════════════════════╗
║ ⚠ Lemonade evicted all models                               ║
║   embed-rerank failed to load → all loaded models cleared.   ║
║   Affected: primary, embed, stt   [reload all] [view logs]   ║
╚══════════════════════════════════════════════════════════════╝
```

- Reuse `RestartBanner.vue` shell (top of dashboard, amber).
- "Affected" = snapshot of prior poll's `model_name`s. Compute once.
- "view logs" → footer Logs tab, sub-tab=`api`, filter pre-set to
  `evicting all models`.
- "reload all" → serial `/api/slots/{name}/load` fan-out (Lemonade
  serializes anyway). Stops on first failure (no feedback loop).
- Auto-dismiss when all affected return to `serving`, or explicit close.

### Load-queue display

Lemonade serializes loads. Architect needs to expose queue position via
`/api/slots` or the slot SSE ring. UI surface:

- **In-card** queue-pos row (above) — single source of truth.
- **Sidebar** badge `primary (Q2)` while queued; gone at serving.
- **Activity tab** synthetic `slot.queue` row, severity=info.

No global queue panel. Promote to footer sub-tab in v0.2.1 only if
power-users complain.

---

## 2. Voice card (whisper + kokoro:cpu)

Existing two-section layout (STT / TTS) survives intact — both children
are still picked independently, still toggle independently. Lemonade just
replaces the runtime under the hood.

Two adjustments:

### Latency disclosure for CPU TTS

```
┌─ voice ───────────── capability slot      2 children · ready ┐
│ ● serving    STT     /v1/audio/transcriptions · :8005        │
│  [whisper-base.en — 0.15 GB ▾]  [◉ whispercpp:rocm ▾]        │
│  [iGPU]                                                      │
│  ─── 0 reqs · — tok/s · 312 MB · 14m ───                     │
│                                                              │
│ ● serving    TTS     /v1/audio/speech · :8006                │
│  [kokoro-82m ▾]  [◉ kokoro:cpu ▾]                            │
│  [CPU] [≈ slower] ⓘ                                          │
│  ─── 0 reqs · — tok/s · 614 MB · 14m ───                     │
└──────────────────────────────────────────────────────────────┘
```

- **Hardware chip** in TTS reads `CPU` (existing `hw-cpu` styling,
  muted not red).
- **`≈ slower` qualifier chip** next to `CPU`. New class
  `cap-chip-perf` — amber-tinted, mono, `cursor: help`. Tooltip:
  *"Kokoro has no GPU build. TTS runs on CPU and may take 1–3 s for a
  short utterance. Latency is normal."*
- `ⓘ` opens the same tooltip on click (touch).

No on-card real-time-factor metric — Lemonade doesn't expose one;
faking it from token rate would mislead.

### Two-runtime health rollup

Existing pill logic unchanged: serving+serving=green `2 children · ready`,
one-serving=green `1/2 serving`, any error=red `error`, else amber `idle`.
Nuclear-evict collapses both children to evicted → pill returns `idle`
correctly. The §1 banner tells the user why; the card stays calm.

---

## 3. Embed+Rerank card

Same shape as today.

**Reject the reciprocal-use indicator.** Embed and rerank are
independent llama-server processes; rerank's input is the user's
query+docs, not embed's output. Reciprocity lives at the RAG
application layer, not the slot layer. A visible link would mislead
users into thinking disabling embed breaks rerank (it doesn't).

Visually identical to today; backend chips read `llamacpp:rocm` for
both children. Catalog must emit Lemonade-native backend IDs verbatim
(`llamacpp:rocm`, `kokoro:cpu`, `whispercpp:rocm`…) so chip text and
dropdown text agree.

---

## 4. NPU/FLM vs Lemonade — backend disclosure

FLM/NPU stays in `NPUBackendCard.vue`. Lemonade-backed children stay in
their existing capability cards. The visual split is the disclosure.

Three layers:

1. **Card layer (default).** Existing hardware+runtime chips:
   `[NPU] [flm]` (toolbox), `[iGPU] [rocm]` (Lemonade), `[CPU]
   [kokoro]` (Lemonade). Chip color is the signal (NPU amber, iGPU
   green, CPU muted).

2. **Endpoint sub-line suffix.** Append `· via lemonade` or `· via
   toolbox` to the existing endpoint line. Tiny mono-faint.

3. **Chip `title` tooltip extension.** Add `process: lemonade-server
   (pid 12345)` or `process: hal0-toolbox-flm@npu0` so operators can
   `journalctl`/`docker logs` without leaving.

No new toggle, no Settings flag. Users never have to know unless
debugging.

---

## 5. Journal panel — log interleaving

Footer has Activity (semantic event ring) and Logs (raw journald via
`/api/logs/stream`, per-slot sub-tabs). Lemonade `/logs/stream` adds a
third source — internal load/evict/state lines journald can't see
(Lemonade is one process logging internally).

**Decision: fold Lemonade lines into Logs, not Activity.** Activity
stays semantic; Logs stays raw text; Lemonade lines are raw text.

### Backend contract (request to API agent)

hal0-api proxies Lemonade `/logs/stream` and re-emits each entry
through the existing `/api/logs/stream` SSE with two extras:

- `source`: `journald` | `lemonade`. `unit` field stays "systemd unit";
  Lemonade lines pin to `lemonade-server`.
- `slot`: hal0 slot name when the line is owned by a known slot
  (server-side parse from `model_name`); null for global lines.

### Sub-tab behavior

- `primary` shows `hal0-slot@primary` journald + Lemonade lines tagged
  `slot=primary`, interleaved by timestamp.
- `api` shows hal0-api journald + Lemonade global lines (evicts,
  backend installs).
- New `lemonade` sub-tab shows only Lemonade lines, all slots.

### Visual interleaving

Add a one-character source prefix per line:

```
[1:23:45]  ⓁⓃ  Loading model: hermes-4-14b-q5_k_m
[1:23:45]  Ⓙⓞ  systemd[1]: Started hal0-slot@primary.service
[1:23:47]  ⓁⓃ  llama_model_load: load model from file
[1:23:51]  ⓁⓃ  llama_init_from_model: KV self size = 1024 MiB
[1:23:51]  Ⓙⓞ  hal0-api: slot.state primary loading→serving
```

- `ⓁⓃ` = lemonade (tiny amber `L` badge), `Ⓙⓞ` = journald (grey `J`).
- Existing ERROR/WARN substring classification still colors rows.
- Click-to-copy and search both work across both sources.
- Autoscroll-with-pause behavior unchanged.

### Tagging quality

`slot` parsing is fuzzy. Policy: tag `slot=X` only when the line
contains a `model_name` currently loaded into X. Otherwise null. Never
guess — a mis-tag costs more than no-tag.

---

## 6. Install wizard — backend install progress

Lemonade adds two concerns: system prereqs (`unzip`, `libxrt-npu2`) and
async backend install (5–60 s per backend, serialized by Lemonade).

### Step 2.5 — system preflight (new)

Insert between step 2 (password) and step 3 (model picks). Same card
chrome:

```
┌─ Preflight ────────────────────────────────────────┐
│  ✓ Strix Halo iGPU detected (gfx1151)              │
│  ✓ NPU detected (amdxdna)                          │
│  ✗ unzip            required by Lemonade           │
│       [install unzip] (sudo)                       │
│  ⚠ libxrt-npu2      required for NPU/FLM           │
│       [install libxrt-npu2] (sudo)                 │
│  [ Skip optional ]            [ Re-check ]         │
└────────────────────────────────────────────────────┘
```

- Rows poll `/api/installer/preflight` (shell-out matrix).
- "install" buttons POST `/api/installer/apt-install` (server-side
  pkexec or pre-sudoed continuation).
- NPU prereqs are warnings not blockers — defer FLM, proceed with
  iGPU-only.

Step counter becomes 9 steps (preflight is step 3 in the indicator).

### Backend install progress (existing install screen, new rows)

Existing install screen already polls `/api/installer/install` and
renders per-row bars. With Lemonade, each row becomes two phases:

```
┌─ Installing ─────────────────────────────────────────┐
│ llamacpp:rocm backend                                │
│   [████████████████████████░░░░░░░]  installing 73%  │
│ hermes-4-14b-q5_k_m (8.4 GB)                         │
│   [████████░░░░░░░░░░░░░░░░░░░░░░░]  downloading 24% │
│ kokoro:cpu backend                                   │
│   [██████████████████████████████░]  installing 95%  │
│ kokoro-82m (327 MB)                                  │
│   [░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░]  queued          │
│ whispercpp:rocm backend                              │
│   [██████████████████████████████★]  ready           │
└──────────────────────────────────────────────────────┘
```

- Backend rows precede model rows (visual grouping).
- Progress channel: **`/api/installer/events` SSE** (request to API
  agent). hal0-api drives `lemonade backends install …` and tails
  `/logs/stream`, mapping to `installer.backend.progress` events.
- Polling fallback: `lemonade backends list` every 2 s; diff recipe
  state to flip rows to ready. SSE preferred.
- Known failure (`flm:npu` install silently fails) → inline error on
  the FLM row ("retry or skip?"). Don't block the wizard — iGPU-only
  is a valid path.

No separate "backends" step. Rows are enough disclosure.

---

## 7. Lemonade web-ui adoption recommendations

Lemonade ships a React/Tauri app at `src/app/src/`, dual-built as
desktop and `/app` web endpoint (per Lemonade's `docs/dev/web-ui.md`),
with a hard Debian-packaging dep constraint. Per primitive:

| Primitive | Recommend | Rationale |
|---|---|---|
| **Whole React app** | **Reject** | Different framework (we're Vue), different visual language, Debian constraint isn't our problem. |
| **`/app` chat surface** | **Reject for dashboard. Adopt for `hal0-chat`.** | We already deferred a built-in chat UI to v0.2.1 (per `hal0_owui_no_base_path`). The Lemonade `/app` endpoint is an OpenAI-compatible chat UI shipped free. Wire it under `hal0-chat.thinmint.dev` as the v0.2 chat experience — zero dashboard cost. |
| **WS `/logs/stream` protocol** | **Adopt** | Already in the migration plan; documented in `hal0_lemonade_ws_protocol.md`. Proxy on hal0-api side, re-emit through existing `/api/logs/stream` SSE. |
| **WS `/realtime` protocol** | **Adopt later** | OpenAI-compatible audio WS. Out of v0.2 dashboard scope; relevant when we add voice-streaming UI in v0.3 or v1.0. |
| **`/v1/health` shape** | **Adopt verbatim** | Already in the contract. UI consumes `{models: [{model_name, backend_url}]}` as canonical loaded-state. |
| **`/v1/stats` shape** | **Adopt** | Replaces dead `/metrics`. UI mapping: `tokens_per_sec`, `kv_cache_usage`, `mem_rss_mb`, `uptime_seconds`. |
| **Recipe identifiers** (`llamacpp:rocm`, etc.) | **Adopt as backend-chip text** | Use them as-is. Don't humanize; the recipe ID is the authoritative term and what `docs/dev/web-ui.md` uses. |
| **Lemonade collection.omni manifest** | **Reject** as a UI element | Per `hal0_lemonade_omni_pattern`: hal0's `capabilities.toml` IS our omni; one-way export only. No "omni card" or "active collection" indicator in the dashboard. |
| **Lemonade install/setup wizard** | **Reject** | Web-app's first-run isn't visible to us; we have our own 9-step wizard with distinct language ("capability slots" / "loadouts"). Don't fork. |

**Net adoption:** protocols (WS taxonomies, REST shapes, recipe IDs).
**Net rejection:** any visible UI component or whole-page surface,
except `/app` chat — out-of-process, parallel subdomain, like OWUI.

---

## 8. Open questions for architect + API

For architect:

1. **Slot wrapper.** Keep per-slot `hal0-slot@<name>` systemd units
   wrapping `lemonade load …`, drop to single
   `hal0-lemonade-server.service`, or hybrid? UI assumes unit names
   survive (Logs sub-tabs key on them).
2. **Queue position source.** Per-slot record (`queue_position`,
   `queue_blocked_by`) or global "currently-loading" field? UI prefers
   per-slot — keeps SSE ring per-slot.
3. **Evict-cascade detection.** Server-side `slot.evict_cascade` event
   with `affected: [name…]` preferred. Cheaper, no race window.

For API:

4. **`/api/installer/events` SSE.** Dedicated endpoint or reuse
   `/api/logs/stream?source=installer`? UI prefers dedicated —
   different lifetime, different audience.
5. **`/api/backends/lemonade`.** Mirror `/api/backends/npu` (pid,
   loaded models, recipe install state)? Not v0.2-critical but enables
   "Lemonade-server" as a peer on Hardware view.
6. **Catalog backend IDs.** Confirm catalog emits `llamacpp:rocm` et
   al. verbatim so chip text matches recipe.
