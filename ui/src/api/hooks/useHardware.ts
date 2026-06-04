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
  kernel: string
  distro: string
  platformLabel: string
  cpu: string
  cores: string
  gpu: string
  gpuVendor: string
  computeCapable: boolean
  vulkanCapable: boolean
  ram: { total: number; used: number; free: number }
  unifiedMb: number
  gttTotalMb: number
  npu: {
    present: boolean
    vendor: string
    name: string
    driver: string
    columns: number
    ctx: number
  }
}

// Format whole seconds-since-boot into "Nd HH:MM" (matching the old
// display string). Returns '' for 0 / missing so the row renders "—".
function formatUptime(seconds: number): string {
  if (!seconds || seconds <= 0) return ''
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const hh = String(h).padStart(2, '0')
  const mm = String(m).padStart(2, '0')
  return d > 0 ? `${d}d ${hh}:${mm}` : `${hh}:${mm}`
}

// Strip the leading "Linux version " the probe keeps for /proc/version
// parity, so the card shows just "7.0.6-2-pve".
function shortKernel(kernel: string): string {
  return (kernel || '').replace(/^Linux version\s+/i, '').trim()
}

// Backend /api/hardware returns a flat shape (cpu_model, ram_mb, gpu_name,
// npu.{present}, hostname, uptime_s, kernel, distro, …). The Hardware view
// dereferences nested fields like H.ram.total directly — passing the raw
// response makes the page crash with "Cannot read properties of undefined".
// Normalize into the v3 component shape, falling back to neutral defaults
// (and the legacy display-shape keys, for the e2e mock / HAL0_DATA seed)
// for fields a given backend doesn't surface.
function normalizeHardware(raw: any): Hardware {
  const ramTotalMb = Number(raw?.ram_total_mb ?? raw?.ram_mb ?? 0)
  const ramFreeMb = Number(raw?.ram_available_mb ?? 0)
  const ramUsedMb = Math.max(0, ramTotalMb - ramFreeMb)
  const mbToGb = (mb: number) => Math.round((mb / 1024) * 10) / 10
  const cores = Number(raw?.cpu_cores ?? 0)
  const threads = Number(raw?.cpu_threads ?? cores)
  const gpu0 = raw?.gpus?.[0] ?? {}
  const npu = raw?.npu ?? {}
  // Legacy display-shape (e2e mock-data + HAL0_DATA seed expose ram:{}).
  const ram = raw?.ram ?? {}
  return {
    name: raw?.hostname ?? raw?.name ?? raw?.extra?.hostname ?? '',
    uptime: raw?.uptime ?? formatUptime(Number(raw?.uptime_s ?? 0)),
    kernel: shortKernel(raw?.kernel ?? raw?.extra?.kernel ?? ''),
    distro: raw?.distro ?? '',
    platformLabel: raw?.platform_label ?? raw?.platform ?? '',
    cpu: raw?.cpu_model ?? raw?.cpu_name ?? raw?.cpu ?? '',
    cores: cores ? `${cores}c · ${threads}t` : (raw?.cores ?? ''),
    gpu: raw?.gpu_name ?? gpu0?.name ?? raw?.gpu ?? '',
    gpuVendor: raw?.gpu_vendor ?? gpu0?.vendor ?? '',
    computeCapable: !!gpu0?.compute_capable,
    vulkanCapable: !!gpu0?.vulkan_capable,
    ram: {
      total: ramTotalMb ? mbToGb(ramTotalMb) : Number(ram?.total ?? 0),
      used: ramTotalMb ? mbToGb(ramUsedMb) : Number(ram?.used ?? 0),
      free: ramTotalMb ? mbToGb(ramFreeMb) : Number(ram?.free ?? 0),
    },
    unifiedMb: Number(raw?.unified_memory_mb ?? 0),
    gttTotalMb: Number(raw?.gtt_total_mb ?? 0),
    npu: {
      present: !!(npu?.present ?? raw?.npu_present),
      vendor: npu?.vendor ?? '',
      name: npu?.name ?? raw?.npu_name ?? '',
      driver: npu?.driver ?? '',
      columns: Number(npu?.columns ?? 0),
      ctx: Number(npu?.ctx ?? 0),
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
