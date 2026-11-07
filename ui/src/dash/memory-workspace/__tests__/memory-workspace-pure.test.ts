import { describe, expect, it } from 'vitest'
import {
  fmtAge,
  installCommandFor,
  laneHealthTone,
  llmCallsByOperation,
  llmOpStatsPath,
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

describe('llmOpStatsPath', () => {
  it('targets the bank-scoped upstream route with split_by=operation', () => {
    expect(llmOpStatsPath('shared')).toBe(
      'v1/default/banks/shared/llm-requests/stats?split_by=operation',
    )
  })

  it('encodes the bank id', () => {
    expect(llmOpStatsPath('team a/b')).toBe(
      'v1/default/banks/team%20a%2Fb/llm-requests/stats?split_by=operation',
    )
  })
})

describe('llmCallsByOperation', () => {
  it('sums the per-bucket series into one row per operation, calls desc', () => {
    const raw = {
      bank_id: 'shared',
      period: '7d',
      trunc: 'day',
      start: '2026-11-01T00:00:00',
      buckets: [
        { time: '2026-11-01T00:00:00', series: { retain: { calls: 2, tokens: 30 }, reflect: { calls: 1, tokens: 5 } } },
        { time: '2026-11-02T00:00:00', series: { retain: { calls: 3, tokens: 40 } } },
      ],
      retention_days: 7,
    }
    expect(llmCallsByOperation(raw)).toEqual([
      { operation: 'retain', calls: 5 },
      { operation: 'reflect', calls: 1 },
    ])
  })

  it('breaks ties by operation name so the row order is stable', () => {
    const raw = { buckets: [{ series: { reflect: { calls: 2 }, retain: { calls: 2 } } }] }
    expect(llmCallsByOperation(raw)).toEqual([
      { operation: 'reflect', calls: 2 },
      { operation: 'retain', calls: 2 },
    ])
  })

  it('returns no rows when the engine sent no series (split_by not honoured)', () => {
    expect(llmCallsByOperation({ buckets: [{ time: 'x', series: null, total: 3 }] })).toEqual([])
  })

  it('handles garbage input', () => {
    expect(llmCallsByOperation(null)).toEqual([])
    expect(llmCallsByOperation(42)).toEqual([])
    expect(llmCallsByOperation({})).toEqual([])
    expect(llmCallsByOperation({ buckets: 'nope' })).toEqual([])
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
