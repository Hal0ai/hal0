// @vitest-environment happy-dom
//
// Memory ▸ Health as an ordered repair path (ADR-0058 decision 12): the verdict, the steps in the
// route's order with each step current in turn, the states with no path (loading, failed, off,
// another engine, unreachable), a partial read, the engine facts strip, the quiet embedder line
// and the banks table. The two strip hooks are replaced; the area object is built as MemoryArea
// builds it, from one health answer.
import { createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MemoryHealth, MemoryHealthReport } from '@/api/hooks/useMemory'
import {
  EMBEDDER_ID,
  GRAPH,
  OLD_EMBEDDER_ID,
  healthReport,
  healthUnreachable,
  makeArea,
  modelsAnswer,
  pathSteps,
} from './area-test-fixtures'
import { cleanup, click, mount } from './shared/test-harness'

const m = vi.hoisted(() => ({
  graph: {} as { data?: unknown; isError?: boolean },
  models: {} as { data?: unknown; isError?: boolean },
}))

vi.mock('@/api/hooks/useMemory', () => ({
  useMemoryGraphStatus: () => m.graph,
  useMemoryModels: () => m.models,
}))

const { HealthTab } = await import('./HealthTab.jsx')

beforeEach(() => {
  m.graph = { data: GRAPH }
  m.models = { data: modelsAnswer() }
})
afterEach(cleanup)

const render = (area = makeArea()) => mount(createElement(HealthTab, { area })).host
const withReport = (report: MemoryHealth) => render(makeArea({ health: report }))
const byId = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const textOf = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
const rows = (host: HTMLElement) => [...host.querySelectorAll<HTMLElement>('[data-testid="ma-health-path"] > li')]
const row = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-testid="ma-health-path"] > li[data-step="${id}"]`)!
const stat = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-stat="${id}"]`)!

/** Nothing the route wrote reaches the page raw: no reason code, no figure key, no JSON. */
function expectNoRawRouteText(host: HTMLElement) {
  const text = host.textContent ?? ''
  expect(text).not.toMatch(/[a-z]+_[a-z_]+/) // reason codes and figure keys
  expect(text).not.toMatch(/[{}[\]]/)
  expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}T/)
}

/** No primary-styled control anywhere on the page: every remedy today is a link or a command. */
/** No figure: the only digit allowed is the product's own name. */
const expectNoFigure = (host: HTMLElement) => expect((host.textContent ?? '').replace(/hal0/g, '')).not.toMatch(/\d/)

function expectNoPrimary(host: HTMLElement) {
  expect(host.querySelector('.primary, [class*="btn-primary"]')).toBeNull()
}

// ── Fixtures for each step current in turn ──────────────────────────────────

const WINDOW = { window_s: 21600 }
const QUEUE_LIMIT = { max_waiting_age_s: 1800 }
const TOP_ERRORS = [
  { message: 'Task exceeded the <n>s wall-clock limit', count: 3 },
  { message: 'HTTP 503 from <host>', count: 1 },
]

/** Extraction current; the queue and knowledge wait on it, in order. */
function extractionFirst(): MemoryHealthReport {
  const base = healthReport()
  return healthReport({
    steps: pathSteps({
      extraction: {
        reason: 'extraction_recent_failures',
        figures: { slot_resolves: true, llm_reachable: true, failures: 4, ...WINDOW, top_errors: TOP_ERRORS },
      },
      queue: {
        reason: 'queue_operations_failed',
        figures: { failed: 3, waiting: 52, oldest_waiting_s: 5400, ...QUEUE_LIMIT },
      },
      knowledge: { reason: 'knowledge_refresh_failed', figures: { pages_stale: 4, models_stale: 0, refresh_failed: 1 } },
    }),
    extraction: { ...base.extraction!, failures_6h: 4, top_errors: TOP_ERRORS },
    queue: { ...base.queue!, waiting: 52, failed: 3, oldest_waiting_s: 5400 },
    knowledge: { ...base.knowledge!, pages_stale: 4, refresh_failed: 1 },
  })
}

function queueAlone(): MemoryHealthReport {
  const base = healthReport()
  return healthReport({
    steps: pathSteps({
      queue: { reason: 'queue_operations_failed', figures: { failed: 1, waiting: 0, oldest_waiting_s: null, ...QUEUE_LIMIT } },
    }),
    queue: { ...base.queue!, failed: 1 },
  })
}

function embedderStep(reason: 'embedder_mixed' | 'embedder_conflict' | 'embedder_needs_switch') {
  const conflict = reason === 'embedder_conflict' ? { live: OLD_EMBEDDER_ID } : null
  const mixed = reason === 'embedder_mixed'
  const configured = reason === 'embedder_needs_switch' ? { provider: 'local', model: 'operator/embed-2' } : EMBEDDER_ID
  return healthReport({
    steps: pathSteps({
      embedder: {
        reason,
        figures: {
          recorded: EMBEDDER_ID,
          configured,
          mixed,
          conflict,
          reasons: [reason.replace('embedder_', '')],
          informational: false,
        },
      },
    }),
    embedder: { recorded: EMBEDDER_ID, configured, mixed, needs_switch: mixed, conflict, informational: false },
  })
}

function knowledgeAlone(): MemoryHealthReport {
  const base = healthReport()
  return healthReport({
    steps: pathSteps({
      knowledge: { reason: 'knowledge_stale', figures: { pages_stale: 5, models_stale: 6, refresh_failed: 0 } },
    }),
    knowledge: { ...base.knowledge!, pages_stale: 5, models_stale: 6 },
  })
}

// ── States with no path ─────────────────────────────────────────────────────

describe('loading', () => {
  it('shows skeletons and not one figure', () => {
    const host = render(makeArea({ healthQuery: {} }))
    expect(byId(host, 'ma-health-loading')).not.toBeNull()
    expect(host.querySelectorAll('.ma-skel').length).toBeGreaterThan(4)
    expect(host.textContent).not.toMatch(/\d/)
    expect(host.querySelector('table')).toBeNull()
    expect(byId(host, 'ma-health-path')).toBeNull()
    expect([...host.querySelectorAll('.ma-stat-label')].map((el) => el.textContent)).toEqual([
      'Engine',
      'Extraction',
      'Embedder',
      'Reranker',
      'Decision model',
    ])
  })

  it('keeps the strip busy, not zero, until the older reads answer; the verdict does not wait', () => {
    m.graph = {}
    const host = render()
    expect(byId(host, 'ma-health-strip')!.getAttribute('aria-busy')).toBe('true')
    expect(byId(host, 'ma-health-summary')!.dataset.state).toBe('clear')
  })
})

describe('hal0 did not answer', () => {
  it('says so, shows no verdict and no figure, and offers to check again', () => {
    const refetch = vi.fn()
    const host = render(makeArea({ healthQuery: { isError: true, refetch } }))
    expect(textOf(byId(host, 'ma-health-failed'))).toContain("Memory's health did not load")
    expectNoFigure(host)
    click(byId(host, 'ma-health-recheck')!)
    expect(refetch).toHaveBeenCalledTimes(1)
  })
})

describe('memory off, or another engine', () => {
  it('memory off: says so and links to engine settings', () => {
    const host = withReport({
      enabled: false,
      reason: 'memory_disabled',
      checked_at: '2026-11-11T03:00:00+00:00',
      verdict: null,
      partial: [],
      steps: [],
    })
    const card = byId(host, 'ma-health-off')!
    expect(textOf(card)).toContain('Memory is turned off')
    expect(card.querySelector('a')!.getAttribute('href')).toBe('#settings/memory')
    expect(byId(host, 'ma-health-path')).toBeNull()
    expect(host.textContent).not.toMatch(/\d/)
  })

  it('a non-Hindsight engine: the fallback notice, no path', () => {
    const host = withReport({
      enabled: true,
      reason: 'engine_unsupported',
      checked_at: '2026-11-11T03:00:00+00:00',
      verdict: null,
      partial: [],
      steps: [],
    })
    expect(textOf(byId(host, 'ma-health-fallback'))).toContain('Memory is not running on Hindsight')
    expect(byId(host, 'ma-health-path')).toBeNull()
  })
})

describe('engine unreachable', () => {
  it('says what is known and what is not, with no figure anywhere', () => {
    const host = withReport(healthUnreachable())
    const card = byId(host, 'ma-health-unreachable')!
    expect(card.querySelector('h2')!.textContent).toBe('The memory engine is not answering')
    expect(textOf(host)).toContain('What is known')
    expect(textOf(host)).toContain('What is not known until it answers')
    expect(host.querySelector('table')).toBeNull()
    expectNoFigure(host)
    expectNoRawRouteText(host)
  })

  it('links to Services, gives hal0 doctor to copy, and checks again', () => {
    const refetch = vi.fn()
    const host = render(makeArea({ healthQuery: { data: healthUnreachable(), refetch } }))
    expect(byId(host, 'ma-health-services')!.getAttribute('href')).toBe('#services')
    expect(byId(host, 'ma-health-doctor')!.querySelector('code')!.textContent).toBe('hal0 doctor')
    click(byId(host, 'ma-health-recheck')!)
    expect(refetch).toHaveBeenCalledTimes(1)
    expectNoPrimary(host)
  })
})

// ── The verdict and the path ────────────────────────────────────────────────

describe('clear', () => {
  it('says nothing needs attention, and in one sentence what was checked', () => {
    const host = render()
    const summary = byId(host, 'ma-health-summary')!
    expect(summary.dataset.state).toBe('clear')
    expect(summary.querySelector('h2')!.textContent).toBe('Nothing needs attention')
    expect(textOf(byId(host, 'ma-health-detail'))).toBe(
      'The engine answers, and the 4 things this page watches are clear: storing memories, the queue, the embedder and knowledge.',
    )
    expect(summary.querySelector('.chip')!.textContent).toBe('working')
    expect(byId(host, 'ma-health-qualifier')).toBeNull()
  })

  it('lists what was checked as one quiet line each, unnumbered', () => {
    const host = render()
    const list = byId(host, 'ma-health-path')!
    expect(list.tagName).toBe('UL')
    expect(rows(host).map((li) => li.dataset.step)).toEqual(['extraction', 'queue', 'embedder', 'knowledge'])
    for (const li of rows(host)) {
      expect(li.dataset.state).toBe('clear')
      expect(li.querySelector('p.ma-step-line')).not.toBeNull()
      expect(li.hasAttribute('aria-current')).toBe(false)
    }
    expect(host.textContent).not.toMatch(/\bstep \d/i)
    expectNoRawRouteText(host)
    expectNoPrimary(host)
  })
})

describe('attention: extraction current, queue and knowledge waiting', () => {
  it('counts the open steps in words and names the current one', () => {
    const host = withReport(extractionFirst())
    const summary = byId(host, 'ma-health-summary')!
    expect(summary.dataset.state).toBe('attention')
    expect(summary.querySelector('h2')!.textContent).toBe('3 things need you')
    expect(textOf(byId(host, 'ma-health-detail'))).toBe(
      'Start with step 1, storing memories: fix what is failing when memories are stored. The other 2 wait on it, in the order below.',
    )
  })

  it('is an ordered list in the route order, with the position and state of each row', () => {
    const host = withReport(extractionFirst())
    expect(byId(host, 'ma-health-path')!.tagName).toBe('OL')
    expect(rows(host).map((li) => [li.dataset.step, li.dataset.state])).toEqual([
      ['extraction', 'current'],
      ['queue', 'waiting'],
      ['embedder', 'clear'],
      ['knowledge', 'waiting'],
    ])
    expect(textOf(row(host, 'extraction'))).toContain('Step 1 of 4 · do this now')
    expect(textOf(row(host, 'embedder'))).toContain('Step 3 of 4 · clear')
  })

  it('marks the current row by more than colour: words, aria-current and a raised row', () => {
    const host = withReport(extractionFirst())
    const current = row(host, 'extraction')
    expect(current.getAttribute('aria-current')).toBe('step')
    expect(current.classList.contains('ma-step-current')).toBe(true)
    expect(host.querySelectorAll('[aria-current="step"]')).toHaveLength(1)
  })

  it('writes each open step from its reason code and figures', () => {
    const host = withReport(extractionFirst())
    expect(textOf(byId(host, 'ma-health-step-extraction-text'))).toBe(
      '4 attempts to store a memory failed in the last 6 h. Until the cause is fixed, operations wait in the queue and a retry would fail.',
    )
    expect(textOf(byId(host, 'ma-health-step-queue-text'))).toBe(
      '3 operations have failed and 52 are waiting, the oldest for 1 h. Retry or discard them on the Operations tab.',
    )
    expect(textOf(byId(host, 'ma-health-step-knowledge-text'))).toBe(
      '1 knowledge page or mental model failed its last refresh, and 4 are stale. The last refresh that worked was on 5 Nov 2026.',
    )
    expectNoRawRouteText(host)
  })

  it('says in words what a waiting step waits on', () => {
    const host = withReport(extractionFirst())
    expect(textOf(byId(host, 'ma-health-step-queue-waits'))).toBe('Waiting on step 1: storing memories.')
    expect(textOf(byId(host, 'ma-health-step-knowledge-waits'))).toBe('Waiting on step 2: the queue.')
  })

  it('gives extraction its links and the grouped errors, and no primary control', () => {
    const host = withReport(extractionFirst())
    expect(byId(host, 'ma-health-step-extraction-services')!.getAttribute('href')).toBe('#services')
    expect(byId(host, 'ma-health-step-extraction-settings')!.getAttribute('href')).toBe('#settings/memory')
    const errors = byId(host, 'ma-health-step-extraction-errors')!
    expect([...errors.querySelectorAll('li')].map(textOf)).toEqual([
      'Task exceeded the Ns wall-clock limit seen 3 times',
      'HTTP 503 from a host seen 1 time',
    ])
    expectNoPrimary(host)
  })

  it('the queue and knowledge rows link to their tabs', () => {
    const host = withReport(extractionFirst())
    expect(byId(host, 'ma-health-step-queue-operations')!.getAttribute('href')).toBe('#memory/operations')
    expect(byId(host, 'ma-health-step-knowledge-knowledge')!.getAttribute('href')).toBe('#memory/knowledge')
    expect(textOf(byId(host, 'ma-health-step-queue-operations'))).toContain('Open Operations')
  })
})

describe('attention: each later step current in turn', () => {
  it('the queue alone', () => {
    const host = withReport(queueAlone())
    expect(byId(host, 'ma-health-summary')!.querySelector('h2')!.textContent).toBe('1 thing needs you')
    expect(textOf(byId(host, 'ma-health-detail'))).toBe(
      'It is step 2, the queue: retry or discard the failed operations.',
    )
    expect(row(host, 'queue').dataset.state).toBe('current')
    expect(textOf(byId(host, 'ma-health-step-queue-text'))).toBe(
      '1 operation has failed, and nothing is waiting. Retry or discard it on the Operations tab.',
    )
    expect(byId(host, 'ma-health-step-queue-operations')).not.toBeNull()
    expectNoPrimary(host)
  })

  it('the embedder, for a mixed store: both models and the switch as a dry run to copy', () => {
    const host = withReport(embedderStep('embedder_mixed'))
    expect(row(host, 'embedder').dataset.state).toBe('current')
    expect(textOf(byId(host, 'ma-health-step-embedder-text'))).toContain('may hold vectors from two embedders')
    expect(textOf(byId(host, 'ma-health-step-embedder-text'))).toContain('hs-embed-1')
    expect(byId(host, 'ma-health-step-embedder-command')!.querySelector('code')!.textContent).toBe(
      'hal0 memory embedder switch hs-embed-1',
    )
    expect(textOf(row(host, 'embedder'))).toContain('It is a dry run by default')
    expect(textOf(row(host, 'embedder'))).toContain('changes nothing')
    // Nothing on the row runs the switch: the only button copies the command.
    const buttons = [...row(host, 'embedder').querySelectorAll('button')]
    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual(['Copy command'])
    expectNoPrimary(host)
  })

  it('the embedder, for a conflict: the record and the running engine, and hal0 doctor', () => {
    const host = withReport(embedderStep('embedder_conflict'))
    const text = textOf(byId(host, 'ma-health-step-embedder-text'))
    expect(text).toBe(
      'The embedder record says hs-embed-1, but the running engine is on BAAI/bge-small-en-v1.5. hal0 has changed neither. Remove the override or correct the record so the two agree.',
    )
    expect(textOf(row(host, 'embedder'))).toContain('conflict')
    expect(byId(host, 'ma-health-step-embedder-command')!.querySelector('code')!.textContent).toBe('hal0 doctor')
  })

  it('the embedder, when settings ask for another: the switch towards the configured model', () => {
    const host = withReport(embedderStep('embedder_needs_switch'))
    expect(byId(host, 'ma-health-step-embedder-command')!.querySelector('code')!.textContent).toBe(
      'hal0 memory embedder switch operator/embed-2',
    )
  })

  it('knowledge alone', () => {
    const host = withReport(knowledgeAlone())
    expect(row(host, 'knowledge').dataset.state).toBe('current')
    expect(textOf(byId(host, 'ma-health-step-knowledge-text'))).toBe(
      '5 knowledge pages and 6 mental models are stale: behind the facts they were built from. No refresh has failed.',
    )
    expect(byId(host, 'ma-health-step-knowledge-knowledge')).not.toBeNull()
    expectNoPrimary(host)
  })

  it('a reason code the page does not know: a generic sentence and its figures, never the code', () => {
    const host = withReport(
      healthReport({
        steps: pathSteps({ queue: { reason: 'queue_brand_new_reason', figures: { failed: 2, stuck_for_s: 4000 } } }),
      }),
    )
    const current = row(host, 'queue')
    expect(textOf(current)).toContain('this page has no description for its cause yet')
    expect(textOf(byId(host, 'ma-health-step-queue-figures'))).toContain('Failed operations2')
    expect(textOf(current)).not.toContain('queue_brand_new_reason')
    expectNoRawRouteText(host)
  })
})

describe('partial and truncated reads', () => {
  it('a partial read is never an unqualified all clear', () => {
    const host = withReport(healthReport({ partial: ['queue', 'banks'] }))
    const summary = byId(host, 'ma-health-summary')!
    expect(summary.querySelector('h2')!.textContent).toBe('Nothing needs attention in what was read')
    expect(summary.querySelector('.chip')).toBeNull()
    expect(textOf(byId(host, 'ma-health-qualifier'))).toBe(
      'Some banks did not answer in time; figures are what was read.',
    )
    // Beside the figures it touches: the queue row and the banks table.
    expect(textOf(byId(host, 'ma-health-step-queue-note'))).toContain('did not answer in time')
    expect(textOf(byId(host, 'ma-health-banks-partial'))).toContain('did not answer in time')
  })

  it('a truncated queue says so beside the queue and extraction figures', () => {
    const base = healthReport()
    const host = withReport(healthReport({ queue: { ...base.queue!, truncated: true } }))
    expect(textOf(byId(host, 'ma-health-step-queue-note'))).toContain('more operations than were read')
    expect(textOf(byId(host, 'ma-health-step-extraction-note'))).toContain('more operations than were read')
  })

  it('a bank whose figures were not read shows the mark, never zero', () => {
    const base = healthReport()
    const host = withReport(
      healthReport({
        partial: ['queue'],
        queue: {
          ...base.queue!,
          by_bank: base.queue!.by_bank.map((b) =>
            b.bank_id === 'shared' ? { ...b, waiting: null, failed: null, oldest_waiting_s: null } : b,
          ),
        },
      }),
    )
    const shared = host.querySelector('[data-bank="shared"]')!
    expect(shared.querySelector('[data-col="waiting"] .ma-missing')).not.toBeNull()
    expect(shared.querySelector('[data-col="failed"]')!.textContent).toBe('–')
    expect(byId(host, 'ma-health-banks-foot')).not.toBeNull()
  })
})

// ── Below the path ──────────────────────────────────────────────────────────

describe('engine facts strip', () => {
  it('is five cells in order, the first three from the health answer', () => {
    const host = render()
    expect(textOf(stat(host, 'engine'))).toContain('Hindsight 1.2.0')
    expect(textOf(stat(host, 'extraction'))).toContain('memory slot')
    expect(textOf(stat(host, 'embedder'))).toContain('hs-embed-1')
    expect(textOf(stat(host, 'reranker'))).toContain('hs-rerank-1')
    expect(textOf(stat(host, 'decision'))).toContain('Off')
  })
})

describe('the ordinary upgraded box', () => {
  it('gets one quiet line from the route embedder fields, and stays clear', () => {
    const host = withReport(
      healthReport({
        embedder: {
          recorded: OLD_EMBEDDER_ID,
          configured: EMBEDDER_ID,
          mixed: false,
          needs_switch: false,
          conflict: null,
          informational: true,
        },
      }),
    )
    const line = byId(host, 'ma-health-embedder')!
    expect(line.dataset.kind).toBe('quiet')
    expect(textOf(line)).toBe(
      'This store was built with BAAI/bge-small-en-v1.5 and the engine stays on it. Settings ask for hs-embed-1, which has no effect until the store is re-embedded.',
    )
    expect(line.querySelector('.chip, button')).toBeNull()
    expect(byId(host, 'ma-health-summary')!.dataset.state).toBe('clear')
  })

  it('is not shown on a box whose store and settings agree', () => {
    expect(byId(render(), 'ma-health-embedder')).toBeNull()
  })
})

describe('banks table', () => {
  it('a row per bank, largest first, every figure from the health answer', () => {
    const host = render()
    const bankRows = [...host.querySelectorAll<HTMLElement>('[data-testid="ma-health-banks"] tbody tr')]
    expect(bankRows.map((tr) => tr.dataset.bank)).toEqual(['Hal0::Agents', 'shared', 'coding-agent::ultrareview-1.4'])
    expect(textOf(bankRows[0].querySelector('[data-col="facts"]'))).toBe('24,530')
    expect(textOf(bankRows[0].querySelector('[data-col="documents"]'))).toBe('4,100')
    expect(bankRows[0].querySelector('a')!.getAttribute('href')).toBe('#memory/facts?bank=Hal0::Agents')
  })

  it('says when the answer could not list the banks', () => {
    const host = withReport(healthReport({ banks: { layout: 'per_bank', total: null, facts: null, list: [] } }))
    expect(byId(host, 'ma-health-banks-error')).not.toBeNull()
  })

  it('says when there are no banks yet', () => {
    const base = healthReport()
    const host = withReport(
      healthReport({
        banks: { layout: 'per_bank', total: 0, facts: 0, list: [] },
        queue: { ...base.queue!, by_bank: [] },
      }),
    )
    expect(byId(host, 'ma-health-banks-empty')).not.toBeNull()
  })
})
