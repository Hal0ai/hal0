// Regression test for docs/rework/r5-sync-assessment-2026-07-19.md §1.1
// ("Gate the mock fallback out of production"): `mockFetch`'s 404/network
// substitution must be GET-only for every allowlisted row, and the
// 404/network-error fallback must never fire in a plain production build.
//
// Before the fix, non-`networkFirst` allowlist rows (e.g. the POST-only
// `/api/memory/banks/:bank/recall` and `/api/memory/banks/:bank/reflect`
// routes) computed `substitutable` without checking the HTTP method, so a
// network-erroring or 404-ing POST could come back as a synthesized 200
// fixture — "succeeded against thin air."
import { afterEach, describe, expect, it, vi } from 'vitest'

const RECALL_URL = '/api/memory/banks/primary/recall'

describe('mockFetch — method gating (r5 §1.1 mock-prod-gate)', () => {
  const originalFetch = globalThis.fetch

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.resetModules()
  })

  it('never substitutes a fixture for a non-GET request on network error', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError('network error'))
    const { mockFetch } = await import('./mock')

    await expect(
      mockFetch(RECALL_URL, { method: 'POST', body: JSON.stringify({ query: 'ups' }) }),
    ).rejects.toThrow('network error')
  })

  it('never substitutes a fixture for a non-GET request on a 404', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 }))
    const { mockFetch } = await import('./mock')

    const res = await mockFetch(RECALL_URL, { method: 'POST' })

    // Must be the real pass-through 404, never a synthesized 200 fixture
    // body (`buildBankRecall`'s `{ results: [...] }` shape).
    expect(res.status).toBe(404)
    const body = await res.text()
    expect(body).not.toContain('"results"')
  })

  it('still allows the GET fallback for the same allowlisted route', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError('network error'))
    const { mockFetch } = await import('./mock')

    // GET substitution is dev/preview/forced-mock only (never plain
    // production); vitest runs with `import.meta.env.DEV === true`, so the
    // fallback is expected to be reachable here.
    const res = await mockFetch(RECALL_URL, { method: 'GET' })
    expect(res.status).toBe(200)
    const payload = await res.json()
    expect(payload).toHaveProperty('results')
  })
})

// ─── Passthrough escape hatch (#1498 / #1527) ──────────────────────
//
// Forced-mock guaranteed SUCCESS for every allowlisted GET by two separate
// mechanisms: a plain row is substituted BEFORE any fetch (so a Playwright
// `page.route` override never sees the request at all — 24 of 30 rows), and a
// `networkFirst` row's non-ok response is replaced with the baked payload (the
// other 6). An error response was therefore unrepresentable, and every UI error
// branch behind an allowlisted route was untestable BY CONSTRUCTION — a working
// error path and a broken one looked identical to the suite. #1471 and #1467
// each hit this independently and each shipped an error branch uncovered.
//
// `window.__hal0MockPassthrough` opts named paths out of every substitution
// branch. These tests pin the mechanism; the specs that depend on it
// (memory-graph-error-v3 and the capability pickers) assert the behaviour it
// unlocks.

describe('mockFetch — __hal0MockPassthrough (#1498 / #1527)', () => {
  const originalFetch = globalThis.fetch
  const g = globalThis as unknown as { __hal0MockPassthrough?: unknown }

  afterEach(() => {
    globalThis.fetch = originalFetch
    delete g.__hal0MockPassthrough
    vi.resetModules()
  })

  // A plain (pre-fetch substituted) allowlisted GET.
  const GRAPH_URL = '/api/memory/banks/shared/graph'
  // A `networkFirst` row — reaches the network, non-ok normally swallowed.
  const CAPS_URL = '/api/capabilities'

  // NOTE ON ENVIRONMENT: vitest runs with `import.meta.env.DEV === true` and
  // FORCED === false, so only the 404 / network-error fallback branches are
  // reachable here — the pre-fetch substitution needs VITE_MOCK_HAL0=1. That
  // branch is covered end-to-end by memory-graph-error-v3.spec.ts, which runs
  // under the forced-mock build. These tests pin the branches this environment
  // can actually reach, rather than asserting a FORCED behaviour it cannot.
  it('substitutes a plain row on 404 when no passthrough is declared', async () => {
    // The status quo every non-opted-in caller relies on — asserted so a change
    // to the default is a deliberate act, not a silent one.
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 }))
    const { mockFetch } = await import('./mock')

    const res = await mockFetch(GRAPH_URL, { method: 'GET' })
    expect(res.status).toBe(200)
  })

  it('lets a claimed plain row surface its real failure', async () => {
    // 404 rather than 503 on purpose: in this environment a 503 passes through
    // anyway, so it would prove nothing. 404 IS substituted by default (the
    // test above), which makes this a real before/after pair.
    g.__hal0MockPassthrough = ['/api/memory/banks/']
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 }))
    const { mockFetch } = await import('./mock')

    const res = await mockFetch(GRAPH_URL, { method: 'GET' })
    expect(res.status).toBe(404)
  })

  it('lets a claimed row surface a 404 instead of the fixture fallback', async () => {
    // 404 has its own substitution branch — one flag must cover them all, or
    // the hatch leaks through whichever branch this comment forgets.
    g.__hal0MockPassthrough = ['/api/memory/banks/']
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 }))
    const { mockFetch } = await import('./mock')

    const res = await mockFetch(GRAPH_URL, { method: 'GET' })
    expect(res.status).toBe(404)
  })

  it('lets a claimed row surface a network-level error', async () => {
    g.__hal0MockPassthrough = ['/api/memory/banks/']
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError('network error'))
    const { mockFetch } = await import('./mock')

    await expect(mockFetch(GRAPH_URL, { method: 'GET' })).rejects.toThrow('network error')
  })

  it('lets a claimed networkFirst row keep its non-ok status', async () => {
    // The `FORCED && networkFirst && !res.ok` rescue needs the forced-mock
    // build; what IS reachable here is the shared 404 fallback, which this row
    // is equally subject to.
    g.__hal0MockPassthrough = [CAPS_URL]
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 }))
    const { mockFetch } = await import('./mock')

    const res = await mockFetch(CAPS_URL, { method: 'GET' })
    expect(res.status).toBe(404)
  })

  it('is scoped — an unclaimed path keeps its mock', async () => {
    // The hatch must not degrade into a blunt "disable mocking" switch, or
    // every opted-in spec would have to re-stub the entire surface.
    g.__hal0MockPassthrough = ['/api/memory/banks/']
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 }))
    const { mockFetch } = await import('./mock')

    const res = await mockFetch('/api/status', { method: 'GET' })
    expect(res.status).toBe(200)
  })

  it('accepts a RegExp entry as well as a string prefix', async () => {
    g.__hal0MockPassthrough = [/\/api\/memory\/banks\/[^/]+\/graph/]
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 }))
    const { mockFetch } = await import('./mock')

    const res = await mockFetch(GRAPH_URL, { method: 'GET' })
    expect(res.status).toBe(404)
  })

  it('ignores a malformed value rather than throwing mid-request', async () => {
    // A spec typo must not take down every request on the page.
    g.__hal0MockPassthrough = 12345
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 }))
    const { mockFetch } = await import('./mock')

    const res = await mockFetch(GRAPH_URL, { method: 'GET' })
    expect(res.status).toBe(200)
  })

  it('accepts a bare string as shorthand for a one-entry list', async () => {
    g.__hal0MockPassthrough = '/api/memory/banks/'
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 }))
    const { mockFetch } = await import('./mock')

    const res = await mockFetch(GRAPH_URL, { method: 'GET' })
    expect(res.status).toBe(404)
  })
})
