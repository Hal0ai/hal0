// hal0 Overview v2 — /api/system-stats (fleet gauges + per-slot live counters).
//
// Polls every 5 s. retry:false so a box without the route fails soft: sections
// read `isError` / undefined `data` and render "—" rather than blocking.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface SystemStatsFleet {
  gpu_util: number | null
  vram_bytes: number | null
  gtt_bytes: number | null
  power_w: number | null
  temp_c: number | null
}

export interface SystemStatsSlot {
  slot_id: string
  state: string
  vram_bytes: number | null
  inflight: number | null
  kv_used: number | null
  ts: string
}

export interface SystemStats {
  ts: string
  fleet: SystemStatsFleet
  slots: SystemStatsSlot[]
}

const POLL_MS = 5_000

export function useSystemStats() {
  return useQuery<SystemStats>({
    queryKey: ['system-stats'],
    queryFn: () => apiGet<SystemStats>(ENDPOINTS.systemStats),
    refetchInterval: POLL_MS,
    retry: false,
  })
}
