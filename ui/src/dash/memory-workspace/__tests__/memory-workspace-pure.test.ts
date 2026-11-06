import { describe, expect, it } from 'vitest'
import {
  fmtAge,
  installCommandFor,
  laneHealthTone,
  normalizeLlmOpStats,
  oldestPendingAge,
  operationsByStatus,
  operationStatusCounts,
  pageIsStale,
  sortBanksByPressure,
  totalQueueDepth,
} from '../memory-workspace-pure'

describe('sortBanksByPressure', () => {
  it('sorts by pending desc, then oldest age desc, then bank_id asc', () => {
    const banks = [
      { bank_id: 'b', pending: 1, processing: 0, oldest_pending_age_s: 10 },
      { bank_id: 'a', pending: 5, processing: 0, oldest_pending_age_s: 1 },
      { bank_id: 'c', pending: 5, processing: 0, oldest_pending_age_s: 9 },
      { bank_id: 'd', pending: 0, processing: 0, oldest_pending_age_s: null },
    ]
    expect(sortBanksByPressure(banks).map((b) => b.bank_id)).toEqual(['c', 'a', 'b', 'd'])
  })
})

describe('totalQueueDepth / oldestPendingAge', () => {
  it('sums pending+processing and takes the max age', () => {
    const banks = [
      { bank_id: 'a', pending: 2, processing: 1, oldest_pending_age_s: 5 },
      { bank_id: 'b', pending: 0, processing: 3, oldest_pending_age_s: null },
    ]
    expect(totalQueueDepth(banks)).toBe(6)
    expect(oldestPendingAge(banks)).toBe(5)
  })

  it('handles empty/undefined', () => {
    expect(totalQueueDepth(undefined)).toBe(0)
    expect(totalQueueDepth([])).toBe(0)
    expect(oldestPendingAge([])).toBeNull()
    expect(oldestPendingAge(undefined)).toBeNull()
  })
})

describe('fmtAge', () => {
  it('formats seconds/minutes/hours and handles bad input', () => {
    expect(fmtAge(5)).toBe('5s')
    expect(fmtAge(125)).toBe('2m')
    expect(fmtAge(7200)).toBe('2h')
    expect(fmtAge(null)).toBe('—')
    expect(fmtAge(undefined)).toBe('—')
    expect(fmtAge(-1)).toBe('—')
    expect(fmtAge(NaN)).toBe('—')
  })
})

describe('laneHealthTone', () => {
  it('maps health strings to tones', () => {
    expect(laneHealthTone('ok')).toBe('ok')
    expect(laneHealthTone('Healthy')).toBe('ok')
    expect(laneHealthTone('degraded')).toBe('warn')
    expect(laneHealthTone('down')).toBe('bad')
    expect(laneHealthTone('weird-status')).toBe('unknown')
    expect(laneHealthTone(undefined)).toBe('unknown')
  })
})

describe('normalizeLlmOpStats', () => {
  it('normalizes an array shape', () => {
    const raw = { by_operation: [{ operation: 'recall', count: 3 }, { operation: 'refresh', count: 10 }] }
    expect(normalizeLlmOpStats(raw)).toEqual([
      { operation: 'refresh', count: 10 },
      { operation: 'recall', count: 3 },
    ])
  })

  it('normalizes an object-map shape with nested counts', () => {
    const raw = { recall: 2, refresh: { count: 7 } }
    expect(normalizeLlmOpStats(raw)).toEqual([
      { operation: 'refresh', count: 7 },
      { operation: 'recall', count: 2 },
    ])
  })

  it('handles garbage input', () => {
    expect(normalizeLlmOpStats(null)).toEqual([])
    expect(normalizeLlmOpStats(42)).toEqual([])
  })
})

describe('pageIsStale', () => {
  it('reflects the explicit flag only', () => {
    expect(pageIsStale({ is_stale: true })).toBe(true)
    expect(pageIsStale({ is_stale: false })).toBe(false)
    expect(pageIsStale(null)).toBe(false)
    expect(pageIsStale(undefined)).toBe(false)
  })
})

describe('operations filtering', () => {
  const ops = [
    { id: '1', status: 'pending' },
    { id: '2', status: 'failed' },
    { id: '3', status: 'pending' },
  ]
  it('filters by status or passes through for all', () => {
    expect(operationsByStatus(ops, 'all')).toHaveLength(3)
    expect(operationsByStatus(ops, 'pending')).toHaveLength(2)
    expect(operationsByStatus(ops, 'failed')).toHaveLength(1)
  })
  it('counts by status', () => {
    expect(operationStatusCounts(ops)).toEqual({ pending: 2, failed: 1 })
  })
})

describe('installCommandFor', () => {
  it('substitutes the harness placeholder', () => {
    const tmpl = 'npx @vectorize-io/hindsight-coding-agents@0.8.5 install <harness> --server self-hosted --api-url x'
    expect(installCommandFor(tmpl, 'claude-code')).toContain('install claude-code --server')
  })
})
