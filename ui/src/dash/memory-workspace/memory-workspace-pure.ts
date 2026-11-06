// hal0 Memory workspace — pure helpers (no React, no fetching) so vitest can
// pin formatting/derivation logic without a DOM or network mocks.

export interface LaneBankRow {
  bank_id: string
  pending: number
  processing: number
  oldest_pending_age_s: number | null
}

/** Sort banks by queue pressure: pending desc, then oldest-pending age desc. Stable for ties (bank_id asc). */
export function sortBanksByPressure(banks: LaneBankRow[]): LaneBankRow[] {
  return [...banks].sort((a, b) => {
    if (b.pending !== a.pending) return b.pending - a.pending
    const ao = a.oldest_pending_age_s ?? -1
    const bo = b.oldest_pending_age_s ?? -1
    if (bo !== ao) return bo - ao
    return a.bank_id < b.bank_id ? -1 : a.bank_id > b.bank_id ? 1 : 0
  })
}

/** Total pending + processing ops across all banks; 0 for an empty/undefined list. */
export function totalQueueDepth(banks: LaneBankRow[] | undefined): number {
  if (!banks || banks.length === 0) return 0
  return banks.reduce((sum, b) => sum + (b.pending || 0) + (b.processing || 0), 0)
}

/** The single oldest-pending age across all banks, or null if nothing is pending. */
export function oldestPendingAge(banks: LaneBankRow[] | undefined): number | null {
  if (!banks) return null
  const ages = banks.map((b) => b.oldest_pending_age_s).filter((a): a is number => a != null)
  return ages.length ? Math.max(...ages) : null
}

/** Human-readable duration ("3s", "4m", "2h"), '—' for null/undefined/negative. */
export function fmtAge(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0 || !Number.isFinite(seconds)) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${Math.round(seconds / 3600)}h`
}

/** Lane health badge tone: 'ok' | 'warn' | 'bad' | 'unknown', driven by the decision lane's health string. */
export function laneHealthTone(health: string | undefined): 'ok' | 'warn' | 'bad' | 'unknown' {
  const h = (health || '').toLowerCase()
  if (!h) return 'unknown'
  if (h.includes('ok') || h.includes('healthy') || h.includes('ready')) return 'ok'
  if (h.includes('degrad') || h.includes('warn') || h.includes('slow')) return 'warn'
  if (h.includes('down') || h.includes('fail') || h.includes('error') || h.includes('unavail')) return 'bad'
  return 'unknown'
}

export interface LlmOpStat {
  operation?: string
  count?: number
  [key: string]: unknown
}

/** Normalize an llm-requests/stats payload (whose exact shape varies by op) into sorted {operation,count} rows, desc by count. */
export function normalizeLlmOpStats(raw: unknown): { operation: string; count: number }[] {
  if (!raw || typeof raw !== 'object') return []
  const obj = raw as Record<string, unknown>
  const byOp = (obj.by_operation ?? obj.operations ?? obj) as unknown
  const rows: { operation: string; count: number }[] = []
  if (Array.isArray(byOp)) {
    for (const r of byOp as LlmOpStat[]) {
      if (r && typeof r.operation === 'string') rows.push({ operation: r.operation, count: Number(r.count) || 0 })
    }
  } else if (byOp && typeof byOp === 'object') {
    for (const [k, v] of Object.entries(byOp as Record<string, unknown>)) {
      const count = typeof v === 'number' ? v : typeof v === 'object' && v && 'count' in (v as object)
        ? Number((v as { count?: unknown }).count) || 0
        : NaN
      if (!Number.isNaN(count)) rows.push({ operation: k, count })
    }
  }
  return rows.sort((a, b) => b.count - a.count)
}

/** A knowledge-base page is stale when the backend says so, or (fallback) its content is older than its source_query's trigger would imply — here we only trust the explicit flag. */
export function pageIsStale(page: { is_stale?: boolean } | null | undefined): boolean {
  return !!page?.is_stale
}

export interface OperationRow {
  id: string
  status: string
  [key: string]: unknown
}

/** Filter + count operations by status for the Operations table's filter chips. */
export function operationsByStatus(ops: OperationRow[], status: string | 'all'): OperationRow[] {
  if (status === 'all') return ops
  return ops.filter((o) => o.status === status)
}

export function operationStatusCounts(ops: OperationRow[]): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const o of ops) counts[o.status] = (counts[o.status] || 0) + 1
  return counts
}

/** Build the copyable coding-agent install one-liner for a specific harness from the template command (which contains a literal `<harness>` placeholder). */
export function installCommandFor(template: string, harness: string): string {
  return template.replace('<harness>', harness)
}
