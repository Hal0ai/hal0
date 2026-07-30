# Dashboard Overhaul — FROZEN DATA CONTRACT (v1)

> Authored by the orchestrator from the FE+BE grounding surveys + user blocker decisions.
> This is the **interface boundary** between the Frontend team and the Backend team.
> Neither team may change a shape here without the orchestrator (reviewer) re-freezing it.
> Branch: `feat/dashboard-overhaul` · worktree `/home/halo/dev/hal0-dashboard`.
> Scope: **main dashboard page only.** Slots/Services sub-routes are reachable but not the deliverable.

---

## 0. Hard rules
- **NO STUB DATA.** Every value rendered comes from a real endpoint. If a source is missing,
  the widget is gated ("source pending") or not shipped — never fake numbers.
- **Status dots derive from real activity state** (the core fix). See §3.
- Frontend reaches the API via the existing Vite proxy (`/api`, `/v1` → :8080). Use the
  typed-hook + React Query pattern already in `ui/src/api/hooks/*.ts`. No new fetch stack.
- All new FE state for the grid persists through the backend layout store (§4), not localStorage.

---

## 1. Read endpoints that ALREADY EXIST (FE: wrap in hooks, do not ask BE to build)

| Need | Endpoint | Poll | Shape (relevant fields) |
|---|---|---|---|
| Slot list | `GET /api/slots` | 5s | `[{name, state, backend, device, model_id, port, tokens_per_sec, ttft_seconds, requests_processing, mem_rss_mb, kv_cache_usage, ctx, uptime_seconds}]` |
| Slot detail | `GET /api/slots/{name}` | 2.5s | same shape, single |
| Slot metrics | `GET /api/slots/metrics` | 2.5s | `{slot: {tokens_per_sec, ttft_seconds, ...}}` |
| Slot capacity | `GET /api/slots/capacity` | 5s | `{per_slot: {slot: {vram_mb, ram_mb, mem_mb, state, model_id}}}` |
| Memory + hw | `GET /api/stats/hardware` | 5s | `{ram_total_mb, ram_used_mb, unified_memory_mb, gtt_total_mb, gtt_used_mb, gpu_util, per_slot{...}, npu_status{ok, model_mb}}` |
| Hardware probe | `GET /api/hardware` | static | host/cpu/gpu/npu/ram identity |
| ComfyUI | `GET /api/comfyui/status` | 3s | `{reachable, engine, mode, running_jobs, pending_jobs, memory{...}, hermes_unit_status, arbiter_block}` |
| ComfyUI queue | ComfyUI native `/queue`,`/history` (already proxy-reachable) | 2s while expanded | native ComfyUI shape |
| Approvals | `GET /api/agent/approvals` + SSE `GET /api/agent/approvals/events` | SSE | `{approvals:[{id, agent, tool, args, client_id, enqueued_at, state}]}` |
| Chat stream | `POST /v1/chat/completions` `{model:"<slot-name>", messages, stream:true}` | — | OpenAI SSE frames |

**Existing FE primitives to REUSE (do not rebuild):** `slot-status.js → slotIndicatorFromPhase()`,
`memory-map.jsx → MemoryMap/useMemoryMapModel`, `inference-pane.jsx`, `comfyui-pane.jsx`,
`command-palette.jsx`, `primitives.jsx` (Modal/Drawer/ConfirmDialog/Banner/Menu/PillToggle).
**Build NEW:** `DCard` shell primitive; the masonry grid + edit mode.

---

## 2. NEW backend endpoints the Backend team MUST build

### 2a. Throughput history (feeds default "Combined throughput" card)
```
GET /api/stats/throughput/history?buckets=20&window_s=100
→ 200 {
    "window_s": 100,
    "bucket_s": 5,
    "samples": [ { "ts": <epoch_s>, "total_tps": <float>, "serving_slots": <int> }, ... ],  // oldest→newest, len≈buckets
    "per_slot": { "<slot>": [<float>, ...] }   // optional; same length; for the opt-in per-slot card
  }
```
- Derive by bucketing the EXISTING in-memory `app.state.tps_events` deques. No new DB.
- If fewer samples than `buckets` exist yet, return what's available (FE pads left). Never fabricate.

### 2b. CPU utilization (feeds default "Utilization" card)
- Extend `GET /api/stats/hardware` snapshot with **`cpu_util`** (float 0–1, real, via psutil over a short interval).
- NPU: expose what's real — `npu_status.ok` (active/idle) and `npu_status.model_mb`. Do **not** invent an NPU %.
  Add `npu_util` (float 0–1) **only if** the NPU-telemetry spike (§5) lands a real source; otherwise omit the key.
- FE renders: iGPU row = `gpu_util` %; CPU row = `cpu_util` %; NPU row = active/idle pill + "% pending driver"
  (becomes a % bar automatically if `npu_util` appears).

### 2c. Dashboard layout persistence (feeds the whole grid)
```
GET  /api/user/dashboard-layout            → 200 DashLayout (see §4)  | 200 {} if none saved
PUT  /api/user/dashboard-layout  <DashLayout> → 204
```
- File-backed JSON in hal0's state dir (single-operator LAN box; no auth). Validate against the §4 schema;
  reject unknown card ids. Server runs the same `reconcile()` invariant (§4) defensively.

### 2d. Service health (feeds default "Services" card) — pending §5 spike
```
GET /api/services/health → 200 {
  "services": [ { "id":"comfyui"|"hermes"|"openwebui"|"n8n", "name":..., "up":bool, "detail":str, "url":str|null, "stat":{label,value}|null } ]
}
```
- comfyui/hermes: reuse existing signals. openwebui/n8n: lightweight reachability ping (§5 spike confirms method).
- A service with no real probe is reported `up:false, detail:"unmonitored"` — **not** a fake "up".

---

## 3. Status-dot state contract (THE core fix — applies everywhere a dot renders)

Backend `slot.state` enum → dot class/visual (FE maps via the EXISTING `slotIndicatorFromPhase()`):

| `slot.state` (BE) | Dot | Animation | Token |
|---|---|---|---|
| `serving` | green | **pulse + glow** | `--ok #6FCF97`, `box-shadow:0 0 8px var(--ok)` |
| `ready` | amber | static | `--hal0-accent #FFB000` |
| `warming` / `starting` / `pulling` | orange | **pulse + glow** | `--warming #F2792B` |
| `idle` | grey | static | `--fg-5` |
| `offline` / `unloading` | grey | static | `--fg-5` |
| `error` | red | static | `--err #EF6B6B` |

- FE MUST verify the green pulse renders when a real slot reports `state:"serving"`. This is the acceptance gate.
- Respect `prefers-reduced-motion: reduce` (kills the pulse loop).

---

## 4. DashLayout schema — v3, fixed bands (FE owns shape; BE validates + persists)

The free-form 12-column grid (v2: `order`/`enabled`/`spans`/`pinned`, drag-reorder, resize, card
library) was replaced by a FIXED-BAND layout in #1061. Rows and cell widths are defined by the
system. The operator can only (a) swap which widget occupies a swappable cell, from that cell's
curated whitelist, and (b) toggle the quick-actions strip.

```ts
type CellId = string;                 // a fixed cell in the band layout
type WidgetId = string;               // an entry in WIDGET_DEFS
interface DashLayout {
  v: 3;                               // schema version
  cells: Record<CellId, WidgetId>;    // which widget occupies each cell
  quickActions: boolean;              // quick-actions strip on/off
}
```

- Transport: `GET`/`PUT /api/user/dashboard-layout`. GET returns `{}` when nothing is saved.
- `reconcile(layout)` runs on load AND on write, **both ends**, with the same rules: every cell in
  the registry is present; a cell keeps its assignment only when the widget is on that cell's own
  `accepts` list *and* is actually built; anything else falls back to the cell's `defaultWidget`.
  `quickActions` is true unless explicitly `false`. FE: `ui/src/api/hooks/useDashLayout.ts`.
  BE: `src/hal0/dashboard/layout_v3.py`. Adding a widget or a cell means editing **both**.
- Read path is fail-soft: a 404, `{}`, a v2 payload, or any error resolves to `DEFAULT_LAYOUT`.
  Write path is fail-soft **only on 404** — every other rejection is toasted and the cache
  re-read, so a save that didn't persist can't look like it did (#1460).
- v2 is TOLERATED, not emitted: `PUT` still accepts a v2 body (a stale cached bundle must not
  start 422-ing) and a pre-#1061 file on disk still round-trips under the v2 pin/span reconcile.
  No shipped UI writes v2.

### Cell registry (CellId → accepts / default)
| cell | accepts (swap whitelist) | default | locked |
|---|---|---|---|
| `memory` | memorybar · memtreemap · memring | `memorybar` | |
| `a1` | throughput · slottrack · power | `throughput` | |
| `a2` | utilization · power · gauges | `utilization` | |
| `a3` | requests · clients | `requests` | |
| `slots` | slots | `slots` | ✅ |
| `c1` | activity · heatmap | `activity` | |
| `c2` | services · quickchat | `services` | |
| `c3` | attention | `attention` | ✅ |

### Widget registry (`built` gates the swap picker)
| widget | built | source |
|---|---|---|
| `memorybar` | ✅ | /api/stats/hardware |
| `memtreemap` | ⬜ | — same-family memory visualization, not yet built |
| `memring` | ⬜ | — same-family memory visualization, not yet built |
| `throughput` | ✅ | /api/stats/throughput/history |
| `slottrack` | ✅ | throughput/history per_slot |
| `power` | ✅ | /api/stats/power |
| `utilization` | ✅ | /api/stats/hardware (gpu+cpu) |
| `gauges` | ⬜ | — not yet built |
| `requests` | ✅ | derived request rollup |
| `clients` | ⬜ | — not yet built |
| `slots` | ✅ | /api/slots |
| `activity` | ✅ | /api/activity |
| `heatmap` | ⬜ | — not yet built |
| `services` | ✅ | /api/services/health + comfyui/status |
| `quickchat` | ✅ | /v1/chat/completions |
| `attention` | ✅ | derived (error/warming slots + approvals + updates) |

---

## 5. Investigation spikes (BACKEND team spawns; findings ESCALATE to user via orchestrator)
Each spike: confirm a REAL source exists, propose the exact endpoint + effort, report to orchestrator. Do NOT ship fake data.
1. **Power/thermal** — probe `/sys/class/hwmon` on CT105 (10.0.1.142): does amdgpu expose `power1_average`,
   k10temp temps, fan rpm? Propose `GET /api/stats/power`.
2. **Scheduler (lemond)** — the survey said "external," but a `Dispatcher` lives in `app.state`. Confirm whether it
   exposes in-flight/queued/dispatch-history. Propose `GET /api/scheduler/status` if real.
3. **NPU util** — coordinate with the live NPU-telemetry session (`xrt-smi` aie-partitions/duty). If a real load
   metric lands, add `npu_util` to §2b.
4. **Service health method** — confirm OpenWebUI/n8n reachability probe (root ping vs /health) for §2d.

---

## 6. Build order (handoff §"Suggested build order", contract-aligned)
1. `DCard` + status-dot (verify §3 green pulse on a real serving slot).
2. `SlotList` anchor wired to `/api/slots` (real serving state).
3. Masonry grid + layout state + edit mode (drag/resize/library/pin) + persistence (§2c, §4).
4. Memory map, Throughput (§2a), Utilization (§2b).
5. Quick chat (real stream), Services (§2d + ComfyUI live queue).
6. Opt-in cards — only those whose §5 spike landed a real source.

## 7. Acceptance gates (orchestrator review)
- A real serving slot shows a **green pulsing** dot end-to-end.
- Zero fabricated numbers (grep for mock/placeholder in new code; mock layer only for tests).
- Layout survives reload AND a different browser (backend store).
- `<1200px` collapses to single column; `prefers-reduced-motion` kills loops.
- `npm run typecheck` + Playwright E2E green; new specs for the grid/edit-mode/dot-state.

---

# Operator Board (#board) — FROZEN DATA CONTRACT (v1)

> Authored & frozen by the UI lead from SPEC §4. The board UI reaches `/api/board/*`
> via the typed-hook + React Query pattern (`useBoard.ts`) over the existing Vite proxy.
> hal0 OWNS the operator board (rework R4 §Agents-and-brain) — these routes read/write
> hal0's own SQLite-backed `BoardStore`, not a Hermes proxy. Hermes is an OPTIONAL
> executor: its kanban plugin only seeds the store on first boot when present, and the
> board works without it. Paths, methods, payload shapes, status codes, and WS events
> stay FROZEN unchanged from the earlier proxy-forward era; neither team changes a shape
> here without re-freeze.

## Hard rules (board)
- **NO STUB DATA.** Every rendered value comes from a real `/api/board/*` response or is gated.
  Mock board data lives ONLY in the e2e/mock layer (`apiMock.ts` board routes + `mock-data.ts`).
- `?board=<slug>` threads through every task/board-scoped call (omit = current board).
- Mutations are audited server-side (`board.<noun>.<verb>`); reads + SSE/WS are not.
- Live board updates arrive via the `/api/board/events` WS — the board reflects ALL mutations
  (operator's, the agent chat's, other workers') through this one transport. Chat ≠ board transport.

## Status / lane model (EXACT)
`VALID_STATUSES` (9): `triage, todo, scheduled, ready, running, blocked, review, done, archived`.
Visible columns (8, in order): `triage, todo, scheduled, ready, running(=in-progress), blocked, review, done`.
`archived` shown only when "Show archived" is on. UI label: `running` → "in-progress".
Move a task = `PATCH /api/board/tasks/{id} {status}`. `done` completes; `blocked` accepts `block_reason`.

## Endpoints (see `endpoints.ts` board section — authoritative)
| hal0 `/api/board/...` | method | audited action |
|---|---|---|
| `/board` | GET | — |
| `/tasks/{id}` | GET | — |
| `/tasks` | POST | board.task.create |
| `/tasks/{id}` | PATCH | board.task.update |
| `/tasks/{id}` | DELETE | board.task.delete |
| `/tasks/{id}/comments` | POST | board.task.comment |
| `/links` | POST / DELETE | board.link.add / board.link.remove |
| `/tasks/bulk` | POST | board.task.bulk |
| `/tasks/{id}/reassign` | POST | board.task.reassign |
| `/tasks/{id}/specify` | POST | board.task.specify |
| `/tasks/{id}/decompose` | POST | board.task.decompose |
| `/tasks/{id}/reclaim` | POST | board.task.reclaim |
| `/tasks/{id}/log?tail=` | GET (pull-only) | — |
| `/dispatch?max=N` | POST (one-shot nudge) | board.dispatch.nudge |
| `/boards` | GET / POST | — / board.board.create |
| `/boards/{slug}` | PATCH / DELETE?delete= | board.board.update / board.board.delete |
| `/boards/{slug}/switch` | POST | board.board.switch |
| `/profiles` | GET | — |
| `/profiles/{name}` | PATCH | board.profile.update |
| `/assignees?board=` | GET | — |
| `/stats?board=` | GET | — |
| `/diagnostics` | GET | — |
| `/workers/active` | GET | — |
| `/runs/{id}` | GET | — |
| `/config` | GET | — (read-only knobs: tick-interval/failure-limit/claim-TTL/max-in-flight) |
| `/orchestration` | GET / PUT | — / board.orchestration.update (4 knobs: orchestrator_profile, default_assignee, auto_decompose, auto_promote_children) |
| `/events` | WS ?since=&board= | — |
| `/chat` | POST(SSE) | board.chat.turn per board mutation · platform.chat.turn per slot mutation (reads unaudited) |

`/profiles/{name}` (PATCH) and `/diagnostics` (GET) are live routes with no
`endpoints.ts` constant yet — genuinely unconsumed backend surface (route
exists, no UI caller), not a doc gap. Adopt-or-defer per the owning lane
before wiring speculatively.

## Wire shapes (board task — the canonical card/drawer shape)
A `Task` from `/board` lanes + `/tasks/{id}`:
```
{ id, title, status, assignee|profile, tenant, priority, workspace,
  created_by, created (relative or iso), body|desc, block_reason,
  schedule?, summary?, deps:{parents:[id], children:[id]},
  comments:[{author, at, body}], events:[{kind, at, json}],
  runs:[{state, profile, dur, at, msg}],
  comment_count, dep_count (e.g. "0/3" or null) }
```
The UI normalizes assignee↔profile, created_by↔createdBy, block_reason↔blockReason in `useBoard.ts`
so view components consume one stable camelCase shape. `/board` returns lanes keyed by status (or a
flat task list the hook buckets by `status`). Empty board ⇒ all 8 lanes render with "— no tasks —".

## Gaps surfaced honestly in the UI (do not fake)
- Orchestration popover: only the 4 PUT knobs are editable (no auto/manual "mode" — the
  server has no such field). tick-interval / failure-limit / claim-TTL / max-in-flight are
  read from `/config` and rendered **read-only with a note**.
- The `/chat` agent is the hal0-brain PLATFORM steward (profile `hal0-brain`, default
  model `hal0/brain` → falls back to the `agent` slot via the resolver chain): board
  tools (reads + audited mutations, incl. get/update_orchestration) plus hal0-api
  self-HTTP tools (slots list/get/load/unload/restart, models, hardware stats,
  installed agents). SSE frame contract adds `{type:"thinking",text}` (model
  reasoning, rendered folded) alongside token/tool_call/tool_result/done/error.
- Drag-and-drop outside any lane ARCHIVES the card (`board-drop-archive` /
  `board-archive-veil`); nothing on the board hard-deletes without an explicit action.
- "Nudge dispatcher" = one-shot `POST /dispatch?max=N`. No continuous start/stop.
- Worker log is pull-only (`/tasks/{id}/log?tail=`); poll on drawer open, no live stream.
- `create_task` may return a `warning` (no dispatcher running) — show it as a toast/banner.

## Acceptance
- Pixel parity vs the prototype (`/home/halo/Development/Projects/hal0/kanban/board/`).
- `npm run typecheck` + Playwright E2E green; every SPEC §5 feature covered in multiple shapes.
