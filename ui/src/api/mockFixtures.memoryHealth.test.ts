// The forced mock's `GET /api/memory/health` (ADR-0058, decision 12): the route is allow-listed,
// its three states carry the fields the real route sends, the state is chosen by a window knob,
// and the queue figures agree with the operation lists the same mock serves.
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ENDPOINTS } from './endpoints'
import type { BankOperations } from './hooks/useHindsight'
import type { MemoryHealthReport } from './hooks/useMemory'
import { MOCK_ALLOWLIST } from './mock'
import { MOCK_BUILDERS, memoryHealthFixture, mockMemoryHealthState } from './mockFixtures'

/** Resolve a URL the way mockFetch does: first allow-list row whose pattern matches the path. */
function serve(url: string): { key: string; payload: unknown } {
  const path = url.split('?')[0]
  for (const row of MOCK_ALLOWLIST) {
    const match = path.match(row.re)
    if (match) return { key: row.key, payload: MOCK_BUILDERS[row.key](url, match) }
  }
  throw new Error(`not allow-listed: ${url}`)
}

const health = () => serve(ENDPOINTS.memoryHealth).payload as MemoryHealthReport
const operations = (bank: string, query = '') =>
  serve(`${ENDPOINTS.memoryBankOperations(bank)}${query}`).payload as BankOperations
const setState = (state?: string) => vi.stubGlobal('window', { __hal0MockMemoryHealth: state })

/** The top-level fields of the route's answer, as src/hal0/api/routes/memory_health.py lists them. */
const REPORT_KEYS = [
  'banks',
  'checked_at',
  'embedder',
  'enabled',
  'engine',
  'extraction',
  'knowledge',
  'partial',
  'queue',
  'steps',
  'verdict',
  'writes',
]

afterEach(() => vi.unstubAllGlobals())

describe('allow-list', () => {
  it('serves the health route from its own builder, and never falls through to the engine card', () => {
    expect(ENDPOINTS.memoryHealth).toBe('/api/memory/health')
    expect(serve(ENDPOINTS.memoryHealth).key).toBe('memoryHealth')
    expect(serve(ENDPOINTS.memoryEngine).key).toBe('memoryEngine')
  })

  it('is substituted without trying the network, so the state knob is the only way in', () => {
    const row = MOCK_ALLOWLIST.find((r) => r.key === 'memoryHealth')!
    expect(row.networkFirst).toBeUndefined()
  })
})

describe('the state knob', () => {
  it('defaults to attention, and ignores a value it does not know', () => {
    expect(mockMemoryHealthState()).toBe('attention')
    setState('nonsense')
    expect(mockMemoryHealthState()).toBe('attention')
    setState('clear')
    expect(mockMemoryHealthState()).toBe('clear')
    setState('unreachable')
    expect(mockMemoryHealthState()).toBe('unreachable')
  })
})

describe.each(['attention', 'clear', 'unreachable'] as const)('%s', (state) => {
  it('carries exactly the route’s top-level fields', () => {
    setState(state)
    const body = health()
    expect(Object.keys(body).sort()).toEqual(REPORT_KEYS)
    expect(body.enabled).toBe(true)
    expect(body.partial).toEqual([])
    expect(body.writes).toBeNull()
    // Seconds and an explicit offset, as the route writes it.
    expect(body.checked_at).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$/)
    for (const step of body.steps) {
      expect(Object.keys(step).sort()).toEqual(['figures', 'id', 'reason_code', 'state', 'waits_on'])
    }
  })
})

describe('attention', () => {
  it('has extraction as the current step and the queue waiting on it', () => {
    const body = health()
    expect(body.verdict).toBe('attention')
    expect(body.steps.map((s) => [s.id, s.state, s.waits_on, s.reason_code])).toEqual([
      ['engine', 'clear', null, null],
      ['extraction', 'current', null, 'extraction_recent_failures'],
      ['queue', 'waiting', 'extraction', 'queue_operations_failed'],
      ['embedder', 'clear', null, null],
      ['knowledge', 'waiting', 'queue', 'knowledge_refresh_failed'],
    ])
    expect(body.steps[1].figures).toMatchObject({ failures: 7, window_s: 21600 })
    expect(body.steps[2].figures).toMatchObject({ failed: 10, waiting: 100, max_waiting_age_s: 1800 })
  })

  it('groups the failure messages the way the route reduces them, most frequent first', () => {
    const { extraction } = health()
    expect(Object.keys(extraction!).sort()).toEqual([
      'concurrency',
      'failures_6h',
      'llm_reachable',
      'mode',
      'slot',
      'slot_resolves',
      'top_errors',
    ])
    expect(extraction!.failures_6h).toBe(7)
    expect(extraction!.top_errors.map((e) => e.count)).toEqual([6, 1])
    expect(extraction!.top_errors[0].message).toBe('APIConnectionError: Connection error.')
    // A number became `<n>`, and the message is cut at 160 characters.
    expect(extraction!.top_errors[1].message).toMatch(/^Task exceeded the <n>s wall-clock limit/)
    expect(extraction!.top_errors[1].message).toHaveLength(160)
  })

  it('counts the queue from the operation lists the mock serves', () => {
    const { queue } = health()
    expect(Object.keys(queue!).sort()).toEqual([
      'by_bank',
      'consolidation_failed',
      'consolidation_pending',
      'failed',
      'oldest_waiting_s',
      'truncated',
      'waiting',
    ])
    for (const row of queue!.by_bank) {
      expect(Object.keys(row).sort()).toEqual(['bank_id', 'failed', 'oldest_waiting_s', 'waiting'])
      expect(row.waiting).toBe(operations(row.bank_id, '?status=pending&limit=1').total)
      expect(row.failed).toBe(operations(row.bank_id, '?status=failed&limit=1').total)
      // A bank with nothing waiting has no oldest waiting operation.
      expect(row.oldest_waiting_s === null).toBe(row.waiting === 0)
    }
    expect(queue!.waiting).toBe(queue!.by_bank.reduce((sum, b) => sum + (b.waiting ?? 0), 0))
    expect(queue!.failed).toBe(queue!.by_bank.reduce((sum, b) => sum + (b.failed ?? 0), 0))
    expect(queue!.oldest_waiting_s).toBe(Math.max(...queue!.by_bank.map((b) => b.oldest_waiting_s ?? 0)))
    expect(queue).toMatchObject({ waiting: 100, failed: 10, truncated: false })
  })

  it('lists every bank the mock has', () => {
    const { banks, queue } = health()
    const listed = (serve(ENDPOINTS.memoryBanks).payload as { banks: { bank_id: string }[] }).banks.map(
      (b) => b.bank_id,
    )
    expect(banks!.layout).toBe('per_bank')
    expect(banks!.total).toBe(listed.length)
    expect(banks!.list.map((b) => b.bank_id)).toEqual(listed)
    expect(queue!.by_bank.map((b) => b.bank_id)).toEqual(listed)
    expect(banks!.facts).toBe(banks!.list.reduce((sum, b) => sum + (b.facts ?? 0), 0))
  })
})

describe('clear', () => {
  it('has every step clear and nothing waiting or failed', () => {
    setState('clear')
    const body = health()
    expect(body.verdict).toBe('clear')
    expect(body.steps.every((s) => s.state === 'clear' && s.reason_code === null && s.waits_on === null)).toBe(true)
    expect(body.queue).toMatchObject({ waiting: 0, failed: 0, oldest_waiting_s: null })
    expect(body.queue!.by_bank.every((b) => b.waiting === 0 && b.failed === 0 && b.oldest_waiting_s === null)).toBe(
      true,
    )
    expect(body.extraction).toMatchObject({ failures_6h: 0, top_errors: [] })
  })

  it('leaves only finished rows in every bank’s operations, so the two agree', () => {
    setState('clear')
    for (const bank of ['primary', 'big', 'ingest', 'empty']) {
      const list = operations(bank, '?limit=100')
      expect(list.operations.every((op) => op.status === 'completed')).toBe(true)
      expect(operations(bank, '?status=failed').total).toBe(0)
      expect(operations(bank, '?status=pending').total).toBe(0)
    }
    expect(operations('big', '?limit=100').total).toBe(30)
  })

  it('does not change the operation lists in the default state', () => {
    expect(operations('big').total).toBe(137)
    expect(operations('big', '?status=failed').total).toBe(9)
  })
})

describe('unreachable', () => {
  it('has every section but the engine null, and the engine step alone', () => {
    setState('unreachable')
    const body = health()
    expect(body.verdict).toBe('unreachable')
    expect(body.engine).toEqual({ reachable: false, version: null })
    for (const section of ['extraction', 'embedder', 'queue', 'knowledge', 'banks'] as const) {
      expect(body[section]).toBeNull()
    }
    expect(body.steps).toEqual([
      {
        id: 'engine',
        state: 'current',
        waits_on: null,
        reason_code: 'engine_unreachable',
        figures: { reachable: false },
      },
    ])
  })

  it('turns the engine card unreachable too, so the mock is one box', () => {
    expect(serve(ENDPOINTS.memoryEngine).payload).toMatchObject({ reachable: true })
    setState('unreachable')
    expect(serve(ENDPOINTS.memoryEngine).payload).toMatchObject({
      enabled: true,
      engine: 'hindsight',
      reachable: false,
      version: null,
    })
  })
})

describe('memoryHealthFixture', () => {
  it('builds a named state whatever the knob says', () => {
    setState('unreachable')
    expect(memoryHealthFixture('clear').verdict).toBe('clear')
    expect(memoryHealthFixture('attention').verdict).toBe('attention')
    expect(memoryHealthFixture().verdict).toBe('unreachable')
  })
})
