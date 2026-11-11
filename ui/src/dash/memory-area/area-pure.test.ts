import { describe, expect, it } from 'vitest'
import type { BankStats, MemoryBank, MemoryEngine } from '@/api/hooks/useHindsight'
import { bankRows, bankTotals, engineState, metaLine, plural, type BankStatsEntry } from './area-pure'

const engine = (over: Partial<MemoryEngine> = {}): MemoryEngine => ({
  enabled: true,
  engine: 'hindsight',
  reachable: true,
  version: '1.2.0',
  features: null,
  banks_total: 2,
  ...over,
})

const stats = (bank: string, over: Partial<BankStats> = {}): BankStatsEntry => ({
  bankId: bank,
  stats: {
    bank_id: bank,
    total_nodes: 100,
    total_links: 0,
    total_documents: 10,
    nodes_by_fact_type: {},
    links_by_link_type: {},
    pending_operations: 0,
    failed_operations: 0,
    operations_by_status: {},
    last_consolidated_at: null,
    pending_consolidation: 0,
    failed_consolidation: 0,
    total_observations: 0,
    ...over,
  },
})

const bank = (id: string, over: Partial<MemoryBank> = {}): MemoryBank => ({ bank_id: id, ...over })

describe('engineState', () => {
  it('is loading until there is an answer', () => {
    expect(engineState({})).toBe('loading')
    expect(engineState({ data: undefined, isError: false })).toBe('loading')
  })

  it('is unreachable when the check failed or the engine did not answer it', () => {
    expect(engineState({ isError: true })).toBe('unreachable')
    expect(engineState({ data: engine({ reachable: false, version: null }) })).toBe('unreachable')
    // Memory not initialised at all: nothing answers.
    expect(engineState({ data: engine({ enabled: false, engine: null, reachable: false }) })).toBe('unreachable')
    // A body that is not an engine answer is not "reachable".
    expect(engineState({ data: {} as MemoryEngine })).toBe('unreachable')
  })

  it('is fallback when memory runs but not on Hindsight', () => {
    expect(engineState({ data: engine({ engine: null, reachable: false }) })).toBe('fallback')
  })

  it('is reachable when Hindsight answered', () => {
    expect(engineState({ data: engine() })).toBe('reachable')
  })

  it('keeps the last answer over a later error', () => {
    expect(engineState({ data: engine(), isError: true })).toBe('reachable')
  })
})

describe('bankRows', () => {
  it('reads each figure from the stats, and sorts the largest bank first', () => {
    const rows = bankRows(
      [bank('small'), bank('Hal0::Agents'), bank('mid')],
      [
        stats('small', { total_nodes: 12 }),
        stats('Hal0::Agents', { total_nodes: 24530, total_documents: 4100, pending_operations: 978, failed_operations: 105 }),
        stats('mid', { total_nodes: 300 }),
      ],
    )
    expect(rows.map((r) => r.id)).toEqual(['Hal0::Agents', 'mid', 'small'])
    expect(rows[0]).toEqual({
      id: 'Hal0::Agents',
      facts: 24530,
      documents: 4100,
      waiting: 978,
      failed: 105,
      reported: true,
    })
  })

  it('leaves a bank whose stats did not answer with no figure, never a zero', () => {
    const rows = bankRows([bank('a'), bank('b')], [stats('a'), { bankId: 'b', stats: null, isError: true }])
    expect(rows.find((r) => r.id === 'b')).toEqual({
      id: 'b',
      facts: null,
      documents: null,
      waiting: null,
      failed: null,
      reported: false,
    })
  })

  it('sorts a bank with an unknown fact count last, after an empty one', () => {
    const rows = bankRows(
      [bank('unknown'), bank('empty'), bank('full')],
      [{ bankId: 'unknown', stats: null }, stats('empty', { total_nodes: 0 }), stats('full', { total_nodes: 5 })],
    )
    expect(rows.map((r) => r.id)).toEqual(['full', 'empty', 'unknown'])
  })

  it("uses the bank list's own count when the stats did not answer", () => {
    const [row] = bankRows([bank('a', { fact_count: 42 })], [{ bankId: 'a', stats: null }])
    expect(row.facts).toBe(42)
    expect(row.reported).toBe(false)
  })

  it('keeps the engine order among banks of the same size', () => {
    const rows = bankRows([bank('b'), bank('a'), bank('c')], [stats('b'), stats('a'), stats('c')])
    expect(rows.map((r) => r.id)).toEqual(['b', 'a', 'c'])
  })

  it('reads a figure that is not a finite number as not reported', () => {
    const [row] = bankRows([bank('a')], [stats('a', { total_documents: Number.NaN, pending_operations: undefined as never })])
    expect(row.documents).toBeNull()
    expect(row.waiting).toBeNull()
  })
})

describe('bankTotals', () => {
  it('adds the banks up', () => {
    const rows = bankRows(
      [bank('a'), bank('b')],
      [
        stats('a', { total_nodes: 24530, pending_operations: 978, failed_operations: 100 }),
        stats('b', { total_nodes: 28425, pending_operations: 115, failed_operations: 5 }),
      ],
    )
    expect(bankTotals(rows)).toEqual({
      banks: 2,
      listed: 2,
      counted: 2,
      reported: 2,
      facts: 52955,
      waiting: 1093,
      failed: 105,
    })
  })

  it('sums only the banks that reported, and says how many that is', () => {
    const rows = bankRows([bank('a'), bank('b')], [stats('a', { total_nodes: 7, failed_operations: 2 }), { bankId: 'b', stats: null }])
    expect(bankTotals(rows)).toMatchObject({ banks: 2, counted: 1, reported: 1, facts: 7, failed: 2 })
  })

  it('has no sum at all when no bank reported', () => {
    const rows = bankRows([bank('a')], [{ bankId: 'a', stats: null }])
    expect(bankTotals(rows)).toMatchObject({ reported: 0, facts: null, waiting: null, failed: null })
  })

  it("takes the engine's own bank count when its list is paged", () => {
    const rows = bankRows([bank('a')], [stats('a')])
    expect(bankTotals(rows, 140)).toMatchObject({ banks: 140, listed: 1 })
    expect(bankTotals(rows, null)).toMatchObject({ banks: 1, listed: 1 })
  })

  it('is all zeros and nulls with no banks', () => {
    expect(bankTotals([])).toEqual({
      banks: 0,
      listed: 0,
      counted: 0,
      reported: 0,
      facts: null,
      waiting: null,
      failed: null,
    })
  })
})

describe('plural', () => {
  it('groups thousands and picks the noun', () => {
    expect(plural(1, 'bank', 'banks')).toBe('1 bank')
    expect(plural(21, 'bank', 'banks')).toBe('21 banks')
    expect(plural(52955, 'fact', 'facts')).toBe('52,955 facts')
    expect(plural(0, 'fact', 'facts')).toBe('0 facts')
  })
})

describe('metaLine', () => {
  const totals = (over = {}) => ({
    banks: 21,
    listed: 21,
    counted: 21,
    reported: 21,
    facts: 52955,
    waiting: 1093,
    failed: 105,
    ...over,
  })
  const input = (over = {}) => ({
    engine: 'reachable' as const,
    banksLoading: false,
    banksFailed: false,
    statsLoading: false,
    totals: totals(),
    ...over,
  })

  it('is a skeleton, with no number, while anything it counts is loading', () => {
    expect(metaLine(input({ engine: 'loading' }))).toEqual({ kind: 'loading' })
    expect(metaLine(input({ banksLoading: true }))).toEqual({ kind: 'loading' })
    expect(metaLine(input({ statsLoading: true }))).toEqual({ kind: 'loading' })
  })

  it('says the engine is unreachable, with no number', () => {
    const line = metaLine(input({ engine: 'unreachable', totals: totals() }))
    expect(line).toEqual({ kind: 'text', text: 'Engine unreachable · no counts available' })
    expect(JSON.stringify(line)).not.toMatch(/\d/)
  })

  it('says so when memory is not on Hindsight', () => {
    expect(metaLine(input({ engine: 'fallback' }))).toEqual({
      kind: 'text',
      text: 'Fallback engine · no counts available',
    })
  })

  it('counts the facts and the banks', () => {
    expect(metaLine(input())).toEqual({ kind: 'text', text: '52,955 facts in 21 banks' })
    expect(metaLine(input({ totals: totals({ banks: 1, listed: 1, counted: 1, facts: 1 }) }))).toEqual({
      kind: 'text',
      text: '1 fact in 1 bank',
    })
  })

  it('says how many banks a partial count covers', () => {
    expect(metaLine(input({ totals: totals({ counted: 18, facts: 52557 }) }))).toEqual({
      kind: 'text',
      text: '21 banks · 52,557 facts in the 18 that reported',
    })
  })

  it('gives no fact count when no bank reported one', () => {
    expect(metaLine(input({ totals: totals({ counted: 0, reported: 0, facts: null }) }))).toEqual({
      kind: 'text',
      text: '21 banks · fact counts not reported',
    })
  })

  it('says there are no banks, and that the bank list did not load', () => {
    expect(metaLine(input({ totals: totals({ banks: 0, listed: 0, counted: 0, facts: null }) }))).toEqual({
      kind: 'text',
      text: 'Engine reachable · no banks yet',
    })
    expect(metaLine(input({ banksFailed: true }))).toEqual({
      kind: 'text',
      text: 'Engine reachable · bank list not available',
    })
  })
})
