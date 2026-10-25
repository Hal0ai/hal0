// hal0 v3 dashboard — /api/stats/hardware (live counters).
//
// Distinct from useHardware (static probe): this hook polls the live
// counters (gtt_used_mb, ram_used_mb, npu_status.model_mb, host.*) at
// 2.5s — same cadence the backend was designed for. Used by the
// MemoryMap component (sidebar + expanded variants).

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'


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

/** One slot's attributed share of the GPU pool (`SlotShare.as_dict()`). */
export interface MemoryBudgetSlotShareJson {
  slot: string
  gb: number
  /** Slot state. "starting" is listed but is not yet a resident footprint. */
  state: string
}

/** The GPU pool, split into who holds it (`GpuBudget.as_dict()`). */
export interface MemoryBudgetGpuJson {
  /** "shared": UMA pool (GTT), shared with the host and other guests.
   *  "dedicated": a discrete GPU's VRAM. */
  kind: 'shared' | 'dedicated'
  total_gb: number
  used_gb: number
  /** The other GPU slots on this box. Excludes the img slot: the image engine
   *  is its own figure (`image_engine_gb`). */
  this_box: MemoryBudgetSlotShareJson[]
  image_engine_gb: number
  /** Pool usage that is neither this box's slots nor the image engine. */
  other_guests_gb: number
  /** How far the attributed footprints overshoot the live usage. Reported,
   *  never drawn and never subtracted. */
  attribution_excess_gb: number
  free_gb: number
}

/** This box's RAM (`RamBudget.as_dict()`). */
export interface MemoryBudgetRamJson {
  /** The container cgroup limit, or the system total. 0 when the backend could
   *  not read /proc/meminfo: treat that as unread, never as a 0 GB box. */
  limit_gb: number
  used_gb: number
  free_gb: number
  source: 'container' | 'system'
}

/** The memory budget, `MemoryBudget.as_dict()` from
 *  src/hal0/hardware/memory_budget.py, exactly as `/api/stats/hardware` serves
 *  it under `memory` (snake_case; every GB figure is MiB / 1024, 1 decimal).
 *  ADR-0039: the one owner of the dashboard's memory figures. */
export interface MemoryBudgetJson {
  /** null on a box with no GPU lane, or when the pool counters were unreadable. */
  gpu: MemoryBudgetGpuJson | null
  ram: MemoryBudgetRamJson
  /** Pool a render can use once image mode has unloaded this box's resident
   *  GPU slots. null exactly when `gpu` is. */
  render_free_gb: number | null
}

/** One row of `per_slot` (`hal0.slots.capacity.build_per_slot`): resident
 *  slots only, in MiB. */
export interface PerSlotRow {
  vram_mb?: number
  ram_mb?: number
  mem_mb?: number
  state?: string
  model_id?: string
}

export interface StatsHardware {
  ram_total_mb?: number
  ram_used_mb?: number
  ram_used_gb?: number
  ram_available_gb?: number
  gtt_used_mb?: number | null
  vram_used_mb?: number | null
  gpu_util?: number | null
  // True when gpu-compute.service pins the perf level, so gpu_util reads a flat 100
  util_is_forced_high?: boolean | null
  gpu_vram_used_mb?: number | null
  gpu_vram_total_mb?: number | null
  // Live iGPU clock + temperature — drive the Inference hero GPU gauge
  gpu_clock_mhz?: number | null
  gpu_temp_c?: number | null
  // §2b new fields — backend adds cpu_util; npu_util only if NPU-telemetry spike lands
  cpu_util?: number | null
  npu_util?: number | null
  npu_status?: { ok: boolean; model_mb: number }
  host?: StatsHardwareHost
  per_upstream?: Record<string, unknown>
  upstream_names?: string[]
  /** Resident slots only (starting slots are in `memory.gpu.this_box`). */
  per_slot?: Record<string, PerSlotRow>
  /** Absent when the backend could not build the budget. */
  memory?: MemoryBudgetJson
}

const POLL_MS = 2_500

const statsHardwareQuery = {
  queryKey: ['stats', 'hardware'],
  queryFn: () => apiGet<StatsHardware>(ENDPOINTS.statsHardware),
  refetchInterval: POLL_MS,
} as const

export function useStatsHardware() {
  return useQuery<StatsHardware>(statsHardwareQuery)
}

/** The memory budget alone: `data.memory` of the same polled query, so it
 *  shares the cache entry (and the 2.5 s poll) with `useStatsHardware`.
 *  `data` is undefined until the first response, and while the backend omits
 *  the block. */
export function useMemoryBudget() {
  return useQuery<StatsHardware, Error, MemoryBudgetJson | undefined>({
    ...statsHardwareQuery,
    select: (d) => d.memory,
  })
}
