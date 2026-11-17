// The forced mock's upstream routes: the preset catalogue and one upstream's model rows are
// allow-listed GETs; the probe, per-model PATCH and refresh answers are exported fixtures that
// `mockFetch` never substitutes. Field names follow src/hal0/api/routes/providers.py.
import { describe, expect, it } from 'vitest'

import { MOCK_ALLOWLIST, mockFetch } from './mock'
import { MOCK_BUILDERS, MOCK_MUTATION_FIXTURES } from './mockFixtures'

function serve(url: string): unknown {
  for (const row of MOCK_ALLOWLIST) {
    const match = url.match(row.re)
    if (match) return MOCK_BUILDERS[row.key](url, match)
  }
  throw new Error(`not allow-listed: ${url}`)
}

const ROW_KEYS = [
  'canonical_id',
  'context',
  'discovered',
  'enabled',
  'id',
  'in_use_by',
  'price_in',
  'price_out',
  'price_unit',
  'recommended',
  'type',
]
const LISTING_KEYS = ['curated', 'fetched_at', 'models', 'name', 'protected', 'stale', 'warning']

describe('mock upstream catalogue', () => {
  const catalog = serve('/api/providers/catalog') as Record<string, Record<string, unknown>>

  it('carries every preset with the richer fields', () => {
    expect(Object.keys(catalog)).toHaveLength(11)
    for (const [id, entry] of Object.entries(catalog)) {
      expect(entry.id).toBe(id)
      expect(entry).toHaveProperty('tools')
      expect(Array.isArray(entry.quirks)).toBe(true)
      expect(typeof entry.rate_limit_headers).toBe('object')
      expect(Array.isArray(entry.models)).toBe(true)
    }
    expect(catalog.hal0_peer.category).toBe('peer')
  })
})

describe('mock upstream model listing', () => {
  it('answers a curated upstream with the route fields', () => {
    const listing = serve('/api/upstreams/openrouter/models') as Record<string, any>
    expect(Object.keys(listing).sort()).toEqual(LISTING_KEYS)
    expect(listing.curated).toBe(true)
    for (const row of listing.models) expect(Object.keys(row).sort()).toEqual(ROW_KEYS)
    expect(listing.models[0].canonical_id).toBe('openrouter/qwen/qwen3-coder')
  })

  it('answers an unconverted upstream as stale and not curated', () => {
    const listing = serve('/api/upstreams/corp/models') as Record<string, any>
    expect(listing.curated).toBe(false)
    expect(listing.stale).toBe(true)
    expect(listing.fetched_at).toBeNull()
  })

  it('has no listing for an unknown upstream', () => {
    expect(serve('/api/upstreams/ghost/models')).toBeNull()
  })

  it('is a GET-only substitution: the mutation routes are not allow-listed', () => {
    const paths = [
      '/api/providers/probe',
      '/api/upstreams/openrouter/refresh',
      '/api/upstreams/openrouter/models/qwen/qwen3-coder',
    ]
    for (const path of paths) {
      expect(MOCK_ALLOWLIST.some((row) => row.re.test(path))).toBe(false)
    }
  })

  it('never substitutes a mutation, even on the listing path', async () => {
    const original = globalThis.fetch
    globalThis.fetch = (async () => new Response(null, { status: 404 })) as typeof fetch
    try {
      const res = await mockFetch('/api/upstreams/openrouter/models', { method: 'PATCH' })
      expect(res.status).toBe(404)
    } finally {
      globalThis.fetch = original
    }
  })
})

describe('mock upstream mutation fixtures', () => {
  it('patches one row, keeping the id the caller sent', () => {
    const answer = MOCK_MUTATION_FIXTURES.upstreamModelPatch('openrouter', 'qwen/qwen3-coder', {
      enabled: false,
    })
    expect(answer.status).toBe(200)
    const row = answer.body as Record<string, any>
    expect(Object.keys(row).sort()).toEqual(ROW_KEYS)
    expect(row.id).toBe('qwen/qwen3-coder')
    expect(row.enabled).toBe(false)
  })

  it('seeds a row for a model only discovery lists', () => {
    const row = MOCK_MUTATION_FIXTURES.upstreamModelPatch('openrouter', 'new/model', {
      enabled: true,
    }).body as Record<string, any>
    expect(row.canonical_id).toBe('openrouter/new/model')
    expect(row.enabled).toBe(true)
  })

  it('answers a patch on an unconverted upstream with the 409 error envelope', () => {
    const answer = MOCK_MUTATION_FIXTURES.upstreamModelPatch('corp', 'corp-embed', { enabled: false })
    expect(answer.status).toBe(409)
    expect(answer.body).toEqual({
      error: {
        code: 'upstream.not_curated',
        message: expect.stringContaining('not converted yet'),
        details: { name: 'corp' },
      },
    })
  })

  it('refreshes a curated row and warns on an unconverted one', () => {
    const curated = MOCK_MUTATION_FIXTURES.upstreamRefresh('openrouter')!
    expect(curated.warning).toBeNull()
    expect(curated.fetched_at).toBeGreaterThan(1763337600)
    const pending = MOCK_MUTATION_FIXTURES.upstreamRefresh('corp')!
    expect(pending.curated).toBe(false)
    expect(pending.warning).toMatch(/returned no models/)
    expect(MOCK_MUTATION_FIXTURES.upstreamRefresh('ghost')).toBeNull()
  })

  it('probes ok and failing, with a null canonical id and no key anywhere', () => {
    const ok = MOCK_MUTATION_FIXTURES.providerProbe(true)
    expect(Object.keys(ok).sort()).toEqual(
      ['error', 'latency_ms', 'models', 'ok', 'rate_limit', 'status'],
    )
    expect(ok.models.every((m) => m.canonical_id === null)).toBe(true)
    expect(Object.keys(ok.rate_limit).sort()).toEqual([
      'limit_requests',
      'limit_tokens',
      'remaining_requests',
      'remaining_tokens',
    ])
    const bad = MOCK_MUTATION_FIXTURES.providerProbe(false)
    expect(bad.ok).toBe(false)
    expect(bad.models).toEqual([])
    expect(JSON.stringify(bad)).not.toMatch(/sk-/)
  })
})
