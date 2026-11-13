// hal0 Overview v2 — pure helpers for the Memory card. No React, no window globals.

import type { BankStats } from '@/api/hooks/useHindsight'
import { parseMemoryRoute, serializeMemoryRoute } from '../memory-area/memory-route-pure'
import { relTime } from './overview-pure'

const DASH = '—'

/**
 * The bank the card shows and its link opens: the stored choice (localStorage `hal0.mem.bank`,
 * written by Memory ▸ Facts) when it is still a bank, else the bank a Memory address
 * (`#memory/facts?bank=<bank>`) names, else the first bank. Null with no banks.
 */
export function chosenBank(banks: string[], stored: string | null, hash: string): string | null {
  if (banks.length === 0) return null
  if (stored && banks.includes(stored)) return stored
  const fromHash = parseMemoryRoute(hash).bank
  if (fromHash && banks.includes(fromHash)) return fromHash
  return banks[0]
}

/**
 * Where the card's link goes, as a route id (no leading `#`): the chosen bank's facts, or the
 * Memory area's first tab when there is no bank to open.
 */
export function memoryCardTarget(bank: string | null): string {
  return (bank ? serializeMemoryRoute({ tab: 'facts', bank }) : serializeMemoryRoute({ tab: 'health' })).slice(1)
}

export interface BankFigures {
  nodes: string
  links: string
  obs: string
  pending: number | null
  consolidated: string | null
}

const fmtInt = (n: unknown): string =>
  typeof n === 'number' && Number.isFinite(n) ? n.toLocaleString('en-US') : DASH

/** The card's three figures plus the pending count and last-consolidation age; unread figures are a dash or null, never 0. */
export function bankFigures(stats: BankStats | undefined, nowS: number = Date.now() / 1000): BankFigures {
  const ms = stats?.last_consolidated_at ? Date.parse(stats.last_consolidated_at) : NaN
  return {
    nodes: fmtInt(stats?.total_nodes),
    links: fmtInt(stats?.total_links),
    obs: fmtInt(stats?.total_observations),
    pending: typeof stats?.pending_operations === 'number' ? stats.pending_operations : null,
    consolidated: Number.isFinite(ms) ? relTime(ms / 1000, nowS) : null,
  }
}
