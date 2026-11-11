// What every tab of the Memory area needs to know about the engine and its banks, worked out
// from the answers the hooks hold. No React and no window.
//
// Two rules run through this file:
//   - A figure that was not reported is `null`, never `0`. A caller shows the "not reported"
//     mark for it. A sum says how many banks it covers, so a partial sum cannot pass for a total.
//   - While an answer is still on its way the state is `loading`, and nothing is counted.

import type { BankStats, MemoryBank, MemoryEngine } from '@/api/hooks/useHindsight'
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

export function engineState(query: QueryLike<MemoryEngine>): EngineState {
  const engine = query.data
  if (!engine) return query.isError ? 'unreachable' : 'loading'
  // A provider with no Hindsight client answers `enabled: true, engine: null`.
  if (engine.enabled === true && engine.engine !== 'hindsight') return 'fallback'
  return engine.reachable === true ? 'reachable' : 'unreachable'
}

/** One bank's stats as the fan-out hook holds them. */
export interface BankStatsEntry {
  bankId: string
  stats?: BankStats | null
  isLoading?: boolean
  isError?: boolean
}

export interface BankRow {
  id: string
  facts: number | null
  documents: number | null
  waiting: number | null
  failed: number | null
  /** The bank's stats answered. False while loading and after a failure. */
  reported: boolean
}

const figure = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

/**
 * One row per listed bank, largest first; a bank whose fact count is unknown sorts last. The
 * order among equals is the engine's.
 */
export function bankRows(banks: MemoryBank[], stats: BankStatsEntry[]): BankRow[] {
  const byId = new Map(stats.map((entry) => [entry.bankId, entry]))
  const rows = banks.map((bank): BankRow => {
    const s = byId.get(bank.bank_id)?.stats ?? null
    return {
      id: bank.bank_id,
      // The bank list sometimes carries a count of its own; it stands in when stats did not answer.
      facts: figure(s?.total_nodes) ?? figure(bank.fact_count),
      documents: figure(s?.total_documents),
      waiting: figure(s?.pending_operations),
      failed: figure(s?.failed_operations),
      reported: !!s,
    }
  })
  return rows
    .map((row, at) => ({ row, at }))
    .sort((a, b) => (b.row.facts ?? -1) - (a.row.facts ?? -1) || a.at - b.at)
    .map(({ row }) => row)
}

export interface BankTotals {
  /** How many banks the engine has. More than `listed` when its list is paged. */
  banks: number
  /** How many banks this page holds rows for. */
  listed: number
  /** How many of them reported a fact count. */
  counted: number
  /** How many reported their operation counts (their stats answered). */
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

/** `1 bank`, `21 banks`, `52,955 facts`. */
export function plural(count: number, one: string, many: string): string {
  return `${formatCount(count) ?? String(count)} ${count === 1 ? one : many}`
}

export type MetaLine = { kind: 'loading' } | { kind: 'text'; text: string }

/**
 * The one-line summary beside the page title. It carries a number only when that number was
 * reported: no counts while loading, none when the engine is down.
 */
export function metaLine(input: {
  engine: EngineState
  banksLoading: boolean
  banksFailed: boolean
  statsLoading: boolean
  totals: BankTotals
}): MetaLine {
  const { engine, totals } = input
  if (engine === 'loading') return { kind: 'loading' }
  if (engine === 'unreachable') return { kind: 'text', text: 'Engine unreachable · no counts available' }
  if (engine === 'fallback') return { kind: 'text', text: 'Fallback engine · no counts available' }
  if (input.banksFailed) return { kind: 'text', text: 'Engine reachable · bank list not available' }
  if (input.banksLoading || input.statsLoading) return { kind: 'loading' }
  if (totals.banks === 0) return { kind: 'text', text: 'Engine reachable · no banks yet' }
  const banks = plural(totals.banks, 'bank', 'banks')
  if (totals.facts === null) return { kind: 'text', text: `${banks} · fact counts not reported` }
  const facts = plural(totals.facts, 'fact', 'facts')
  if (totals.counted < totals.banks) {
    return { kind: 'text', text: `${banks} · ${facts} in the ${formatCount(totals.counted)} that reported` }
  }
  return { kind: 'text', text: `${facts} in ${banks}` }
}
