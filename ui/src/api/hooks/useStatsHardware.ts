// hal0 v3 dashboard — /api/stats/hardware (live counters).
//
// Distinct from useHardware (static probe): this hook polls the live
// counters (gtt_used_mb, ram_used_mb, npu_status.model_mb, host.*) at
// 2.5s — same cadence the backend was designed for. Used by the
// MemoryMap component (sidebar + expanded variants).

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface StatsHardwareTenant {
  vmid: number
  name: string
  type: 'lxc' | 'qemu'
  status: string
  mem_mb: number
  maxmem_mb: number
}

export interface StatsHardwareHost {
  configured: boolean
  detected?: boolean
  detection?: 'detected' | 'uncertain' | 'not_detected'
  hint?: string
  ok?: boolean
  node?: string
  host_mem_total_mb?: number
  host_mem_used_mb?: number
  host_mem_free_mb?: number
  tenants_running?: number
  tenants_total?: number
  tenants?: StatsHardwareTenant[]
}

export interface StatsHardware {
  ram_used_mb?: number
  ram_used_gb?: number
  ram_available_gb?: number
  gtt_used_mb?: number | null
  vram_used_mb?: number | null
  gpu_util?: number | null
  gpu_vram_used_mb?: number | null
  gpu_vram_total_mb?: number | null
  npu_status?: { ok: boolean; model_mb: number }
  host?: StatsHardwareHost
}

const POLL_MS = 2_500

export function useStatsHardware() {
  return useQuery<StatsHardware>({
    queryKey: ['stats-hardware'],
    queryFn: () => apiGet<StatsHardware>(ENDPOINTS.statsHardware),
    refetchInterval: POLL_MS,
  })
}
