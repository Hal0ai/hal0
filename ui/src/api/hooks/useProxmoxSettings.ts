// hal0 v3 dashboard — /api/settings/proxmox.
//
// The full-shape sibling of /api/stats/hardware's slim host block.
// The stats endpoint runs through pve.project_slim() and strips
// tenants[] + a few aggregate fields. The expanded MemoryMap variant
// needs the full shape to show per-tenant memory rows.
//
// Lower cadence (10s) because the data is the same on every poll
// unless someone is rebooting tenants — the dashboard's 2.5s stats
// hook handles the actively-changing numbers (host_mem_used_mb,
// gtt_used_mb, etc.).

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface ProxmoxTenant {
  vmid: number
  name: string
  type: 'lxc' | 'qemu'
  status: string
  mem_mb: number
  maxmem_mb: number
  maxcpu?: number
  cpu_pct?: number
  node?: string
}

export interface ProxmoxFullStatus {
  configured: boolean
  ok?: boolean
  node?: string
  host_mem_total_mb?: number
  host_mem_used_mb?: number
  host_mem_free_mb?: number
  host_cpu_pct?: number
  host_cpu_count?: number
  host_uptime_s?: number
  tenants_running?: number
  tenants_total?: number
  tenants_allocated_mb?: number
  tenants?: ProxmoxTenant[]
  error?: string
}

export interface ProxmoxSettings {
  configured: boolean
  host: string
  port: number
  user: string
  token_name: string
  verify_ssl: boolean
  token_value_set: boolean
  status: ProxmoxFullStatus
}

const POLL_MS = 10_000

export function useProxmoxSettings() {
  return useQuery<ProxmoxSettings>({
    queryKey: ['settings', 'proxmox'],
    queryFn: () => apiGet<ProxmoxSettings>(ENDPOINTS.proxmoxSettings),
    refetchInterval: POLL_MS,
  })
}
