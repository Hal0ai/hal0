import { describe, expect, it } from 'vitest'
import type { MemoryEngine, MentalModel } from '@/api/hooks/useHindsight'
import type { BankTotals } from './area-pure'
import {
  ENGINE_DEFAULT_EMBEDDER,
  embedderNotice,
  embedderView,
  engineStrip,
  healthVerdict,
  knowledgeLine,
  knowledgeTotals,
  queueLine,
  refreshFailed,
  segmentsText,
  type KnowledgeTotals,
} from './health-pure'

const ENGINE: MemoryEngine = {
  enabled: true,
  engine: 'hindsight',
  reachable: true,
  version: '1.2.0',
  features: null,
  banks_total: 21,
}

// The embedder record as GET /api/memory/models carries it.
const record = (over: Record<string, unknown> = {}) => ({
  provider: 'local',
  model: 'hs-embed-1',
  recorded: 'hs-embed-1',
  recorded_provider: 'local',
  configured: 'hs-embed-1',
  configured_provider: 'local',
  mixed: false,
  needs_switch: false,
  source: 'record',
  recorded_at: '2026-11-06T09:00:00Z',
  effective: { provider: 'local', model: 'hs-embed-1', source: 'process' },
  conflict: null,
  record_error: null,
  store_unavailable: null,
  ...over,
})

const CLEAN = record()
const UPGRADED = record({ recorded: 'BAAI/bge-small-en-v1.5', needs_switch: true })
const MIXED = record({ mixed: true, needs_switch: true })
const CONFLICT_LIVE = record({ conflict: { live: { provider: 'local', model: 'BAAI/bge-small-en-v1.5' } } })
const CONFLICT_DISK = record({ conflict: { on_disk: { provider: 'tei', model: 'hs-embed-1' } } })

const GRAPH = {
  enabled: true,
  extraction_slot: 'memory',
  mode: 'dedicated',
  slot_resolves: true,
  available_slots: ['agent', 'memory'],
}

const MODELS = {
  embeddings: CLEAN,
  reranker: { provider: 'local', model: 'hs-rerank-1' },
  decision: { enabled: false, provider: 'local', model: 'decider', reachable: false },
}

const cell = (cells: ReturnType<typeof engineStrip>, id: string) => cells.find((c) => c.id === id)!

const totals = (over: Partial<BankTotals> = {}): BankTotals => ({
  banks: 21,
  listed: 21,
  counted: 21,
  reported: 21,
  facts: 52955,
  waiting: 0,
  failed: 0,
  ...over,
})

const knowledge = (over: Partial<KnowledgeTotals> = {}): KnowledgeTotals => ({
  banks: 21,
  reported: 21,
  models: 5,
  stale: 0,
  refreshFailed: 0,
  ...over,
})

describe('embedderView', () => {
  it('is clean when the store and the settings agree', () => {
    expect(embedderView(CLEAN)).toEqual({ state: 'clean', recorded: 'hs-embed-1', configured: 'hs-embed-1', other: null })
  })

  it('reads the ordinary upgraded box: recorded differs from configured, nothing else', () => {
    expect(embedderView(UPGRADED)).toEqual({
      state: 'differs',
      recorded: 'BAAI/bge-small-en-v1.5',
      configured: 'hs-embed-1',
      other: null,
    })
  })

  it('reads a difference by name when the route has no needs_switch', () => {
    const { needs_switch: _dropped, ...older } = UPGRADED
    expect(embedderView(older).state).toBe('differs')
    const { needs_switch: _dropped2, ...same } = CLEAN
    expect(embedderView(same).state).toBe('clean')
  })

  it('reads a mixed store, and names the model it started on', () => {
    expect(embedderView(MIXED)).toEqual({
      state: 'mixed',
      recorded: 'hs-embed-1',
      configured: 'hs-embed-1',
      other: ENGINE_DEFAULT_EMBEDDER,
    })
  })

  it('reads a conflict with the running engine or with its unit files', () => {
    expect(embedderView(CONFLICT_LIVE)).toMatchObject({ state: 'conflict', other: 'BAAI/bge-small-en-v1.5' })
    // A provider that is not the local one is named in place of its model.
    expect(embedderView(CONFLICT_DISK)).toMatchObject({ state: 'conflict', other: 'the tei provider' })
  })

  it('puts a conflict ahead of a mixed store', () => {
    expect(embedderView(record({ mixed: true, conflict: { live: { provider: 'local', model: 'x' } } })).state).toBe('conflict')
  })

  it('ignores an empty conflict', () => {
    expect(embedderView(record({ conflict: {} })).state).toBe('clean')
  })

  it.each([[undefined], [null], [{}], ['nope'], [[]]])('is unknown for %j', (raw) => {
    expect(embedderView(raw)).toEqual({ state: 'unknown', recorded: null, configured: null, other: null })
  })

  it('is unknown, with the configured model kept, for an API from before the record', () => {
    expect(embedderView({ provider: 'local', model: 'hs-embed-1' })).toEqual({
      state: 'unknown',
      recorded: null,
      configured: 'hs-embed-1',
      other: null,
    })
  })
})

describe('embedderNotice', () => {
  const notice = (raw: unknown) => embedderNotice(embedderView(raw))

  it('says nothing for a clean record, or one that was not reported', () => {
    expect(notice(CLEAN)).toBeNull()
    expect(notice({})).toBeNull()
  })

  it('is one quiet line for the upgraded box, naming both models', () => {
    const n = notice(UPGRADED)!
    expect(n.kind).toBe('quiet')
    expect(n.status).toBeNull()
    expect(segmentsText(n.segments)).toBe(
      'This store was built with BAAI/bge-small-en-v1.5 and the engine stays on it. ' +
        'Settings ask for hs-embed-1, which has no effect until the store is re-embedded.',
    )
    expect(n.segments).toContainEqual({ code: 'BAAI/bge-small-en-v1.5' })
    expect(n.segments).toContainEqual({ code: 'hs-embed-1' })
  })

  it('is a warning for a mixed store, naming both models', () => {
    const n = notice(MIXED)!
    expect(n).toMatchObject({ kind: 'warning', status: 'mixed' })
    expect(segmentsText(n.segments)).toBe(
      'This store may hold vectors from two embedders: it existed before the engine moved from ' +
        'BAAI/bge-small-en-v1.5 to hs-embed-1. Recall over older facts is degraded until the store is re-embedded.',
    )
    expect(n.segments).toContainEqual({ code: 'BAAI/bge-small-en-v1.5' })
    expect(n.segments).toContainEqual({ code: 'hs-embed-1' })
  })

  it('adds what the settings ask for when a mixed store is also behind them', () => {
    const n = notice(record({ mixed: true, needs_switch: true, configured: 'hs-embed-2' }))!
    expect(segmentsText(n.segments)).toContain('Settings ask for hs-embed-2, which has no effect')
  })

  it('is a warning for a conflict, naming both models', () => {
    const n = notice(CONFLICT_LIVE)!
    expect(n).toMatchObject({ kind: 'warning', status: 'conflict' })
    expect(segmentsText(n.segments)).toBe(
      'The embedder record says hs-embed-1, but the engine is set to run on BAAI/bge-small-en-v1.5. ' +
        'hal0 has changed neither. Remove the override or correct the record so the two agree.',
    )
    expect(n.segments).toContainEqual({ code: 'hs-embed-1' })
    expect(n.segments).toContainEqual({ code: 'BAAI/bge-small-en-v1.5' })
  })

  it('names the provider when the conflict is with one that is not local', () => {
    expect(segmentsText(notice(CONFLICT_DISK)!.segments)).toContain('set to run on the tei provider')
  })
})

describe('engineStrip', () => {
  const strip = (over: Record<string, unknown> = {}) =>
    engineStrip({ engine: ENGINE, graph: GRAPH, models: MODELS, ...over })

  it('is five cells in a fixed order', () => {
    expect(strip().map((c) => c.label)).toEqual(['Engine', 'Extraction', 'Embedder', 'Reranker', 'Decision model'])
  })

  it('names the engine with its version, and says it is reachable', () => {
    expect(cell(strip(), 'engine')).toEqual({
      id: 'engine',
      label: 'Engine',
      value: 'Hindsight 1.2.0',
      helper: 'memory engine',
      chip: { tone: 'ok', word: 'reachable' },
    })
    expect(cell(strip({ engine: { ...ENGINE, version: null } }), 'engine').value).toBe('Hindsight')
  })

  it('shows the extraction slot, its mode, and that it resolves', () => {
    expect(cell(strip(), 'extraction')).toMatchObject({
      value: 'memory slot',
      helper: 'dedicated slot · resolves to a model',
      chip: null,
    })
    expect(cell(strip({ graph: { ...GRAPH, mode: 'shared', extraction_slot: 'agent' } }), 'extraction')).toMatchObject({
      value: 'agent slot',
      helper: 'shared slot · resolves to a model',
    })
  })

  it('flags an extraction slot that does not resolve', () => {
    expect(cell(strip({ graph: { ...GRAPH, slot_resolves: false } }), 'extraction')).toMatchObject({
      value: 'memory slot',
      helper: 'dedicated slot · no enabled slot with this name',
      chip: { tone: 'err', word: 'unreachable' },
    })
  })

  it('says routing to the target is off, without calling extraction off or judging the slot', () => {
    const off = { ...GRAPH, enabled: false, slot_resolves: false }
    expect(cell(strip({ graph: off }), 'extraction')).toEqual({
      id: 'extraction',
      label: 'Extraction',
      value: 'memory slot',
      helper: 'routing to this target is off in Engine settings',
      chip: null,
    })
    // A slot that is not in use is not a finding.
    const verdict = healthVerdict({
      strip: strip({ graph: off }),
      embedder: embedderView(CLEAN),
      totals: totals(),
      knowledge: knowledge(),
    })
    expect(verdict.kind).toBe('clear')
  })

  it('names an upstream model in place of a slot', () => {
    const graph = { ...GRAPH, mode: 'custom', target: { kind: 'upstream', model: 'minimax/m2', alias: 'hal0/memory' } }
    expect(cell(strip({ graph }), 'extraction')).toMatchObject({
      value: 'minimax/m2',
      helper: 'upstream model, through hal0',
      chip: null,
    })
  })

  it('shows the embedder for each state of its record', () => {
    const embedder = (embeddings: unknown) => cell(strip({ models: { ...MODELS, embeddings } }), 'embedder')
    expect(embedder(CLEAN)).toMatchObject({ value: 'hs-embed-1', helper: 'the model this store was built with', chip: null })
    expect(embedder(UPGRADED)).toMatchObject({
      value: 'BAAI/bge-small-en-v1.5',
      helper: 'settings ask for hs-embed-1',
      chip: null,
    })
    expect(embedder(MIXED)).toMatchObject({
      value: 'BAAI/bge-small-en-v1.5 and hs-embed-1',
      chip: { tone: 'warn', word: 'mixed' },
    })
    expect(embedder(CONFLICT_LIVE)).toMatchObject({
      value: 'hs-embed-1 and BAAI/bge-small-en-v1.5',
      chip: { tone: 'warn', word: 'conflict' },
    })
    expect(embedder({ provider: 'local', model: 'hs-embed-1' })).toMatchObject({
      value: 'hs-embed-1',
      helper: 'configured model',
    })
  })

  it('shows the reranker and the decision model', () => {
    expect(cell(strip(), 'reranker')).toMatchObject({ value: 'hs-rerank-1', helper: 'local provider' })
    expect(cell(strip(), 'decision')).toMatchObject({ value: 'Off', helper: 'optional component, not enabled', chip: null })
    const on = { ...MODELS, decision: { enabled: true, model: 'decider-1', reachable: true } }
    expect(cell(strip({ models: on }), 'decision')).toMatchObject({
      value: 'decider-1',
      chip: { tone: 'ok', word: 'reachable' },
    })
    const down = { ...MODELS, decision: { enabled: true, model: 'decider-1', reachable: false } }
    expect(cell(strip({ models: down }), 'decision').chip).toEqual({ tone: 'err', word: 'unreachable' })
  })

  it('leaves a cell with no value, not a guess, when its route did not answer', () => {
    const cells = strip({ graph: null, models: {} })
    for (const id of ['extraction', 'embedder', 'reranker', 'decision']) {
      expect(cell(cells, id)).toMatchObject({ value: null, chip: null })
    }
  })
})

describe('queueLine', () => {
  it('sums waiting and failed operations, and says across how many banks', () => {
    expect(queueLine(totals({ waiting: 1093, failed: 105 }))).toEqual({
      known: true,
      text: '1,093 operations waiting and 105 failed, summed across 21 banks.',
    })
    expect(queueLine(totals({ banks: 1, listed: 1, reported: 1, waiting: 1, failed: 0 })).text).toBe(
      '1 operation waiting and 0 failed, summed across 1 bank.',
    )
  })

  it('says which banks a partial sum covers', () => {
    expect(queueLine(totals({ reported: 18, waiting: 40, failed: 2 })).text).toBe(
      '40 operations waiting and 2 failed, summed across the 18 of 21 banks that reported.',
    )
  })

  it('has no figure when no bank reported', () => {
    expect(queueLine(totals({ reported: 0, waiting: null, failed: null }))).toEqual({
      known: false,
      text: 'Not reported: no bank answered with its operation counts.',
    })
  })

  it('says there is no queue with no banks', () => {
    expect(queueLine(totals({ banks: 0, listed: 0, reported: 0, waiting: null, failed: null })).known).toBe(true)
  })
})

describe('knowledge', () => {
  const model = (over: Partial<MentalModel> = {}): MentalModel => ({
    id: 'mm',
    name: 'Model',
    source_query: 'q',
    is_stale: false,
    last_refreshed_at: '2026-11-05T08:00:00Z',
    last_refresh_failed_at: null,
    ...over,
  })

  it('counts a failed refresh only when it is later than the last success', () => {
    expect(refreshFailed(model())).toBe(false)
    expect(refreshFailed(model({ last_refresh_failed_at: '2026-11-04T08:00:00Z' }))).toBe(false)
    expect(refreshFailed(model({ last_refresh_failed_at: '2026-11-06T08:00:00Z' }))).toBe(true)
    expect(refreshFailed(model({ last_refreshed_at: null, last_refresh_failed_at: '2026-11-06T08:00:00Z' }))).toBe(true)
  })

  it('adds the mental models of the banks that answered', () => {
    const t = knowledgeTotals(
      [
        { bankId: 'a', models: [model({ is_stale: true }), model()] },
        { bankId: 'b', models: [model({ is_stale: true, last_refresh_failed_at: '2026-11-06T08:00:00Z' })] },
        { bankId: 'c', models: null, isError: true },
      ],
      3,
    )
    expect(t).toEqual({ banks: 3, reported: 2, models: 3, stale: 2, refreshFailed: 1 })
  })

  it('says how many are stale, and across how many banks', () => {
    expect(knowledgeLine(knowledge({ models: 12, stale: 5 }))).toEqual({
      known: true,
      text: '5 of 12 mental models are stale, summed across 21 banks.',
    })
    expect(knowledgeLine(knowledge({ models: 12, stale: 5, refreshFailed: 1 })).text).toBe(
      '5 of 12 mental models are stale and 1 failed its last refresh, summed across 21 banks.',
    )
    expect(knowledgeLine(knowledge({ models: 12, stale: 5, refreshFailed: 3, reported: 20 })).text).toBe(
      '5 of 12 mental models are stale and 3 failed their last refresh, summed across the 20 of 21 banks that reported.',
    )
  })

  it('reads well for one model and for none', () => {
    expect(knowledgeLine(knowledge({ models: 1, stale: 1 })).text).toBe(
      'The 1 mental model is stale, summed across 21 banks.',
    )
    expect(knowledgeLine(knowledge({ models: 0 })).text).toBe('No mental models yet, summed across 21 banks.')
  })

  it('has no figure when no bank answered', () => {
    expect(knowledgeLine(knowledge({ reported: 0, models: 0 }))).toEqual({
      known: false,
      text: 'Not reported: no bank answered with its mental models.',
    })
  })
})

describe('healthVerdict', () => {
  const verdict = (over: Record<string, unknown> = {}) => {
    const models = (over.models as typeof MODELS) ?? MODELS
    const graph = (over.graph as typeof GRAPH) ?? GRAPH
    return healthVerdict({
      strip: engineStrip({ engine: ENGINE, graph, models }),
      embedder: embedderView(models.embeddings),
      totals: (over.totals as BankTotals) ?? totals(),
      knowledge: (over.knowledge as KnowledgeTotals) ?? knowledge(),
      banksFailed: over.banksFailed as boolean | undefined,
    })
  }

  it('is clear when nothing failed and everything was reported', () => {
    expect(verdict()).toEqual({
      kind: 'clear',
      lead: 'Nothing needs attention',
      detail:
        'The engine answers, the embedder record is consistent, and no operation or mental-model refresh has failed.',
      findings: [],
    })
  })

  it('stays clear with operations waiting and mental models stale: both are normal', () => {
    expect(verdict({ totals: totals({ waiting: 978 }), knowledge: knowledge({ stale: 5 }) }).kind).toBe('clear')
  })

  it('stays clear on the ordinary upgraded box', () => {
    expect(verdict({ models: { ...MODELS, embeddings: UPGRADED } }).kind).toBe('clear')
  })

  it('lists each thing that failed, and counts them in the lead', () => {
    const v = verdict({
      graph: { ...GRAPH, slot_resolves: false },
      models: { ...MODELS, embeddings: MIXED },
      totals: totals({ failed: 105 }),
      knowledge: knowledge({ refreshFailed: 1 }),
    })
    expect(v.kind).toBe('attention')
    expect(v.lead).toBe('4 things need attention')
    expect(v.findings.map((f) => f.id)).toEqual(['extraction', 'embedder', 'queue', 'knowledge'])
    expect(v.findings.map((f) => f.text)).toEqual([
      'Extraction is stalled: its slot does not match an enabled LLM slot.',
      'The store may hold vectors from two embedders.',
      '105 operations have failed.',
      '1 mental model failed its last refresh.',
    ])
  })

  it('reads well for one finding', () => {
    const v = verdict({ totals: totals({ failed: 1 }) })
    expect(v.lead).toBe('1 thing needs attention')
    expect(v.findings[0].text).toBe('1 operation has failed.')
  })

  it('names a conflict as a finding', () => {
    expect(verdict({ models: { ...MODELS, embeddings: CONFLICT_LIVE } }).findings).toEqual([
      { id: 'embedder', text: 'The embedder record and the engine disagree.' },
    ])
  })

  it('never puts an order on the findings in words', () => {
    const v = verdict({ totals: totals({ failed: 3 }), knowledge: knowledge({ refreshFailed: 2 }) })
    const words = [v.lead, v.detail, ...v.findings.map((f) => f.text)].join(' ')
    expect(words).not.toMatch(/\bstep\b/i)
    expect(words).not.toMatch(/\bfirst,|\bthen\b/i)
  })

  it('does not call it clear when something was not reported', () => {
    const v = verdict({ models: {}, totals: totals({ reported: 18 }), knowledge: knowledge({ reported: 20 }) })
    expect(v.kind).toBe('partial')
    expect(v.lead).toBe('No problems found in what was reported')
    expect(v.detail).toBe(
      'Not reported: the embedder record, operation counts for every bank, mental models for every bank.',
    )
  })

  it('says the bank list did not load', () => {
    const v = verdict({ banksFailed: true, totals: totals({ banks: 0, listed: 0, reported: 0, failed: null }) })
    expect(v.kind).toBe('partial')
    expect(v.detail).toContain('the bank list')
  })
})
