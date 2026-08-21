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
    truncated: boolean
  }
}

describe('buildBankUnits (mock fixture)', () => {
  it('returns a deterministic ~26-fact set for the primary bank', () => {
    const page = unitsFor('primary')
    expect(page.total_matched).toBeGreaterThanOrEqual(24)
    expect(page.total_matched).toBeLessThanOrEqual(30)
    expect(page.items.length).toBeGreaterThan(0)
  })

  it('never reports truncated — the mock dataset is far under the server slab cap', () => {
    // PR #1987 review B2/M10: the real endpoint's `truncated` signals its
    // 2000-row upstream slab cap; the mock always returns false since it
    // never gets close to that.
    expect(unitsFor('primary').truncated).toBe(false)
  })

  it('falls back to recency for a sort value the server does not accept', () => {
    // PR #1987 review M10: the mock used to have its own 'oldest' sort the
    // server 422s on; it now only understands 'recency'/'salience' like the
    // real endpoint, defaulting anything else to 'recency' rather than
    // pretending to support a third mode.
    const recency = unitsFor('primary', 'sort=recency').items.map((r) => r.id)
    const unknown = unitsFor('primary', 'sort=oldest').items.map((r) => r.id)
    expect(unknown).toEqual(recency)
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

  it('filters by a comma-joined multi-type OR (task A3b contract, fix round)', () => {
    const page = unitsFor('primary', 'type=world,experience&limit=100')
    expect(page.items.length).toBeGreaterThan(0)
    expect(page.items.every((r) => r.fact_type === 'world' || r.fact_type === 'experience')).toBe(true)
    // total_matched must be the TRUTHFUL count for the OR'd set, not the
    // unfiltered total — this is exactly what fixes the "showing X-Y of N"
    // / pagination mismatch the C4 review found when 2 of 3 type toggles
    // are active.
    const singleTypeTotals =
      unitsFor('primary', 'type=world&limit=100').total_matched +
      unitsFor('primary', 'type=experience&limit=100').total_matched
    expect(page.total_matched).toBe(singleTypeTotals)
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

  it('excludes invalidated units by default (upstream archive behaviour)', () => {
    const page = unitsFor('primary', 'limit=100')
    expect(page.items.every((r) => r.state === 'valid')).toBe(true)
    expect(page.items.some((r) => r.id === 'f9' || r.id === 'f20')).toBe(false)
  })

  it('state=invalidated lists exactly the archived units', () => {
    const page = unitsFor('primary', 'state=invalidated&limit=100')
    expect(page.items.length).toBe(2)
    expect(page.items.every((r) => r.state === 'invalidated')).toBe(true)
    expect(page.items.map((r) => r.id).sort()).toEqual(['f20', 'f9'])
  })

  it('state=valid is an explicit no-op equivalent to the default', () => {
    const page = unitsFor('primary', 'state=valid&limit=100')
    expect(page.items.every((r) => r.state === 'valid')).toBe(true)
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
  function historyFor(id: string) {
    const path = `/api/memory/banks/primary/memories/${id}/history`
    const match = path.match(HISTORY_RE) as RegExpMatchArray
    return MOCK_BUILDERS.unitHistory(path, match)
  }

  it('returns a bare JSON array of events for an observation fact (matches upstream 0.8.4)', () => {
    // f6 — 'Prefers terse technical answers' — is fact_type: observation.
    const history = historyFor('f6')
    expect(Array.isArray(history)).toBe(true)
    expect((history as unknown[]).length).toBeGreaterThan(0)
  })

  it('returns null (mock.ts turns this into a 404) for a non-observation fact', () => {
    // f1 — 'Installed hal0 on Debian 13' — is fact_type: experience.
    expect(historyFor('f1')).toBeNull()
  })

  it('returns null (404) for an unknown unit id', () => {
    expect(historyFor('does-not-exist')).toBeNull()
  })
})
