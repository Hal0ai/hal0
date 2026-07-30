// hal0 v3 dashboard — mock fetch harness (Phase B1).
//
// Two activation modes (mirrors ui-vue.bak/src/composables/useMock.js):
//   1. Forced mock: `VITE_MOCK_HAL0=1` at build/dev time. Every
//      allowlisted GET URL returns baked data from `HAL0_DATA` without
//      touching the network.
//   2. Per-endpoint fallback (dev/preview only — see `DEV` below): when a
//      live GET fails (404 / network error), allowlisted URLs swap in the
//      mock so the local dashboard never crashes on absent endpoints.
//      Real 2xx / 5xx pass through. This never runs in a plain production
//      build: `import.meta.env.DEV` is statically `false` there, and
//      `FORCED` requires the opt-in `VITE_MOCK_HAL0=1` build flag that
//      production builds never set.
//
// Both modes are GET-only. A mutating request (POST/PUT/DELETE/PATCH) is
// NEVER substituted — even on an allowlisted path, even in forced-mock —
// so a network-erroring "create" can't come back looking like a 200
// success against thin air. See `docs/rework/r5-sync-assessment-2026-07-19.md`
// §1.1 ("Gate the mock fallback out of production").
//
// Fixture data lives in `./mockFixtures.ts` and is loaded via a dynamic
// `import()` — only once `mockFetch` has already decided a substitution
// is actually happening. `MOCK_ALLOWLIST` below only carries route
// metadata (regex + key + networkFirst), so matching a URL never pulls
// the fixture payloads (the ~6-week Memory story, the 600-node synthetic
// graph, seed profiles/stacks/…) into the hot path or the production
// bundle.
//
// Ambient typing lives in `src/types/globals.d.ts` — no local
// `declare global` here (it would conflict on `HAL0_DATA` modifiers).

const FORCED = !!(import.meta.env && (import.meta.env as any).VITE_MOCK_HAL0 === '1')

// Vite statically replaces `import.meta.env.DEV` at build time (`true` for
// `vite`/`vite dev`, `false` for `vite build`), so in a production build the
// `FORCED || DEV` checks below collapse to `FORCED || false` — bundlers can
// dead-code-eliminate the fallback branches entirely when FORCED is also
// statically false (the normal case: no build script sets VITE_MOCK_HAL0).
const DEV = !!(import.meta.env && (import.meta.env as any).DEV)

export function isMockForced() {
  return FORCED
}

// Forced-mock warm-up: kick off the `./mockFixtures.ts` dynamic import once,
// eagerly, at module init instead of waiting for the first substituted GET
// to trigger it cold (see `buildMockPayload` below). In dev/e2e the dynamic
// import is a real dev-server module fetch, not a bundled function call —
// paying that round-trip lazily on e.g. the page's first `/api/slots`
// request pushes the response past React's initial commit, so a
// query-driven loading/populated branch switch (SlotsView) observably
// remounts children (ActivityLog) that a caller — or a Playwright locator
// that already resolved a "visible" element — was holding onto. Warming
// here collapses the fetch into app bootstrap so the first real call
// resolves against an already-settled (or already in-flight) promise.
// FORCED is only ever true under the opt-in `VITE_MOCK_HAL0=1` build flag
// (dev/e2e), so this never runs — and never pulls fixtures into — a plain
// production bundle.
let mockFixturesWarm: Promise<typeof import('./mockFixtures')> | null = FORCED
  ? import('./mockFixtures')
  : null

// ─── Allowlist (first match wins) ─────────────────────────────────
// Route metadata only — no builder function references, so this array is
// cheap to construct eagerly and never pulls `./mockFixtures.ts` (and its
// fixture payloads) into the module graph. `key` looks up the builder in
// `MOCK_BUILDERS` (./mockFixtures.ts) once a substitution is confirmed.
//
// `networkFirst` rows try the real network even in FORCED mock mode and only
// substitute the baked payload when the GET fails (network error / 404 / a
// dev-proxy 5xx). This keeps Playwright `page.route` overrides authoritative
// for these endpoints (the e2e suite runs with VITE_MOCK_HAL0=1 and drives
// /api/profiles, /api/stacks, /api/chat-templates via route fulfils), while
// unrouted dev/preview builds still get a plausible payload.
//
// ALL substitution — forced or fallback, networkFirst or not — is GET-only;
// mutations (POST/PUT/DELETE/PATCH) always pass through untouched.
type AllowRow = { re: RegExp; key: string; networkFirst?: boolean }

export const MOCK_ALLOWLIST: ReadonlyArray<AllowRow> = Object.freeze([
  { re: /^\/api\/status$/, key: 'status' },
  { re: /^\/api\/slots$/, key: 'slots' },
  { re: /^\/api\/slots\/[^/]+$/, key: 'slotDetail' }, // 404-style — Slot detail not in mock
  { re: /^\/api\/models$/, key: 'models' },
  { re: /^\/api\/models\/updates\/check$/, key: 'modelUpdatesCheck', networkFirst: true },
  { re: /^\/api\/backends$/, key: 'backends' },
  // networkFirst: the Voice/Image-gen settings pages need per-spec catalog
  // fixtures (real bare-array `catalogs.<slot>.<child>` rows — #1454), so
  // page.route overrides must stay authoritative here like the sibling
  // profiles/stacks/chat-templates rows below.
  { re: /^\/api\/capabilities$/, key: 'capabilities', networkFirst: true },
  { re: /^\/api\/hardware$/, key: 'hardware' },
  { re: /^\/api\/npu\/occupancy$/, key: 'npuOccupancy' },
  { re: /^\/api\/journal$/, key: 'journal' },
  { re: /^\/api\/updates\/state$/, key: 'updatesState' },
  { re: /^\/api\/secrets$/, key: 'secrets' },
  { re: /^\/api\/meta\/enums$/, key: 'metaEnums', networkFirst: true },
  { re: /^\/api\/profiles$/, key: 'profiles', networkFirst: true },
  { re: /^\/api\/stacks$/, key: 'stacks', networkFirst: true },
  { re: /^\/api\/chat-templates$/, key: 'chatTemplates', networkFirst: true },
  // ── Memory (Hindsight) — engine + bank-scoped surface ────────────
  // Forced-mock + 404-fallback story for the Memory graph overhaul. The
  // bank id is captured as group 1. ORDER MATTERS: the more-specific
  // sub-paths (entities/graph, stats/timeseries) sit before the broader
  // ones (graph, stats) since `matchAllowlist` returns first match.
  { re: /^\/api\/memory\/graph\/status$/, key: 'memoryGraphStatus' },
  { re: /^\/api\/memory\/engine$/, key: 'memoryEngine' },
  { re: /^\/api\/memory\/banks$/, key: 'memoryBanks' },
  { re: /^\/api\/memory\/banks\/([^/]+)\/stats\/timeseries$/, key: 'bankTimeseries' },
  { re: /^\/api\/memory\/banks\/([^/]+)\/stats$/, key: 'bankStats' },
  { re: /^\/api\/memory\/banks\/([^/]+)\/graph\/subgraph$/, key: 'bankSubgraph' },
  { re: /^\/api\/memory\/banks\/([^/]+)\/entities\/graph$/, key: 'bankEntityGraph' },
  { re: /^\/api\/memory\/banks\/([^/]+)\/graph$/, key: 'bankGraph' },
  { re: /^\/api\/memory\/banks\/([^/]+)\/documents$/, key: 'bankDocuments' },
  { re: /^\/api\/memory\/banks\/([^/]+)\/mental-models$/, key: 'bankMentalModels' },
  { re: /^\/api\/memory\/banks\/([^/]+)\/directives$/, key: 'bankDirectives' },
  { re: /^\/api\/memory\/banks\/([^/]+)\/operations$/, key: 'bankOperations' },
  // recall/reflect are POST-only on the real backend — they stay in the
  // allowlist so a network-erroring GET fallback path can never accidentally
  // apply to them via a future refactor, but the GET-only gate below is what
  // actually keeps their real POST traffic from ever being substituted.
  { re: /^\/api\/memory\/banks\/([^/]+)\/recall$/, key: 'bankRecall' },
  { re: /^\/api\/memory\/banks\/([^/]+)\/reflect$/, key: 'bankReflect' },
])

function parsePath(url: string | URL | Request): string | null {
  let s: string
  if (typeof url === 'string') s = url
  else if (url instanceof URL) s = url.pathname + url.search
  else {
    try {
      s = (url as Request).url
    } catch {
      return null
    }
  }
  if (s.startsWith('http')) {
    try {
      return new URL(s).pathname
    } catch {
      return null
    }
  }
  const q = s.indexOf('?')
  return q >= 0 ? s.slice(0, q) : s
}

// Like parsePath but keeps the query string so builders can read params
// (the allowlist matches on the stripped path, but some builders — recall,
// subgraph — need ?mode=/?top_k=/etc). Forced-mock short-circuits
// page.route, so this is the only place those params survive.
function pathWithSearch(url: string | URL | Request): string | null {
  let s: string
  if (typeof url === 'string') s = url
  else if (url instanceof URL) s = url.pathname + url.search
  else {
    try {
      s = (url as Request).url
    } catch {
      return null
    }
  }
  if (s.startsWith('http')) {
    try {
      const u = new URL(s)
      return u.pathname + u.search
    } catch {
      return null
    }
  }
  return s
}

function matchAllowlist(path: string) {
  for (const row of MOCK_ALLOWLIST) {
    const m = path.match(row.re)
    if (m) return { row, match: m }
  }
  return null
}

// ─── Per-path passthrough escape hatch (#1498 / #1527) ─────────────
//
// Forced-mock guarantees SUCCESS for every allowlisted GET, by two separate
// mechanisms in `mockFetch` below:
//
//   1. A plain (non-`networkFirst`) row is substituted BEFORE any fetch runs,
//      so no request is issued at all — a Playwright `page.route` override
//      never even sees the URL. 24 of the 30 allowlisted routes are this kind.
//   2. A `networkFirst` row does hit the network, but any non-ok response is
//      replaced with the baked payload. The remaining 6 are this kind.
//
// Either way an error response is unrepresentable, so every UI error branch
// behind an allowlisted route — "engine unreachable", "could not load
// capabilities", any `isError` render — is untestable BY CONSTRUCTION. That
// is not a gap in two specs; it is a structural blind spot across the whole
// class, and it means a working error path and a broken one look identical
// from the suite's point of view.
//
// This is the opt-out. A spec declares the paths it wants to drive itself:
//
//   await page.addInitScript(() => {
//     window.__hal0MockPassthrough = ['/api/memory/banks/']
//   })
//
// …and every substitution path below is skipped for matching requests, so the
// spec's own `page.route` fulfil (503, 500, network abort) reaches the app
// exactly as written. Accepts string prefixes or RegExps.
//
// Safe by construction outside tests: it can only ever DISABLE substitution,
// never enable it, and nothing sets the global in dev or production — where
// it is simply an undefined lookup per allowlisted GET.
function passthroughRequested(path: string): boolean {
  const raw = (globalThis as unknown as { __hal0MockPassthrough?: unknown })
    .__hal0MockPassthrough
  if (!raw) return false
  const list = Array.isArray(raw) ? raw : [raw]
  for (const entry of list) {
    if (typeof entry === 'string' && entry && path.startsWith(entry)) return true
    if (entry instanceof RegExp && entry.test(path)) return true
  }
  return false
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body ?? null), {
    status: body == null ? 404 : status,
    headers: { 'Content-Type': 'application/json' },
  })
}

// Resolves the fixture payload for an allowlist hit. Dynamically imports
// `./mockFixtures.ts` — the ONLY place that module is ever reached from —
// so its builder functions and fixture data (Memory story, FU2 synthetic
// graph, seed profiles/stacks/chat-templates…) are never pulled into the
// bundle unless a mock substitution is actually about to happen. Prefers
// the warmed-up promise above so a FORCED call never pays a cold fetch.
async function buildMockPayload(key: string, url: string, match: RegExpMatchArray): Promise<unknown> {
  const { MOCK_BUILDERS } = await (mockFixturesWarm ?? import('./mockFixtures'))
  const build = MOCK_BUILDERS[key]
  return build ? build(url, match) : null
}

/**
 * Drop-in `fetch` replacement. Forced-mock short-circuits any allowlisted
 * GET URL. Otherwise we let the real fetch run and only substitute on 404 /
 * network failure for allowlisted GET URLs, and only in dev/preview builds
 * (or forced-mock) — never in a plain production build.
 */
export async function mockFetch(
  url: string | URL | Request,
  options?: RequestInit,
): Promise<Response> {
  const path = parsePath(url)
  if (!path) return fetch(url as any, options)

  const method = String(
    options?.method ??
      (typeof Request !== 'undefined' && url instanceof Request ? url.method : 'GET'),
  ).toUpperCase()

  const hit = matchAllowlist(path)
  // builders receive the query-bearing path (subgraph/recall read params)
  const builderUrl = pathWithSearch(url) ?? path
  // Substitution — forced or fallback — is GET-only for every row, no
  // exceptions. A network-erroring or 404-ing POST/PUT/PATCH/DELETE always
  // surfaces as a real failure to the caller.
  // #1498 / #1527: a spec can claim a path and drive its own responses —
  // including failures, which forced-mock otherwise makes unrepresentable.
  // Gating `substitutable` covers the 404 and network-error fallbacks too, so
  // one flag opts a path out of EVERY substitution branch rather than leaving
  // a hole in whichever one this comment forgets to mention.
  const substitutable = !!hit && method === 'GET' && !passthroughRequested(path)
  // The 404/network-error fallback (mode 2) only runs in dev/preview or
  // forced-mock — never in a plain production build.
  const fallbackAllowed = FORCED || DEV

  if (FORCED && hit && substitutable && !hit.row.networkFirst) {
    return jsonResponse(await buildMockPayload(hit.row.key, builderUrl, hit.match))
  }

  let res: Response
  try {
    res = await fetch(url as any, options)
  } catch (e) {
    if (hit && substitutable && fallbackAllowed) {
      // network-level failure on a mocked GET — fall back (dev/preview or
      // forced-mock only; see `fallbackAllowed` above)
      return jsonResponse(await buildMockPayload(hit.row.key, builderUrl, hit.match))
    }
    throw e
  }
  if (hit && substitutable) {
    if (res.status === 404 && fallbackAllowed) {
      return jsonResponse(await buildMockPayload(hit.row.key, builderUrl, hit.match))
    }
    // Forced mock + networkFirst: an unrouted GET lands on the vite proxy
    // (ECONNREFUSED → 5xx). Serve the baked payload for any failed read so
    // dev/preview never renders an error shell, while a page.route-fulfilled
    // 2xx stays authoritative.
    if (FORCED && hit.row.networkFirst && !res.ok) {
      return jsonResponse(await buildMockPayload(hit.row.key, builderUrl, hit.match))
    }
  }
  return res
}
