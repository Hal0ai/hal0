# Vue dashboard archive — snapshot summary

The `ui-vue.bak/` tree (37k+ LOC, 174 files) was deleted in Phase B2 of
the v3 React dashboard cutover (PR for `feat/dash-v3-B2-playwright` →
`feat/dash-v3-react`). This note replaces it with a pointer-only summary
so future history-spelunking doesn't require digging in `git log`.

## What it was

The `v0.2.1-alpha.1` Vue 3 dashboard preserved verbatim from base commit
`3fef556` (PR #199 release). Built with Vue 3 + Pinia + `<script setup>`
SFCs, served from `ui-vue.bak/src/`:

- `App.vue` + `router.js` — vue-router shell
- `views/` — top-level page components (one per route)
- `components/` — shared chrome (TopBar, Sidebar, Footer, modals, primitives)
- `composables/` — useMock.js (MOCK_DATA), useTweaks, useBanners
- `stores/` — Pinia stores (api, slots, models, settings, lemonade,
  agent approvals, mcp)

## Why it stayed around through Phase A

Phase A scaffolded the React tree from the design prototype but did not
yet wire API hooks. Keeping the Vue tree on disk meant Phase B1 could
copy the API contract (endpoint shapes, polling cadences, SSE event
streams) without re-deriving it from the Python backend.

## What it has been replaced by

- **Frontend tree:** `ui/` — React 18 + TypeScript + Vite + Tailwind 4,
  built around `ui/src/dash/*.jsx` (the design prototype, transpiled by
  `@vitejs/plugin-react`).
- **E2E suite:** `ui/tests/e2e/` — 10 per-route spec files (28 tests)
  using `@playwright/test`, with `apiMock` + `sseHarness` + `mock-data`
  fixtures mirroring the Vue suite's structure.
- **API contract reference:** the Vue tree's `src/api/*` + `src/stores/*`
  modules informed Phase B1's hook design. Phase B1 owns the React port
  (`ui/src/api/` + `ui/src/stores/`).

## Where to recover the source if needed

`git log --all -- ui-vue.bak/` from any branch that predates this
deletion (PR for `feat/dash-v3-B2-playwright`). The `feat/dash-v3-react`
branch's prior HEAD `50fe89d` retains the full tree until that branch
is rebased.

## Spec retirement decisions

The Vue suite had 25 spec files. The React port consolidates to 10
per-route smoke specs (one per route, ~3 assertions each). The dropped
specs were:

| Vue spec                              | React replacement                       |
| ------------------------------------- | --------------------------------------- |
| `chrome.spec.ts`                      | folded into `dashboard-v3.spec.ts`      |
| `dashboard.spec.ts`                   | `dashboard-v3.spec.ts`                  |
| `dashboard-lemonade-state.spec.ts`    | deferred to Phase C polish              |
| `extras-v2.spec.ts`                   | split into hardware/backends/logs/agent |
| `first-run-bundle-picker.spec.ts`     | folded into `firstrun-v3.spec.ts`       |
| `firstrun-v2.spec.ts`                 | `firstrun-v3.spec.ts`                   |
| `footer.spec.ts`                      | deferred to Phase C polish              |
| `hardware.spec.ts`                    | `hardware-v3.spec.ts`                   |
| `lemonade-admin.spec.ts`              | deferred to Phase C polish              |
| `lemonade-journal.spec.ts`            | deferred to Phase C polish              |
| `lemonade-voice-chip.spec.ts`         | deferred to Phase C polish              |
| `logs.spec.ts`                        | `logs-v3.spec.ts`                       |
| `mcp-v2.spec.ts`                      | `mcp-v3.spec.ts`                        |
| `models.spec.ts` + `models-v2.spec.ts`| `models-v3.spec.ts`                     |
| `models-slots-refactor.spec.ts`       | deferred to Phase B1 follow-up          |
| `npu-swap-ux.spec.ts`                 | deferred to Phase C polish              |
| `polish.spec.ts`                      | dropped — covered by per-route smoke    |
| `primitives.spec.ts`                  | dropped — DOM-level, not user-visible   |
| `settings.spec.ts` + `settings-v2.spec.ts` | `settings-v3.spec.ts`              |
| `slot-lifecycle.spec.ts`              | deferred to Phase B1 follow-up          |
| `slots-v2.spec.ts`                    | `slots-v3.spec.ts`                      |
| `update.spec.ts`                      | deferred to Phase C polish              |

Deferred specs are not lost — they re-enter scope once Phase B1 wires
the matching store hooks and Phase C polish lands the missing UX
(model-pull errors, NPU swap progress, OmniRouter voice chip).
