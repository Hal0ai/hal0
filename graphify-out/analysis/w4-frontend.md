# FRONTEND (ui/) — graphify analysis

Worker slice: `ui/` (React/TS dashboard). Analysis covers the API client seam,
the hooks layer, the Zustand stores, the dashboard panes, and the mock fallback.

## Scope caveats (corrections to the brief)

The prompt names three "key frontend nodes" — `BoardStore`, `apiGet()`, `connect()`.
The graph shows only **one** of those is actually in `ui/`:

| Node              | Source                                | Tier   | Degree | Notes |
|-------------------|----------------------------------------|--------|--------|-------|
| `BoardStore`      | `src/hal0/board/store.py:83` (Python) | backend | 114 | NOT ui/. The "Operator Board" UI backend state machine; UI talks to it via `/api/board/*` + WS through `useBoard.ts`. |
| `connect()`       | `src/hal0/db/connection.py:78` (Python)| backend | 114 | NOT ui/. SQLite connection factory. |
| `apiGet()`        | `ui/src/api/client.ts:107` (TS)       | ui     | 105 | The actual frontend seam. |

I report on the real frontend seam (`apiGet`, the `client.ts`/`endpoints.ts`/`mock.ts` trio, 40+ hooks, 56 panes, 3 Zustand stores). The Python `BoardStore` and `connect()` show up only where they intersect the UI — via the contract (`/api/board/*`, SQLite-backed endpoints).

## Findings

### 1. Three-layer UI architecture, hand-glued

**The stack (verified):** React + ReactDOM + `@tanstack/react-query` (confirmed via community 230, 245 in `ui/package.json`), `zustand` (community 230), Vite for build.

Three layers:

- **Seam** (`community 0`, single community for the API surface)
  - `ui/src/api/client.ts` (123 LOC) — `api()`, `apiGet`, `apiPost`, `apiPatch`, `apiPut`, `apiDelete` + `Hal0Error` envelope (`client.ts:19-34`), ported from `ui-vue.bak/src/composables/useApi.js`.
  - `ui/src/api/endpoints.ts` (464 LOC) — single `ENDPOINTS` catalogue (`endpoints.ts:8`). "One file so a Cmd+Shift+F surfaces every URL the dashboard touches" (header comment, `endpoints.ts:1-6`). Functions interpolate path params (`slot(name)`, `slotRestart(name)`, `comfyuiWorkflowLaunch(name)`).
  - `ui/src/lib/queryClient.ts` (28 LOC) — one `QueryClient`, `staleTime: 30_000`, `retry: 1`, `refetchOnWindowFocus: false` (operator dashboard leave-open behavior).

- **Hooks** (`ui/src/api/hooks/`, 40+ `.ts` files, multiple communities)
  - Every hook imports `apiGet`/`apiPost`/etc from `../client` and `ENDPOINTS` from `../endpoints` — same pattern as `useStatsHardware.ts:8-10`.
  - God hooks: `useBoard.ts` (1494 LOC), `useSlots.ts` (717), `useModels.ts` (612), `useAgents.ts` (538). `useBoard.ts` alone covers REST queries + mutations + the **WS event stream** (`useBoardEventsStream()` in graph) + SSE chat.
  - Polling cadence lives per-hook (e.g. `POLL_MS = 2_500` at `useStatsHardware.ts:49`).

- **Panes** (`ui/src/dash/`, 56 entries)
  - The wrapper panes chosen by `#dashboard`/`#slots`/`#services`/etc are the file names in the directory listing.

### 2. State layer — only **3** Zustand stores (and one giant TanStack cache)

`ui/src/stores/` contains exactly three files:
- `useBannerStore.ts` — banner catalog (`BANNER_CATALOG` at `:43`, `CATALOG_BY_ID` at `:212`).
- `useToastStore.ts` — toast queue + `installToastGlobal()` at `:83` (publishes on `window.__hal0Toast`).
- `useTweaksStore.ts` — appearance/dev tweaks with `localStorage` persist (`:61-74`), gated `APPEARANCE_KEYS` subset outside dev (`:68-70`).

Almost everything else lives in **TanStack Query caches**, keyed by resource. The hook files themselves are the only "store" surface — there is no separate state library beyond TanStack + zustand.

### 3. Mock fallback (`ui/src/api/mock.ts`, 1233 LOC) is a parallel API

`mockFetch()` (`mock.ts:1187`) is the default fetcher in `api()` (`client.ts:63`: `const fetcher = raw ? fetch : mockFetch`). It is **drop-in**, three modes:

1. **Forced mock** — `VITE_MOCK_HAL0=1` (`mock.ts:21`) short-circuits any allowlisted URL.
2. **networkFirst** — allowlisted row flagged `networkFirst: true` (e.g. `models/updates/check`, `meta/enums`, `profiles`, `stacks`, `chat-templates` per the sample at `mock.ts:1084-1097`). Real network wins; mock is fallback on failure.
3. **404 / network failure** — non-networkFirst allowlisted paths swap in the mock builder if the live call fails.

32 allowlist rows total (`grep -c "^\s*{ re:" = 32`). Builders (`buildStatus`, `buildModels`, `buildBackends`, …) read baked payload from `window.HAL0_DATA` (`mock.ts:27-29`). Two narrow fixtures (`__hal0MockMemoryEnabled`, `__hal0MockModelUpdates`) let Playwright specs override forced-mock defaults (`mock.ts:40-42, 60-64`).

**Risk:** 1233 LOC of hand-rolled HTTP plumbing, parallel to the real API. Any drift between mock builders and the backend is invisible until a builder is exercised.

### 4. Pane ↔ hook wiring (community map)

Quick community index from `graphify query "..."`:

| Pane (`ui/src/dash/`) | Hooks it consumes | File LOC |
|------------------------|-------------------|----------|
| `dashboard-redesign.jsx` (DashboardRedesignView) | `useSlots` + `useHardware` + `useStatsHardware` + `useStatsPower` + `useThroughputHistory` + `useRequestsRollup` + `useServices` + `useConfigUrls` + `useActivityRecent` + `useApprovalList` + `useSlotDrift`/`useRestartDriftedSlots` + `useDashLayout`/`useSaveDashLayout` (`dashboard-redesign.jsx:21-42`) | 1080 |
| `comfyui-pane.jsx` | `useComfyui`, plus swarm hooks from `useServices` (community 19) | 793 |
| `connections.jsx` | uses `/api/mcp/*` + `/api/upstreams/*` (community 86) | 1486 |
| `stacks.jsx` | `useStacks`, `useProfiles` (community 50) | 850 |
| `Benchmarks.tsx` | `useThroughputHistory`, `useStatsHardware`, etc. (community 208) | 1160 |
| `slots.jsx` + `slot-modals.jsx` + `inference-pane.jsx` + `slot-list.jsx` + `slot-status.js` | `useSlots` family (`useSlotEdit`, `useSlotRestart`, `useSlotLoad`, `useSlotUnload`, `useSlotSwap`, `useComfyui`) — community 11 | — |
| `models.jsx` + `model-drawer.jsx` + `model-modals.jsx` | `useModels` (`usePullJob`, `useClearPullJob`, `useChatTemplates`) — community 10 | — |
| `memory-map.jsx` + `memory-graph*.jsx` + `memory.jsx` + `agents/agent-card.jsx` + `agents/agents-overview.jsx` + `agents/memory-tab.jsx` | bridged via `*-hook-bridge.ts` modules — community 1, 50, 163, 406, 535 | — |
| `board/{board-view.jsx, kcard.jsx, lane.jsx, task-drawer.jsx, agent-chat.jsx, orchestration-popover.jsx, new-*-modal.jsx}` | `useBoard` via `board-hook-bridge.ts` (community 31) | — |

### 5. The "hook bridge" pattern — the dominant smell

`ui/src/main.tsx:142-159` (board) and `main.tsx:119-130` (agents), `main.tsx:132-140` (memory) all import a `<topic>-hook-bridge.ts` **before** their `.jsx` consumers. The bridge publishes TanStack hooks on `window.__hal0Use*` so the legacy `.jsx` modules (which still use `Object.assign(window, …)` from the in-browser Babel prototype — see `main.tsx:1-18` commentary) can read them via globals without converting to ESM.

Net effect: a second parallel API seam layered on top of `apiGet()`. The "real" React stack is isolated to `ui/src/api/*` + `useDashLayout.ts`/`useStatsHardware.ts`-style files; the v2 prototype panes route around it via these bridges.

### 6. Dashboard layout (`useDashLayout.ts`) — fail-soft, fixed-band

Per `useDashLayout.ts:1-15`: schema `{ v: 3, cells: Record<cellId, widgetId>, quickActions: boolean }`. Fail-soft contract: any backend error → silent fallback to `DEFAULT_LAYOUT` (`:12-14`). 15 widgets (`WIDGET_DEFS` `:42-59`), 8 fixed cells (`CELL_DEFS` `:79-88`), 2 locked (`slots`, `c3 attention`). Persisted via PUT (endpoint referenced in `dashboard-redesign.jsx:11` comment as `/api/dashboard/layout`).

Renders with `metric-cards.jsx` "—" pattern: missing probe → empty cell, never fabricated value (`dashboard-redesign.jsx:13-15`, also in `useDashLayout.ts:1`).

### 7. Tests are thin and mismatched to API surface

- `ui/src/api/hooks/__tests__/` has only **3** files: `boardActors.test.mjs`, `logRing.test.mjs`, `rawLevel.test.mjs`. No hook-level tests at all.
- `ui/src/dash/__tests__/` has only **5** files: `model-sort.test.mjs`, `model-types.test.mjs`, `react-hooks-order.test.mjs`, `slot-status.consistency.test.mjs`, `slot-status.disabled-running.test.mjs`.
- `ui/src/auth/__tests__/` has **1** (`gateDecision.test.mjs`).
- No tests for `mockFetch`, no tests for `api()` error envelope, no tests for pane ↔ hook wiring.

Total UI tests: **9 files**. Total API hooks: **40+**. Coverage ratio ~22%.

## Risks / Smells

1. **God hook `useBoard.ts` (1494 LOC)** — combines REST queries, mutations, **WS event stream**, and **SSE chat**. Single file holds what would otherwise be three hooks. Cross-community fanout makes it a hotspot for regressions.

2. **Hand-rolled mock fallback (1233 LOC)** — `mock.ts` reimplements dispatch, allowlist matching, network-first semantics, and forced-mock gates. Builders must stay in sync with the 40+ backend endpoints behind `ENDPOINTS`. Drift is invisible until a builder is exercised.

3. **Two-layer API seam** — `apiGet()` (community 0) is the ESM stack; `window.__hal0Use*` bridges are the legacy-pane layer. Hot path: any new hook must decide which layer it's in; new panes still must coexist with the window-globals prototype.

4. **Panes with deep hook dependency counts** — `dashboard-redesign.jsx` reads **14 hooks** (verified `dashboard-redesign.jsx:21-42`). Mount failures cascade. No view-level smoke test exists.

5. **`Hal0Error` envelope contract** — depends on backend `hal0.api.middleware.error_codes`. No client-side tests assert envelope shape changes don't break `err.code` branching (`client.ts:75-93`).

6. **No REQUEST/WS contract test** — `useBoardEventsStream()` and the SSE chat path are big custom state machines; no dedicated test directory covers either.

7. **State concentration in TanStack caches** — only 3 Zustand stores means a hook failure produces no graceful degradation beyond the `useStatsHardware`/`useDashLayout` fail-soft pattern. Most panes will simply blank.

## Recommendations (concrete)

1. **Split `useBoard.ts` (1494 LOC) along transport boundaries**: `useBoardRest.ts`, `useBoardMutations.ts`, `useBoardEventsStream.ts`. Size is the signal — community 31 nodes are reachable from a single file.

2. **Promote the bridge files to plain ESM imports** for new panes. Either convert `board/`, `agents/`, `memory/` panes one at a time, or stop adding new panes that depend on `window.__hal0Use*`.

3. **Add `mockFetch` regression tests**: assert every `ENDPOINTS` key has a builder row, every builder returns a structurally-valid response shape (use the seam tests in `ui/src/api/hooks/__tests__/boardActors.test.mjs` as a template).

4. **One pane integration test per route**: render `DashboardRedesignView`, `SlotsView`, `ModelsView`, `BenchmarksView` with `MockQueryClient` + `mockFetch` asserting "—" placeholders for missing probes. Test count goes 9 → ~14.

5. **Assert `Hal0Error` envelope shape in tests** — small unit suite at `ui/src/api/__tests__/client.test.ts` covering 2xx/204/parse-error/non-envelope branches (`client.ts:75-103`).

6. **Type-audit `ENDPOINTS`**: 464 LOC, no test ensures every entry compiles to a real route. Add a type-level test that walks `ENDPOINTS` + reports any key whose value doesn't match `/^\/api\//` or a documented frontend route.

7. **Document `BoardStore` (Python) ↔ `useBoard.ts` (TS) contract** — the node has 114 edges in the graph; one file sits on each side of the seam; the user prompt flagged it as frontend but it's actually backend. The rework board (§2.1) should make the seam explicit.

## Citations (file:line)

- `ui/src/api/client.ts:61` — `api()` fetcher switch
- `ui/src/api/client.ts:63` — `fetcher = raw ? fetch : mockFetch`
- `ui/src/api/client.ts:107, 110, 114, 118, 122` — `apiGet/apiPost/apiPatch/apiPut/apiDelete`
- `ui/src/api/endpoints.ts:8` — `ENDPOINTS` catalogue
- `ui/src/api/mock.ts:21` — `FORCED = VITE_MOCK_HAL0 === '1'`
- `ui/src/api/mock.ts:27` — `data()` reads `window.HAL0_DATA`
- `ui/src/api/mock.ts:1084-1097` — first 5 allowlist rows
- `ui/src/api/mock.ts:1187` — `mockFetch()` drop-in
- `ui/src/api/mock.ts:1220-1223` — 404 → builder fallback
- `ui/src/lib/queryClient.ts:15-28` — singleton `QueryClient`
- `ui/src/api/hooks/useStatsHardware.ts:8-10, 49-57` — canonical hook shape
- `ui/src/api/hooks/useBoard.ts:23-25` — `boardKey()` exported for bridges+specs
- `ui/src/api/hooks/useDashLayout.ts:1-30` — v3 schema + fail-soft contract
- `ui/src/api/hooks/useDashLayout.ts:42-59, 79-88` — widget + cell registries
- `ui/src/stores/useTweaksStore.ts:61-74` — zustand `persist` to localStorage
- `ui/src/dash/dashboard-redesign.jsx:21-42` — 14-hook fan-in
- `ui/src/dash/dashboard-redesign.jsx:11` — references `/api/dashboard/layout`
- `ui/src/main.tsx:1-18` — prototype vs ESM layering explanation
- `ui/src/main.tsx:119-130, 132-140, 142-159` — three `<topic>-hook-bridge` chains
- `src/hal0/board/store.py:83` — `BoardStore` (backend; graph node)
- `src/hal0/db/connection.py:78` — `connect()` (backend; graph node)
