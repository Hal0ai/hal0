// hal0 dashboard — Memory map (sidebar + expanded variants).
//
// Spec: docs/superpowers/specs/2026-05-28-memory-map-redesign-design.md
//
// All attribution math lives in useMemoryMapModel(); the MemoryMap
// component is a pure renderer (added in Task 6). The same component
// drives the compact sidebar widget and the full-width hardware-page
// section via the `variant` prop.

import { useSlots } from '@/api/hooks/useSlots'
import { useHardware } from '@/api/hooks/useHardware'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { useProxmoxSettings } from '@/api/hooks/useProxmoxSettings'

const LIVE_STATES = new Set(['ready', 'serving', 'idle', 'warming'])
const SAFETY_MARGIN_GB = 2
const MB_PER_GB = 1024

const round1 = (n) => Math.round(n * 10) / 10

function mbToGb(mb) {
  if (mb == null || Number.isNaN(mb)) return 0
  return round1(mb / MB_PER_GB)
}

function deviceFor(slot) {
  const d = (slot.device || '').toLowerCase()
  if (d === 'npu') return 'npu'
  if (d === 'cpu') return 'cpu'
  if (d === 'gpu-vulkan' || d === 'vulkan') return 'vulkan'
  if (d === 'gpu-rocm' || d === 'rocm' || d.startsWith('gpu')) return 'rocm'
  return 'cpu'
}

function attributeSlotShares({ liveSlots, gttUsedGb, npuModelGb }) {
  const npuLive = liveSlots.filter((s) => deviceFor(s) === 'npu')
  const gpuLive = liveSlots.filter((s) => {
    const d = deviceFor(s)
    return d === 'rocm' || d === 'vulkan'
  })

  const gpuTotalWeight = gpuLive.reduce(
    (acc, s) => acc + (s.metrics?.mem || 1),
    0,
  ) || gpuLive.length || 1

  return liveSlots.map((s) => {
    const device = deviceFor(s)
    if (device === 'npu') {
      const share = npuLive.length > 0 ? npuModelGb / npuLive.length : 0
      return { slot: s, device, bytesGb: round1(share), approx: npuLive.length > 1 }
    }
    if (device === 'rocm' || device === 'vulkan') {
      const weight = s.metrics?.mem || 1
      const share = gpuTotalWeight > 0 ? gttUsedGb * (weight / gpuTotalWeight) : 0
      return { slot: s, device, bytesGb: round1(share), approx: gpuLive.length > 1 }
    }
    // cpu
    return { slot: s, device, bytesGb: round1(s.metrics?.mem || 0), approx: false }
  })
}

export function useMemoryMapModel() {
  const hw = useHardware()
  const stats = useStatsHardware()
  const slotsQ = useSlots()
  const pveSettings = useProxmoxSettings()
  const slots = slotsQ.data || []

  // Pool total from the static probe — unified_memory_mb when the platform
  // advertises it (Strix Halo), else ram_mb. Fall back to live ram_used_mb
  // only as a last resort.
  const rawHw = hw.data || {}
  const ramTotalGb = mbToGb(rawHw.ram?.total ? rawHw.ram.total * MB_PER_GB : null)
  const unifiedFromProbe = mbToGb(rawHw.unified_memory_mb || 0)
  const unifiedGb = unifiedFromProbe || ramTotalGb || mbToGb(stats.data?.ram_used_mb || 0)
  const platformLabel = rawHw.platform_label || rawHw.platform || ''
  const memoryKind = rawHw.memory_kind === 'unified' ? 'unified' : 'system'

  const ramUsedGb = mbToGb(stats.data?.ram_used_mb || 0)
  const gttUsedGb = mbToGb(
    stats.data?.gtt_used_mb ?? stats.data?.vram_used_mb ?? 0,
  )
  const npuModelGb = mbToGb(stats.data?.npu_status?.model_mb || 0)

  const liveSlots = slots.filter((s) => LIVE_STATES.has((s.state || '').toLowerCase()))
  const attributed = attributeSlotShares({ liveSlots, gttUsedGb, npuModelGb })

  const cpuUsedGb = attributed
    .filter((a) => a.device === 'cpu')
    .reduce((acc, a) => acc + a.bytesGb, 0)
  const otherRamGb = Math.max(0, round1(ramUsedGb - cpuUsedGb))
  const selfShareGb = round1(ramUsedGb + gttUsedGb + npuModelGb)

  // ── Host block ──
  // Stats endpoint host: { configured, [detected], [hint], ok?, host_mem_*, ... }
  // Settings endpoint full: { status: { tenants[], host_cpu_count, ... } }
  const statsHost = stats.data?.host || { configured: false }
  const settingsStatus = pveSettings.data?.status
  let host
  if (statsHost.configured && statsHost.ok !== false) {
    const hostTotalGb = mbToGb(statsHost.host_mem_total_mb || 0)
    const hostUsedGb = mbToGb(statsHost.host_mem_used_mb || 0)
    const hostFreeGb = mbToGb(statsHost.host_mem_free_mb || 0)
    const othersGb = Math.max(0, round1(hostUsedGb - selfShareGb))
    // Tenants come from the FULL-shape /api/settings/proxmox response,
    // not the slim stats response (project_slim strips tenants[]).
    const tenants = (settingsStatus?.tenants || []).map((t) => ({
      vmid: t.vmid,
      name: t.name,
      type: t.type,
      memGb: mbToGb(t.mem_mb || 0),
      maxGb: mbToGb(t.maxmem_mb || 0),
    }))
    host = {
      mode: 'configured',
      totalGb: hostTotalGb,
      usedGb: hostUsedGb,
      freeGb: hostFreeGb,
      selfShareGb,
      othersGb,
      tenants,
    }
  } else if (statsHost.detected) {
    host = {
      mode: 'detected_unconfigured',
      hint: statsHost.hint || 'Configure Proxmox to see host pressure.',
    }
  } else {
    host = { mode: 'off' }
  }

  // ── Headroom ──
  const poolHeadroom = unifiedGb - (gttUsedGb + ramUsedGb + npuModelGb)
  let limitedBy = 'pool'
  let candidate = poolHeadroom
  if (host.mode === 'configured' && host.freeGb < candidate) {
    candidate = host.freeGb
    limitedBy = 'host'
  }
  // cgroup branch: best-effort, defer to follow-up issue. limitedBy stays
  // pool/host today.
  const availableGb = Math.max(0, round1(candidate - SAFETY_MARGIN_GB))

  return {
    pool: { totalGb: unifiedGb, kind: memoryKind, platformLabel },
    host,
    self: {
      ramUsedGb,
      gttUsedGb,
      npuModelGb,
      otherRamGb,
      selfShareGb,
      slots: attributed.map((a) => ({
        name: a.slot.name,
        device: a.device,
        bytesGb: a.bytesGb,
        modelId: a.slot.model || '',
        approx: a.approx,
      })),
    },
    headroom: { availableGb, limitedBy },
    loading: hw.isLoading || stats.isLoading || slotsQ.isLoading,
  }
}
