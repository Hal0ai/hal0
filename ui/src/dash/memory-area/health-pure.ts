// What the Health tab says, worked out from ONE answer: `GET /api/memory/health`.
//
// That route decides the order (src/hal0/memory/health_path.py): the steps form one chain,
// the first open step is `current`, a later open step is `waiting` and names the step it waits
// on, and the rest are `clear`. It sends states, reason codes and figures; the sentences are
// written here, one entry per reason code the route can send (STEP_COPY). A code this file does
// not know still gets a row: a generic sentence and its figures under plain labels.
//
// Two older reads fill the engine facts strip where the health answer has no field:
//   GET /api/memory/models         the reranker and the decision model
//   GET /api/memory/graph/status   the upstream model's name, and whether hal0's routing of
//                                  extraction is switched off
//
// Three rules run through this file:
//   - Nothing the route wrote reaches the page raw: no reason code, no figure key, no JSON, no
//     timestamp. A reason becomes a sentence, a key a label, a time a duration or a date.
//   - A figure that was not reported is left out of a sentence, never written as `0`.
//   - A read that was cut short says so beside the figures it affects (`partial`, `truncated`),
//     and an answer with such a gap is never called clear without that said.
//
// No React and no window.

import type { MentalModel } from '@/api/hooks/useHindsight'
import type {
  MemoryGraphStatus,
  MemoryHealthEmbedder,
  MemoryHealthErrorGroup,
  MemoryHealthReport,
  MemoryHealthStep,
} from '@/api/hooks/useMemory'
import { plural } from './area-pure'
import { groupErrors } from './operations-pure'
import { formatCount, formatDate, formatDuration } from './shared/format-pure'

export type Tone = 'ok' | 'warn' | 'err' | 'info' | 'neutral'

/** A run of text, a name set in mono (a model, a slot), or a figure set a step brighter. */
export type Segment = string | { code: string } | { em: string }

const code = (value: string): Segment => ({ code: value })
const em = (value: string): Segment => ({ em: value })

/** The same sentence as plain text, for a test or an accessible name. */
export function segmentsText(segments: Segment[]): string {
  return segments.map((s) => (typeof s === 'string' ? s : 'code' in s ? s.code : s.em)).join('')
}

export interface Chip {
  tone: Tone
  word: string
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const count = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null

const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value.trim() : null

const upperFirst = (value: string): string => value.charAt(0).toUpperCase() + value.slice(1)
const lowerFirst = (value: string): string => value.charAt(0).toLowerCase() + value.slice(1)

/** `fold_in` → `fold in`: a route identifier as words, for an id or key this file has no name for. */
const asWords = (value: string): string => value.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase()

// ── What was cut short ────────────────────────────────────────────────────────

/** Beside a figure summed over banks, when some of them did not answer. */
export const PARTIAL_BANKS_NOTE = 'Some banks did not answer in time; figures are what was read.'
/** Beside the embedder's facts, when the record itself was not read. */
export const PARTIAL_EMBEDDER_NOTE = 'The embedder record was not read in time.'
/** Beside failure reasons and ages, when a bank held more operations than are read. */
export const TRUNCATED_NOTE =
  'A bank holds more operations than were read: the counts are exact, and the reasons and ages come from the part that was read.'

const BANK_SECTIONS = ['extraction', 'queue', 'knowledge', 'banks'] as const

type Report = MemoryHealthReport

const isPartial = (report: Report, name: string): boolean => (report.partial as string[]).includes(name)
const isTruncated = (report: Report): boolean => report.queue?.truncated === true

/**
 * Every gap in the answer, in words, for the top of the page. `null` when the read was whole.
 */
export function incompleteNote(report: Report): string | null {
  const notes: string[] = []
  if (BANK_SECTIONS.some((name) => isPartial(report, name))) notes.push(PARTIAL_BANKS_NOTE)
  if (isPartial(report, 'embedder')) notes.push(PARTIAL_EMBEDDER_NOTE)
  if (isTruncated(report)) notes.push(TRUNCATED_NOTE)
  return notes.length > 0 ? notes.join(' ') : null
}

/** The gap that touches one step's figures, in words; `null` when there is none. */
function stepNote(stepId: string, report: Report): string | null {
  const notes: string[] = []
  if (stepId === 'embedder') {
    if (isPartial(report, 'embedder')) notes.push(PARTIAL_EMBEDDER_NOTE)
  } else if (isPartial(report, stepId)) {
    notes.push(PARTIAL_BANKS_NOTE)
  }
  if ((stepId === 'extraction' || stepId === 'queue') && isTruncated(report)) notes.push(TRUNCATED_NOTE)
  return notes.length > 0 ? notes.join(' ') : null
}

/** Under the banks table: its rows come from the `banks` and `queue` sections. */
export function banksNote(report: Report): string | null {
  return isPartial(report, 'banks') || isPartial(report, 'queue') ? PARTIAL_BANKS_NOTE : null
}

// ── Embedder ──────────────────────────────────────────────────────────────────

const LOCAL_PROVIDER = 'local'

type EmbedderId = { provider?: unknown; model?: unknown }

/** The model for the local provider, the provider otherwise (as `hal0 doctor` words it). */
function describeEmbedder(id: unknown): string | null {
  if (!isRecord(id)) return null
  const provider = text((id as EmbedderId).provider)
  if (provider && provider !== LOCAL_PROVIDER) return `the ${provider} provider`
  return text((id as EmbedderId).model)
}

/**
 * `unknown`        the answer carried no embedder record
 * `clean`          the store and the settings agree
 * `informational`  they differ only because settings still hold the shipped default: the
 *                  ordinary upgraded box, which needs nothing (the route marks this case)
 * `differs`        the operator set another embedder than the store was built with
 * `mixed`          the store may hold vectors from two embedders
 * `conflict`       the engine runs, or would start on, another embedder than the record says
 */
export type EmbedderState = 'unknown' | 'clean' | 'informational' | 'differs' | 'mixed' | 'conflict'

export interface EmbedderView {
  state: EmbedderState
  /** What the store was built with, in words; null when not reported. */
  recorded: string | null
  /** What the settings ask for. */
  configured: string | null
  /** The embedder a conflict is with. */
  other: string | null
}

/** The health answer's `embedder` section as the page reads it. */
export function embedderView(embedder: MemoryHealthEmbedder | null | undefined): EmbedderView {
  const e: Record<string, unknown> = isRecord(embedder) ? (embedder as unknown as Record<string, unknown>) : {}
  const recorded = describeEmbedder(e.recorded)
  const configured = describeEmbedder(e.configured)
  if (!recorded) return { state: 'unknown', recorded: null, configured, other: null }
  const conflict = isRecord(e.conflict) ? e.conflict : null
  const against = conflict ? (describeEmbedder(conflict.live) ?? describeEmbedder(conflict.on_disk)) : null
  if (conflict && Object.keys(conflict).length > 0) return { state: 'conflict', recorded, configured, other: against }
  if (e.mixed === true) return { state: 'mixed', recorded, configured, other: null }
  if (e.informational === true) return { state: 'informational', recorded, configured, other: null }
  const differs = e.needs_switch === true || (!!configured && configured !== recorded)
  return { state: differs ? 'differs' : 'clean', recorded, configured, other: null }
}

/**
 * The quiet line under the engine strip for the ordinary upgraded box: one sentence, nothing to
 * do. Every other state is either fine or a step of the path, so it gets no line here.
 */
export function embedderNotice(view: EmbedderView): Segment[] | null {
  if (view.state !== 'informational' || !view.recorded) return null
  const asks: Segment[] =
    view.configured && view.configured !== view.recorded
      ? [' Settings ask for ', code(view.configured), ', which has no effect until the store is re-embedded.']
      : []
  return ['This store was built with ', code(view.recorded), ' and the engine stays on it.', ...asks]
}

/**
 * The line that re-embeds the store, as `hal0 doctor` prints it: towards the configured embedder,
 * or the recorded one when the two already agree (a mixed store is repaired by a switch to the
 * embedder it is on). `null` when no model is known.
 */
export function switchCommand(embedder: unknown): string | null {
  const e = isRecord(embedder) ? embedder : {}
  const configured = isRecord(e.configured) ? (e.configured as EmbedderId) : {}
  const recorded = isRecord(e.recorded) ? (e.recorded as EmbedderId) : {}
  const model = text(configured.model) ?? text(recorded.model)
  if (!model || /\s/.test(model)) return null
  const provider = text(configured.provider) ?? text(recorded.provider)
  const flag = provider && provider !== LOCAL_PROVIDER && !/\s/.test(provider) ? ` --provider ${provider}` : ''
  return `hal0 memory embedder switch ${model}${flag}`
}

// ── The steps, in words ───────────────────────────────────────────────────────

/** What a step is about, as it is named in "Waiting on step 1: storing memories". */
const STEP_NAMES: Record<string, string> = {
  engine: 'the memory engine',
  extraction: 'storing memories',
  queue: 'the queue',
  embedder: 'the embedder',
  knowledge: 'knowledge',
}

export function stepName(id: string): string {
  return STEP_NAMES[id] ?? (asWords(id) || 'this step')
}

/** What the copy of one step is written from: its own figures, and the report's sections. */
interface CopyInput {
  figures: Record<string, unknown>
  report: Report | null
}

interface StepCopy {
  /** What to do, in plain words. */
  title: string
  /** One or two sentences. */
  body: Segment[]
}

const DEFAULT_WINDOW_S = 6 * 60 * 60
const DEFAULT_MAX_WAITING_S = 30 * 60

const windowText = (figures: Record<string, unknown>): string =>
  formatDuration(count(figures.window_s) ?? DEFAULT_WINDOW_S) ?? '6 h'

/** "the `memory` slot", or a phrase that names no slot when the answer carried none. */
function slotWords(report: Report | null): Segment[] {
  const slot = text(report?.extraction?.slot)
  return slot ? ['the ', code(slot), ' slot'] : ['its extraction slot']
}

const UNTIL_STORED = 'nothing agents send becomes a fact'

function queueWaiting(figures: Record<string, unknown>, report: Report | null) {
  return {
    failed: count(figures.failed) ?? count(report?.queue?.failed),
    waiting: count(figures.waiting) ?? count(report?.queue?.waiting),
    oldest: formatDuration(count(figures.oldest_waiting_s) ?? count(report?.queue?.oldest_waiting_s)),
  }
}

function embedderFacts(figures: Record<string, unknown>, report: Report | null) {
  const source = isRecord(figures.recorded) ? figures : (report?.embedder ?? figures)
  const view = embedderView(source as unknown as MemoryHealthEmbedder)
  const conflict = isRecord((source as Record<string, unknown>).conflict)
    ? ((source as Record<string, unknown>).conflict as Record<string, unknown>)
    : null
  return {
    recorded: view.recorded,
    configured: view.configured,
    live: conflict ? describeEmbedder(conflict.live) : null,
    onDisk: conflict ? describeEmbedder(conflict.on_disk) : null,
  }
}

function knowledgeFacts(figures: Record<string, unknown>, report: Report | null) {
  return {
    pagesStale: count(figures.pages_stale) ?? count(report?.knowledge?.pages_stale) ?? 0,
    modelsStale: count(figures.models_stale) ?? count(report?.knowledge?.models_stale) ?? 0,
    refreshFailed: count(figures.refresh_failed) ?? count(report?.knowledge?.refresh_failed),
    lastSuccess: formatDate(report?.knowledge?.last_success_at),
  }
}

const KNOWLEDGE_ONE = 'knowledge page or mental model'
const KNOWLEDGE_MANY = 'knowledge pages and mental models'

/**
 * One entry per reason code the route can send (src/hal0/memory/health_path.py, and the
 * embedder's reasons in src/hal0/memory/embedder_verdict.py).
 */
const STEP_COPY: Record<string, (input: CopyInput) => StepCopy> = {
  engine_unreachable: () => ({
    title: 'Check the memory engine service',
    body: ['The memory engine is not answering, so nothing else on this page can be read.'],
  }),

  extraction_slot_unresolved: ({ report }) => ({
    title: 'Give the extraction slot a model',
    body: [
      'Storing a memory runs on ',
      ...slotWords(report),
      `, and that slot has no model. Until it has one, ${UNTIL_STORED}.`,
    ],
  }),

  extraction_llm_unreachable: ({ figures, report }) => ({
    title: 'Reconnect extraction to its model',
    body:
      figures.slot_resolves === true && text(report?.extraction?.slot)
        ? [
            'Storing a memory runs on ',
            ...slotWords(report),
            `, and its model is not accepting connections. Until it does, ${UNTIL_STORED}.`,
          ]
        : [
            `The model that turns what agents send into facts is not accepting connections. Until it does, ${UNTIL_STORED}.`,
          ],
  }),

  extraction_recent_failures: ({ figures, report }) => {
    const failures = count(figures.failures) ?? count(report?.extraction?.failures_6h)
    const window = windowText(figures)
    const lead: Segment[] =
      failures === null || failures === 0
        ? [`Attempts to store a memory failed in the last ${window}.`]
        : [em(plural(failures, 'attempt', 'attempts')), ` to store a memory failed in the last ${window}.`]
    return {
      title: 'Fix what is failing when memories are stored',
      body: [...lead, ' Until the cause is fixed, operations wait in the queue and a retry would fail.'],
    }
  },

  queue_operations_failed: ({ figures, report }) => {
    const { failed, waiting, oldest } = queueWaiting(figures, report)
    const one = failed === 1
    const lead: Segment[] =
      failed === null || failed === 0
        ? ['Operations have failed']
        : [em(plural(failed, 'operation', 'operations')), one ? ' has failed' : ' have failed']
    const rest: Segment[] =
      waiting === null
        ? []
        : waiting === 0
          ? [', and nothing is waiting']
          : [
              ' and ',
              em(formatCount(waiting) ?? String(waiting)),
              waiting === 1 ? ' is waiting' : ' are waiting',
              oldest ? (waiting === 1 ? `, for ${oldest}` : `, the oldest for ${oldest}`) : '',
            ]
    return {
      title: 'Retry or discard the failed operations',
      body: [...lead, ...rest, `. Retry or discard ${one ? 'it' : 'them'} on the Operations tab.`],
    }
  },

  queue_waiting_too_long: ({ figures, report }) => {
    const { waiting, oldest } = queueWaiting(figures, report)
    const limit = formatDuration(count(figures.max_waiting_age_s) ?? DEFAULT_MAX_WAITING_S) ?? '30 min'
    const lead: Segment[] =
      waiting === null || waiting === 0
        ? [oldest ? `An operation has been waiting for ${oldest}` : 'An operation has been waiting too long']
        : waiting === 1
          ? [em('1 operation'), oldest ? ` has been waiting for ${oldest}` : ' has been waiting too long']
          : [em(plural(waiting, 'operation', 'operations')), ' are waiting', oldest ? `, the oldest for ${oldest}` : '']
    return {
      title: 'Find out why the queue is not moving',
      body: [
        ...lead,
        `. Nothing has failed, but this page asks for you once one has waited longer than ${limit}. The Operations tab shows what is running.`,
      ],
    }
  },

  embedder_record_unreadable: ({ figures, report }) => {
    const { recorded } = embedderFacts(figures, report)
    return {
      title: 'Repair the embedder record',
      body: [
        'The record of the embedder this store was built with cannot be read. hal0 has left the file as it is and keeps the engine on ',
        recorded ? code(recorded) : 'the embedder it runs now',
        '.',
      ],
    }
  },

  embedder_store_unavailable: () => ({
    title: 'Bring the memory store back',
    body: [
      'The place the memory store is kept is not available: a link that does not resolve, or a disk that is not mounted. Nothing is recorded about its embedder until it is back.',
    ],
  }),

  embedder_conflict: ({ figures, report }) => {
    const { recorded, live, onDisk } = embedderFacts(figures, report)
    const seen: Segment[] = []
    if (live) seen.push('the running engine is on ', code(live))
    if (onDisk) seen.push(live ? ' and ' : '', 'its unit files would start it on ', code(onDisk))
    if (seen.length === 0) seen.push('the engine is set to run on another embedder')
    return {
      title: 'Make the embedder record and the engine agree',
      body: [
        'The embedder record says ',
        recorded ? code(recorded) : 'one embedder',
        ', but ',
        ...seen,
        '. hal0 has changed neither. Remove the override or correct the record so the two agree.',
      ],
    }
  },

  embedder_mixed: ({ figures, report }) => {
    const { recorded, configured } = embedderFacts(figures, report)
    const asks: Segment[] =
      configured && configured !== recorded ? [' Settings ask for ', code(configured), '.'] : []
    return {
      title: 'Re-embed the store on one embedder',
      body: [
        'This store may hold vectors from two embedders: it existed before the engine moved to ',
        recorded ? code(recorded) : 'the embedder it runs now',
        '. Recall over older facts is degraded until the store is re-embedded.',
        ...asks,
      ],
    }
  },

  embedder_needs_switch: ({ figures, report }) => {
    const { recorded, configured } = embedderFacts(figures, report)
    return {
      title: 'Re-embed the store on the embedder settings ask for',
      body: [
        'This store was built with ',
        recorded ? code(recorded) : 'one embedder',
        ' and the engine stays on it. Settings ask for ',
        configured ? code(configured) : 'another',
        ', which has no effect until the store is re-embedded.',
      ],
    }
  },

  knowledge_refresh_failed: ({ figures, report }) => {
    const { pagesStale, modelsStale, refreshFailed, lastSuccess } = knowledgeFacts(figures, report)
    const stale = pagesStale + modelsStale
    const lead: Segment[] =
      refreshFailed === null || refreshFailed === 0
        ? [`${upperFirst(KNOWLEDGE_MANY)} failed their last refresh`]
        : [
            em(plural(refreshFailed, KNOWLEDGE_ONE, KNOWLEDGE_MANY)),
            refreshFailed === 1 ? ' failed its last refresh' : ' failed their last refresh',
          ]
    return {
      title: 'Refresh the knowledge that failed',
      body: [
        ...lead,
        stale > 0 ? `, and ${formatCount(stale)} ${stale === 1 ? 'is' : 'are'} stale` : '',
        '.',
        lastSuccess ? ` The last refresh that worked was on ${lastSuccess}.` : '',
      ],
    }
  },

  knowledge_stale: ({ figures, report }) => {
    const { pagesStale, modelsStale } = knowledgeFacts(figures, report)
    const parts: string[] = []
    if (pagesStale > 0) parts.push(plural(pagesStale, 'knowledge page', 'knowledge pages'))
    if (modelsStale > 0) parts.push(plural(modelsStale, 'mental model', 'mental models'))
    const stale = pagesStale + modelsStale
    const lead: Segment[] =
      parts.length === 0
        ? [`${upperFirst(KNOWLEDGE_MANY)} are stale`]
        : [em(parts.join(' and ')), stale === 1 ? ' is stale' : ' are stale']
    return {
      title: 'Refresh stale knowledge',
      body: [...lead, `: behind the facts ${stale === 1 ? 'it was' : 'they were'} built from. No refresh has failed.`],
    }
  },
}

/** Every reason code this file has its own words for. */
export const KNOWN_REASON_CODES: readonly string[] = Object.freeze(Object.keys(STEP_COPY))

/** Plain labels for the figures of a step whose reason this file does not know. */
const FIGURE_LABELS: Record<string, string> = {
  banks: 'Banks',
  failed: 'Failed operations',
  waiting: 'Waiting operations',
  oldest_waiting_s: 'Oldest waiting',
  max_waiting_age_s: 'Asks for you after',
  failures: 'Failed attempts to store a memory',
  window_s: 'Counted over the last',
  slot_resolves: 'The extraction slot has a model',
  llm_reachable: 'The extraction model accepts connections',
  pages_stale: 'Stale knowledge pages',
  models_stale: 'Stale mental models',
  refresh_failed: 'Failed refreshes',
  recorded: 'Store built with',
  configured: 'Settings ask for',
  mixed: 'Store may hold two embedders',
  reachable: 'Engine answers',
}

/** Figures that are lists, flags for this file's own use, or already said another way. */
const FIGURE_SKIP = new Set(['top_errors', 'reasons', 'informational', 'conflict'])

export interface LabelledFigure {
  label: string
  value: string
}

/**
 * A step's figures under plain labels: a count with its thousands comma, a length of time as a
 * duration, a flag as yes or no, an embedder as its model. Anything else (a list, a nested
 * object, an empty value) is left out; nothing is printed as JSON.
 */
export function labelledFigures(figures: unknown): LabelledFigure[] {
  if (!isRecord(figures)) return []
  const out: LabelledFigure[] = []
  for (const [key, raw] of Object.entries(figures)) {
    if (FIGURE_SKIP.has(key)) continue
    const label = FIGURE_LABELS[key] ?? upperFirst(asWords(key.replace(/_s$/, '')))
    let value: string | null = null
    if (typeof raw === 'boolean') value = raw ? 'yes' : 'no'
    else if (typeof raw === 'number') value = /_s$/.test(key) ? formatDuration(raw) : formatCount(raw)
    else if (typeof raw === 'string') value = text(raw)?.split(/\r?\n/)[0].slice(0, 80) ?? null
    else if (isRecord(raw)) value = describeEmbedder(raw)
    if (label && value) out.push({ label, value })
  }
  return out
}

/**
 * The words for an open step: a title in plain words, and one or two sentences built from its
 * reason code and figures. A code with no entry gets a generic sentence; its figures are shown
 * beside it by `labelledFigures`, never as JSON and never as the code itself.
 */
export function stepCopy(
  step: Pick<MemoryHealthStep, 'id' | 'reason_code' | 'figures'>,
  report: Report | null = null,
): StepCopy & { known: boolean } {
  const figures = isRecord(step.figures) ? step.figures : {}
  const write = typeof step.reason_code === 'string' ? STEP_COPY[step.reason_code] : undefined
  if (write) return { ...write({ figures, report }), known: true }
  const reported = labelledFigures(figures).length > 0
  return {
    title: `${upperFirst(stepName(step.id))} needs attention`,
    body: [
      'This step is open, and this page has no description for its cause yet.',
      reported ? ' The figures the check reported are below.' : ' The check reported no figures with it.',
    ],
    known: false,
  }
}

/** One sentence for a step that is clear: what was checked, and what it found. */
function clearLine(step: MemoryHealthStep, report: Report): Segment[] {
  const figures = isRecord(step.figures) ? step.figures : {}
  if (step.id === 'extraction') {
    return [`No attempt to store a memory failed in the last ${windowText(figures)}.`]
  }
  if (step.id === 'queue') {
    const { waiting, oldest } = queueWaiting(figures, report)
    if (waiting === null) return ['Nothing has failed.']
    if (waiting === 0) return ['Nothing is waiting and nothing has failed.']
    return [
      'Nothing has failed. ',
      em(plural(waiting, 'operation', 'operations')),
      waiting === 1 ? ' is waiting' : ' are waiting',
      oldest ? (waiting === 1 ? `, for ${oldest}` : `, the oldest for ${oldest}`) : '',
      '.',
    ]
  }
  if (step.id === 'embedder') {
    const view = embedderView(report.embedder)
    if (view.state === 'unknown') return ['The embedder record was not reported, so nothing is known about it.']
    if (view.state === 'informational') {
      return ['The store stays on ', code(view.recorded as string), ', the embedder it was built with. Nothing to do.']
    }
    return ['The store and the engine are on ', code(view.recorded as string), '.']
  }
  if (step.id === 'knowledge') {
    const pages = count(report.knowledge?.pages)
    const models = count(report.knowledge?.models)
    if (pages === null || models === null) return ['Nothing is stale and no refresh has failed.']
    const total = pages + models
    if (total === 0) return ['No knowledge pages or mental models yet.']
    return [
      em(plural(total, KNOWLEDGE_ONE, KNOWLEDGE_MANY)),
      total === 1 ? ' is fresh.' : ' are fresh.',
    ]
  }
  if (step.id === 'engine') return ['The memory engine answers.']
  return ['Nothing is open here.']
}

/**
 * The route masks the parts of an engine error that change from one failure to the next, so
 * that the same failure groups together: a number becomes `<n>`, an id `<id>`, a host address
 * `<host>`. This turns each placeholder into something a person reads ("exceeded the Ns
 * wall-clock limit", "from a host"). Text that is not a placeholder is left as it was.
 */
const ERROR_PLACEHOLDERS: Record<string, string> = { n: 'N', id: 'ID', host: 'a host' }

export function readableError(message: string): string {
  return message.replace(/<(n|id|host)>/g, (_, key: string) => ERROR_PLACEHOLDERS[key])
}

// ── The path ──────────────────────────────────────────────────────────────────

/** Where a step's link goes. The component turns it into an address. */
export type StepLinkTarget = 'services' | 'settings' | 'operations' | 'knowledge'

export interface StepLink {
  to: StepLinkTarget
  label: string
}

export interface StepCommand {
  command: string
  /** What running it does, and that it changes nothing. */
  note: string
}

export interface PathStep {
  id: string
  /** Its place in the path, from 1, and how many steps the path has. */
  position: number
  of: number
  state: 'current' | 'waiting' | 'clear'
  /** What the step is about: "storing memories". */
  name: string
  /** Open: what to do. Clear: the name, as a title. */
  title: string
  body: Segment[]
  chip: Chip | null
  /** A waiting step: what it waits on, in words. */
  waitsOn: string | null
  /** The most frequent failure messages, cut to one line, with how often each was seen. */
  errors: MemoryHealthErrorGroup[]
  /** The figures of a step whose reason has no words here. Empty for every known reason. */
  figures: LabelledFigure[]
  links: StepLink[]
  /** A command to review. Only the current step carries one. */
  command: StepCommand | null
  /** What was cut short in the read behind this step's figures. */
  note: string | null
}

const SWITCH_NOTE =
  'Run it in a shell on the box. It is a dry run by default: it checks the store and prints the plan, and changes nothing.'
const DOCTOR_NOTE = "Run it in a shell on the box. It prints hal0's checks, with the file or override this is about, and changes nothing."

const SWITCH_REASONS = new Set(['embedder_mixed', 'embedder_needs_switch'])

function currentChip(step: MemoryHealthStep, report: Report): Chip | null {
  const figures = isRecord(step.figures) ? step.figures : {}
  switch (step.reason_code) {
    case 'extraction_recent_failures': {
      const failures = count(figures.failures) ?? count(report.extraction?.failures_6h)
      if (failures === null || failures === 0) return { tone: 'err', word: 'failing' }
      return {
        tone: 'err',
        word: `${formatCount(failures)} ${failures === 1 ? 'failure' : 'failures'} in ${windowText(figures)}`,
      }
    }
    case 'extraction_slot_unresolved':
    case 'extraction_llm_unreachable':
    case 'embedder_record_unreadable':
    case 'knowledge_refresh_failed':
      return { tone: 'err', word: 'failing' }
    case 'queue_operations_failed':
      return { tone: 'err', word: 'failed' }
    case 'embedder_store_unavailable':
      return { tone: 'err', word: 'unreachable' }
    case 'embedder_mixed':
      return { tone: 'warn', word: 'mixed' }
    case 'embedder_conflict':
      return { tone: 'warn', word: 'conflict' }
    case 'knowledge_stale':
      return { tone: 'warn', word: 'stale' }
    default:
      return null
  }
}

function stepLinks(step: MemoryHealthStep): StepLink[] {
  if (step.id === 'extraction') {
    return step.state === 'clear'
      ? []
      : [
          { to: 'services', label: 'Open Services' },
          { to: 'settings', label: 'Open engine settings' },
        ]
  }
  if (step.id === 'queue') return [{ to: 'operations', label: 'Open Operations' }]
  if (step.id === 'knowledge') return [{ to: 'knowledge', label: 'Open Knowledge' }]
  return []
}

function stepCommand(step: MemoryHealthStep, report: Report): StepCommand | null {
  // A command is offered where it can be acted on: the step to do now.
  if (step.id !== 'embedder' || step.state !== 'current') return null
  if (SWITCH_REASONS.has(String(step.reason_code))) {
    const figures = isRecord(step.figures) && isRecord(step.figures.recorded) ? step.figures : report.embedder
    const command = switchCommand(figures)
    if (command) return { command, note: SWITCH_NOTE }
  }
  return { command: 'hal0 doctor', note: DOCTOR_NOTE }
}

/**
 * The repair path, in the order the route sent it. The engine step is left out: with the engine
 * unreachable the page is another one altogether, and with it reachable the step is always
 * clear, so "step 1" is the first thing that can need the operator.
 */
export function buildPath(report: Report): PathStep[] {
  const steps = report.steps.filter((step) => step.id !== 'engine')
  const positions = new Map(steps.map((step, at) => [step.id, at + 1]))
  return steps.map((step, at): PathStep => {
    const state: PathStep['state'] = step.state === 'current' || step.state === 'waiting' ? step.state : 'clear'
    const base = {
      id: step.id,
      position: at + 1,
      of: steps.length,
      state,
      name: stepName(step.id),
      links: stepLinks({ ...step, state }),
      note: stepNote(step.id, report),
    }
    if (state === 'clear') {
      return {
        ...base,
        title: upperFirst(stepName(step.id)),
        body: clearLine(step, report),
        chip: null,
        waitsOn: null,
        errors: [],
        figures: [],
        command: null,
      }
    }
    const copy = stepCopy(step, report)
    let waitsOn: string | null = null
    if (state === 'waiting') {
      const on = text(step.waits_on)
      const onPosition = on ? positions.get(on) : undefined
      waitsOn = on
        ? onPosition
          ? `Waiting on step ${onPosition}: ${stepName(on)}`
          : `Waiting on ${stepName(on)}`
        : 'Waiting on an earlier step'
    }
    return {
      ...base,
      title: copy.title,
      body: copy.body,
      chip: state === 'current' ? currentChip(step, report) : { tone: 'neutral', word: 'waiting' },
      waitsOn,
      errors:
        step.id === 'extraction'
          ? groupErrors(report.extraction?.top_errors ?? (isRecord(step.figures) ? step.figures.top_errors : null)).map(
              (group) => ({ ...group, message: readableError(group.message) }),
            )
          : [],
      figures: copy.known ? [] : labelledFigures(step.figures),
      command: stepCommand({ ...step, state }, report),
    }
  })
}

// ── The verdict ───────────────────────────────────────────────────────────────

export interface Verdict {
  kind: 'clear' | 'attention'
  lead: string
  detail: string
  /** What was cut short in the read, in words. Shown beside the verdict whenever it is set. */
  qualifier: string | null
  chip: Chip | null
  /** How many steps are open. */
  open: number
}

const listWords = (words: string[]): string =>
  words.length <= 1 ? words.join('') : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`

/**
 * Does anything need the operator? Decided from the path: clear only when no step is open.
 * A read that was cut short is said in `qualifier`, and then "clear" is said of what was read,
 * with no "working" chip.
 */
export function healthVerdict(report: Report, path: PathStep[] = buildPath(report)): Verdict {
  const qualifier = incompleteNote(report)
  const open = path.filter((step) => step.state !== 'clear')
  if (open.length === 0) {
    const watched = path.map((step) => step.name)
    return {
      kind: 'clear',
      lead: qualifier ? 'Nothing needs attention in what was read' : 'Nothing needs attention',
      detail:
        watched.length > 0
          ? `The engine answers, and ${watched.length === 1 ? 'the one thing' : `the ${watched.length} things`} this page watches ${watched.length === 1 ? 'is' : 'are'} clear: ${listWords(watched)}.`
          : 'The engine answers.',
      qualifier,
      chip: qualifier ? null : { tone: 'ok', word: 'working' },
      open: 0,
    }
  }
  const current = open.find((step) => step.state === 'current') ?? open[0]
  const others = open.length - 1
  const where = `step ${current.position}, ${current.name}`
  return {
    kind: 'attention',
    lead: open.length === 1 ? '1 thing needs you' : `${formatCount(open.length)} things need you`,
    detail:
      others === 0
        ? `It is ${where}: ${lowerFirst(current.title)}.`
        : `Start with ${where}: ${lowerFirst(current.title)}. ${others === 1 ? 'The other one waits' : `The other ${others} wait`} on it, in the order below.`,
    qualifier,
    chip: null,
    open: open.length,
  }
}

// ── Knowledge ─────────────────────────────────────────────────────────────────

/**
 * A failed refresh is a fault only when it is later than the last one that worked. Health reads
 * its knowledge figures from the route; the Knowledge tab (knowledge-pure.ts) reads this rule.
 */
export function refreshFailed(model: Pick<MentalModel, 'last_refreshed_at' | 'last_refresh_failed_at'>): boolean {
  if (!model.last_refresh_failed_at) return false
  return !model.last_refreshed_at || model.last_refresh_failed_at > model.last_refreshed_at
}

// ── Engine facts strip ────────────────────────────────────────────────────────

export interface StripCell {
  id: 'engine' | 'extraction' | 'embedder' | 'reranker' | 'decision'
  label: string
  /** A name. `null` is drawn as "not reported". */
  value: string | null
  helper: string | null
  chip: Chip | null
}

const MODE_WORDS: Record<string, string> = {
  dedicated: 'dedicated slot',
  shared: 'shared slot',
  custom: 'custom target',
}

type GraphStatus = Partial<MemoryGraphStatus> & { mode?: string | null }

/**
 * The extraction cell. The slot, its mode, whether it resolves, the concurrency and the failure
 * count are the health answer's. Two things it does not carry come from the graph-status read:
 * the name of an upstream model, and whether hal0's routing of extraction is switched off.
 */
function extractionCell(report: Report, graph: GraphStatus | null | undefined): StripCell {
  const base = { id: 'extraction' as const, label: 'Extraction' }
  const extraction = report.extraction
  const slot = text(extraction?.slot)
  const upstream = graph?.target?.kind === 'upstream' ? text(graph.target.model) : null
  if (!extraction || (!slot && !upstream)) return { ...base, value: null, helper: null, chip: null }
  const value = upstream ?? `${slot} slot`
  // The switch in Engine settings turns hal0's routing of extraction to this target off. It does
  // not stop the engine extracting, so this is not "extraction is off".
  if (graph?.enabled === false) {
    return { ...base, value, helper: 'routing to this target is off in Engine settings', chip: null }
  }
  const step = report.steps.find((s) => s.id === 'extraction')
  const open = !!step && step.state !== 'clear'
  const concurrency = count(extraction.concurrency)
  const failures = count(extraction.failures_6h)
  const helper = [
    upstream ? 'upstream model, through hal0' : (MODE_WORDS[extraction.mode ?? ''] ?? null),
    extraction.slot_resolves === false ? 'no enabled slot with this name' : null,
    concurrency !== null && concurrency > 0 ? `${formatCount(concurrency)} at a time` : null,
    failures !== null ? `${plural(failures, 'failure', 'failures')} in ${windowText(isRecord(step?.figures) ? step.figures : {})}` : null,
  ]
    .filter(Boolean)
    .join(' · ')
  const chip: Chip | null = open
    ? { tone: 'err', word: 'failing' }
    : extraction.llm_reachable === true
      ? { tone: 'ok', word: 'working' }
      : null
  return { ...base, value, helper: helper || null, chip }
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
    case 'informational':
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
        value: view.recorded,
        helper: 'the store may also hold vectors from an earlier embedder',
        chip: { tone: 'warn', word: 'mixed' },
      }
    case 'conflict':
      return {
        ...base,
        value: view.recorded,
        helper: view.other ? `the engine is set to run on ${view.other}` : 'the record and the engine disagree',
        chip: { tone: 'warn', word: 'conflict' },
      }
  }
}

function rerankerCell(reranker: unknown): StripCell {
  const r = isRecord(reranker) ? reranker : null
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
  const d = isRecord(decision) ? decision : null
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

/**
 * The five cells of the engine facts strip, always in this order. Engine and Embedder are read
 * from the health answer alone; Extraction from it and the graph-status read; Reranker and
 * Decision model from the models read, because the health answer does not carry them.
 */
export function engineStrip(input: {
  report: Report
  graph: GraphStatus | null | undefined
  models: ModelsAnswer | null | undefined
}): StripCell[] {
  const version = text(input.report.engine?.version)
  return [
    {
      id: 'engine',
      label: 'Engine',
      value: version ? `Hindsight ${version}` : 'Hindsight',
      helper: 'memory engine',
      chip: { tone: 'ok', word: 'reachable' },
    },
    extractionCell(input.report, input.graph),
    embedderCell(embedderView(input.report.embedder)),
    rerankerCell(input.models?.reranker),
    decisionCell(input.models?.decision),
  ]
}
