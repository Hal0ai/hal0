// @vitest-environment happy-dom
//
// Memory ▸ Health, the interim view: each of its states, the copy of each embedder state, and
// the banks table. The hooks are replaced; the area object is built as MemoryArea builds it.
import { createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MemoryBank } from '@/api/hooks/useHindsight'
import type { MentalModelsEntry } from './health-pure'
import {
  BANKS,
  ENGINE,
  GRAPH,
  STATS,
  bankStats,
  embedderRecord,
  makeArea,
  mentalModel,
  modelsAnswer,
  type FakeQuery,
} from './area-test-fixtures'
import { cleanup, click, mount } from './shared/test-harness'

const m = vi.hoisted(() => ({
  graph: {} as { data?: unknown; isError?: boolean },
  models: {} as { data?: unknown; isError?: boolean },
  mental: null as null | ((ids: string[]) => unknown[]),
  mentalFor: [] as string[][],
}))

vi.mock('@/api/hooks/useMemory', () => ({
  useMemoryGraphStatus: () => m.graph,
  useMemoryModels: () => m.models,
}))
vi.mock('./area-data', () => ({
  useAllBankStats: () => [],
  useAllMentalModels: (ids: string[]) => {
    m.mentalFor.push(ids)
    return m.mental ? m.mental(ids) : ids.map((bankId) => ({ bankId, models: [] }))
  },
}))

const { HealthTab } = await import('./HealthTab.jsx')

beforeEach(() => {
  m.graph = { data: GRAPH }
  m.models = { data: modelsAnswer() }
  m.mental = null
  m.mentalFor = []
})
afterEach(cleanup)

const render = (area = makeArea()) => mount(createElement(HealthTab, { area })).host
const byId = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const stat = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-stat="${id}"]`)!
const textOf = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()

/** Every state: nothing on this page is called a step, and nothing puts the page in an order. */
function expectNoStepWording(host: HTMLElement) {
  expect(host.textContent).not.toMatch(/\bstep\b/i)
  expect(host.textContent).not.toMatch(/do this now|waiting on step/i)
}

describe('loading', () => {
  it('shows skeletons and not one figure', () => {
    const host = render(makeArea({ engineQuery: {} }))
    expect(byId(host, 'ma-health-loading')).not.toBeNull()
    expect(host.querySelectorAll('.ma-skel').length).toBeGreaterThan(4)
    // No digit anywhere: a loading page never shows a zero.
    expect(host.textContent).not.toMatch(/\d/)
    expect(host.querySelector('table')).toBeNull()
    // The five cells keep their labels while their values load.
    expect([...host.querySelectorAll('.ma-stat-label')].map((el) => el.textContent)).toEqual([
      'Engine',
      'Extraction',
      'Embedder',
      'Reranker',
      'Decision model',
    ])
    expectNoStepWording(host)
  })

  it('keeps the strip and the summary as skeletons until the extraction and model routes answer', () => {
    m.graph = {}
    const host = render()
    expect(byId(host, 'ma-health-strip')!.getAttribute('aria-busy')).toBe('true')
    expect(stat(host, 'engine').textContent).not.toContain('Hindsight')
    expect(byId(host, 'ma-health-summary')!.dataset.state).toBe('loading')
    expect(byId(host, 'ma-health-embedder')).toBeNull()
  })

  it('draws a skeleton, not a zero, in a bank row whose stats are still loading', () => {
    const stats = BANKS.map((b) => ({ bankId: b.bank_id, stats: null, isLoading: true }))
    const host = render(makeArea({ stats }))
    const cells = [...host.querySelectorAll('[data-testid="ma-health-banks"] tbody td')]
    expect(cells).toHaveLength(BANKS.length * 4)
    for (const cell of cells) {
      expect(cell.querySelector('.ma-skel')).not.toBeNull()
      expect(cell.textContent).toBe('')
    }
    expect(byId(host, 'ma-health-summary')!.dataset.state).toBe('loading')
    expect(byId(host, 'ma-health-banks-foot')).toBeNull()
  })
})

describe('engine unreachable', () => {
  const down = (over: Partial<FakeQuery<typeof ENGINE>> = {}) =>
    makeArea({
      engineQuery: { data: { ...ENGINE, reachable: false, version: null, banks_total: null }, ...over },
      banksQuery: { isError: true },
    })

  it('says what is known and what is not, and shows no figure', () => {
    const host = render(down())
    const card = byId(host, 'ma-health-unreachable')!
    expect(card.querySelector('h2')!.textContent).toBe('The memory engine is not answering')
    expect(card.querySelector('.chip')!.textContent).toBe('unreachable')
    expect(textOf(card)).toContain('Nothing here is counted as zero')
    expect(textOf(host)).toContain('What is known')
    expect(textOf(host)).toContain("Hindsight did not answer this dashboard's check.")
    expect(textOf(host)).toContain('What is not known until it answers')
    for (const key of ['Banks', 'Queue', 'Knowledge', 'Engine facts']) {
      expect([...host.querySelectorAll('dt')].map((dt) => dt.textContent)).toContain(key)
    }
    // No figure at all (the product's name is the only digit on the page).
    expect(host.textContent!.replace(/hal0/g, '')).not.toMatch(/\d/)
    expect(host.querySelector('table')).toBeNull()
    expect(byId(host, 'ma-health-strip')).toBeNull()
    expectNoStepWording(host)
  })

  it('links to Services and gives the doctor command to copy', () => {
    const host = render(down())
    expect(byId(host, 'ma-health-services')!.getAttribute('href')).toBe('#services')
    const box = byId(host, 'ma-health-doctor')!
    expect(box.querySelector('code')!.textContent).toBe('hal0 doctor')
    expect(box.querySelector('button')!.getAttribute('aria-label')).toBe('Copy command')
  })

  it('shows the error the check came back with, when there is one', () => {
    const host = render(makeArea({ engineQuery: { isError: true, error: { message: 'HTTP 503' } } }))
    expect(byId(host, 'ma-health-unreachable')).not.toBeNull()
    const rows = [...host.querySelectorAll('.ma-kv-row')].map(textOf)
    expect(rows).toContain('ErrorHTTP 503')
  })

  it('checks again on request', () => {
    const refetch = vi.fn()
    const host = render(down({ refetch }))
    click(byId(host, 'ma-health-recheck')!)
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('never asks the routes that only a reachable engine answers', () => {
    render(down())
    expect(m.mentalFor).toEqual([])
  })
})

describe('memory engine is not Hindsight', () => {
  it('keeps the wording of the notice the old page showed', () => {
    const host = render(makeArea({ engineQuery: { data: { ...ENGINE, engine: null, reachable: false } } }))
    const card = byId(host, 'ma-health-fallback')!
    expect(textOf(card)).toContain(
      "Memory is running on a non-Hindsight fallback engine — volatile, writes don't survive restart. " +
        "Graph extraction, reflect, and directives aren't available either.",
    )
    expect(card.querySelector('a')!.getAttribute('href')).toBe('#settings/memory')
    expect(host.querySelector('table')).toBeNull()
    expectNoStepWording(host)
  })
})

describe('all clear', () => {
  it('says nothing needs attention', () => {
    const host = render()
    const summary = byId(host, 'ma-health-summary')!
    expect(summary.dataset.state).toBe('clear')
    expect(summary.querySelector('h2')!.textContent).toBe('Nothing needs attention')
    expect(summary.querySelector('.chip')!.textContent).toBe('working')
    expect(byId(host, 'ma-health-findings')).toBeNull()
    expect(byId(host, 'ma-health-embedder')).toBeNull()
    expectNoStepWording(host)
  })

  it('shows the five engine facts', () => {
    const host = render()
    expect(textOf(stat(host, 'engine'))).toBe('EngineHindsight 1.2.0reachablememory engine')
    expect(textOf(stat(host, 'extraction'))).toBe('Extractionmemory slotdedicated slot · resolves to a model')
    expect(textOf(stat(host, 'embedder'))).toBe('Embedderhs-embed-1the model this store was built with')
    expect(textOf(stat(host, 'reranker'))).toBe('Rerankerhs-rerank-1local provider')
    expect(textOf(stat(host, 'decision'))).toBe('Decision modelOffoptional component, not enabled')
  })

  it('has one line for the queue and one for knowledge, each said to be a sum, each with its link', () => {
    m.mental = (ids) =>
      ids.map((bankId, at) => ({ bankId, models: at === 0 ? [mentalModel({ is_stale: true }), mentalModel()] : [] }))
    const stats = BANKS.map((b, at) => ({
      bankId: b.bank_id,
      stats: bankStats(b.bank_id, { ...STATS[b.bank_id], pending_operations: at === 0 ? 978 : 115 }),
    }))
    const host = render(makeArea({ stats, hash: '#memory/health?bank=shared' }))
    expect(textOf(byId(host, 'ma-health-work')!.querySelector('.ma-card-meta'))).toBe(
      'each line is a sum of per-bank figures',
    )
    const queue = byId(host, 'ma-health-queue')!
    expect(textOf(queue.querySelector('dd'))).toBe('1,208 operations waiting and 0 failed, summed across 3 banks.')
    expect(queue.querySelector('a')!.getAttribute('href')).toBe('#memory/operations?bank=shared')
    expect(textOf(queue.querySelector('a'))).toBe('Open Operations')
    const knowledge = byId(host, 'ma-health-knowledge')!
    expect(textOf(knowledge.querySelector('dd'))).toBe('1 of 2 mental models are stale, summed across 3 banks.')
    expect(knowledge.querySelector('a')!.getAttribute('href')).toBe('#memory/knowledge?bank=shared')
    // Operations waiting and a stale model are normal: still clear.
    expect(byId(host, 'ma-health-summary')!.dataset.state).toBe('clear')
  })

  it('asks for the mental models of every listed bank', () => {
    render()
    expect(m.mentalFor.at(-1)!.slice().sort()).toEqual(BANKS.map((b) => b.bank_id).sort())
  })

  it('draws no chart', () => {
    const host = render()
    expect(host.querySelector('canvas')).toBeNull()
    // The only drawings are the arrows of the two go-links.
    expect(host.querySelectorAll('svg')).toHaveLength(2)
  })
})

describe('banks table', () => {
  it('is a real table with header cells, in a scroll region the keyboard can reach', () => {
    const host = render()
    const region = host.querySelector('.ma-table-region')!
    expect(region.getAttribute('role')).toBe('region')
    expect(region.getAttribute('aria-label')).toBe('Banks table')
    expect(region.getAttribute('tabindex')).toBe('0')
    const table = region.querySelector('table')!
    const heads = [...table.querySelectorAll('thead th')]
    expect(heads.map((th) => th.textContent)).toEqual(['Bank', 'Facts', 'Documents', 'Waiting', 'Failed'])
    for (const th of heads) expect(th.getAttribute('scope')).toBe('col')
    for (const th of table.querySelectorAll('tbody th')) expect(th.getAttribute('scope')).toBe('row')
  })

  it('lists the banks as they are, largest first, each linking to its facts', () => {
    const host = render()
    const rows = [...host.querySelectorAll('[data-testid="ma-health-banks"] tbody tr')]
    expect(rows.map((tr) => tr.querySelector('th')!.textContent)).toEqual([
      'Hal0::Agents',
      'shared',
      'coding-agent::ultrareview-1.4',
    ])
    expect(rows.map((tr) => tr.querySelector('a')!.getAttribute('href'))).toEqual([
      '#memory/facts?bank=Hal0::Agents',
      '#memory/facts?bank=shared',
      '#memory/facts?bank=coding-agent::ultrareview-1.4',
    ])
    expect([...rows[0].querySelectorAll('td')].map((td) => td.textContent)).toEqual(['24,530', '4,100', '0', '0'])
    expect(byId(host, 'ma-health-banks-foot')).toBeNull()
  })

  it('marks a figure that was not reported, and says what the mark means', () => {
    const stats = [
      { bankId: 'shared', stats: STATS.shared },
      { bankId: 'Hal0::Agents', stats: null, isError: true },
      { bankId: 'coding-agent::ultrareview-1.4', stats: STATS['coding-agent::ultrareview-1.4'] },
    ]
    const host = render(makeArea({ stats }))
    const row = host.querySelector('tr[data-bank="Hal0::Agents"]')!
    const marks = [...row.querySelectorAll('td [role="img"]')]
    expect(marks).toHaveLength(4)
    for (const mark of marks) {
      expect(mark.getAttribute('aria-label')).toBe('not reported')
      expect(mark.textContent).toBe('–')
    }
    expect(textOf(byId(host, 'ma-health-banks-foot'))).toBe('– means the engine did not report a figure')
    // A sum that leaves a bank out says so.
    expect(textOf(byId(host, 'ma-health-queue')!.querySelector('dd'))).toBe(
      '0 operations waiting and 0 failed, summed across the 2 of 3 banks that reported.',
    )
    expect(byId(host, 'ma-health-summary')!.dataset.state).toBe('partial')
  })

  it('says when the list is only the first page', () => {
    const host = render(makeArea({ banksQuery: { data: { banks: BANKS, total: 140 } } }))
    expect(textOf(byId(host, 'ma-health-banks-paged'))).toBe('Showing the first 3 of 140 banks.')
  })

  it('has an empty state for no banks, and one for a bank list that did not load', () => {
    const none = render(makeArea({ banksQuery: { data: { banks: [] as MemoryBank[] } } }))
    expect(textOf(byId(none, 'ma-health-banks-empty'))).toContain('No banks yet')
    expect(none.querySelector('table')).toBeNull()
    cleanup()

    const refetch = vi.fn()
    const failed = render(makeArea({ banksQuery: { isError: true, refetch } }))
    expect(textOf(byId(failed, 'ma-health-banks-error'))).toContain('The bank list did not load')
    click(byId(failed, 'ma-health-banks-error')!.querySelector('button')!)
    expect(refetch).toHaveBeenCalledTimes(1)
    expect(textOf(byId(failed, 'ma-health-queue')!.querySelector('dd'))).toBe(
      'Not reported: the bank list did not load.',
    )
    expect(byId(failed, 'ma-health-summary')!.dataset.state).toBe('partial')
  })
})

describe('embedder record', () => {
  const withRecord = (over: Record<string, unknown>) => {
    m.models = { data: modelsAnswer(embedderRecord(over)) }
    return render()
  }

  it('is one quiet line on the ordinary upgraded box: no chip, no warning, still clear', () => {
    const host = withRecord({ recorded: 'BAAI/bge-small-en-v1.5', needs_switch: true })
    const note = byId(host, 'ma-health-embedder')!
    expect(note.dataset.kind).toBe('quiet')
    expect(note.querySelector('.chip')).toBeNull()
    expect(textOf(note)).toBe(
      'This store was built with BAAI/bge-small-en-v1.5 and the engine stays on it. ' +
        'Settings ask for hs-embed-1, which has no effect until the store is re-embedded.',
    )
    expect(textOf(stat(host, 'embedder'))).toBe('EmbedderBAAI/bge-small-en-v1.5settings ask for hs-embed-1')
    expect(byId(host, 'ma-health-summary')!.dataset.state).toBe('clear')
  })

  it('is a warning that names both models when the store is mixed', () => {
    const host = withRecord({ mixed: true, needs_switch: true })
    const note = byId(host, 'ma-health-embedder')!
    expect(note.querySelector('.chip')!.textContent).toBe('mixed')
    expect(note.querySelector('.chip')!.getAttribute('data-tone')).toBe('warn')
    expect(textOf(note.querySelector('.ma-notice-text'))).toBe(
      'This store may hold vectors from two embedders: it existed before the engine moved from ' +
        'BAAI/bge-small-en-v1.5 to hs-embed-1. Recall over older facts is degraded until the store is re-embedded.',
    )
    expect([...note.querySelectorAll('.ma-mono')].map((el) => el.textContent)).toEqual([
      'BAAI/bge-small-en-v1.5',
      'hs-embed-1',
    ])
    expect(textOf(stat(host, 'embedder'))).toContain('BAAI/bge-small-en-v1.5 and hs-embed-1mixed')
    expect(byId(host, 'ma-health-summary')!.dataset.state).toBe('attention')
    expect(textOf(byId(host, 'ma-health-findings'))).toBe('The store may hold vectors from two embedders.')
  })

  it('is a warning that names both models when the record and the engine disagree', () => {
    const host = withRecord({ conflict: { live: { provider: 'local', model: 'BAAI/bge-small-en-v1.5' } } })
    const note = byId(host, 'ma-health-embedder')!
    expect(note.querySelector('.chip')!.textContent).toBe('conflict')
    expect(note.querySelector('.chip')!.getAttribute('data-tone')).toBe('warn')
    expect(textOf(note.querySelector('.ma-notice-text'))).toBe(
      'The embedder record says hs-embed-1, but the engine is set to run on BAAI/bge-small-en-v1.5. ' +
        'hal0 has changed neither. Remove the override or correct the record so the two agree.',
    )
    expect(byId(host, 'ma-health-summary')!.dataset.state).toBe('attention')
  })

  it('has no button in any of its states: nothing here starts a switch', () => {
    for (const over of [
      { recorded: 'BAAI/bge-small-en-v1.5', needs_switch: true },
      { mixed: true, needs_switch: true },
      { conflict: { on_disk: { provider: 'local', model: 'other' } } },
    ]) {
      const host = withRecord(over)
      expect(byId(host, 'ma-health-embedder')!.querySelector('button')).toBeNull()
      expect(byId(host, 'ma-health-embedder')!.querySelector('a')).toBeNull()
      cleanup()
    }
  })

  it('shows the cell as not reported, and no notice, when the route carries no record', () => {
    m.models = { data: {} }
    const host = render()
    expect(byId(host, 'ma-health-embedder')).toBeNull()
    expect(stat(host, 'embedder').querySelector('[role="img"]')!.getAttribute('aria-label')).toBe('not reported')
    expect(byId(host, 'ma-health-summary')!.dataset.state).toBe('partial')
  })
})

describe('needs attention', () => {
  it('lists what failed, in a plain list', () => {
    m.graph = { data: { ...GRAPH, slot_resolves: false } }
    m.mental = (ids): MentalModelsEntry[] =>
      ids.map((bankId, at) => ({
        bankId,
        models: at === 0 ? [mentalModel({ is_stale: true, last_refresh_failed_at: '2026-11-06T08:00:00Z' })] : [],
      }))
    const stats = BANKS.map((b) => ({
      bankId: b.bank_id,
      stats: bankStats(b.bank_id, { ...STATS[b.bank_id], failed_operations: b.bank_id === 'shared' ? 105 : 0 }),
    }))
    const host = render(makeArea({ stats }))
    const summary = byId(host, 'ma-health-summary')!
    expect(summary.dataset.state).toBe('attention')
    expect(summary.querySelector('h2')!.textContent).toBe('3 things need attention')
    const list = byId(host, 'ma-health-findings')!
    expect(list.tagName).toBe('UL')
    expect([...list.querySelectorAll('li')].map((li) => li.dataset.finding)).toEqual([
      'extraction',
      'queue',
      'knowledge',
    ])
    expect(textOf(stat(host, 'extraction'))).toContain('unreachable')
    expect(host.querySelector('tr[data-bank="shared"] [data-col="failed"]')!.textContent).toBe('105')
    expectNoStepWording(host)
  })
})
