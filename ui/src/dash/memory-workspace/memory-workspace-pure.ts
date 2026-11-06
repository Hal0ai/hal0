// hal0 Memory workspace — pure helpers (no React, no fetching) so vitest can
// pin formatting/derivation logic without a DOM or network mocks.

export interface LaneBankRow {
  bank_id: string
  pending: number
  processing: number
  oldest_pending_age_s: number | null
}

/** Narrow an unknown value to a plain object map (null for anything else). */
function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  return value as Record<string, unknown>
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

export interface LlmOpCallsRow {
  operation: string
  calls: number
}

/**
 * Upstream path for the LLM request stats the Overview card renders.
 *
 * Hindsight 1.2.0 (the pinned engine, `HINDSIGHT_API_PIN` in
 * `src/hal0/memory/engine_upgrade.py`) exposes llm-request stats only on the
 * bank-scoped route `/v1/default/banks/{bank_id}/llm-requests/stats` — there
 * is no non-bank `/v1/default/llm-requests/stats`. The engine passthrough
 * admits `v1/default/banks/**` and forwards query params verbatim, so
 * `split_by=operation` (the only split the engine honours besides `model`)
 * is what makes each bucket carry a per-operation `series`.
 */
export function llmOpStatsPath(bank: string): string {
  return `v1/default/banks/${encodeURIComponent(bank)}/llm-requests/stats?split_by=operation`
}

/**
 * Project an llm-requests/stats payload into one row per operation.
 *
 * The engine answers with time buckets
 * (`{bank_id, period, trunc, start, buckets[], retention_days}`); each
 * bucket's `series` maps an operation name to that bucket's `{calls, tokens}`
 * sums, and is only present when the request asked to split by operation.
 * Per-operation totals are therefore the sum of `series[*].calls` over the
 * buckets. Rows are ordered by calls desc, then operation name asc.
 */
export function llmCallsByOperation(raw: unknown): LlmOpCallsRow[] {
  const buckets = asRecord(raw)?.buckets
  if (!Array.isArray(buckets)) return []
  const totals = new Map<string, number>()
  for (const bucket of buckets) {
    const series = asRecord(asRecord(bucket)?.series)
    if (!series) continue
    for (const [operation, sums] of Object.entries(series)) {
      const calls = asRecord(sums)?.calls
      if (typeof calls !== 'number' || !Number.isFinite(calls)) continue
      totals.set(operation, (totals.get(operation) ?? 0) + calls)
    }
  }
  return [...totals.entries()]
    .map(([operation, calls]) => ({ operation, calls }))
    .sort((a, b) => b.calls - a.calls || a.operation.localeCompare(b.operation))
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
