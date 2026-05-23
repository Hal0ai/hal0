// hal0 v3 dashboard — Lemonade runtime hooks (Phase B1).
//
// Ported semantics from ui-vue.bak/src/stores/lemonade.js:
//   - /v1/health polled every 2s — loaded models, max budget, version.
//   - /v1/stats polled every 5s — last-request snapshot (TTFT, tok/s).
//
// Per the hal0_lemonade_ws_protocol memory, no model-load WS event
// exists on /logs/stream — polling /v1/health stays canonical.

import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface LoadedModelEntry {
  model_name: string
  backend_url?: string
  last_use?: number
}

export interface LemonadeHealth {
  loaded: LoadedModelEntry[]
  max_loaded: number | null
  version: string | null
  throughput_mbps: number | null
  // Lemonade /v1/health does not currently surface a queue depth or a
  // coresident rollup (see hal0_lemonade_v1_load_schema). The hook
  // preserves them as `null` so the UI can distinguish "not surfaced"
  // from "really zero" and hide chips that have no real signal yet.
  queued: number | null
  coresident: boolean | null
}

export interface LemonadeStats {
  time_to_first_token?: number
  tokens_per_second?: number
  prompt_tokens?: number
  output_tokens?: number
  input_tokens?: number
}

const POLL_HEALTH_MS = 2_000
const POLL_STATS_MS = 5_000

/** Polls `/v1/health` every 2s. Single source of truth for "is lemond up". */
export function useLemonadeHealth(): UseQueryResult<LemonadeHealth> {
  return useQuery({
    queryKey: ['lemonade', 'health'],
    queryFn: async () => {
      const body = await apiGet<any>(ENDPOINTS.lemonade.health)
      return {
        loaded: Array.isArray(body?.loaded) ? body.loaded : [],
        max_loaded: typeof body?.max_loaded === 'number' ? body.max_loaded : null,
        version: typeof body?.version === 'string' ? body.version : null,
        throughput_mbps:
          typeof body?.throughput_mbps === 'number' ? body.throughput_mbps : null,
        // Explicit null (NOT 0) when the field is missing — see #221.
        queued: typeof body?.queued === 'number' ? body.queued : null,
        coresident: typeof body?.coresident === 'boolean' ? body.coresident : null,
      }
    },
    refetchInterval: POLL_HEALTH_MS,
    staleTime: 0,
  })
}

/** Polls `/v1/stats` every 5s — last-request rollup. */
export function useLemonadeStats(): UseQueryResult<LemonadeStats> {
  return useQuery({
    queryKey: ['lemonade', 'stats'],
    queryFn: () => apiGet<LemonadeStats>(ENDPOINTS.lemonade.stats),
    refetchInterval: POLL_STATS_MS,
    staleTime: 0,
  })
}

/** Loaded-model list as a `Set<model_name>` for O(1) SlotCard lookups. */
export function useLoadedModelNames(): Set<string> {
  const { data } = useLemonadeHealth()
  return new Set((data?.loaded ?? []).map((m) => m.model_name).filter(Boolean))
}

/**
 * Roll-up suitable for chrome / footer chips. Returns the legacy v2
 * shape ({status, version, loaded, budget, throughput, queued}) so the
 * prototype JSX consuming `HAL0_DATA.lemond` can swap in a one-liner.
 */
export function useLemondRollup() {
  const health = useLemonadeHealth()
  const stats = useLemonadeStats()
  const h = health.data
  const isUp = health.isSuccess && !!h
  return {
    status: isUp ? 'up' : health.isError ? 'down' : 'connecting',
    version: h?.version ?? '—',
    loaded: h?.loaded.length ?? 0,
    budget: h?.max_loaded ?? 4,
    throughput: h?.throughput_mbps ?? null,
    // queued + coresident are null until Lemonade exposes them on
    // /v1/health (#221, follow-up filed). Chips that consume these
    // guard with `!= null` so they hide instead of lying with 0.
    queued: h?.queued ?? null,
    coresident: h?.coresident ?? null,
    lastTtft: stats.data?.time_to_first_token ?? null,
    lastTokPerSec: stats.data?.tokens_per_second ?? null,
  }
}
