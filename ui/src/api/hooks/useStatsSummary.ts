// hal0 Overview v2 — /api/stats?window=<1h|24h|7d> request-metric rollup.
//
// Polls every 30 s. retry:false so a box without the route fails soft:
// the Usage card reads undefined `data` and renders "—" instead of blocking.
// `buckets` is optional: older servers return totals + by_model only.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export type StatsWindow = '1h' | '24h' | '7d'

export interface StatsModelRow {
  model_id: string
  ttft_ms: { p50: number | null; p95: number | null }
  tps_decode: { avg: number | null; p50: number | null }
  tps_prefill: { avg: number | null }
  spec_accept_rate: number | null
  ok: number
  errors: number
}

export interface StatsSummary {
  window: { from: string; to: string; bucket: string }
  totals: { requests: number; ok: number; errors: number; tokens_completed: number }
  by_model: StatsModelRow[]
  buckets?: Array<{ ts: string; requests: number; tokens_completed: number }>
}

const POLL_MS = 30_000

export function useStatsSummary(window: StatsWindow) {
  return useQuery<StatsSummary>({
    queryKey: ['stats-summary', window],
    queryFn: () => apiGet<StatsSummary>(`${ENDPOINTS.stats}?window=${window}`),
    refetchInterval: POLL_MS,
    retry: false,
  })
}
