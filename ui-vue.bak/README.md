# hal0 dashboard — Vue 3 (archived)

This is the **v0.2.1 Vue 3 dashboard** preserved verbatim from the
`feat/dash-v3-react` base commit (`3fef556`). It is the dashboard that
shipped in `v0.2.1-alpha.1` (PR #199).

**It is not built or deployed in v0.3.** The live `ui/` is a fresh
React + TypeScript + Vite scaffold built around the design prototype at
`/tmp/hal0-design-v3-react/hal0-v2/project/`. See PR #200 for the Phase A
scaffold and the follow-up Phase B (API wiring) and Phase C (Playwright +
LXC deploy) plans.

Why keep it around:

- **API contract reference** — the Vue dashboard's `src/api/*` hooks +
  Pinia stores are the canonical reference for which `/api` and `/v1`
  endpoints the hal0-api speaks. Phase B will re-implement these against
  the React tree using `@tanstack/react-query` + Zustand.
- **E2E test reference** — `tests/e2e/*.spec.ts` documents the user
  flows that must keep working when the React tree ships. Phase C will
  port these specs against the new DOM.

If you need to run the Vue tree locally for comparison:

```sh
cd ui-vue.bak
npm install
npm run dev
```

When v0.3 ships and the React tree reaches feature parity, this
directory will be deleted.
