// Memory v2 — `useUnitHistory` response normalization. Upstream
// (hindsight-api 0.8.4) returns a bare JSON array of history events (not a
// `{events:[...]}` dict) and 404s for non-observation facts (it only
// tracks history for observation-type facts). These are the two pure bits
// pulled out of the hook so they're unit-testable without mounting it.
import { describe, expect, it } from 'vitest'

import { Hal0Error } from '../../client'
import { normalizeUnitHistory, unitHistoryOrEmptyOn404 } from '../useHindsight'

describe('normalizeUnitHistory', () => {
  it('wraps a bare array (the real upstream shape)', () => {
    const events = [{ state: 'valid', at: '2026-05-25T09:00:00.000Z', reason: null }]
    expect(normalizeUnitHistory(events)).toEqual({ events })
  })

  it('passes through a dict with an events array', () => {
    const events = [{ state: 'invalidated', at: '2026-06-01T00:00:00.000Z', reason: 'stale' }]
    expect(normalizeUnitHistory({ events, unit_id: 'f1' })).toEqual({ events })
  })

  it('falls back to an empty events array for a dict with no events key', () => {
    expect(normalizeUnitHistory({ unit_id: 'f1' })).toEqual({ events: [] })
  })

  it('falls back to an empty events array for null/undefined', () => {
    expect(normalizeUnitHistory(null)).toEqual({ events: [] })
    expect(normalizeUnitHistory(undefined)).toEqual({ events: [] })
  })

  it('falls back to an empty events array for any other shape', () => {
    expect(normalizeUnitHistory('nope')).toEqual({ events: [] })
    expect(normalizeUnitHistory(42)).toEqual({ events: [] })
  })
})

describe('unitHistoryOrEmptyOn404', () => {
  it('resolves a 404 Hal0Error to an empty history, not an error', () => {
    const err = new Hal0Error('not found', { status: 404, code: 'memory.engine_error' })
    expect(unitHistoryOrEmptyOn404(err)).toEqual({ events: [] })
  })

  it('rethrows a non-404 Hal0Error', () => {
    const err = new Hal0Error('unavailable', { status: 503, code: 'memory.unavailable' })
    expect(() => unitHistoryOrEmptyOn404(err)).toThrow(err)
  })

  it('rethrows a non-Hal0Error', () => {
    const err = new TypeError('network error')
    expect(() => unitHistoryOrEmptyOn404(err)).toThrow(err)
  })
})
