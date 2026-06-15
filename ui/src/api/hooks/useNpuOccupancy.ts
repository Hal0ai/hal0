// hal0 v3 dashboard — /api/npu/occupancy (NPU occupancy card).
//
// AIE column allocation (from an xrt-smi probe exec'd into the live FLM
// container, cached server-side ~30s) plus the per-FLM-slot composition.
// The NPU is single-tenant: one FLM process claims the whole 8-column array,
// so cols_used is effectively 0 or 8. The card merges per-slot tok/s · ttft ·
// RAM from useSlots / useStatsHardware — this hook owns only the columns.
//
// Polled at 2.5s (the hot path is cheap; the expensive xrt-smi exec is behind
// the server's own TTL cache). `columns_available: false` → degraded grid.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface NpuOccupancySlot {
  name: string
  model: string | null
  state: 'serving' | 'ready' | 'loaded' | 'idle' | 'offline'
  cols: number[]
  gb: number | null
}

export interface NpuOccupancy {
  present: boolean
  rows: number
  cols: number
  tiles: number
  tops_peak: number
  cols_total: number
  cols_used: number
  serving: boolean
  single_tenant: boolean
  columns_available: boolean
  slots: NpuOccupancySlot[]
}

const POLL_MS = 2_500

export function useNpuOccupancy() {
  return useQuery<NpuOccupancy>({
    queryKey: ['npu', 'occupancy'],
    queryFn: () => apiGet<NpuOccupancy>(ENDPOINTS.npuOccupancy),
    refetchInterval: POLL_MS,
  })
}
