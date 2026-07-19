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
