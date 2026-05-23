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

const POLL_MS = 10_000

export function useHardware() {
  return useQuery({
    queryKey: ['hardware'],
    queryFn: () => apiGet<Hardware>(ENDPOINTS.hardware),
    refetchInterval: POLL_MS,
  })
}
