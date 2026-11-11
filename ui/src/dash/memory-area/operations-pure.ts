// What the Operations tab says, worked out from two answers:
//   GET /api/memory/health                      the queue figures, and whether extraction is the
//                                               step that has to be fixed first
//   GET /api/memory/banks/{b}/operations        the rows, newest first, a page at a time
//
// The figures come from the health route and from nowhere else, so this tab and Health always
// agree. That route leaves a batch's parent row out and counts its children, which is why the
// list asks the engine to do the same (`exclude_parents`): a row here is one piece of work.
//
// Two rules run through this file:
//   - A figure that was not reported is `null`, never `0`.
//   - Nothing the engine wrote reaches the page raw: a status and a type become plain words, a
//     time becomes a duration, and an error message is cut to one line.
//
// No React and no window.

import type { BankOperation } from '@/api/hooks/useHindsight'
import type {
  MemoryHealth,
  MemoryHealthErrorGroup,
  MemoryHealthQueue,
  MemoryHealthQueueBank,
  MemoryHealthReport,
} from '@/api/hooks/useMemory'
import { formatCount, formatDuration } from './shared/format-pure'

export type Tone = 'ok' | 'warn' | 'err' | 'info' | 'neutral'

const count = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** `1 operation`, `105 operations`. */
export function plural(n: number, one: string, many: string): string {
  return `${formatCount(n) ?? String(n)} ${n === 1 ? one : many}`
}

// ── Status ────────────────────────────────────────────────────────────────────

/** The status filter's choices. `all` asks the engine for every status. */
export type OperationFilter = 'all' | 'waiting' | 'running' | 'failed' | 'done'

export const OPERATION_FILTERS: ReadonlyArray<{ id: OperationFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'waiting', label: 'Waiting' },
  { id: 'running', label: 'Running' },
  { id: 'failed', label: 'Failed' },
  { id: 'done', label: 'Done' },
]

/** The engine's `status` query value for a filter; `undefined` sends none. */
const FILTER_STATUS: Record<OperationFilter, string | undefined> = {
  all: undefined,
  waiting: 'pending',
  running: 'processing',
  failed: 'failed',
  done: 'completed',
}

export function filterStatus(filter: OperationFilter): string | undefined {
  return FILTER_STATUS[filter]
}

export type StatusKind = 'waiting' | 'running' | 'failed' | 'done' | 'stopped' | 'other'

export interface StatusView {
  kind: StatusKind
  /** The chip's word. */
  word: string
  tone: Tone
}

const STATUS_VIEWS: Record<string, StatusView> = {
  pending: { kind: 'waiting', word: 'waiting', tone: 'neutral' },
  processing: { kind: 'running', word: 'running', tone: 'info' },
  failed: { kind: 'failed', word: 'failed', tone: 'err' },
  completed: { kind: 'done', word: 'done', tone: 'ok' },
  cancelled: { kind: 'stopped', word: 'stopped', tone: 'neutral' },
}

/** An engine status as the word the page uses. One this file does not know keeps its own word. */
export function operationStatus(raw: unknown): StatusView {
  const key = typeof raw === 'string' ? raw.trim().toLowerCase() : ''
  const known = STATUS_VIEWS[key]
  if (known) return known
  return { kind: 'other', word: key ? key.replace(/[_-]+/g, ' ') : 'not reported', tone: 'neutral' }
}

// ── Type ──────────────────────────────────────────────────────────────────────

const TYPE_LABELS: Record<string, string> = {
  retain: 'Store a memory',
  batch_retain: 'Store a batch',
  file_convert_retain: 'Store a file',
  document_ingest: 'Store a document',
  consolidation: 'Consolidate',
  observation_extract: 'Extract observations',
  refresh_mental_model: 'Refresh a mental model',
}

export interface TypeView {
  /** What the operation does, in plain words. */
  label: string
  /** The engine's own name for it, for a title attribute. Empty when it sent none. */
  raw: string
}

/** `batch_retain` → "Store a batch". A type this file does not know reads as its own words. */
export function operationType(op: Pick<BankOperation, 'operation_type' | 'task_type'>): TypeView {
  const raw = String(op.operation_type || op.task_type || '').trim()
  if (!raw) return { label: 'Operation', raw: '' }
  const known = TYPE_LABELS[raw.toLowerCase()]
  if (known) return { label: known, raw }
  const words = raw.replace(/[_-]+/g, ' ').trim().toLowerCase()
  return { label: words.charAt(0).toUpperCase() + words.slice(1), raw }
}

/** `retried once`, `retried 3 times`; `null` when the engine has not retried it. */
export function retriedText(retryCount: unknown): string | null {
  const n = count(retryCount)
  if (n === null || n < 1) return null
  return n === 1 ? 'retried once' : `retried ${formatCount(n)} times`
}

/** `next try in 12 min`, or `next try due` once the time has passed; `null` when none is set. */
export function nextTryText(nextRetryAt: unknown, now: number = Date.now()): string | null {
  const at = typeof nextRetryAt === 'string' ? Date.parse(nextRetryAt) : NaN
  if (Number.isNaN(at)) return null
  const ahead = (at - now) / 1000
  if (ahead <= 0) return 'next try due'
  if (ahead < 60) return 'next try in under 1 min'
  return `next try in ${formatDuration(ahead)}`
}

// ── Error messages ────────────────────────────────────────────────────────────

/** A one-line message is cut here. */
export const ERROR_LINE_MAX = 110
/** The most of a message the expanded view shows. */
export const ERROR_FULL_MAX = 2000

function cut(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text
}

export interface ShortError {
  /** The first line, on one line, cut at ERROR_LINE_MAX. */
  line: string
  /** The whole message, cut at ERROR_FULL_MAX. */
  full: string
  /** `full` says more than `line` does. */
  more: boolean
}

/** An engine `error_message` for a row: one line, and the rest behind a control. */
export function shortError(message: unknown, max = ERROR_LINE_MAX): ShortError | null {
  if (typeof message !== 'string') return null
  const full = message.trim()
  if (!full) return null
  const first = full.split(/\r?\n/).find((ln) => ln.trim()) ?? ''
  const oneLine = first.replace(/\s+/g, ' ').trim()
  const line = cut(oneLine, max)
  return { line, full: cut(full, ERROR_FULL_MAX), more: line !== full }
}

/**
 * The health route's grouped failure messages, ready for a sentence: each cut to one line,
 * groups that read the same after cutting added together, most frequent first.
 */
export function groupErrors(groups: unknown, max = ERROR_LINE_MAX): MemoryHealthErrorGroup[] {
  if (!Array.isArray(groups)) return []
  const totals = new Map<string, number>()
  for (const group of groups) {
    if (!isRecord(group)) continue
    const short = shortError(group.message, max)
    const n = count(group.count)
    if (!short || n === null || n <= 0) continue
    totals.set(short.line, (totals.get(short.line) ?? 0) + n)
  }
  return [...totals.entries()]
    .map(([message, n]) => ({ message, count: n }))
    .sort((a, b) => b.count - a.count || a.message.localeCompare(b.message))
}

/**
 * Why a request failed, as one sentence fit to show beside the control that sent it. A body
 * that is not a sentence (JSON, markup, nothing) is replaced by `fallback`.
 */
export function plainError(error: unknown, fallback = 'The engine did not accept the request.'): string {
  const raw =
    error instanceof Error ? error.message : typeof error === 'string' ? error : ''
  const line = (raw.split(/\r?\n/).find((ln) => ln.trim()) ?? '').replace(/\s+/g, ' ').trim()
  if (!line || /^[[{<]/.test(line)) return fallback
  const text = cut(line, 200)
  return /[.!?…]$/.test(text) ? text : `${text}.`
}

// ── Durations ─────────────────────────────────────────────────────────────────

/** A length of time as a figure and its unit (`6`, `h`), for a stat cell. */
export function durationParts(seconds: unknown): { value: number; unit: string } | null {
  const text = formatDuration(count(seconds))
  if (text === null) return null
  const at = text.lastIndexOf(' ')
  const value = Number(text.slice(0, at).replace(/,/g, ''))
  return Number.isFinite(value) ? { value, unit: text.slice(at + 1) } : null
}

// ── Paging ────────────────────────────────────────────────────────────────────

/** Rows on one page of the list. */
export const PAGE_SIZE = 25

export interface PageWindow {
  /** 1-based. */
  page: number
  pages: number
  /** 1-based positions of the first and last row shown; both 0 when there is none. */
  from: number
  to: number
  total: number
  hasPrev: boolean
  hasNext: boolean
  prevOffset: number
  nextOffset: number
}

/** The last valid offset for `total` rows: a page that emptied falls back to the one before. */
export function clampOffset(total: number, limit: number, offset: number): number {
  if (!(total > 0) || !(limit > 0)) return 0
  const last = Math.floor((total - 1) / limit) * limit
  return Math.min(Math.max(0, Math.floor(offset / limit) * limit), last)
}

export function pageWindow(total: number, limit: number, offset: number): PageWindow {
  const size = limit > 0 ? limit : PAGE_SIZE
  const rows = total > 0 ? total : 0
  const at = clampOffset(rows, size, offset)
  const pages = Math.max(1, Math.ceil(rows / size))
  return {
    page: Math.floor(at / size) + 1,
    pages,
    from: rows === 0 ? 0 : at + 1,
    to: Math.min(rows, at + size),
    total: rows,
    hasPrev: at > 0,
    hasNext: at + size < rows,
    prevOffset: Math.max(0, at - size),
    nextOffset: at + size < rows ? at + size : at,
  }
}

// ── Several banks in one list ─────────────────────────────────────────────────

/** How many of its newest rows each bank is asked for when every bank is shown. */
export const MERGE_PER_BANK = 50
/** The most rows the merged list holds. */
export const MERGE_CAP = 200

/** An operation with the bank it belongs to. */
export type OperationRow = BankOperation & { bank: string }

export interface BankList {
  bankId: string
  operations: BankOperation[]
  /** How many rows the bank has in this status; `null` when the engine did not say. */
  total: number | null
}

export interface MergedList {
  rows: OperationRow[]
  /** Rows across the banks that answered; `null` when one did not say. */
  total: number | null
  /** The list stops short of `total`: older rows exist and are not in it. */
  capped: boolean
}

const time = (value: unknown): number => {
  const ms = typeof value === 'string' ? Date.parse(value) : NaN
  return Number.isNaN(ms) ? -Infinity : ms
}

/**
 * One list, newest first, from the newest rows of several banks.
 *
 * Each bank sent only its newest `perBank` rows. A bank that has more may hold rows that fall
 * between the ones that were read from the others, so the merged list stops at the oldest row
 * that every such bank was read back to. What it holds is then exactly the newest rows across
 * all the banks, with none missing in between. It is cut at `cap`.
 */
export function mergeOperationLists(
  lists: BankList[],
  { perBank = MERGE_PER_BANK, cap = MERGE_CAP }: { perBank?: number; cap?: number } = {},
): MergedList {
  let horizon = -Infinity
  let total: number | null = 0
  const rows: OperationRow[] = []
  for (const list of lists) {
    const read = list.operations.length
    // With no total, a full answer may not be the whole list.
    const more = list.total !== null ? list.total > read : read >= perBank
    if (list.total === null && more) total = null
    else if (total !== null) total += list.total ?? read
    if (more && read > 0) horizon = Math.max(horizon, time(list.operations[read - 1].created_at))
    for (const op of list.operations) rows.push({ ...op, bank: list.bankId })
  }
  const order = new Map(lists.map((list, at) => [list.bankId, at]))
  const kept = rows
    .filter((row) => time(row.created_at) >= horizon)
    .sort(
      (a, b) =>
        time(b.created_at) - time(a.created_at) ||
        (order.get(a.bank) ?? 0) - (order.get(b.bank) ?? 0) ||
        String(a.id).localeCompare(String(b.id)),
    )
    .slice(0, cap)
  return { rows: kept, total, capped: total === null || kept.length < total }
}

/** The banks a filter has rows in, so a quiet bank is not asked for nothing. */
export function banksForFilter(
  filter: OperationFilter,
  bankIds: string[],
  byBank: MemoryHealthQueueBank[] | null,
): string[] {
  if (!byBank || (filter !== 'waiting' && filter !== 'failed')) return bankIds
  const figures = new Map(byBank.map((row) => [row.bank_id, row]))
  return bankIds.filter((id) => {
    const row = figures.get(id)
    // A bank the health answer does not list, or has no figure for, is asked.
    return !row || count(row[filter]) !== 0
  })
}

/** When the newest finished operation finished; `null` when no row is finished. */
export function lastCompletedAt(lists: BankList[]): string | null {
  let best: string | null = null
  for (const list of lists) {
    for (const op of list.operations) {
      if (operationStatus(op.status).kind !== 'done') continue
      const at = op.updated_at || op.created_at
      if (at && time(at) > time(best)) best = at
    }
  }
  return best
}

// ── The health answer ─────────────────────────────────────────────────────────

/** Why extraction is the step to fix first. */
export interface ExtractionCause {
  /** The route's reason code; `null` when it sent one this file does not know. */
  reason: 'slot_unresolved' | 'llm_unreachable' | 'recent_failures' | null
  slot: string | null
  /** Stores that failed inside the window. */
  failures: number | null
  /** The window, as a duration (`6 h`). */
  window: string
  /** The most frequent failure message, cut to one line, and how often it was seen. */
  top: MemoryHealthErrorGroup | null
  /** How many other messages were seen. */
  otherMessages: number
}

export interface QueueReport {
  kind: 'ready'
  queue: MemoryHealthQueue | null
  /** Set while extraction is the current open step: retrying would fail. */
  cause: ExtractionCause | null
  /** Some banks did not answer in time: the figures are what was read. */
  partial: boolean
  /** A bank held more operations than were read. */
  truncated: boolean
  checkedAt: string | null
}

/**
 * `loading`      no answer yet
 * `failed`       hal0's API did not answer, or answered with something else
 * `off`          memory is turned off
 * `unsupported`  memory runs on an engine with no operations
 * `unreachable`  the memory engine does not answer
 * `ready`        the figures
 */
export type QueueHealth =
  | { kind: 'loading' | 'failed' | 'off' | 'unsupported' | 'unreachable' }
  | QueueReport

const REASONS: Record<string, ExtractionCause['reason']> = {
  extraction_slot_unresolved: 'slot_unresolved',
  extraction_llm_unreachable: 'llm_unreachable',
  extraction_recent_failures: 'recent_failures',
}

const DEFAULT_WINDOW_S = 6 * 60 * 60

function extractionCause(report: MemoryHealthReport): ExtractionCause | null {
  const steps = Array.isArray(report.steps) ? report.steps : []
  const step = steps.find((s) => isRecord(s) && s.id === 'extraction' && s.state === 'current')
  if (!step) return null
  const figures = isRecord(step.figures) ? step.figures : {}
  const extraction = isRecord(report.extraction) ? report.extraction : null
  const groups = groupErrors(extraction?.top_errors ?? figures.top_errors)
  return {
    reason: REASONS[String(step.reason_code ?? '')] ?? null,
    slot: typeof extraction?.slot === 'string' && extraction.slot ? extraction.slot : null,
    failures: count(extraction?.failures_6h) ?? count(figures.failures),
    window: formatDuration(count(figures.window_s) ?? DEFAULT_WINDOW_S) ?? '6 h',
    top: groups[0] ?? null,
    otherMessages: Math.max(0, groups.length - 1),
  }
}

/** Read the health answer. Anything that is not the route's shape reads as `failed`. */
export function readQueueHealth(query: { data?: MemoryHealth | null; isError?: boolean }): QueueHealth {
  const data: unknown = query.data
  if (data === undefined || data === null) return { kind: query.isError ? 'failed' : 'loading' }
  if (!isRecord(data)) return { kind: 'failed' }
  if (data.enabled === false) return { kind: 'off' }
  if (data.verdict === null && data.reason === 'engine_unsupported') return { kind: 'unsupported' }
  const engine = isRecord(data.engine) ? data.engine : null
  if (!engine) return { kind: 'failed' }
  if (data.verdict === 'unreachable' || engine.reachable !== true) return { kind: 'unreachable' }
  const report = data as unknown as MemoryHealthReport
  const queue = isRecord(report.queue) ? report.queue : null
  return {
    kind: 'ready',
    queue,
    cause: extractionCause(report),
    partial: Array.isArray(report.partial) && report.partial.includes('queue'),
    truncated: queue?.truncated === true,
    checkedAt: typeof report.checked_at === 'string' ? report.checked_at : null,
  }
}

export interface QueueFigures {
  waiting: number | null
  failed: number | null
  oldestWaitingS: number | null
  consolidationPending: number | null
  consolidationFailed: number | null
}

const NO_FIGURES: QueueFigures = {
  waiting: null,
  failed: null,
  oldestWaitingS: null,
  consolidationPending: null,
  consolidationFailed: null,
}

/**
 * The figures for the scope: every bank, or one. A bank's waiting and failed counts are its row
 * of the health answer; its consolidation figures are the bank's own stats (`bankStats`), which
 * are what the health route adds up.
 */
export function queueFigures(
  queue: MemoryHealthQueue | null,
  bank: string | null,
  bankStats?: { pending_consolidation?: unknown; failed_consolidation?: unknown } | null,
): QueueFigures {
  if (!queue) return NO_FIGURES
  if (bank === null) {
    return {
      waiting: count(queue.waiting),
      failed: count(queue.failed),
      oldestWaitingS: count(queue.oldest_waiting_s),
      consolidationPending: count(queue.consolidation_pending),
      consolidationFailed: count(queue.consolidation_failed),
    }
  }
  const row = (Array.isArray(queue.by_bank) ? queue.by_bank : []).find((b) => b?.bank_id === bank)
  return {
    waiting: count(row?.waiting),
    failed: count(row?.failed),
    oldestWaitingS: count(row?.oldest_waiting_s),
    consolidationPending: count(bankStats?.pending_consolidation),
    consolidationFailed: count(bankStats?.failed_consolidation),
  }
}

/** Nothing waiting and nothing failed, both reported. */
export function queueIsClear(figures: QueueFigures): boolean {
  return figures.waiting === 0 && figures.failed === 0
}

export interface BankBreakdown {
  /** Banks with something waiting or failed, or with a figure that was not reported. */
  rows: MemoryHealthQueueBank[]
  /** Banks with nothing waiting and nothing failed. They are counted, not listed. */
  quiet: number
}

/** The per-bank table: most failed first, then most waiting. */
export function bankBreakdown(queue: MemoryHealthQueue | null): BankBreakdown {
  const all = Array.isArray(queue?.by_bank) ? queue.by_bank.filter((b) => isRecord(b)) : []
  const rows = all
    .map((b) => ({
      bank_id: String(b.bank_id ?? ''),
      waiting: count(b.waiting),
      failed: count(b.failed),
      oldest_waiting_s: count(b.oldest_waiting_s),
    }))
    .filter((b) => b.bank_id && !(b.waiting === 0 && b.failed === 0))
    .sort(
      (a, b) =>
        (b.failed ?? -1) - (a.failed ?? -1) ||
        (b.waiting ?? -1) - (a.waiting ?? -1) ||
        a.bank_id.localeCompare(b.bank_id),
    )
  return { rows, quiet: all.length - rows.length }
}

/** `all on big`, `in 5 banks`: where a figure of the whole queue sits. `null` when it is zero. */
export function spreadText(queue: MemoryHealthQueue | null, key: 'waiting' | 'failed'): string | null {
  const holders = (Array.isArray(queue?.by_bank) ? queue.by_bank : []).filter(
    (b) => (count(b?.[key]) ?? 0) > 0,
  )
  if (holders.length === 0) return null
  if (holders.length === 1) return `all on ${holders[0].bank_id}`
  return `in ${formatCount(holders.length)} banks`
}

// ── Retry and discard, many at once ───────────────────────────────────────────

/** The most operations one run of "retry all" or "discard" takes on. */
export const BULK_CAP = 1000

export type BulkAction = 'retry' | 'discard'

/**
 * `collecting`  reading which operations are failed
 * `running`     `done` of `total` handled so far
 * `finished`    every one handled, or it stopped at the first that failed (`error`)
 */
export type BulkRun =
  | { phase: 'collecting'; action: BulkAction }
  | { phase: 'running'; action: BulkAction; done: number; total: number }
  | {
      phase: 'finished'
      action: BulkAction
      done: number
      total: number
      /** Why it stopped; `null` when it ran to the end. */
      error: string | null
      /** More failed operations exist than one run takes. */
      more: boolean
    }

const VERB = {
  retry: { ing: 'Retrying', ed: 'Queued', again: ' again', none: 'retry', past: 'retried' },
  discard: { ing: 'Discarding', ed: 'Discarded', again: '', none: 'discard', past: 'discarded' },
} as const

/** What a run says while it works and when it ends. */
export function bulkText(run: BulkRun): string {
  const verb = VERB[run.action]
  if (run.phase === 'collecting') return 'Reading the failed operations.'
  if (run.phase === 'running') {
    return `${verb.ing} ${formatCount(run.done + 1)} of ${formatCount(run.total)}.`
  }
  if (run.total === 0) return run.error ?? `Nothing to ${verb.none}: no failed operation was found.`
  if (run.error) {
    const left = plural(run.total - run.done, 'operation was', 'operations were')
    return `Stopped after ${formatCount(run.done)} of ${formatCount(run.total)}. ${left} not ${verb.past}. ${run.error}`
  }
  const ops = plural(run.done, 'failed operation', 'failed operations')
  const rest = run.more ? ' More are left: run it again for the rest.' : ''
  return `${verb.ed} ${ops}${verb.again}.${rest}`
}
