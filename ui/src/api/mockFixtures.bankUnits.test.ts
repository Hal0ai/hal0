// Memory v2 — `buildBankUnits` / `buildBankTags` / `buildUnitHistory` mock
// builder unit tests. These are the pure filter/sort/paginate bits called
// out in the task brief for TDD coverage (`ui/src/api/mock.ts`'s
// `mockFetch` is GET-only substitution, exercised separately in
// `mock.test.ts`; here we call the registered builders directly, the same
// way `buildMockPayload` does).
import { describe, expect, it } from 'vitest'

import { MOCK_BUILDERS } from './mockFixtures'

const BANK_RE = /^\/api\/memory\/banks\/([^/]+)\/units$/
const HISTORY_RE = /^\/api\/memory\/banks\/([^/]+)\/memories\/([^/]+)\/history$/

function unitsFor(bank: string, query = '') {
  const path = `/api/memory/banks/${bank}/units`
  const match = path.match(BANK_RE) as RegExpMatchArray
  const url = query ? `${path}?${query}` : path
  return MOCK_BUILDERS.bankUnits(url, match) as {
    items: Array<Record<string, unknown>>
    total_matched: number
    next_offset: number | null
  }
}

describe('buildBankUnits (mock fixture)', () => {
  it('returns a deterministic ~26-fact set for the primary bank', () => {
    const page = unitsFor('primary')
    expect(page.total_matched).toBeGreaterThanOrEqual(24)
    expect(page.total_matched).toBeLessThanOrEqual(30)
    expect(page.items.length).toBeGreaterThan(0)
  })

  it('every row carries salience and link_counts_by_type', () => {
    const page = unitsFor('primary')
    for (const row of page.items) {
      expect(typeof row.salience).toBe('number')
      expect(row.link_counts_by_type).toBeTypeOf('object')
    }
  })

  it('filters by fact_type', () => {
    const page = unitsFor('primary', 'type=observation')
    expect(page.items.length).toBeGreaterThan(0)
    expect(page.items.every((r) => r.fact_type === 'observation')).toBe(true)
  })

  it('filters by tags (topic)', () => {
    const page = unitsFor('primary', 'tags=performance')
    expect(page.items.length).toBeGreaterThan(0)
    expect(page.items.every((r) => (r.tags as string[]).includes('performance'))).toBe(true)
  })

  it('filters by q against text/context, case-insensitively', () => {
    const page = unitsFor('primary', 'q=UNDERVOLT')
    expect(page.items.length).toBeGreaterThan(0)
    expect(
      page.items.every(
        (r) =>
          String(r.text).toLowerCase().includes('undervolt') ||
          String(r.context).toLowerCase().includes('undervolt'),
      ),
    ).toBe(true)
  })

  it('filters by documentId', () => {
    const page = unitsFor('primary', 'document_id=doc-thermal-notes')
    expect(page.items.length).toBeGreaterThan(0)
    expect(page.items.every((r) => r.document_id === 'doc-thermal-notes')).toBe(true)
  })

  it('filters by from/to occurred window and intersects the timeseries window non-trivially', () => {
    // buildBankTimeseries emits 21 daily buckets ending 2026-06-12 — assert
    // the fixture has units landing inside that same window.
    const page = unitsFor('primary', 'from=2026-05-23T00:00:00.000Z&to=2026-06-12T23:59:59.000Z')
    expect(page.items.length).toBeGreaterThan(0)
  })

  it('sorts by recency (default) descending', () => {
    const page = unitsFor('primary', 'limit=100')
    const dates = page.items.map((r) => new Date(r.occurred_start as string).getTime())
    for (let i = 1; i < dates.length; i++) expect(dates[i]).toBeLessThanOrEqual(dates[i - 1])
  })

  it('sorts by salience descending when requested', () => {
    const page = unitsFor('primary', 'sort=salience&limit=100')
    const saliences = page.items.map((r) => r.salience as number)
    for (let i = 1; i < saliences.length; i++) expect(saliences[i]).toBeLessThanOrEqual(saliences[i - 1])
  })

  it('paginates with limit/offset and a correct next_offset', () => {
    const first = unitsFor('primary', 'limit=5&offset=0')
    expect(first.items).toHaveLength(5)
    expect(first.next_offset).toBe(5)

    const second = unitsFor('primary', `limit=5&offset=${first.next_offset}`)
    expect(second.items).toHaveLength(5)
    expect(second.items[0].id).not.toBe(first.items[0].id)
  })

  it('next_offset is null on the last page', () => {
    const page = unitsFor('primary', 'limit=100&offset=0')
    expect(page.next_offset).toBeNull()
  })

  it('the empty bank has no units', () => {
    const page = unitsFor('empty')
    expect(page.items).toHaveLength(0)
    expect(page.total_matched).toBe(0)
    expect(page.next_offset).toBeNull()
  })
})

describe('buildBankTags (mock fixture)', () => {
  it('returns tag/count rows shaped {tag, count}', () => {
    const path = '/api/memory/banks/primary/tags'
    const match = path.match(/^\/api\/memory\/banks\/([^/]+)\/tags$/) as RegExpMatchArray
    const page = MOCK_BUILDERS.bankTags(path, match) as { items: Array<{ tag: string; count: number }> }
    expect(page.items.length).toBeGreaterThan(0)
    for (const row of page.items) {
      expect(typeof row.tag).toBe('string')
      expect(typeof row.count).toBe('number')
    }
  })
})

describe('buildUnitHistory (mock fixture)', () => {
  it('returns a passthrough history dict for a known unit id', () => {
    const path = '/api/memory/banks/primary/memories/f1/history'
    const match = path.match(HISTORY_RE) as RegExpMatchArray
    const history = MOCK_BUILDERS.unitHistory(path, match) as Record<string, unknown>
    expect(history.unit_id).toBe('f1')
    expect(Array.isArray(history.events)).toBe(true)
  })
})
