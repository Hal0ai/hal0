// hal0 Overview v2 — pure model for the Usage card (GET /api/stats).
// No React, no window globals: unit-testable in isolation.

import type { StatsModelRow, StatsSummary, StatsWindow } from '@/api/hooks/useStatsSummary'
import { fmtK } from './overview-pure'

const DASH = '—'

export interface UsageRow {
  model: string
  req: number
  share: number
  ttft: string
  tps: string
  accept: string
  errors: number
}

export interface UsageModel {
  empty: boolean
  tokens: string
  requests: number | null
  errors: number | null
  decode: string
  prefill: string
  ttft: string
  p95: string
  buckets: number[]
  bucketNote: string
  rows: UsageRow[]
}

const BUCKET_NOTE: Record<StatsWindow, string> = {
  '1h': 'tokens per 2.5 min',
  '24h': 'tokens per hour',
  '7d': 'tokens per 8 h',
}

const finite = (n: number | null | undefined): n is number => typeof n === 'number' && Number.isFinite(n)
const reqOf = (m: StatsModelRow) => m.ok + m.errors
const secs = (ms: number | null | undefined) => (finite(ms) ? `${(ms / 1000).toFixed(1)}s` : DASH)
const tps = (n: number | null | undefined) => (finite(n) ? n.toFixed(1) : DASH)

/** Request-weighted mean of a per-model figure; null when no model reports it. */
function weighted(models: StatsModelRow[], pick: (m: StatsModelRow) => number | null): number | null {
  let sum = 0
  let weight = 0
  for (const m of models) {
    const v = pick(m)
    if (!finite(v)) continue
    const w = Math.max(reqOf(m), 1)
    sum += v * w
    weight += w
  }
  return weight > 0 ? sum / weight : null
}

export function usageModel(s: StatsSummary | undefined, win: StatsWindow): UsageModel {
  const bucketNote = BUCKET_NOTE[win]
  if (!s) {
    return { empty: false, tokens: DASH, requests: null, errors: null, decode: DASH, prefill: DASH, ttft: DASH, p95: DASH, buckets: [], bucketNote, rows: [] }
  }
  const buckets = (s.buckets ?? []).map((b) => b.tokens_completed)
  const empty = s.totals.requests === 0
  if (empty) {
    return { empty, tokens: DASH, requests: 0, errors: 0, decode: DASH, prefill: DASH, ttft: DASH, p95: DASH, buckets, bucketNote, rows: [] }
  }
  const models = s.by_model
  const prefill = weighted(models, (m) => m.tps_prefill.avg)
  const p95s = models.map((m) => m.ttft_ms.p95).filter(finite)
  const rows: UsageRow[] = [...models]
    .sort((a, b) => reqOf(b) - reqOf(a))
    .map((m) => ({
      model: m.model_id,
      req: reqOf(m),
      share: (reqOf(m) / s.totals.requests) * 100,
      ttft: secs(m.ttft_ms.p50),
      tps: tps(m.tps_decode.p50),
      accept: finite(m.spec_accept_rate) ? `${Math.round(m.spec_accept_rate * 100)}%` : DASH,
      errors: m.errors,
    }))
  return {
    empty,
    tokens: fmtK(s.totals.tokens_completed),
    requests: s.totals.requests,
    errors: s.totals.errors,
    decode: tps(weighted(models, (m) => m.tps_decode.avg)),
    prefill: prefill === null ? DASH : String(Math.round(prefill)),
    ttft: secs(weighted(models, (m) => m.ttft_ms.p50)),
    p95: p95s.length ? secs(Math.max(...p95s)) : DASH,
    buckets,
    bucketNote,
    rows,
  }
}
