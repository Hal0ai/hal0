// hal0 v3 dashboard — /api/stats/hardware (live counters).
//
// Distinct from useHardware (static probe): this hook polls the live
// counters (gtt_used_mb, ram_used_mb, npu_status.model_mb, host.*) at
// 2.5s — same cadence the backend was designed for. Used by the
// MemoryMap component (sidebar + expanded variants).

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

/**
 * Per-tenant shape — emitted by GET /api/settings/proxmox (full shape).
 * NOT present on GET /api/stats/hardware (project_slim strips it; see
 * src/hal0/hardware/pve.py:_SLIM_DROP_KEYS). Exported here for reuse
 * by the future settings-shape hook.
 */
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
  per_upstream?: Record<string, unknown>
  upstream_names?: string[]
}

const POLL_MS = 2_500

export function useStatsHardware() {
  return useQuery<StatsHardware>({
    queryKey: ['stats', 'hardware'],
    queryFn: () => apiGet<StatsHardware>(ENDPOINTS.statsHardware),
    refetchInterval: POLL_MS,
  })
}
