// hal0 v3 dashboard — hardware hook (Phase B1).
//
// /api/hardware — read-only host info: CPU, GPU, NPU, memory, disk.
// Low refresh cadence (10s) — values are slow-moving but the memory
// section shows used/free which is worth refreshing.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface Hardware {
  name: string
  uptime: string
  cpu: string
  cores: string
  gpu: string
  ram: { total: number; used: number; free: number }
  npu?: { present: boolean; columns?: number; ctx?: number }
}

// Backend /api/hardware returns a flat shape (cpu_model, ram_mb, gpu_name,
// npu.{present}, …). HardwareView dereferences nested fields like
// H.ram.total, H.npu.columns directly — passing the raw response makes
// the page crash with "Cannot read properties of undefined". Normalize
// into the v3 component shape, falling back to neutral defaults for
// fields the backend doesn't surface (columns/ctx, hostname, uptime).
function normalizeHardware(raw: any): Hardware {
  const ramTotalMb = Number(raw?.ram_mb ?? raw?.ram_total_mb ?? 0)
  const ramFreeMb = Number(raw?.ram_available_mb ?? 0)
  const ramUsedMb = Math.max(0, ramTotalMb - ramFreeMb)
  const mbToGb = (mb: number) => Math.round((mb / 1024) * 10) / 10
  const cores = Number(raw?.cpu_cores ?? 0)
  const threads = Number(raw?.cpu_threads ?? cores)
  return {
    name: raw?.hostname ?? raw?.name ?? raw?.extra?.hostname ?? '',
    uptime: raw?.uptime ?? '',
    cpu: raw?.cpu_model ?? raw?.cpu_name ?? raw?.cpu ?? '',
    cores: cores ? `${cores}c · ${threads}t` : '',
    gpu: raw?.gpu_name ?? raw?.gpus?.[0]?.name ?? raw?.gpu ?? '',
    ram: {
      total: mbToGb(ramTotalMb),
      used: mbToGb(ramUsedMb),
      free: mbToGb(ramFreeMb),
    },
    npu: {
      present: !!(raw?.npu?.present ?? raw?.npu_present),
      columns: Number(raw?.npu?.columns ?? 0),
      ctx: Number(raw?.npu?.ctx ?? 0),
    },
  }
}

const POLL_MS = 10_000

export function useHardware() {
  return useQuery({
    queryKey: ['hardware'],
    queryFn: async () => normalizeHardware(await apiGet<any>(ENDPOINTS.hardware)),
    refetchInterval: POLL_MS,
  })
}
