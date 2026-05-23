# hal0 Dashboard v2 — Implementation Plan

_2026-05-23. Sourced from `claude-design` handoff bundles:_
- _v0.2 base — bundle `auhguXgmXV_rq-UfD-1Dog`, extracted at `/tmp/hal0-design/hal0-v2/`_
- _v0.3 carry-in (MCP Servers page + footer/primitives polish) — at `/tmp/hal0-design-v3/` (mirror `/home/halo/Development/hal0/hal0-dash-v.03/design_handoff_mcp_servers/`)_

Closes issue **#148** (v0.2.1 Vue dashboard retarget + slot-state polling).
Operates inside the v0.2 Lemonade migration window — PRs #137, #151–163 already retired the per-modality toolboxes, simplified `SlotManager` → `LemonadeClient`, and landed `/v1/health` polling + NPU exclusivity + nuclear-evict banner.

---

## Locked decisions

| Topic | Decision |
|---|---|
| Stack | **Vue 3 + Pinia + Tailwind v4** (preserve; recreate prototype's React/JSX pixel-perfectly in Vue) |
| Rollout | Long-lived **`feat/dash-v2-rework`** branch; PR-per-route; merge to main when complete |
| Backend gap strategy | **Hybrid** — parallelize low-risk read-only backend work (#145 metrics, #146 FLM install); UI mocks #142 (multi-modal slots) until contract stable |
| Dev host | **hal0 LXC (10.0.1.142)** via `ssh hal0` |
| Tweaks panel | **Dev-only behind `import.meta.env.DEV`** |
| Issue tracking | One GH issue per PR via `/to-issues` |
| Design source canonical | **v0.3** (`/tmp/hal0-design-v3/`) for everything henceforth — bug fixes + MCP page additions |

---

## Slice ledger (live)

| # | Slice | Issue | Status | PR | Branch |
|---|---|---|---|---|---|
| 1 | Token harmonization | #164 | **MERGED** | #177 | `feat/dash-v2-1a-tokens` |
| 2 | Pinia store skeleton | #165 | **MERGED** | #178 | `feat/dash-v2-1b-stores` |
| 3 | Mock harness | #166 | pending | — | — |
| 4 | Primitives | #167 | in-flight | — | `feat/dash-v2-1a-primitives` |
| 5 | Chrome | #168 | pending | — | — |
| 6 | Dashboard / | #169 | pending | — | — |
| 7 | Slots /slots | #170 | pending | — | — |
| 8 | Models /models | #171 | pending | — | — |
| 9 | FirstRun /firstrun | #172 | pending | — | — |
| 10 | Settings /settings | #173 | pending | — | — |
| 11 | Extras (Hardware/Backends/Logs/Agent) | #174 | pending | — | — |
| 12 | Polish | #175 | pending | — | — |
| 13 | Cutover | #176 | pending | — | — |
| **14** | **MCP Servers page (v0.3 carry-in)** | **#180** | pending | — | — |

---

## v0.3 fold-in (2026-05-23 update)

A v0.3 design iteration shipped mid-rework with bug fixes + a new MCP Servers page. Folded into this rewrite rather than deferred.

- **All future slices use v0.3 source as canonical** (`/tmp/hal0-design-v3/`).
- **Carry-in fixes** (absorbed into briefs):
  - `primitives.jsx` — Menu onClick honors callback before toast; post-install "Take the tour" dispatches CustomEvent; **19th banner** `skip-path` (slots scope, slice #4)
  - `chrome.jsx` — footer journal pane gains source filter (merged/hal0/lemond) + search + amber highlight + empty state (slice #5)
  - `dashboard.css` — 189 net new lines (mostly MCP-page styles; folded into slice #14)
- **NEW slice #14** — Dashboard v2 — MCP Servers page (`/agents/mcp`) → issue **#180**. Blocked by slices 4 (primitives) + 5 (chrome). New sidebar group "Agents · v0.3" containing `Agents` / `MCP Servers` / `Memory (coming soon)`.

---

## Phase 0 — Foundations (3 PRs) ✅ partial

### 0a · Token harmonization (#164 → PR #177 MERGED)
Added design's surface ramp + device chip colors + motion tokens as aliases over existing `--hal0-*` in `ui/src/style.css`. 34 new tokens, zero component edits, 38/38 Playwright green.

### 0b · Pinia store layout (#165 → PR #178 MERGED)
5 new stores: `useLemonadeStore` (2s /v1/health poll), `useBackendsStore`, `useBannerStore` (18-state catalog), `useToastStore`, `useTweaksStore` (dev-only). Extended `useSlotsStore.device`. Refactored existing `useNuclearEvictBanner.js` to consume `useLemonadeStore`. PR-11's spec still passes.

### 0c · Mock harness (#166 — pending)
Port designer's `HAL0_DATA` to `ui/src/composables/useMock.js`. `VITE_MOCK_LEMONADE=1` for offline dev. Same shapes as Playwright `apiMock.ts`.

## Phase 1 — Primitives + Chrome

### 1a · Primitives (#167 — in-flight)
`<Modal> <Drawer> <ConfirmDialog> <Banner> <BannerStack> <Menu> <Toast> <ToastStack>`. Adds 19th banner `skip-path`. Sources from v0.3 `primitives.jsx`.

### 1b · Chrome (#168)
TopBar / Sidebar (full ≥1280, icon-collapse 1080–1279, hidden <720) / Footer with v0.3 journal pane (source filter + search + highlight + empty state) / BottomTabs (<720). Sidebar gains **Agents · v0.3** group containing `Agents` / `MCP Servers` / `Memory (coming soon)`. Mounts ToastStack at root.

## Phase 2 — Per-route rewrites (6 PRs, parallel after Phase 1)

| PR | Branch | Route(s) |
|---|---|---|
| 6 | `feat/dash-v2-2-dashboard` | `/` |
| 7 | `feat/dash-v2-3-slots` | `/slots`, `/slots/:name` |
| 8 | `feat/dash-v2-4-models` | `/models` |
| 9 | `feat/dash-v2-5-firstrun` | `/firstrun` |
| 10 | `feat/dash-v2-6-settings` | `/settings` |
| 11 | `feat/dash-v2-7-extras` | `/hardware /backends /logs /agent` |

## Phase 2.5 — MCP page (v0.3 carry-in)

### 14 · MCP Servers (#180)
New route `/agents/mcp`. LiveTimeline oscilloscope. Install drawer (Catalog + URL/manifest tabs). Connect-client modal (CC/Desktop/Cursor). 8 mock servers, 3 clients, 12-item catalog. Blocked by #167 primitives + #168 chrome (needs Agents · v0.3 sidebar group). Sources from v0.3 `mcp.jsx`, `mcp-modals.jsx`, `mcp-data.jsx`, `mcp.css`, 974 lines of CSS.

## Phase 3 — Polish + cutover

### PR 8 · Polish (#175) — Skeleton loaders, a11y, TopBar overflow, drift banners
### PR 9 · Cutover (#176) — Delete old views, full test pass, tag `v0.2.1-alpha.1`

---

## Parallel backend work (low-risk, alongside Phase 2)

| Issue | Risk | Plan |
|---|---|---|
| **#145** Metrics aggregator | LOW | Already in flight (worktree `feat/lemonade-metrics-shim-pr12`); contract = `useLemonadeStore` mock shape |
| **#146** FLM/NPU install path | LOW | Spawn alongside Phase 2; UI consumes `/api/backends/flm-npu` as inventory |
| **#142** Multi-modal slots | HIGH | WAIT. UI ships with mock shapes per ADR-0008 §4. Backend lands AFTER cutover |

---

## Agent rules (codified from observed failures)

- **Worktree path discipline** — agents MUST use their worktree absolute path, not `/home/halo/dev/hal0/`. CWD pin defends `cd` slips; Edit/Write/Read take absolute paths that bypass it. Two consecutive slice agents leaked writes into main before catching it. Brief every spawn with explicit worktree path + periodic `git -C /home/halo/dev/hal0 status -s` checks.
- **Serial spawn** — concurrent worktree agents in ONE parent message inherit each other's branches. Spawn one per turn.
- **Single-file ownership** — when multiple agents share a worktree, partition files; no `git add -A` sweeps.
- **STOP gate** — PR opened + CI green, NOT merged. Orchestrator merges.
- **CI gate** — `ruff format --check && ruff check && npm --prefix ui run test:e2e`

---

## Risk register

| Risk | Mitigation |
|---|---|
| React→Vue port: heavy state in chat/slots/models 3-pane | Per-slice reviewer pass |
| BabelStandalone + EDITMODE — dropped; Tweaks panel goes dev-only | Briefed |
| Mock-vs-real shape drift on Lemonade endpoints | Contract docs gate every backend PR |
| Parallel session collisions during a 2-week push | `gh pr list` + `git log origin/main` before merge |
| LXC has no git push auth | `format-patch` from LXC → `git am` + push from hal0-dev |
| Agent worktree path leaks | Briefed in every spawn (see Agent rules above) |

---

## Source artifacts

- Design bundle v0.2: `/tmp/hal0-design/hal0-v2/`
- Design bundle v0.3 carry-in: `/tmp/hal0-design-v3/` (mirror at `/home/halo/Development/hal0/hal0-dash-v.03/design_handoff_mcp_servers/`)
- Live dashboard audit: see agent transcripts — Vue 3 + Pinia, 12 routes, ~30 components
- Backend audit: PRs #137, #151–163 (Lemonade adoption + dashboard slot-state polling). Open: #140 (E2E primary), #142 (multi-modal), #145 (metrics, in flight), #146 (FLM install), #147 (cutover)
