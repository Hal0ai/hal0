// @vitest-environment happy-dom
//
// Memory ▸ Operations: each of its states, the retry and discard rules, the filter, the page
// controls and the wording for a health read that was cut short. The requests are replaced (the
// whole of operations-data); the area object is built as MemoryArea builds it.
import { act, createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BankOperation } from '@/api/hooks/useHindsight'
import type { MemoryHealth } from '@/api/hooks/useMemory'
import { BANKS, ENGINE, makeArea } from './area-test-fixtures'
import type { OperationListEntry, OperationListsOptions } from './operations-data'
import { cleanup, click, mount } from './shared/test-harness'

const NOW = '2026-11-05T12:00:00Z'
const AGENTS = 'Hal0::Agents'
const LONG = 'coding-agent::ultrareview-1.4'

type Lists = (ids: string[], opts: OperationListsOptions) => OperationListEntry[]

const m = vi.hoisted(() => ({
  health: {} as { data?: unknown; isError?: boolean; isFetching?: boolean; refetch?: () => void },
  stats: {} as { data?: unknown; isError?: boolean },
  lists: null as unknown as Lists,
  listCalls: [] as { ids: string[]; opts: OperationListsOptions }[],
  refetchLists: vi.fn(),
  retry: { mutateAsync: vi.fn() },
  consolidate: { mutate: vi.fn(), isPending: false },
  collect: vi.fn(),
  retryOp: vi.fn(),
  discardOp: vi.fn(),
  refresh: vi.fn(),
}))

vi.mock('./operations-data', () => ({
  useQueueHealth: () => m.health,
  useBankConsolidation: (bank: string | null) => (bank ? m.stats : {}),
  useOperationLists: (ids: string[], opts: OperationListsOptions) => {
    m.listCalls.push({ ids, opts })
    return { entries: opts.enabled === false ? [] : m.lists(ids, opts), refetch: m.refetchLists }
  },
  useRetryOne: () => m.retry,
  useConsolidateNow: () => m.consolidate,
  collectFailed: (...args: unknown[]) => m.collect(...args),
  retryOperation: (...args: unknown[]) => m.retryOp(...args),
  discardOperation: (...args: unknown[]) => m.discardOp(...args),
  useQueueRefresh: () => m.refresh,
}))

const { OperationsTab } = await import('./OperationsTab.jsx')

// ── The health answer ─────────────────────────────────────────────────────────

/** A backlog: a queue on two banks, 105 failed, and extraction failing unless `extraction` says clear. */
function healthBody(over: { extraction?: 'current' | 'clear'; queue?: Record<string, unknown>; partial?: string[] } = {}) {
  const extractionOpen = (over.extraction ?? 'current') === 'current'
  const topErrors = [{ message: 'APIConnectionError: Connection error.', count: 37 }]
  return {
    enabled: true,
    verdict: 'attention',
    checked_at: '2026-11-05T12:00:00+00:00',
    partial: over.partial ?? [],
    engine: { reachable: true, version: '1.2.0' },
    extraction: {
      slot: 'memory',
      mode: 'dedicated',
      slot_resolves: true,
      llm_reachable: true,
      concurrency: 2,
      failures_6h: extractionOpen ? 37 : 0,
      top_errors: extractionOpen ? topErrors : [],
    },
    embedder: null,
    queue: {
      waiting: 1093,
      failed: 105,
      oldest_waiting_s: 6 * 3600,
      consolidation_pending: 1899,
      consolidation_failed: 8,
      by_bank: [
        { bank_id: 'shared', waiting: 115, failed: 0, oldest_waiting_s: 3600 },
        { bank_id: AGENTS, waiting: 978, failed: 105, oldest_waiting_s: 6 * 3600 },
        { bank_id: LONG, waiting: 0, failed: 0, oldest_waiting_s: null },
      ],
      truncated: false,
      ...over.queue,
    },
    knowledge: null,
    banks: null,
    writes: null,
    steps: [
      { id: 'engine', state: 'clear', waits_on: null, reason_code: null, figures: { reachable: true } },
      extractionOpen
        ? {
            id: 'extraction',
            state: 'current',
            waits_on: null,
            reason_code: 'extraction_recent_failures',
            figures: { failures: 37, window_s: 21600, top_errors: topErrors },
          }
        : { id: 'extraction', state: 'clear', waits_on: null, reason_code: null, figures: {} },
      {
        id: 'queue',
        state: extractionOpen ? 'waiting' : 'current',
        waits_on: extractionOpen ? 'extraction' : null,
        reason_code: 'queue_operations_failed',
        figures: {},
      },
    ],
  } as unknown as MemoryHealth
}

/** Nothing waiting, nothing failed, every step clear. */
function clearBody() {
  const body = healthBody({
    extraction: 'clear',
    queue: {
      waiting: 0,
      failed: 0,
      oldest_waiting_s: null,
      consolidation_pending: 0,
      consolidation_failed: 0,
      by_bank: BANKS.map((b) => ({ bank_id: b.bank_id, waiting: 0, failed: 0, oldest_waiting_s: null })),
    },
  }) as unknown as { verdict: string; steps: { state: string }[] }
  body.verdict = 'clear'
  for (const step of body.steps) step.state = 'clear'
  return body as unknown as MemoryHealth
}

// ── Operations ────────────────────────────────────────────────────────────────

const WALL_CLOCK =
  "Task exceeded the 14400s wall-clock limit for 'batch_retain' (stage=llm.openai.retain_extract_facts+structured.queued) and was cancelled. Raise HINDSIGHT_API_RETAIN_WALL_TIMEOUT if this is a legitimately long operation."

const op = (id: string, minutesAgo: number, over: Partial<BankOperation> = {}): BankOperation => {
  const createdAt = new Date(Date.parse(NOW) - minutesAgo * 60_000).toISOString()
  return {
    id,
    operation_id: id,
    task_type: 'batch_retain',
    operation_type: 'batch_retain',
    status: 'pending',
    created_at: createdAt,
    updated_at: createdAt,
    error_message: null,
    retry_count: 0,
    next_retry_at: null,
    ...over,
  }
}

const OPS: Record<string, BankOperation[]> = {
  shared: [op('s-1', 40), op('s-2', 480, { status: 'completed', task_type: 'retain', operation_type: 'retain' })],
  [AGENTS]: [
    op('a-1', 4, { status: 'processing' }),
    op('a-2', 60, {
      task_type: 'consolidation',
      operation_type: 'consolidation',
      next_retry_at: new Date(Date.parse(NOW) + 12 * 60_000).toISOString(),
    }),
    op('a-3', 120, { status: 'failed', error_message: 'APIConnectionError: Connection error.', retry_count: 3 }),
    op('a-4', 300, {
      status: 'failed',
      task_type: 'refresh_mental_model',
      operation_type: 'refresh_mental_model',
      error_message: WALL_CLOCK,
    }),
  ],
  [LONG]: [],
}

const entry = (bankId: string, operations: BankOperation[], total: number | null = operations.length) =>
  ({ bankId, operations, total, isLoading: false, isError: false, error: null }) satisfies OperationListEntry

const defaultLists: Lists = (ids, opts) =>
  ids.map((bankId) => {
    const all = OPS[bankId] ?? []
    return entry(bankId, opts.status ? all.filter((o) => o.status === opts.status) : all)
  })

beforeEach(() => {
  vi.useFakeTimers({ now: new Date(NOW), toFake: ['Date'] })
  m.health = { data: healthBody(), refetch: vi.fn() }
  m.stats = { data: { pending_consolidation: 1800, failed_consolidation: 8 } }
  m.lists = defaultLists
  m.listCalls = []
  m.refetchLists = vi.fn()
  m.retry = { mutateAsync: vi.fn().mockResolvedValue({}) }
  m.consolidate = { mutate: vi.fn(), isPending: false }
  m.collect = vi.fn().mockResolvedValue({ targets: [], more: false })
  m.retryOp = vi.fn().mockResolvedValue({})
  m.discardOp = vi.fn().mockResolvedValue({})
  m.refresh = vi.fn()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const render = (area = makeArea({ hash: '#memory/operations' })) => mount(createElement(OperationsTab, { area })).host
const inBank = (bank: string, over: Parameters<typeof makeArea>[0] = {}) =>
  makeArea({ hash: `#memory/operations?bank=${bank}`, ...over })
const byId = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const allById = (host: HTMLElement, id: string) => [...host.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const stat = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-stat="${id}"]`)!
const textOf = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
const state = (host: HTMLElement) => byId(host, 'ma-operations')!.dataset.state
const segment = (host: HTMLElement, id: string) =>
  host.querySelector<HTMLButtonElement>(`[data-testid="ma-ops-filter"] [data-option-id="${id}"]`)!
const rowOf = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`tr[data-op="${id}"]`)!

/** Let awaited requests resolve and their renders commit. */
async function flush() {
  await act(async () => {
    for (let i = 0; i < 20; i++) await Promise.resolve()
  })
}

describe('loading', () => {
  it('shows skeletons and not one figure while the engine check is out', () => {
    const host = render(makeArea({ hash: '#memory/operations', engineQuery: {} }))
    expect(state(host)).toBe('loading')
    expect(host.querySelectorAll('.ma-skel').length).toBeGreaterThan(5)
    // No digit anywhere: a loading page never shows a zero.
    expect(host.textContent).not.toMatch(/\d/)
    expect([...host.querySelectorAll('.ma-stat-label')].map((el) => el.textContent)).toEqual([
      'Waiting',
      'Failed',
      'Oldest waiting',
      'Waiting on consolidation',
      'Failed consolidation',
    ])
  })

  it('keeps the figures as skeletons until the health answer arrives, and shows no failed card', () => {
    m.health = {}
    m.lists = (ids) => ids.map((bankId) => ({ ...entry(bankId, []), isLoading: true }))
    const host = render()
    expect(state(host)).toBe('loading')
    expect(byId(host, 'ma-ops-strip')!.getAttribute('aria-busy')).toBe('true')
    expect(byId(host, 'ma-ops-list-loading')).not.toBeNull()
    expect(byId(host, 'ma-ops-failed')).toBeNull()
    expect(byId(host, 'ma-ops-cause')).toBeNull()
    // No figure anywhere under the scope bar (a bank id may hold a digit of its own).
    expect(byId(host, 'ma-ops-strip')!.textContent).not.toMatch(/\d/)
    expect(byId(host, 'ma-ops-list')!.textContent).not.toMatch(/\d/)
  })

  it('waits for the chosen bank’s consolidation figures before showing its strip', () => {
    m.stats = {}
    const host = render(inBank(AGENTS))
    expect(byId(host, 'ma-ops-strip')!.getAttribute('aria-busy')).toBe('true')
    expect(textOf(byId(host, 'ma-ops-strip'))).not.toMatch(/\d/)
  })
})

describe('engine unreachable', () => {
  const expectUnreachable = (host: HTMLElement) => {
    expect(state(host)).toBe('unreachable')
    const card = byId(host, 'ma-ops-unreachable')!
    expect(card.querySelector('h3')!.textContent).toBe('The memory engine is not answering')
    expect(card.querySelector('.chip')!.textContent).toBe('unreachable')
    expect(card.querySelector('a')!.getAttribute('href')).toBe('#memory/health')
    // The whole tab: no figure, no list, no action.
    expect(host.textContent).not.toMatch(/\d/)
    expect(host.querySelector('table')).toBeNull()
    expect(host.querySelector('button')).toBeNull()
    expect(byId(host, 'ma-scope')).toBeNull()
  }

  it('is the whole tab when the health answer says so, even if the engine check has not caught up', () => {
    m.health = {
      data: {
        enabled: true,
        verdict: 'unreachable',
        checked_at: '2026-11-05T12:00:00+00:00',
        partial: [],
        engine: { reachable: false, version: null },
        extraction: null,
        embedder: null,
        queue: null,
        knowledge: null,
        banks: null,
        writes: null,
        steps: [{ id: 'engine', state: 'current', waits_on: null, reason_code: 'engine_unreachable', figures: {} }],
      },
    }
    expectUnreachable(render())
  })

  it('is the whole tab when the engine check says so', () => {
    const area = makeArea({
      hash: '#memory/operations',
      engineQuery: { data: { ...ENGINE, reachable: false, version: null } },
      banksQuery: { isError: true },
    })
    expectUnreachable(render(area))
  })

  it('says so for a fallback engine, and for memory turned off', () => {
    const fallback = render(makeArea({ hash: '#memory/operations', engineQuery: { data: { ...ENGINE, engine: null } } }))
    expect(byId(fallback, 'ma-ops-fallback')).not.toBeNull()
    cleanup()
    m.health = { data: { enabled: false, reason: 'memory_disabled', verdict: null, partial: [], steps: [] } }
    expect(byId(render(), 'ma-ops-off')).not.toBeNull()
  })
})

describe('backlog, with extraction failing', () => {
  it('leads with the cause: what is failing, how often, the top reason, and the way to Health', () => {
    const host = render()
    const card = byId(host, 'ma-ops-cause')!
    expect(card.querySelector('h2')!.textContent).toBe('New memories are failing to be stored')
    expect(card.querySelector('.chip')!.textContent).toBe('failing')
    const text = textOf(byId(host, 'ma-ops-cause-text'))
    expect(text).toBe(
      '37 attempts to store a memory failed in the last 6 h. Until that is fixed, operations wait here and a retry would fail.',
    )
    expect(textOf(byId(host, 'ma-ops-cause-reason'))).toBe(
      'Most frequent reason, seen 37 times: APIConnectionError: Connection error.',
    )
    expect(byId(host, 'ma-ops-cause-health')!.getAttribute('href')).toBe('#memory/health')
    // The cause offers no action of its own.
    expect(card.querySelector('button')).toBeNull()
    // It comes before the figures.
    const order = [...host.querySelectorAll('[data-testid]')].map((el) => el.getAttribute('data-testid'))
    expect(order.indexOf('ma-ops-cause')).toBeLessThan(order.indexOf('ma-ops-strip'))
  })

  it('shows the health route’s figures, each with a label and a unit', () => {
    const host = render()
    expect(textOf(stat(host, 'waiting'))).toBe('Waiting1,093operationsin 2 banks')
    expect(textOf(stat(host, 'failed'))).toBe(`Failed105operationsall on ${AGENTS}`)
    expect(textOf(stat(host, 'oldest'))).toBe('Oldest waiting6hin the queue')
    expect(textOf(stat(host, 'consolidation-waiting'))).toContain('1,899facts')
    expect(textOf(stat(host, 'consolidation-failed'))).toContain('8facts')
    expect(host.querySelector('[data-testid="ma-ops-strip"]')!.getAttribute('aria-label')).toBe(
      'Queue summary, all banks',
    )
  })

  it('disables every retry and says why, with a link to Health; discard stays available', () => {
    const host = render()
    const retryAll = byId(host, 'ma-ops-retry-all') as HTMLButtonElement
    expect(retryAll.disabled).toBe(true)
    const reason = host.ownerDocument.getElementById(retryAll.getAttribute('aria-describedby')!)!
    expect(textOf(reason)).toBe('Retrying will fail until storing memories works again. Open Health')
    expect(reason.querySelector('a')!.getAttribute('href')).toBe('#memory/health')

    const rowRetry = rowOf(host, 'a-3').querySelector<HTMLButtonElement>('[data-testid="ma-ops-retry"]')!
    expect(rowRetry.disabled).toBe(true)
    const rowReason = host.ownerDocument.getElementById(rowRetry.getAttribute('aria-describedby')!)!
    expect(textOf(rowReason)).toContain('Retrying will fail until storing memories works again.')

    const discard = byId(host, 'ma-ops-discard') as HTMLButtonElement
    expect(discard.disabled).toBe(false)
    expect(discard.textContent).toBe('Discard 105 failed operations')
    expect(discard.className).toBe('btn danger')

    click(retryAll)
    click(rowRetry)
    expect(m.collect).not.toHaveBeenCalled()
    expect(m.retry.mutateAsync).not.toHaveBeenCalled()
  })

  it('has no primary button', () => {
    const host = render()
    for (const button of host.querySelectorAll('button.btn')) {
      expect(button.className).toMatch(/\b(ghost|danger)\b/)
    }
    expect(host.querySelector('a.btn')).toBeNull()
  })

  it('does not link to the legacy tools page', () => {
    const host = render()
    expect(host.querySelector('a[href^="#memworkspace"]')).toBeNull()
    expect(host.textContent).not.toMatch(/legacy/i)
  })
})

describe('queue by bank', () => {
  it('lists the banks that hold something, each a link to its operations, and counts the rest', () => {
    const host = render()
    const card = byId(host, 'ma-ops-banks')!
    const region = card.querySelector('[role="region"]')!
    expect(region.getAttribute('aria-label')).toBe('Queue by bank table')
    expect(region.getAttribute('tabindex')).toBe('0')
    expect([...card.querySelectorAll('thead th')].map((th) => th.textContent)).toEqual([
      'Bank',
      'Waiting',
      'Failed',
      'Oldest waiting',
    ])
    const rows = [...card.querySelectorAll('tbody tr')]
    expect(rows.map((tr) => tr.getAttribute('data-bank'))).toEqual([AGENTS, 'shared'])
    expect(rows.map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent))).toEqual([
      ['978', '105', '6 h'],
      ['115', '0', '1 h'],
    ])
    expect(rows[0].querySelector('a')!.getAttribute('href')).toBe(`#memory/operations?bank=${AGENTS}`)
    expect(byId(host, 'ma-ops-banks-quiet')!.textContent).toBe('1 bank has nothing waiting or failed.')
    expect(card.textContent).not.toContain(LONG)
  })

  it('is not shown for one bank: there is nothing to break down', () => {
    expect(byId(render(inBank(AGENTS)), 'ma-ops-banks')).toBeNull()
  })

  it('marks a figure the engine did not report, and never prints a zero for it', () => {
    m.health = {
      data: healthBody({
        queue: { by_bank: [{ bank_id: 'shared', waiting: null, failed: null, oldest_waiting_s: null }] },
      }),
    }
    const host = render()
    const cells = [...byId(host, 'ma-ops-banks')!.querySelectorAll('tbody td')]
    expect(cells).toHaveLength(3)
    for (const cell of cells) {
      expect(cell.querySelector('[aria-label="not reported"]')).not.toBeNull()
      expect(cell.textContent).toBe('–')
    }
    expect(byId(host, 'ma-ops-banks-foot')!.textContent).toBe('– means the engine did not report a figure')
  })
})

describe('a health read that was cut short', () => {
  it('says nothing when the read finished', () => {
    expect(byId(render(), 'ma-ops-incomplete')).toBeNull()
  })

  it('says in words that the figures are partial', () => {
    m.health = { data: healthBody({ partial: ['queue'] }) }
    const host = render()
    expect(textOf(byId(host, 'ma-ops-partial'))).toBe(
      'Some banks did not answer in time, so these figures cover only the banks that did. The real numbers may be higher.',
    )
    expect(byId(host, 'ma-ops-truncated')).toBeNull()
    // The figures that were read are still shown.
    expect(textOf(stat(host, 'waiting'))).toContain('1,093')
  })

  it('says in words that a bank was truncated, and what that leaves exact', () => {
    m.health = { data: healthBody({ queue: { truncated: true } }) }
    const host = render()
    expect(textOf(byId(host, 'ma-ops-truncated'))).toBe(
      'A bank holds more operations than were read. The counts are exact; the oldest waiting time and the reasons for failure come from the part that was read.',
    )
    expect(byId(host, 'ma-ops-partial')).toBeNull()
  })
})

describe('the list', () => {
  it('names each row in plain words, with the engine’s name only in a title', () => {
    const host = render()
    const table = byId(host, 'ma-ops-table')!
    expect(table.closest('[role="region"]')!.getAttribute('aria-label')).toBe('Operations table')
    expect([...table.querySelectorAll('thead th')].map((th) => th.textContent)).toEqual([
      'Operation',
      'Bank',
      'Queued',
      'Status',
      'Actions',
    ])
    // Newest first, across both banks.
    expect([...table.querySelectorAll('tbody tr')].map((tr) => tr.getAttribute('data-op'))).toEqual([
      'a-1',
      's-1',
      'a-2',
      'a-3',
      'a-4',
      's-2',
    ])
    const batch = rowOf(host, 'a-1').querySelector('th')!
    expect(batch.textContent).toBe('Store a batch')
    expect(batch.getAttribute('title')).toBe('batch_retain')
    expect(rowOf(host, 'a-2').querySelector('th')!.textContent).toBe('Consolidate')
    expect(rowOf(host, 'a-4').querySelector('th')!.textContent).toBe('Refresh a mental model')
    expect(rowOf(host, 's-2').querySelector('th')!.textContent).toBe('Store a memory')
    // Neither the engine's type names nor its status names are on the page as text.
    const words = [...table.querySelectorAll('tbody th, tbody .chip')].map((el) => el.textContent).join(' ')
    expect(words).not.toMatch(/_|pending|processing|completed/)
  })

  it('shows when a row was queued as a relative time, with the full time in a title and no raw timestamp', () => {
    const host = render()
    const when = rowOf(host, 'a-3').querySelector('time')!
    expect(when.textContent).toBe('2 h ago')
    expect(when.getAttribute('title')).toMatch(/^5 Nov 2026, \d\d:\d\d$/)
    expect(textOf(host)).not.toMatch(/\d{4}-\d\d-\d\dT/)
  })

  it('shows the status as a word, the engine’s retries, and the next try when one is set', () => {
    const host = render()
    expect(rowOf(host, 'a-1').querySelector('.chip')!.textContent).toBe('running')
    expect(rowOf(host, 's-1').querySelector('.chip')!.textContent).toBe('waiting')
    expect(rowOf(host, 's-2').querySelector('.chip')!.textContent).toBe('done')
    expect(textOf(rowOf(host, 'a-3').querySelector('.ma-ops-status'))).toBe('failedretried 3 times')
    expect(textOf(rowOf(host, 'a-2').querySelector('.ma-ops-status'))).toBe('waitingnext try in 12 min')
    // No retry count is shown for a row the engine has not retried.
    expect(textOf(rowOf(host, 's-1').querySelector('.ma-ops-status'))).toBe('waiting')
  })

  it('cuts a failed row’s error to one line, and opens the whole of it on request', () => {
    const host = render()
    const short = rowOf(host, 'a-3')
    expect(short.querySelector('[data-testid="ma-ops-error-text"]')!.textContent).toBe(
      'APIConnectionError: Connection error.',
    )
    // A message that fits has nothing to expand.
    expect(short.querySelector('[data-testid="ma-ops-error-toggle"]')).toBeNull()

    const long = rowOf(host, 'a-4')
    const text = long.querySelector('[data-testid="ma-ops-error-text"]')!
    const toggle = long.querySelector<HTMLButtonElement>('[data-testid="ma-ops-error-toggle"]')!
    expect(text.textContent!.endsWith('…')).toBe(true)
    expect(text.textContent!.length).toBeLessThan(WALL_CLOCK.length)
    expect(toggle.textContent).toBe('Show full error')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(toggle.getAttribute('aria-controls')).toBe(text.id)

    click(toggle)
    expect(text.textContent).toBe(WALL_CLOCK)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(toggle.textContent).toBe('Show less')
  })

  it('has no link and no button in a row that offers nothing', () => {
    const host = render()
    for (const id of ['a-1', 's-1', 's-2']) {
      expect(rowOf(host, id).querySelector('a, button')).toBeNull()
    }
  })

  it('wraps a long bank id instead of cutting it', () => {
    m.lists = (ids) => ids.map((bankId) => entry(bankId, bankId === LONG ? [op('l-1', 5)] : []))
    const host = render()
    const cell = rowOf(host, 'l-1').querySelector('td.ma-ops-bank')!
    expect(cell.textContent).toBe(LONG)
  })

  it('says so when the merged list stops short of every bank’s rows', () => {
    m.lists = (ids) =>
      ids.map((bankId) => (bankId === AGENTS ? entry(bankId, OPS[AGENTS], 1083) : entry(bankId, OPS[bankId] ?? [])))
    const host = render()
    // Rows older than the last one read from the bank that has more are left out.
    expect([...host.querySelectorAll('[data-testid="ma-ops-table"] tbody tr')].map((tr) => tr.getAttribute('data-op'))).toEqual([
      'a-1',
      's-1',
      'a-2',
      'a-3',
      'a-4',
    ])
    expect(byId(host, 'ma-ops-capped')!.textContent).toBe(
      'These are the newest 5 of 1,085 operations across the banks. Choose a bank to page through all of its operations.',
    )
  })

  it('does not claim a merged list is capped when it holds everything', () => {
    expect(byId(render(), 'ma-ops-capped')).toBeNull()
  })

  it('says the list did not load, in a sentence, and offers to check again', () => {
    m.lists = (ids) =>
      ids.map((bankId) => ({
        ...entry(bankId, [], null),
        isError: true,
        error: new Error('{"error":{"code":"memory.engine_unreachable"}}'),
      }))
    const host = render()
    const card = byId(host, 'ma-ops-list-error')!
    expect(card.querySelector('h3')!.textContent).toBe('The operations list did not load')
    expect(textOf(card)).toContain('The engine did not answer the request for its operations.')
    expect(card.textContent).not.toContain('{')
    click(byId(host, 'ma-ops-list-recheck')!)
    expect(m.refetchLists).toHaveBeenCalledTimes(1)
  })

  it('says which banks are missing when only some answered', () => {
    m.lists = (ids) =>
      ids.map((bankId) =>
        bankId === 'shared' ? { ...entry(bankId, [], null), isError: true, error: new Error('x') } : entry(bankId, OPS[bankId] ?? []),
      )
    const host = render()
    expect(byId(host, 'ma-ops-banks-unread')!.textContent).toBe(
      '1 bank did not answer, so its operations are not in this list.',
    )
    expect(rowOf(host, 'a-1')).not.toBeNull()
  })
})

describe('the status filter', () => {
  it('is one radio group with All chosen', () => {
    const host = render()
    const group = byId(host, 'ma-ops-filter')!
    expect(group.getAttribute('role')).toBe('radiogroup')
    expect(group.getAttribute('aria-label')).toBe('Operation status')
    expect([...group.querySelectorAll('[role="radio"]')].map((b) => b.textContent)).toEqual([
      'All',
      'Waiting',
      'Running',
      'Failed',
      'Done',
    ])
    expect(group.querySelectorAll('[aria-checked="true"]')).toHaveLength(1)
    expect(segment(host, 'all').getAttribute('aria-checked')).toBe('true')
  })

  it('asks the engine for the chosen status, and only the banks that have such rows', () => {
    const host = render()
    click(segment(host, 'failed'))
    const call = m.listCalls.at(-1)!
    expect(call.opts.status).toBe('failed')
    expect(call.ids).toEqual([AGENTS])
    expect([...host.querySelectorAll('[data-testid="ma-ops-table"] tbody tr')].map((tr) => tr.getAttribute('data-op'))).toEqual([
      'a-3',
      'a-4',
    ])
    click(segment(host, 'done'))
    expect(m.listCalls.at(-1)!.opts.status).toBe('completed')
    expect(m.listCalls.at(-1)!.ids).toEqual(BANKS.map((b) => b.bank_id))
  })

  it('says a filter has no rows, and offers to show all', () => {
    m.health = { data: healthBody({ extraction: 'clear' }) }
    const host = render(inBank('shared'))
    click(segment(host, 'failed'))
    const empty = byId(host, 'ma-ops-filter-empty')!
    expect(empty.querySelector('h3')!.textContent).toBe('No failed operations')
    expect(textOf(empty)).toContain('Nothing in shared is failed right now.')
    expect(host.querySelector('table.ma-ops-table')).toBeNull()

    const showAll = byId(host, 'ma-ops-show-all') as HTMLButtonElement
    expect(showAll.textContent).toBe('Show all')
    click(showAll)
    expect(segment(host, 'all').getAttribute('aria-checked')).toBe('true')
    expect(rowOf(host, 's-1')).not.toBeNull()
    expect(byId(host, 'ma-ops-filter-empty')).toBeNull()
  })
})

describe('paging one bank', () => {
  const sixty = (ids: string[], opts: OperationListsOptions) =>
    ids.map((bankId) => {
      const offset = opts.offset ?? 0
      const rows = Array.from({ length: Math.min(opts.limit, 60 - offset) }, (_, at) => op(`p-${offset + at}`, offset + at))
      return entry(bankId, rows, 60)
    })

  it('asks the engine for one page, and says where the page is in the list', () => {
    m.lists = sixty
    const host = render(inBank(AGENTS))
    const call = m.listCalls.at(-1)!
    expect(call.ids).toEqual([AGENTS])
    expect(call.opts).toMatchObject({ limit: 25, offset: 0, status: undefined })
    const nav = byId(host, 'ma-ops-pages')!
    expect(nav.tagName).toBe('NAV')
    expect(nav.getAttribute('aria-label')).toBe('Operations pages')
    expect(byId(host, 'ma-ops-range')!.textContent).toBe('1–25 of 60 operations · page 1 of 3')
    expect(host.querySelectorAll('[data-testid="ma-ops-table"] tbody tr')).toHaveLength(25)
    // The first page has nowhere to go back to.
    expect(byId(host, 'ma-ops-prev')).toBeNull()
    expect(byId(host, 'ma-ops-next')!.tagName).toBe('BUTTON')
  })

  it('walks forward to the last page and back', () => {
    m.lists = sixty
    const host = render(inBank(AGENTS))
    click(byId(host, 'ma-ops-next')!)
    expect(m.listCalls.at(-1)!.opts.offset).toBe(25)
    expect(byId(host, 'ma-ops-range')!.textContent).toBe('26–50 of 60 operations · page 2 of 3')
    click(byId(host, 'ma-ops-next')!)
    expect(byId(host, 'ma-ops-range')!.textContent).toBe('51–60 of 60 operations · page 3 of 3')
    expect(host.querySelectorAll('[data-testid="ma-ops-table"] tbody tr')).toHaveLength(10)
    expect(byId(host, 'ma-ops-next')).toBeNull()
    click(byId(host, 'ma-ops-prev')!)
    expect(m.listCalls.at(-1)!.opts.offset).toBe(25)
  })

  it('starts again from the first page when the filter changes', () => {
    m.lists = sixty
    const host = render(inBank(AGENTS))
    click(byId(host, 'ma-ops-next')!)
    click(segment(host, 'waiting'))
    expect(m.listCalls.at(-1)!.opts).toMatchObject({ offset: 0, status: 'pending' })
  })

  it('shows no page controls for a list that fits one page', () => {
    const host = render(inBank(AGENTS))
    expect(byId(host, 'ma-ops-range')!.textContent).toBe('1–4 of 4 operations')
    expect(byId(host, 'ma-ops-prev')).toBeNull()
    expect(byId(host, 'ma-ops-next')).toBeNull()
  })

  it('pages a merged list here, 25 rows at a time', () => {
    m.lists = (ids) =>
      ids.map((bankId) =>
        bankId === AGENTS ? entry(bankId, Array.from({ length: 40 }, (_, at) => op(`m-${at}`, at))) : entry(bankId, []),
      )
    const host = render()
    expect(m.listCalls.at(-1)!.opts).toMatchObject({ limit: 50, offset: 0 })
    expect(byId(host, 'ma-ops-range')!.textContent).toBe('1–25 of 40 operations · page 1 of 2')
    click(byId(host, 'ma-ops-next')!)
    expect(byId(host, 'ma-ops-range')!.textContent).toBe('26–40 of 40 operations · page 2 of 2')
    expect(host.querySelectorAll('[data-testid="ma-ops-table"] tbody tr')).toHaveLength(15)
    // The engine is not asked again: the offset is this page's own.
    expect(m.listCalls.at(-1)!.opts.offset).toBe(0)
  })
})

describe('retry', () => {
  beforeEach(() => {
    m.health = { data: healthBody({ extraction: 'clear' }) }
  })

  it('is offered on a failed row, and nowhere else', () => {
    const host = render()
    expect(byId(host, 'ma-ops-cause')).toBeNull()
    const buttons = allById(host, 'ma-ops-retry')
    expect(buttons.map((b) => b.closest('tr')!.getAttribute('data-op'))).toEqual(['a-3', 'a-4'])
    expect(buttons[0].className).toBe('btn ghost sm')
    expect((buttons[0] as HTMLButtonElement).disabled).toBe(false)
    expect(buttons[0].getAttribute('aria-label')).toBe('Retry: Store a batch, queued 2 h ago')
    expect(byId(host, 'ma-ops-list-blocked')).toBeNull()
  })

  it('retries one operation and says it was queued again', async () => {
    const host = render()
    click(rowOf(host, 'a-3').querySelector('[data-testid="ma-ops-retry"]')!)
    expect(m.retry.mutateAsync).toHaveBeenCalledWith({ bank: AGENTS, id: 'a-3' })
    await flush()
    const note = rowOf(host, 'a-3').querySelector('[data-testid="ma-ops-row-note"]')!
    expect(note.textContent).toBe('Queued again.')
    expect(note.getAttribute('role')).toBe('status')
    expect(m.refresh).toHaveBeenCalledWith([AGENTS])
  })

  it('shows a failed retry on its row as a sentence, never a raw body', async () => {
    m.retry.mutateAsync = vi.fn().mockRejectedValue(new Error('{"detail":"boom"}'))
    const host = render()
    click(rowOf(host, 'a-4').querySelector('[data-testid="ma-ops-retry"]')!)
    await flush()
    const note = rowOf(host, 'a-4').querySelector<HTMLElement>('[data-testid="ma-ops-row-note"]')!
    expect(note.dataset.kind).toBe('error')
    expect(note.textContent).toBe('Retry failed. The engine did not accept the request.')
    expect(note.textContent).not.toContain('{')
    // The other row is untouched.
    expect(rowOf(host, 'a-3').querySelector('[data-testid="ma-ops-row-note"]')).toBeNull()
  })

  it('retries every failed operation in the scope, one request each, and reports the count', async () => {
    m.collect = vi.fn().mockResolvedValue({
      targets: [
        { bank: AGENTS, id: 'a-3' },
        { bank: AGENTS, id: 'a-4' },
      ],
      more: false,
    })
    const host = render()
    const retryAll = byId(host, 'ma-ops-retry-all') as HTMLButtonElement
    expect(retryAll.textContent).toBe('Retry all failed')
    expect(retryAll.className).toBe('btn ghost')
    expect(retryAll.disabled).toBe(false)
    click(retryAll)
    // Only the banks that hold a failure are read.
    expect(m.collect).toHaveBeenCalledWith([AGENTS], 1000)
    await flush()
    expect(m.retryOp.mock.calls).toEqual([
      [AGENTS, 'a-3'],
      [AGENTS, 'a-4'],
    ])
    expect(m.discardOp).not.toHaveBeenCalled()
    const status = byId(host, 'ma-ops-run')!
    expect(status.getAttribute('role')).toBe('status')
    expect(status.textContent).toBe('Queued 2 failed operations again.')
    expect(m.refresh).toHaveBeenCalledWith([AGENTS])
  })

  it('shows progress while it runs, and holds both buttons', async () => {
    let release: () => void = () => {}
    m.collect = vi.fn().mockResolvedValue({ targets: [{ bank: AGENTS, id: 'a-3' }, { bank: AGENTS, id: 'a-4' }], more: false })
    m.retryOp = vi
      .fn()
      .mockResolvedValueOnce({})
      .mockImplementationOnce(() => new Promise<void>((resolve) => (release = resolve)))
    const host = render()
    click(byId(host, 'ma-ops-retry-all')!)
    await flush()
    expect(byId(host, 'ma-ops-run')!.textContent).toBe('Retrying 2 of 2.')
    expect((byId(host, 'ma-ops-retry-all') as HTMLButtonElement).disabled).toBe(true)
    expect((byId(host, 'ma-ops-discard') as HTMLButtonElement).disabled).toBe(true)
    release()
    await flush()
    expect(byId(host, 'ma-ops-run')!.textContent).toBe('Queued 2 failed operations again.')
    expect((byId(host, 'ma-ops-retry-all') as HTMLButtonElement).disabled).toBe(false)
  })

  it('stops at the first failure and reports where, how many were left, and why', async () => {
    m.collect = vi.fn().mockResolvedValue({
      targets: [1, 2, 3, 4].map((n) => ({ bank: AGENTS, id: `f-${n}` })),
      more: false,
    })
    m.retryOp = vi.fn().mockResolvedValueOnce({}).mockRejectedValueOnce(new Error('hindsight-api is not responding'))
    const host = render()
    click(byId(host, 'ma-ops-retry-all')!)
    await flush()
    expect(m.retryOp).toHaveBeenCalledTimes(2)
    expect(byId(host, 'ma-ops-run')!.textContent).toBe(
      'Stopped after 1 of 4. 3 operations were not retried. hindsight-api is not responding.',
    )
  })

  it('acts on the chosen bank only', async () => {
    const host = render(inBank(AGENTS))
    expect(byId(host, 'ma-ops-failed-count')!.textContent).toBe(`105 failed operations in ${AGENTS}`)
    click(byId(host, 'ma-ops-retry-all')!)
    expect(m.collect).toHaveBeenCalledWith([AGENTS], 1000)
    await flush()
    expect(byId(host, 'ma-ops-run')!.textContent).toBe('Nothing to retry: no failed operation was found.')
  })

  it('shows no failed card for a scope with nothing failed', () => {
    expect(byId(render(inBank('shared')), 'ma-ops-failed')).toBeNull()
  })
})

describe('discard', () => {
  const open = (host: HTMLElement) => {
    click(byId(host, 'ma-ops-discard')!)
    return byId(host, 'ma-ops-discard-confirm')!
  }

  it('opens a confirm in place of the buttons, and discards nothing yet', () => {
    const host = render()
    const confirm = open(host)
    expect(confirm.getAttribute('role')).toBe('group')
    expect(confirm.querySelector('h3')!.textContent).toBe('Discard 105 failed operations?')
    expect(textOf(confirm.querySelector('p'))).toBe(
      'These are 105 writes that were never stored. What agents sent in them will never become facts. Discarding cannot be undone.',
    )
    const [danger, keep] = [...confirm.querySelectorAll('button')]
    expect(danger.textContent).toBe('Discard 105 failed operations')
    expect(danger.className).toBe('btn danger')
    expect(keep.textContent).toBe('Keep them')
    // Focus starts on the choice that keeps the data.
    expect(host.ownerDocument.activeElement).toBe(keep)
    // The buttons it replaced are gone while it is open.
    expect(byId(host, 'ma-ops-discard')).toBeNull()
    expect(byId(host, 'ma-ops-retry-all')).toBeNull()
    expect(m.collect).not.toHaveBeenCalled()
    expect(m.discardOp).not.toHaveBeenCalled()
  })

  it('"Keep them" closes it, discards nothing, and returns focus to the button', () => {
    const host = render()
    const confirm = open(host)
    click([...confirm.querySelectorAll('button')][1])
    expect(byId(host, 'ma-ops-discard-confirm')).toBeNull()
    expect(m.collect).not.toHaveBeenCalled()
    expect(m.discardOp).not.toHaveBeenCalled()
    expect(host.ownerDocument.activeElement).toBe(byId(host, 'ma-ops-discard'))
  })

  it('confirming deletes each failed operation and reports the count', async () => {
    m.collect = vi.fn().mockResolvedValue({
      targets: [
        { bank: AGENTS, id: 'a-3' },
        { bank: AGENTS, id: 'a-4' },
      ],
      more: false,
    })
    const host = render()
    click(open(host).querySelector('button')!)
    expect(byId(host, 'ma-ops-discard-confirm')).toBeNull()
    await flush()
    expect(m.discardOp.mock.calls).toEqual([
      [AGENTS, 'a-3'],
      [AGENTS, 'a-4'],
    ])
    expect(m.retryOp).not.toHaveBeenCalled()
    expect(byId(host, 'ma-ops-run')!.textContent).toBe('Discarded 2 failed operations.')
    expect(m.refresh).toHaveBeenCalledWith([AGENTS])
  })

  it('reports a failure to read the failed operations as a sentence', async () => {
    m.collect = vi.fn().mockRejectedValue(new Error('<html>502</html>'))
    const host = render()
    click(open(host).querySelector('button')!)
    await flush()
    expect(byId(host, 'ma-ops-run')!.textContent).toBe('The failed operations could not be read.')
    expect(m.discardOp).not.toHaveBeenCalled()
  })

  it('names one thing in the singular, and its other button "Keep it"', () => {
    m.health = {
      data: healthBody({
        queue: { failed: 1, by_bank: [{ bank_id: AGENTS, waiting: 4, failed: 1, oldest_waiting_s: 60 }] },
      }),
    }
    const host = render()
    expect(byId(host, 'ma-ops-discard')!.textContent).toBe('Discard 1 failed operation')
    const confirm = open(host)
    expect(confirm.querySelector('h3')!.textContent).toBe('Discard 1 failed operation?')
    expect([...confirm.querySelectorAll('button')].map((b) => b.textContent)).toEqual([
      'Discard 1 failed operation',
      'Keep it',
    ])
  })
})

describe('the empty queue', () => {
  beforeEach(() => {
    m.health = { data: clearBody() }
    m.lists = (ids, opts) =>
      ids.map((bankId) => {
        const done = (OPS[bankId] ?? []).filter((o) => o.status === 'completed')
        return entry(bankId, opts.status && opts.status !== 'completed' ? [] : done)
      })
  })

  it('says nothing is waiting and nothing has failed, when the last operation finished, and what appears here', () => {
    const host = render()
    expect(state(host)).toBe('clear')
    const card = byId(host, 'ma-ops-clear')!
    expect(card.querySelector('h2')!.textContent).toBe('Nothing is waiting. Nothing has failed.')
    expect(card.querySelector('.chip')!.textContent).toBe('done')
    const text = textOf(card)
    expect(text).toContain('Every operation agents queued has been worked through.')
    expect(text).toContain('The last one finished 8 h ago.')
    expect(text).toContain('Operations appear below while they wait or run')
    expect(text).toContain('One that fails stays listed until you retry or discard it.')
    const when = byId(host, 'ma-ops-last-completed')!
    expect(when.tagName).toBe('TIME')
    expect(when.getAttribute('title')).toMatch(/^5 Nov 2026, \d\d:\d\d$/)
  })

  it('shows the figures as reported zeros, with units, and no failed card or cause', () => {
    const host = render()
    expect(textOf(stat(host, 'waiting'))).toBe('Waiting0operations')
    expect(textOf(stat(host, 'failed'))).toBe('Failed0operations')
    expect(textOf(stat(host, 'oldest'))).toBe('Oldest waitingnonenothing is waiting')
    expect(byId(host, 'ma-ops-failed')).toBeNull()
    expect(byId(host, 'ma-ops-cause')).toBeNull()
    expect(byId(host, 'ma-ops-banks')).toBeNull()
  })

  it('asks when the last operation finished only once the queue is known to be clear', () => {
    render()
    const asked = m.listCalls.filter((c) => c.opts.limit === 1)
    expect(asked.at(-1)!.opts).toMatchObject({ status: 'completed', limit: 1, enabled: true })
    cleanup()
    m.listCalls = []
    m.health = { data: healthBody() }
    render()
    expect(m.listCalls.filter((c) => c.opts.limit === 1).every((c) => c.opts.enabled === false)).toBe(true)
  })

  it('leaves the time out when no operation has ever finished, and says the queue is empty', () => {
    m.lists = (ids) => ids.map((bankId) => entry(bankId, []))
    const host = render()
    expect(byId(host, 'ma-ops-last-completed')).toBeNull()
    expect(textOf(byId(host, 'ma-ops-clear'))).not.toContain('The last one finished')
    const empty = byId(host, 'ma-ops-list-empty')!
    expect(empty.querySelector('h3')!.textContent).toBe('The queue is empty')
    expect(empty.querySelector('a')!.getAttribute('href')).toBe('#memory/health')
  })

  it('still lists the operations that finished', () => {
    const host = render()
    expect(rowOf(host, 's-2').querySelector('.chip')!.textContent).toBe('done')
  })
})

describe('scope', () => {
  it('offers every bank, with "All banks" first and chosen when the address names none', () => {
    const host = render()
    const radios = [...byId(host, 'ma-scope-bank')!.querySelectorAll<HTMLButtonElement>('[role="radio"]')]
    expect(radios.map((r) => r.textContent)).toEqual(['All banks', ...BANKS.map((b) => b.bank_id)])
    expect(radios[0].getAttribute('aria-checked')).toBe('true')
  })

  it('writes the chosen bank into the address, and nothing for all banks', () => {
    const replaceRoute = vi.fn()
    const host = render(inBank(AGENTS, { replaceRoute }))
    const radios = [...byId(host, 'ma-scope-bank')!.querySelectorAll<HTMLButtonElement>('[role="radio"]')]
    expect(radios[2].getAttribute('aria-checked')).toBe('true')
    click(radios[1])
    expect(replaceRoute).toHaveBeenLastCalledWith({ bank: 'shared' })
    click(radios[0])
    expect(replaceRoute).toHaveBeenLastCalledWith({ bank: null })
  })

  it('shows one bank’s figures from its row of the health answer and its own stats', () => {
    const host = render(inBank(AGENTS))
    expect(byId(host, 'ma-ops-strip')!.getAttribute('aria-label')).toBe(`Queue summary, ${AGENTS}`)
    expect(textOf(stat(host, 'waiting'))).toBe('Waiting978operations')
    expect(textOf(stat(host, 'failed'))).toBe('Failed105operations')
    expect(textOf(stat(host, 'consolidation-waiting'))).toContain('1,800facts')
    expect(m.listCalls.at(-1)!.ids).toEqual([AGENTS])
  })

  it('says so when the address names a bank the engine does not list', () => {
    const host = render(inBank('gone'))
    expect(state(host)).toBe('unknown-bank')
    expect(textOf(byId(host, 'ma-ops-unknown-bank'))).toContain('The address names gone, which the engine does not list.')
    expect(byId(host, 'ma-scope')).not.toBeNull()
    expect(host.querySelector('table')).toBeNull()
  })
})

describe('consolidate now', () => {
  it('is disabled with its reason while every bank is shown', () => {
    const host = render()
    const button = byId(host, 'ma-ops-consolidate') as HTMLButtonElement
    expect(button.textContent).toBe('Consolidate now')
    expect(button.className).toBe('btn ghost sm')
    expect(button.disabled).toBe(true)
    expect(host.ownerDocument.getElementById(button.getAttribute('aria-describedby')!)!.textContent).toBe(
      'Choose a bank to consolidate it.',
    )
  })

  it('queues a consolidation for the chosen bank and says so', () => {
    const host = render(inBank(AGENTS))
    const button = byId(host, 'ma-ops-consolidate') as HTMLButtonElement
    expect(button.disabled).toBe(false)
    click(button)
    expect(m.consolidate.mutate).toHaveBeenCalledTimes(1)
    const [bank, handlers] = m.consolidate.mutate.mock.calls[0]
    expect(bank).toBe(AGENTS)
    act(() => handlers.onSuccess())
    const status = byId(host, 'ma-ops-consolidate-status')!
    expect(status.getAttribute('role')).toBe('status')
    expect(status.textContent).toBe('Consolidation queued. It appears in this list as an operation.')
    expect(m.refresh).toHaveBeenCalledWith([AGENTS])
  })

  it('says why in a sentence when the engine refuses', () => {
    const host = render(inBank(AGENTS))
    click(byId(host, 'ma-ops-consolidate')!)
    const [, handlers] = m.consolidate.mutate.mock.calls[0]
    act(() => handlers.onError(new Error('[{"loc":["body"]}]')))
    expect(byId(host, 'ma-ops-consolidate-status')!.textContent).toBe(
      'The engine did not accept the request to consolidate.',
    )
  })
})

describe('the health request failing', () => {
  it('says the summary did not load, shows no figure, and keeps the list', () => {
    const refetch = vi.fn()
    m.health = { isError: true, refetch }
    const host = render()
    expect(state(host)).toBe('failed')
    const card = byId(host, 'ma-ops-summary-error')!
    expect(card.querySelector('h3')!.textContent).toBe('The queue summary did not load')
    expect(byId(host, 'ma-ops-strip')).toBeNull()
    expect(byId(host, 'ma-ops-failed')).toBeNull()
    expect(rowOf(host, 'a-1')).not.toBeNull()
    click(byId(host, 'ma-ops-summary-recheck')!)
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('treats a body that is not the route’s shape the same way, without throwing', () => {
    m.health = { data: {} }
    const host = render()
    expect(byId(host, 'ma-ops-summary-error')).not.toBeNull()
    expect(rowOf(host, 'a-1')).not.toBeNull()
  })
})
