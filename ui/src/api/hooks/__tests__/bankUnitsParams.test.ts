// Memory v2 — query-string serialization for `useBankUnits(bank, params)`.
// Pure-function unit test per the task brief: `useHindsight.ts` hooks have no
// existing unit-test coverage, so this exercises the exported serializer
// directly rather than mounting the hook.
import { describe, expect, it } from 'vitest'

import { serializeBankUnitsParams } from '../useHindsight'

describe('serializeBankUnitsParams', () => {
  it('returns an empty string for no params', () => {
    expect(serializeBankUnitsParams()).toBe('')
    expect(serializeBankUnitsParams({})).toBe('')
  })

  it('omits undefined/empty fields', () => {
    expect(serializeBankUnitsParams({ q: '', tags: [], limit: undefined })).toBe('')
  })

  it('serializes q, type, from, to, documentId, sort', () => {
    const qs = serializeBankUnitsParams({
      q: 'undervolt',
      type: 'observation',
      from: '2026-05-01T00:00:00.000Z',
      to: '2026-06-01T00:00:00.000Z',
      documentId: 'doc-thermal-notes',
      sort: 'salience',
    })
    const params = new URLSearchParams(qs.replace(/^\?/, ''))
    expect(params.get('q')).toBe('undervolt')
    expect(params.get('type')).toBe('observation')
    expect(params.get('from')).toBe('2026-05-01T00:00:00.000Z')
    expect(params.get('to')).toBe('2026-06-01T00:00:00.000Z')
    // camelCase hook param -> snake_case query param, matching the backend.
    expect(params.get('document_id')).toBe('doc-thermal-notes')
    expect(params.get('sort')).toBe('salience')
  })

  it('joins tags with commas', () => {
    const qs = serializeBankUnitsParams({ tags: ['thermal', 'performance'] })
    expect(qs).toBe('?tags=thermal%2Cperformance')
  })

  it('serializes limit/offset as numbers', () => {
    const qs = serializeBankUnitsParams({ limit: 20, offset: 40 })
    const params = new URLSearchParams(qs.replace(/^\?/, ''))
    expect(params.get('limit')).toBe('20')
    expect(params.get('offset')).toBe('40')
  })

  it('keeps offset=0 (falsy but meaningful)', () => {
    const qs = serializeBankUnitsParams({ offset: 0, limit: 10 })
    const params = new URLSearchParams(qs.replace(/^\?/, ''))
    expect(params.get('offset')).toBe('0')
  })

  it('starts with a leading "?" when non-empty', () => {
    expect(serializeBankUnitsParams({ q: 'x' }).startsWith('?')).toBe(true)
  })
})
