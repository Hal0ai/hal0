// What every tab of the Memory area needs to know about the engine and its banks, worked out
// from the answers the hooks hold. No React and no window.
//
// The page's summary line, the banks table on Health and the bank figures a tab is handed all
// come from ONE answer, the composed `GET /api/memory/health`. Nothing here is added up from
// per-bank requests.
//
// Two rules run through this file:
//   - A figure that was not reported is `null`, never `0`. A caller shows the "not reported"
//     mark for it. A sum says how many banks it covers, so a partial sum cannot pass for a total.
//   - While an answer is still on its way the state is `loading`, and nothing is counted.

import type { MemoryEngine } from '@/api/hooks/useHindsight'
import type { MemoryHealth, MemoryHealthReport, MemoryHealthStep } from '@/api/hooks/useMemory'
import { formatCount } from './shared/format-pure'

/** The part of a query result these functions read. */
export interface QueryLike<T> {
  data?: T | null
  isError?: boolean
  error?: unknown
}

/**
 * `loading`      no answer yet
 * `unreachable`  the check failed, or the engine did not answer it
 * `fallback`     memory runs, but not on Hindsight: nothing this area shows exists
 * `reachable`    Hindsight answered
 */
export type EngineState = 'loading' | 'unreachable' | 'fallback' | 'reachable'

/** The engine check (`GET /api/memory/engine`), as the tabs that list banks read it. */
export function engineState(query: QueryLike<MemoryEngine>): EngineState {
  const engine = query.data
  if (!engine) return query.isError ? 'unreachable' : 'loading'
  // A provider with no Hindsight client answers `enabled: true, engine: null`.
  if (engine.enabled === true && engine.engine !== 'hindsight') return 'fallback'
  return engine.reachable === true ? 'reachable' : 'unreachable'
}

// ── The health answer ─────────────────────────────────────────────────────────

/**
 * `loading`      no answer yet
 * `failed`       hal0's own API did not answer, or answered with something else
 * `off`          memory is turned off
 * `unsupported`  memory runs on an engine with no banks, operations or knowledge
 * `unreachable`  the memory engine does not answer; the report carries no figure
 * `ready`        the report
 */
export type HealthView =
  | { kind: 'loading' }
  | { kind: 'failed' }
  | { kind: 'off' }
  | { kind: 'unsupported' }
  | { kind: 'unreachable'; report: MemoryHealthReport }
  | { kind: 'ready'; report: MemoryHealthReport }

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

function section<T>(value: unknown): T | null {
  return isRecord(value) ? (value as T) : null
}

/**
 * Read the health answer. Anything that is not the route's shape reads as `failed`, and a report
 * comes back with every list a list and every section an object or `null`, so a caller never
 * has to check a shape again.
 */
export function readHealth(query: QueryLike<MemoryHealth>): HealthView {
  const data: unknown = query.data
  if (data === undefined || data === null) return { kind: query.isError ? 'failed' : 'loading' }
  if (!isRecord(data)) return { kind: 'failed' }
  if (data.enabled === false) return { kind: 'off' }
  if (data.verdict === null && data.reason === 'engine_unsupported') return { kind: 'unsupported' }
  const engine = section<MemoryHealthReport['engine']>(data.engine)
  if (!engine) return { kind: 'failed' }
  const report: MemoryHealthReport = {
    enabled: true,
    verdict: data.verdict === 'clear' ? 'clear' : data.verdict === 'unreachable' ? 'unreachable' : 'attention',
    checked_at: typeof data.checked_at === 'string' ? data.checked_at : '',
    partial: (Array.isArray(data.partial) ? data.partial : []).filter(
      (name): name is MemoryHealthReport['partial'][number] => typeof name === 'string',
    ),
    engine: { reachable: engine.reachable === true, version: typeof engine.version === 'string' ? engine.version : null },
    extraction: section(data.extraction),
    embedder: section(data.embedder),
    queue: section(data.queue),
    knowledge: section(data.knowledge),
    banks: section(data.banks),
    writes: null,
    steps: (Array.isArray(data.steps) ? data.steps : []).filter(
      (step): step is MemoryHealthStep => isRecord(step) && typeof step.id === 'string',
    ),
  }
  if (data.verdict === 'unreachable' || !report.engine.reachable) return { kind: 'unreachable', report }
  // A verdict this file does not know is decided from the steps, never assumed clear.
  if (data.verdict !== 'clear' && data.verdict !== 'attention') {
    report.verdict = report.steps.some((step) => step.state !== 'clear') ? 'attention' : 'clear'
  }
  return { kind: 'ready', report }
}

/** The engine's state as the health answer has it, for a tab that does not ask the engine itself. */
export function engineFromHealth(view: HealthView): EngineState {
  if (view.kind === 'loading') return 'loading'
  if (view.kind === 'unsupported') return 'fallback'
  return view.kind === 'ready' ? 'reachable' : 'unreachable'
}

// ── Banks ─────────────────────────────────────────────────────────────────────

export interface BankRow {
  id: string
  facts: number | null
  documents: number | null
  waiting: number | null
  failed: number | null
  /** The bank's operation counts were read. False for a bank that did not answer in time. */
  reported: boolean
}

const figure = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

/**
 * One row per bank, joined from the two places the health answer keeps a bank: `banks.list` has
 * its facts and documents, `queue.by_bank` its waiting and failed operations. A bank named in
 * only one of them still gets a row, with the other figures not reported.
 *
 * Largest first; a bank whose fact count is unknown sorts last. The order among equals is the
 * engine's.
 */
export function healthBankRows(report: Pick<MemoryHealthReport, 'banks' | 'queue'> | null | undefined): BankRow[] {
  const list = Array.isArray(report?.banks?.list) ? report.banks.list : []
  const byBank = Array.isArray(report?.queue?.by_bank) ? report.queue.by_bank : []
  const queue = new Map(
    byBank.filter((row) => isRecord(row) && typeof row.bank_id === 'string').map((row) => [row.bank_id, row]),
  )
  const rows: BankRow[] = []
  const seen = new Set<string>()
  const add = (id: string, bank: { facts?: unknown; documents?: unknown } | null) => {
    if (seen.has(id)) return
    seen.add(id)
    const q = queue.get(id)
    const waiting = figure(q?.waiting)
    const failed = figure(q?.failed)
    rows.push({
      id,
      facts: figure(bank?.facts),
      documents: figure(bank?.documents),
      waiting,
      failed,
      reported: waiting !== null && failed !== null,
    })
  }
  for (const bank of list) {
    if (isRecord(bank) && typeof bank.bank_id === 'string' && bank.bank_id) add(bank.bank_id, bank)
  }
  for (const id of queue.keys()) add(id, null)
  return rows
    .map((row, at) => ({ row, at }))
    .sort((a, b) => (b.row.facts ?? -1) - (a.row.facts ?? -1) || a.at - b.at)
    .map(({ row }) => row)
}

export interface BankTotals {
  /** How many banks the engine has. */
  banks: number
  /** How many banks this page holds rows for. */
  listed: number
  /** How many of them reported a fact count. */
  counted: number
  /** How many reported their operation counts. */
  reported: number
  /** Sums over the banks that reported; `null` when none did. */
  facts: number | null
  waiting: number | null
  failed: number | null
}

const sum = (values: (number | null)[]): number | null => {
  const known = values.filter((v): v is number => v !== null)
  return known.length > 0 ? known.reduce((a, b) => a + b, 0) : null
}

export function bankTotals(rows: BankRow[], engineTotal?: number | null): BankTotals {
  const reported = rows.filter((row) => row.reported)
  return {
    banks: Math.max(figure(engineTotal) ?? 0, rows.length),
    listed: rows.length,
    counted: rows.filter((row) => row.facts !== null).length,
    reported: reported.length,
    facts: sum(rows.map((row) => row.facts)),
    waiting: sum(reported.map((row) => row.waiting)),
    failed: sum(reported.map((row) => row.failed)),
  }
}

/** The health answer could not list the banks at all (its `banks.total` is not a number). */
export function bankListMissing(view: HealthView): boolean {
  return view.kind === 'ready' && figure(view.report.banks?.total) === null
}

/** `1 bank`, `21 banks`, `52,955 facts`. */
export function plural(count: number, one: string, many: string): string {
  return `${formatCount(count) ?? String(count)} ${count === 1 ? one : many}`
}

// ── The summary line ──────────────────────────────────────────────────────────

export type MetaLine = { kind: 'loading' } | { kind: 'text'; text: string }

/**
 * The one-line summary beside the page title: whether the engine answers, how many banks, how
 * many facts. It carries a number only when that number was reported: no counts while loading,
 * none when the engine is down.
 */
export function metaLine(view: HealthView): MetaLine {
  if (view.kind === 'loading') return { kind: 'loading' }
  if (view.kind === 'failed') return { kind: 'text', text: 'Memory status did not load · no counts available' }
  if (view.kind === 'off') return { kind: 'text', text: 'Memory is turned off · no counts available' }
  if (view.kind === 'unsupported') return { kind: 'text', text: 'Fallback engine · no counts available' }
  if (view.kind === 'unreachable') return { kind: 'text', text: 'Engine unreachable · no counts available' }
  if (bankListMissing(view)) return { kind: 'text', text: 'Engine reachable · bank list not available' }
  const totals = bankTotals(healthBankRows(view.report), view.report.banks?.total)
  if (totals.banks === 0) return { kind: 'text', text: 'Engine reachable · no banks yet' }
  const banks = plural(totals.banks, 'bank', 'banks')
  if (totals.facts === null) return { kind: 'text', text: `${banks} · fact counts not reported` }
  const facts = plural(totals.facts, 'fact', 'facts')
  if (totals.counted < totals.banks) {
    return { kind: 'text', text: `${banks} · ${facts} in the ${formatCount(totals.counted)} that reported` }
  }
  return { kind: 'text', text: `${facts} in ${banks}` }
}
