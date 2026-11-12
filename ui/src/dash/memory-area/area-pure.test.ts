import { describe, expect, it } from 'vitest'
import type { MemoryEngine } from '@/api/hooks/useHindsight'
import type { MemoryHealth, MemoryHealthReport } from '@/api/hooks/useMemory'
import { memoryHealthFixture } from '@/api/mockFixtures'
import {
  bankListMissing,
  bankTotals,
  engineFromHealth,
  engineState,
  healthBankRows,
  metaLine,
  plural,
  readHealth,
  type HealthView,
} from './area-pure'
import { healthReport, healthUnreachable } from './area-test-fixtures'

const engine = (over: Partial<MemoryEngine> = {}): MemoryEngine => ({
  enabled: true,
  engine: 'hindsight',
  reachable: true,
  version: '1.2.0',
  features: null,
  banks_total: 2,
  ...over,
})

/** A health answer holding these banks: `[id, facts, documents, waiting, failed]`. */
type BankTuple = [string, number | null, number | null, number | null, number | null]
function withBanks(banks: BankTuple[], over: Partial<MemoryHealthReport> = {}): MemoryHealthReport {
  const base = healthReport()
  return healthReport({
    banks: {
      layout: 'per_bank',
      total: banks.length,
      facts: banks.reduce((sum, b) => sum + (b[1] ?? 0), 0),
      list: banks.map(([bank_id, facts, documents]) => ({ bank_id, facts, documents })),
    },
    queue: {
      ...base.queue!,
      by_bank: banks.map(([bank_id, , , waiting, failed]) => ({ bank_id, waiting, failed, oldest_waiting_s: null })),
    },
    ...over,
  })
}

const view = (data: unknown, isError = false): HealthView => readHealth({ data: data as MemoryHealth, isError })
const reportOf = (read: HealthView) => (read as { report: MemoryHealthReport }).report

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

describe('readHealth', () => {
  it('is loading until there is an answer, and failed when the request failed', () => {
    expect(readHealth({})).toEqual({ kind: 'loading' })
    expect(readHealth({ isError: true })).toEqual({ kind: 'failed' })
  })

  it("reads a body that is not the route's as failed, never as clear", () => {
    // The e2e catch-all answers an unrouted request with `{}`.
    expect(view({})).toEqual({ kind: 'failed' })
    expect(view('nope')).toEqual({ kind: 'failed' })
    expect(view([])).toEqual({ kind: 'failed' })
  })

  it('tells memory off from an engine with nothing to report on', () => {
    const noPath = { checked_at: '2026-11-11T03:12:17+00:00', verdict: null, partial: [], steps: [] }
    expect(view({ ...noPath, enabled: false, reason: 'memory_disabled' })).toEqual({ kind: 'off' })
    expect(view({ ...noPath, enabled: true, reason: 'engine_unsupported' })).toEqual({ kind: 'unsupported' })
  })

  it('is unreachable when the engine does not answer, whatever else the body says', () => {
    expect(view(healthUnreachable()).kind).toBe('unreachable')
    expect(view({ ...healthReport(), engine: { reachable: false, version: null } }).kind).toBe('unreachable')
  })

  it('hands back the report with every list a list and every section an object or null', () => {
    const read = view({ enabled: true, verdict: 'clear', engine: { reachable: true }, partial: 'x', steps: null, queue: [] })
    expect(read.kind).toBe('ready')
    const report = reportOf(read)
    expect(report.partial).toEqual([])
    expect(report.steps).toEqual([])
    expect(report.queue).toBeNull()
    expect(report.banks).toBeNull()
    expect(report.engine).toEqual({ reachable: true, version: null })
  })

  it('decides a verdict it does not know from the steps, and never assumes clear', () => {
    const open = healthReport({
      steps: [{ id: 'queue', state: 'current', waits_on: null, reason_code: 'queue_operations_failed', figures: {} }],
    })
    expect(reportOf(view({ ...open, verdict: 'something-new' })).verdict).toBe('attention')
    expect(reportOf(view({ ...healthReport(), verdict: 'something-new' })).verdict).toBe('clear')
  })

  it('reads each state of the forced mock', () => {
    expect(view(memoryHealthFixture('attention')).kind).toBe('ready')
    expect(view(memoryHealthFixture('clear')).kind).toBe('ready')
    expect(view(memoryHealthFixture('partial')).kind).toBe('ready')
    expect(view(memoryHealthFixture('unreachable')).kind).toBe('unreachable')
  })
})

describe('engineFromHealth', () => {
  it('gives the engine state a tab would otherwise ask the engine for', () => {
    expect(engineFromHealth({ kind: 'loading' })).toBe('loading')
    expect(engineFromHealth({ kind: 'unsupported' })).toBe('fallback')
    expect(engineFromHealth(view(healthReport()))).toBe('reachable')
    expect(engineFromHealth(view(healthUnreachable()))).toBe('unreachable')
    expect(engineFromHealth({ kind: 'failed' })).toBe('unreachable')
    expect(engineFromHealth({ kind: 'off' })).toBe('unreachable')
  })
})

describe('healthBankRows', () => {
  it('joins facts and documents from banks.list with waiting and failed from queue.by_bank', () => {
    const rows = healthBankRows(
      withBanks([
        ['small', 12, 2, 0, 0],
        ['Hal0::Agents', 24530, 4100, 978, 105],
        ['mid', 300, 40, 3, 0],
      ]),
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

  it('joins by bank id, whatever order each list is in', () => {
    const report = withBanks([
      ['a', 5, 1, 7, 2],
      ['b', 9, 3, 0, 1],
    ])
    report.queue!.by_bank.reverse()
    const rows = healthBankRows(report)
    expect(rows.find((r) => r.id === 'a')).toMatchObject({ facts: 5, waiting: 7, failed: 2 })
    expect(rows.find((r) => r.id === 'b')).toMatchObject({ facts: 9, waiting: 0, failed: 1 })
  })

  it('leaves a figure that was not read with no value, never a zero', () => {
    const rows = healthBankRows(
      withBanks([
        ['a', 100, 10, 0, 0],
        ['b', 42, null, null, null],
      ]),
    )
    expect(rows.find((r) => r.id === 'b')).toEqual({
      id: 'b',
      facts: 42,
      documents: null,
      waiting: null,
      failed: null,
      reported: false,
    })
  })

  it('gives a bank the queue does not name its facts, and no operation counts', () => {
    const report = withBanks([['a', 100, 10, 0, 0]])
    report.queue!.by_bank = []
    expect(healthBankRows(report)).toEqual([
      { id: 'a', facts: 100, documents: 10, waiting: null, failed: null, reported: false },
    ])
  })

  it('still lists a bank only the queue names', () => {
    const report = withBanks([['a', 100, 10, 0, 0]])
    report.queue!.by_bank.push({ bank_id: 'late', waiting: 4, failed: 1, oldest_waiting_s: 60 })
    expect(healthBankRows(report).find((r) => r.id === 'late')).toEqual({
      id: 'late',
      facts: null,
      documents: null,
      waiting: 4,
      failed: 1,
      reported: true,
    })
  })

  it('sorts an unknown fact count last, after an empty bank, and keeps the engine order among equals', () => {
    const rows = healthBankRows(
      withBanks([
        ['unknown', null, null, 0, 0],
        ['empty', 0, 0, 0, 0],
        ['b', 5, 1, 0, 0],
        ['a', 5, 1, 0, 0],
      ]),
    )
    expect(rows.map((r) => r.id)).toEqual(['b', 'a', 'empty', 'unknown'])
  })

  it('reads a figure that is not a finite number as not reported', () => {
    const [row] = healthBankRows(withBanks([['a', 1, Number.NaN, undefined as never, '3' as never]]))
    expect(row.documents).toBeNull()
    expect(row.waiting).toBeNull()
    expect(row.failed).toBeNull()
  })

  it('has no rows without a report, or when the sections are missing', () => {
    expect(healthBankRows(null)).toEqual([])
    expect(healthBankRows({ banks: null, queue: null })).toEqual([])
  })

  it('reads the forced mock: six banks, the two that did not answer without operation counts', () => {
    const rows = healthBankRows(memoryHealthFixture('partial') as unknown as MemoryHealthReport)
    expect(rows).toHaveLength(6)
    const big = rows.find((r) => r.id === 'big')!
    expect(big.facts).not.toBeNull()
    expect(big).toMatchObject({ documents: null, waiting: null, failed: null, reported: false })
  })
})

describe('bankTotals', () => {
  it('adds the banks up', () => {
    const rows = healthBankRows(
      withBanks([
        ['a', 24530, 1, 978, 100],
        ['b', 28425, 1, 115, 5],
      ]),
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
    const rows = healthBankRows(
      withBanks([
        ['a', 7, 1, 0, 2],
        ['b', null, null, null, null],
      ]),
    )
    expect(bankTotals(rows)).toMatchObject({ banks: 2, counted: 1, reported: 1, facts: 7, failed: 2 })
  })

  it('has no sum at all when no bank reported', () => {
    const rows = healthBankRows(withBanks([['a', null, null, null, null]]))
    expect(bankTotals(rows)).toMatchObject({ reported: 0, facts: null, waiting: null, failed: null })
  })

  it("takes the engine's own bank count when it is larger than the list", () => {
    const rows = healthBankRows(withBanks([['a', 1, 1, 0, 0]]))
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
  it('is a skeleton, with no number, while the health answer is on its way', () => {
    expect(metaLine({ kind: 'loading' })).toEqual({ kind: 'loading' })
  })

  it('says the engine is unreachable, with no number', () => {
    const line = metaLine(view(healthUnreachable()))
    expect(line).toEqual({ kind: 'text', text: 'Engine unreachable · no counts available' })
    expect(JSON.stringify(line)).not.toMatch(/\d/)
  })

  it('says so when hal0 itself did not answer, without blaming the engine', () => {
    expect(metaLine({ kind: 'failed' })).toEqual({
      kind: 'text',
      text: 'Memory status did not load · no counts available',
    })
  })

  it('says so when memory is off, or not on Hindsight', () => {
    expect(metaLine({ kind: 'off' })).toEqual({ kind: 'text', text: 'Memory is turned off · no counts available' })
    expect(metaLine({ kind: 'unsupported' })).toEqual({
      kind: 'text',
      text: 'Fallback engine · no counts available',
    })
  })

  it('counts the facts and the banks from the health answer', () => {
    expect(metaLine(view(healthReport()))).toEqual({ kind: 'text', text: '27,542 facts in 3 banks' })
    expect(metaLine(view(withBanks([['a', 1, 0, 0, 0]])))).toEqual({ kind: 'text', text: '1 fact in 1 bank' })
  })

  it('says how many banks a partial count covers', () => {
    const report = withBanks([
      ['a', 3000, 1, 0, 0],
      ['b', null, null, null, null],
      ['c', 12, 1, 0, 0],
    ])
    expect(metaLine(view(report))).toEqual({ kind: 'text', text: '3 banks · 3,012 facts in the 2 that reported' })
  })

  it("gives no fact count when no bank reported one, though the route's own sum reads zero", () => {
    const report = withBanks([
      ['a', null, null, null, null],
      ['b', null, null, null, null],
    ])
    expect(report.banks!.facts).toBe(0)
    expect(metaLine(view(report))).toEqual({ kind: 'text', text: '2 banks · fact counts not reported' })
  })

  it('says there are no banks, and that the bank list did not load', () => {
    expect(metaLine(view(withBanks([])))).toEqual({ kind: 'text', text: 'Engine reachable · no banks yet' })
    const unlisted = view(healthReport({ banks: { layout: 'per_bank', total: null, facts: null, list: [] } }))
    expect(bankListMissing(unlisted)).toBe(true)
    expect(metaLine(unlisted)).toEqual({ kind: 'text', text: 'Engine reachable · bank list not available' })
    expect(bankListMissing(view(healthReport()))).toBe(false)
  })
})
