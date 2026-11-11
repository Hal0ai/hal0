// What the interim Health tab says, worked out from the answers of the per-subject routes:
//   GET /api/memory/engine         reachable, version
//   GET /api/memory/graph/status   extraction slot, mode, whether it resolves
//   GET /api/memory/models         embedder record, reranker, decision model
//   GET /api/memory/banks/{b}/stats and .../mental-models, once per bank
//
// This is a status view with no order to it. Which fault to fix first, and what waits on what,
// is what the composed `GET /api/memory/health` answers; this tab does not read it yet, and
// nothing here says it.
//
// Every sum names how many banks it covers, because each one is added up in the browser from
// per-bank answers and a bank that did not answer is not in it. No React and no window.

import type { MemoryEngine, MentalModel } from '@/api/hooks/useHindsight'
import type { MemoryGraphStatus } from '@/api/hooks/useMemory'
import type { BankTotals } from './area-pure'
import { plural } from './area-pure'
import { formatCount } from './shared/format-pure'

export type Tone = 'ok' | 'warn' | 'err' | 'info' | 'neutral'

/** A run of text, or a name set in mono (a model, a slot, a bank id). */
export type Segment = string | { code: string }

const code = (value: string): Segment => ({ code: value })

/** The same sentence as plain text, for a test or an accessible name. */
export function segmentsText(segments: Segment[]): string {
  return segments.map((s) => (typeof s === 'string' ? s : s.code)).join('')
}

// ── Embedder record ───────────────────────────────────────────────────────────

/**
 * The embedder a Hindsight store is built with when nothing says otherwise. A store marked
 * `mixed` existed under this model before the engine moved to the recorded one, and the models
 * route does not carry its name. Mirrors ENGINE_DEFAULT_EMBEDDER in
 * src/hal0/memory/embedder_record.py.
 */
export const ENGINE_DEFAULT_EMBEDDER = 'BAAI/bge-small-en-v1.5'
const LOCAL_PROVIDER = 'local'

/**
 * `unknown`   the route did not carry an embedder record
 * `clean`     recorded and configured agree
 * `differs`   settings ask for another model than the store was built with (an upgraded box)
 * `mixed`     the store may hold vectors from two models
 * `conflict`  the engine runs, or would start on, another model than the record says
 */
export type EmbedderState = 'unknown' | 'clean' | 'differs' | 'mixed' | 'conflict'

export interface EmbedderView {
  state: EmbedderState
  /** What the store was built with, in words; null when not reported. */
  recorded: string | null
  /** What the settings ask for. */
  configured: string | null
  /** The second model a mixed store or a conflict involves. */
  other: string | null
}

const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value.trim() : null

/** The model for the local provider, the provider otherwise (as `hal0 doctor` words it). */
function describeEmbedder(provider: unknown, model: unknown): string | null {
  const p = text(provider)
  const m = text(model)
  if (p && p !== LOCAL_PROVIDER) return `the ${p} provider`
  return m
}

type Pair = { provider?: unknown; model?: unknown }

const asPair = (value: unknown): Pair | null =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Pair) : null

export function embedderView(embeddings: unknown): EmbedderView {
  const e = (asPair(embeddings) ?? {}) as Record<string, unknown>
  // An API from before the record existed carries only the configured `model`.
  const configured = describeEmbedder(e.configured_provider ?? e.provider, e.configured ?? e.model)
  const recorded = describeEmbedder(e.recorded_provider, e.recorded)
  if (!recorded) return { state: 'unknown', recorded: null, configured, other: null }

  const conflict = asPair(e.conflict) as { live?: unknown; on_disk?: unknown } | null
  const against = asPair(conflict?.live) ?? asPair(conflict?.on_disk)
  if (against) {
    return {
      state: 'conflict',
      recorded,
      configured,
      other: describeEmbedder(against.provider, against.model),
    }
  }
  if (e.mixed === true) {
    return { state: 'mixed', recorded, configured, other: ENGINE_DEFAULT_EMBEDDER }
  }
  const differs =
    typeof e.needs_switch === 'boolean' ? e.needs_switch : !!configured && configured !== recorded
  return { state: differs ? 'differs' : 'clean', recorded, configured, other: null }
}

export interface EmbedderNotice {
  /** `quiet` is one informational line. `warning` is a notice with a chip. */
  kind: 'quiet' | 'warning'
  status: string | null
  segments: Segment[]
}

/** The sentence under the engine strip, or null when the record needs no attention. */
export function embedderNotice(view: EmbedderView): EmbedderNotice | null {
  const { recorded, configured, other } = view
  if (!recorded) return null
  const asks: Segment[] =
    configured && configured !== recorded
      ? [' Settings ask for ', code(configured), ', which has no effect until the store is re-embedded.']
      : []
  if (view.state === 'differs') {
    return {
      kind: 'quiet',
      status: null,
      segments: ['This store was built with ', code(recorded), ' and the engine stays on it.', ...asks],
    }
  }
  if (view.state === 'mixed') {
    return {
      kind: 'warning',
      status: 'mixed',
      segments: [
        'This store may hold vectors from two embedders: it existed before the engine moved from ',
        code(other ?? ENGINE_DEFAULT_EMBEDDER),
        ' to ',
        code(recorded),
        '. Recall over older facts is degraded until the store is re-embedded.',
        ...asks,
      ],
    }
  }
  if (view.state === 'conflict') {
    return {
      kind: 'warning',
      status: 'conflict',
      segments: [
        'The embedder record says ',
        code(recorded),
        ', but the engine is set to run on ',
        code(other ?? 'another embedder'),
        '. hal0 has changed neither. Remove the override or correct the record so the two agree.',
      ],
    }
  }
  return null
}

// ── Engine facts strip ────────────────────────────────────────────────────────

export interface StripCell {
  id: 'engine' | 'extraction' | 'embedder' | 'reranker' | 'decision'
  label: string
  /** A name. `null` is drawn as "not reported". */
  value: string | null
  helper: string | null
  chip: { tone: Tone; word: string } | null
}

const MODE_WORDS: Record<string, string> = {
  dedicated: 'dedicated slot',
  shared: 'shared slot',
  custom: 'custom target',
}

type GraphStatus = Partial<MemoryGraphStatus> & { mode?: string | null }

function extractionCell(graph: GraphStatus | null | undefined): StripCell {
  const base = { id: 'extraction' as const, label: 'Extraction' }
  const slot = text(graph?.extraction_slot)
  if (!graph || !slot) return { ...base, value: null, helper: null, chip: null }
  const upstream = graph.target?.kind === 'upstream' ? text(graph.target.model) : null
  const value = upstream ?? `${slot} slot`
  // The switch in Engine settings turns hal0's routing of extraction to this target off. It does
  // not stop the engine extracting, so this is not "extraction is off", and whether the target
  // resolves says nothing while it is not in use.
  if (graph.enabled === false) {
    return { ...base, value, helper: 'routing to this target is off in Engine settings', chip: null }
  }
  if (upstream) return { ...base, value, helper: 'upstream model, through hal0', chip: null }
  const mode = MODE_WORDS[graph.mode ?? ''] ?? null
  if (graph.slot_resolves === false) {
    return {
      ...base,
      value,
      helper: [mode, 'no enabled slot with this name'].filter(Boolean).join(' · '),
      chip: { tone: 'err', word: 'unreachable' },
    }
  }
  return { ...base, value, helper: [mode, 'resolves to a model'].filter(Boolean).join(' · '), chip: null }
}

function embedderCell(view: EmbedderView): StripCell {
  const base = { id: 'embedder' as const, label: 'Embedder' }
  switch (view.state) {
    case 'unknown':
      return view.configured
        ? { ...base, value: view.configured, helper: 'configured model', chip: null }
        : { ...base, value: null, helper: null, chip: null }
    case 'clean':
      return { ...base, value: view.recorded, helper: 'the model this store was built with', chip: null }
    case 'differs':
      return {
        ...base,
        value: view.recorded,
        helper: view.configured ? `settings ask for ${view.configured}` : 'settings ask for another model',
        chip: null,
      }
    case 'mixed':
      return {
        ...base,
        value: `${view.other} and ${view.recorded}`,
        helper: 'two models in one store',
        chip: { tone: 'warn', word: 'mixed' },
      }
    case 'conflict':
      return {
        ...base,
        value: `${view.recorded} and ${view.other ?? 'another embedder'}`,
        helper: 'the record and the engine disagree',
        chip: { tone: 'warn', word: 'conflict' },
      }
  }
}

function rerankerCell(reranker: unknown): StripCell {
  const r = asPair(reranker)
  const provider = text(r?.provider)
  return {
    id: 'reranker',
    label: 'Reranker',
    value: text(r?.model),
    helper: provider ? `${provider} provider` : null,
    chip: null,
  }
}

function decisionCell(decision: unknown): StripCell {
  const base = { id: 'decision' as const, label: 'Decision model' }
  const d = asPair(decision) as { enabled?: unknown; model?: unknown; reachable?: unknown } | null
  if (!d || typeof d.enabled !== 'boolean') return { ...base, value: null, helper: null, chip: null }
  if (!d.enabled) return { ...base, value: 'Off', helper: 'optional component, not enabled', chip: null }
  return {
    ...base,
    value: text(d.model) ?? 'Enabled',
    helper: 'optional component',
    chip: d.reachable === true ? { tone: 'ok', word: 'reachable' } : { tone: 'err', word: 'unreachable' },
  }
}

export interface ModelsAnswer {
  embeddings?: unknown
  reranker?: unknown
  decision?: unknown
}

/** The five cells of the engine facts strip, always in this order. */
export function engineStrip(input: {
  engine: MemoryEngine | null | undefined
  graph: GraphStatus | null | undefined
  models: ModelsAnswer | null | undefined
}): StripCell[] {
  const version = text(input.engine?.version)
  return [
    {
      id: 'engine',
      label: 'Engine',
      value: version ? `Hindsight ${version}` : 'Hindsight',
      helper: 'memory engine',
      chip: { tone: 'ok', word: 'reachable' },
    },
    extractionCell(input.graph),
    embedderCell(embedderView(input.models?.embeddings)),
    rerankerCell(input.models?.reranker),
    decisionCell(input.models?.decision),
  ]
}

// ── Queue and knowledge, summed across banks ──────────────────────────────────

/** "summed across 21 banks" / "summed across the 18 of 21 banks that reported". */
function across(reported: number, banks: number): string {
  if (reported >= banks) return `summed across ${plural(banks, 'bank', 'banks')}`
  return `summed across the ${formatCount(reported)} of ${plural(banks, 'bank', 'banks')} that reported`
}

export interface SummaryLine {
  /** False when no bank reported, so there is no figure to show. */
  known: boolean
  text: string
}

export function queueLine(totals: BankTotals): SummaryLine {
  if (totals.banks === 0) return { known: true, text: 'No banks yet, so there is no queue.' }
  if (totals.reported === 0 || totals.waiting === null || totals.failed === null) {
    return { known: false, text: 'Not reported: no bank answered with its operation counts.' }
  }
  return {
    known: true,
    text: `${plural(totals.waiting, 'operation', 'operations')} waiting and ${formatCount(totals.failed)} failed, ${across(totals.reported, totals.banks)}.`,
  }
}

/** One bank's mental models as the fan-out hook holds them. */
export interface MentalModelsEntry {
  bankId: string
  models?: MentalModel[] | null
  isLoading?: boolean
  isError?: boolean
}

export interface KnowledgeTotals {
  banks: number
  reported: number
  models: number
  stale: number
  /** Models whose last refresh failed after their last success. */
  refreshFailed: number
}

/** A failed refresh is a fault only when it is later than the last one that worked. */
export function refreshFailed(model: Pick<MentalModel, 'last_refreshed_at' | 'last_refresh_failed_at'>): boolean {
  if (!model.last_refresh_failed_at) return false
  return !model.last_refreshed_at || model.last_refresh_failed_at > model.last_refreshed_at
}

export function knowledgeTotals(entries: MentalModelsEntry[], banks: number): KnowledgeTotals {
  const reported = entries.filter((entry) => Array.isArray(entry.models))
  const models = reported.flatMap((entry) => entry.models ?? [])
  return {
    banks: Math.max(banks, entries.length),
    reported: reported.length,
    models: models.length,
    stale: models.filter((m) => m.is_stale === true).length,
    refreshFailed: models.filter(refreshFailed).length,
  }
}

export function knowledgeLine(totals: KnowledgeTotals): SummaryLine {
  if (totals.banks === 0) return { known: true, text: 'No banks yet, so there are no mental models.' }
  if (totals.reported === 0) {
    return { known: false, text: 'Not reported: no bank answered with its mental models.' }
  }
  const where = across(totals.reported, totals.banks)
  if (totals.models === 0) return { known: true, text: `No mental models yet, ${where}.` }
  const stale =
    totals.models === 1
      ? `The 1 mental model is ${totals.stale === 1 ? 'stale' : 'fresh'}`
      : `${formatCount(totals.stale)} of ${formatCount(totals.models)} mental models are stale`
  const failed =
    totals.refreshFailed > 0
      ? ` and ${formatCount(totals.refreshFailed)} failed ${totals.refreshFailed === 1 ? 'its' : 'their'} last refresh`
      : ''
  return { known: true, text: `${stale}${failed}, ${where}.` }
}

// ── What needs attention ──────────────────────────────────────────────────────

export interface Finding {
  id: 'extraction' | 'embedder' | 'queue' | 'knowledge'
  text: string
}

export interface Verdict {
  /**
   * `clear`      nothing found, and everything this page checks was reported
   * `attention`  at least one finding
   * `partial`    nothing found, but something was not reported
   */
  kind: 'clear' | 'attention' | 'partial'
  lead: string
  detail: string
  findings: Finding[]
}

/**
 * What this page can see that needs the operator, in no order. Waiting operations and stale
 * mental models are normal on a busy box and are not findings; a failure is.
 */
export function healthVerdict(input: {
  strip: StripCell[]
  embedder: EmbedderView
  totals: BankTotals
  knowledge: KnowledgeTotals
  /** The bank list itself did not load, so no per-bank figure exists. */
  banksFailed?: boolean
}): Verdict {
  const { strip, embedder, totals, knowledge } = input
  const findings: Finding[] = []
  const extraction = strip.find((cell) => cell.id === 'extraction')
  if (extraction?.chip?.tone === 'err') {
    findings.push({
      id: 'extraction',
      text: 'Extraction is stalled: its slot does not match an enabled LLM slot.',
    })
  }
  if (embedder.state === 'mixed' || embedder.state === 'conflict') {
    findings.push({
      id: 'embedder',
      text:
        embedder.state === 'mixed'
          ? 'The store may hold vectors from two embedders.'
          : 'The embedder record and the engine disagree.',
    })
  }
  if ((totals.failed ?? 0) > 0) {
    findings.push({
      id: 'queue',
      text: `${plural(totals.failed as number, 'operation has', 'operations have')} failed.`,
    })
  }
  if (knowledge.refreshFailed > 0) {
    findings.push({
      id: 'knowledge',
      text: `${plural(knowledge.refreshFailed, 'mental model', 'mental models')} failed ${knowledge.refreshFailed === 1 ? 'its' : 'their'} last refresh.`,
    })
  }
  if (findings.length > 0) {
    return {
      kind: 'attention',
      lead: findings.length === 1 ? '1 thing needs attention' : `${findings.length} things need attention`,
      detail: 'These are listed in no particular order. This page does not yet say which to fix first.',
      findings,
    }
  }

  const missing: string[] = []
  if (input.banksFailed) missing.push('the bank list')
  if (!extraction?.value) missing.push('the extraction target')
  if (embedder.state === 'unknown') missing.push('the embedder record')
  if (totals.banks > 0 && totals.reported < totals.banks) missing.push('operation counts for every bank')
  if (knowledge.banks > 0 && knowledge.reported < knowledge.banks) missing.push('mental models for every bank')
  if (missing.length > 0) {
    return {
      kind: 'partial',
      lead: 'No problems found in what was reported',
      detail: `Not reported: ${missing.join(', ')}.`,
      findings: [],
    }
  }
  return {
    kind: 'clear',
    lead: 'Nothing needs attention',
    detail:
      'The engine answers, the embedder record is consistent, and no operation or mental-model refresh has failed.',
    findings: [],
  }
}
