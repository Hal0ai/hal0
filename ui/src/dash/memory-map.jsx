// hal0 dashboard — the memory model hook.
//
// The model lives in memory-map-pure.ts (buildMemoryMapModel), a pure
// function of the backend memory budget; useMemoryMapModel() below only feeds
// it. The top-bar ruler, the dashboard cards and the inference pane all read it,
// so they show one set of figures.

import { useSlots } from '@/api/hooks/useSlots'
import { useHardware } from '@/api/hooks/useHardware'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { useProxmoxSettings } from '@/api/hooks/useProxmoxSettings'
import { buildMemoryMapModel } from './memory-map-pure'

// A thin hook over buildMemoryMapModel (memory-map-pure.ts). Every memory
// figure — pool total, per-slot attribution, other guests, the RAM limit —
// comes from the backend's `memory` block on /api/stats/hardware (ADR-0039);
// the pure function reshapes it and the hook only feeds it.
export function useMemoryMapModel() {
  const hw = useHardware()
  const stats = useStatsHardware()
  const slotsQ = useSlots()
  const pveSettings = useProxmoxSettings()
  const rawHw = hw.data || {}

  const model = buildMemoryMapModel({
    memory: stats.data?.memory,
    perSlot: stats.data?.per_slot || {},
    slots: slotsQ.data || [],
    statsHost: stats.data?.host,
    // Tenants come from the FULL-shape /api/settings/proxmox response, not the
    // slim stats response (project_slim strips tenants[]).
    pveTenants: pveSettings.data?.status?.tenants || [],
    selfHostname: rawHw.name || '',
    platformLabel: rawHw.platform_label || rawHw.platform || '',
  })

  return {
    ...model,
    loading:
      model.loading || hw.isLoading || stats.isLoading || slotsQ.isLoading || pveSettings.isLoading,
  }
}

// Window export keeps parity with dashboard.jsx's debug exports.
Object.assign(window, { useMemoryMapModel })
