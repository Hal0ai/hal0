// hal0 Overview v2 — pure helpers for the Memory card. No React, no window globals.

import type { BankStats } from '@/api/hooks/useHindsight'
import { relTime } from './overview-pure'

const DASH = '—'

/**
 * The bank the Memory pane would open: the stored choice (localStorage `hal0.mem.bank`) when it is
 * still a bank, else the bank a `#memory/<bank>` or `#memory/bank?bank=<bank>` hash names, else the
 * first bank. Null with no banks.
 */
export function chosenBank(banks: string[], stored: string | null, hash: string): string | null {
  if (banks.length === 0) return null
  if (stored && banks.includes(stored)) return stored
  const qIdx = hash.indexOf('?')
  const fromQuery = qIdx === -1 ? null : new URLSearchParams(hash.slice(qIdx + 1)).get('bank')
  if (fromQuery && banks.includes(fromQuery)) return fromQuery
  const path = (qIdx === -1 ? hash : hash.slice(0, qIdx)).replace(/^#/, '').split('/')
  if (path[0] === 'memory' && path[1]) {
    let seg = path[1]
    try {
      seg = decodeURIComponent(seg)
    } catch {
      /* keep the raw segment */
    }
    if (banks.includes(seg)) return seg
  }
  return banks[0]
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
