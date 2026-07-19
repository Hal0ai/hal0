# mock-prod-gate verification (Phase 0 launch-blocker #3)

Spec: `docs/rework/r5-sync-assessment-2026-07-19.md` §1.1 ("Gate the mock
fallback out of production").

**Verdict: NOT already fixed on descar.** All three requirements had gaps.
Fixed on branch `team/mock-gate` (see commit below) — this is a real,
reproducible defect, not a stale board entry.

## What was verified

The defect as filed: `mockFetch` (`ui/src/api/mock.ts`) substitutes a baked
fixture on 404/network-error, ignoring HTTP method, so a network-erroring
POST could come back as a fake 200 — "succeeded against thin air."

### (a) `method === 'GET'` gating for ALL rows

**Before:** `ui/src/api/mock.ts:1204` (old) —
`const substitutable = !!hit && (!hit.row.networkFirst || method === 'GET')`.
For any allowlist row WITHOUT `networkFirst` (the majority — `/api/status`,
`/api/secrets`, all `/api/memory/banks/:bank/*` routes, …), `!hit.row.networkFirst`
is `true`, so the method check was skipped entirely: **any method
substituted**, not just GET.

Concretely exploitable: `/api/memory/banks/:bank/recall` and `/api/memory/banks/:bank/reflect`
are real **POST-only** endpoints (`src/hal0/api/routes/memory_admin.py:522,523`,
consumed via `useRecall`/`useReflect` in `ui/src/api/hooks/useHindsight.ts:357,367`
through `apiPost` → `mockFetch`, no `raw:true` escape hatch). Neither row set
`networkFirst`, so a network-erroring or 404-ing POST to either endpoint was
substituted with a fake 200 fixture body.

**After:** `ui/src/api/mock.ts` — `substitutable = !!hit && method === 'GET'`,
unconditionally, for every row. Regression test:
`ui/src/api/mock.test.ts` ("never substitutes a fixture for a non-GET
request on network error" / "... on a 404").

### (b) Fallback gated behind FORCED/DEV, not live in a plain prod build

**Before:** `FORCED` (`ui/src/api/mock.ts:21`, old) only gated the
short-circuit-before-fetch branch. The 404/network-error *fallback* blocks
(after a real `fetch()` was attempted) had no `FORCED`/dev check at all —
they ran unconditionally, including in a production build.

**After:** added `const DEV = !!(import.meta.env && import.meta.env.DEV)`
next to `FORCED`; both the network-error `catch` branch and the `res.status
=== 404` branch now additionally require `fallbackAllowed = FORCED || DEV`.
`import.meta.env.DEV` is a Vite build-time constant (`false` for `vite
build`), and no build script sets `VITE_MOCK_HAL0` (grepped; only
`playwright.config.ts`'s e2e `webServer` sets it) — so in a plain production
build `fallbackAllowed` collapses to `false` at compile time.

### (c) Fixtures lazy-imported, not bundled into prod

**Before:** `ui/src/api/mock.ts` held ~1000 lines of builder functions and
fixture data (the 6-week Memory story, the FU2 600-node synthetic graph,
seed profiles/stacks/chat-templates) inline. `MOCK_ALLOWLIST` referenced the
builder functions directly, and `ui/src/api/client.ts:11` statically imports
`mockFetch` from `./mock` — so all of it shipped in every build regardless
of `FORCED`/`DEV`.

**After:** split into `ui/src/api/mockFixtures.ts` (builders + fixture
data, exported as `MOCK_BUILDERS: Record<string, Builder>`).
`ui/src/api/mock.ts`'s `MOCK_ALLOWLIST` now carries only `{re, key,
networkFirst}` (no function refs — cheap to construct eagerly for routing).
`buildMockPayload()` reaches the fixtures only via `await
import('./mockFixtures')`, called from the four call sites that already
decided a substitution is happening.

Combined with the (b) fix, this doesn't just code-split the fixtures — in a
`vite build` production build, all four call sites collapse to
compile-time-`false` conditions, so Rollup/esbuild dead-code-eliminate the
`import('./mockFixtures')` calls entirely. Verified directly: built
`npx vite build` and grepped the output bundle (`dist/assets/index-*.js`)
for fixture-exclusive string literals (`CyberPower`, `iGPU touched`,
`dir-disk-guard`, `mm-power-resilience`, `op-ing-9012`, `ryzenadj`) — **zero
occurrences**. The fixture module ships in zero bytes of the production
bundle.

## Test status

No vitest unit-test runner existed in `ui/` before this change (only
Playwright e2e). Added a minimal one: `ui/vitest.config.ts` (node
environment), `vitest` devDependency, `npm run test:unit` script, and
`ui/src/api/mock.test.ts` (3 tests: non-GET not substituted on network
error, non-GET not substituted on 404, GET fallback still works). All pass.

## Verify-gate results (run from `ui/`)

- `npx tsc --noEmit` — 0 errors.
- `npx eslint .` — 0 errors (note: this repo's `eslint.config.js` only has a
  rule block for `src/dash/**/*.jsx`; `src/api/*.ts` isn't covered by any
  ruleset, pre-existing and unrelated to this change).
- `npx vitest run` — 3/3 pass.
- `npx vite build` — succeeds; fixture strings confirmed absent from output
  (see above).
