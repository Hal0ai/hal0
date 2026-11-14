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
  state: 'serving' | 'ready' | 'loaded' | 'idle' | 'offline' | 'off'
  cols: number[]
  gb: number | null
  // Capability hit the NPU within the server's activity window (~6s) or has
  // a request in flight. Drives the grid's per-capability colour takeover.
  engine?: 'flm' | 'xdna'
  active?: boolean
  last_used_age_s?: number | null
  // Set on rows the backend synthesizes for the anchor's STT/embed: the anchor's name. These ride
  // the anchor's one process and are never separate holders.
  served_by?: string
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
  shared_warning?: string | null
  npu_engines?: {
    flm: { image: string | null; version: string | null }
    xdna: { image: string | null; tag: string | null; pulled: boolean | null }
  } | null
  npu_host?: {
    device_node: string | null
    driver_loaded: boolean | null
    xdna_gen: number | null
  } | null
}

const POLL_MS = 2_500

/** `poll: false` for an observer that only needs the shape of the answer (the sidebar's NPU link):
 *  it shares the query but does not hold a 2.5 s poll open on every page. */
export function useNpuOccupancy({ poll = true }: { poll?: boolean } = {}) {
  return useQuery<NpuOccupancy>({
    queryKey: ['npu', 'occupancy'],
    queryFn: () => apiGet<NpuOccupancy>(ENDPOINTS.npuOccupancy),
    refetchInterval: poll ? POLL_MS : false,
  })
}
