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
  const ramTotalGb = rawHw.ram?.total ?? 0
  const unifiedFromProbe = mbToGb(rawHw.unified_memory_mb || 0)
  const unifiedGb =
    unifiedFromProbe ||
    ramTotalGb ||
    mbToGb(stats.data?.ram_total_mb || 0)
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
  // Cadence note: stats refresh at 2.5s, settings at 10s. selfShareGb
  // (from stats) and tenants[] (from settings) can be briefly out of
  // sync (<7.5s window) — accepted because tenant allocations change
  // slowly. Don't unify the cadences; the slim/full split is by design.
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
    // Filter out the LXC running hal0 itself — it's already represented
    // as 'this hal0 LXC' via selfShareGb. The static probe exposes our
    // hostname; tenants whose `name` matches are dropped.
    const selfHostname = (rawHw.name || '').toLowerCase().trim()
    const tenants = (settingsStatus?.tenants || [])
      .filter((t) => !selfHostname || (t.name || '').toLowerCase().trim() !== selfHostname)
      .map((t) => ({
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
    loading: hw.isLoading || stats.isLoading || slotsQ.isLoading || pveSettings.isLoading,
  }
}

// ── Render helpers ─────────────────────────────────────────────────────

function colorForDevice(device) {
  if (device === 'npu') return 'var(--dev-npu)'
  if (device === 'cpu') return 'var(--dev-cpu)'
  if (device === 'vulkan') return 'var(--dev-vulkan)'
  return 'var(--dev-rocm)'
}

function fmtGb(n) {
  if (n == null) return '—'
  if (n < 1) return `${(n * 1024).toFixed(0)} MB`
  return `${n.toFixed(1)} GB`
}

function PctSeg({ widthPct, color, title }) {
  if (widthPct <= 0) return null
  return <i style={{ width: `${widthPct}%`, background: color }} title={title} />
}

function HeadroomLabel({ availableGb, limitedBy }) {
  return (
    <div className="memmap-headroom mono">
      Headroom for new models:&nbsp;
      <b>{fmtGb(availableGb)}</b>
      <span className="dim">&nbsp;— limited by {limitedBy}</span>
    </div>
  )
}

function PveNudge({ hint, onConfigure }) {
  return (
    <div className="memmap-pve-nudge mono" role="status">
      <span>⚠ Hosted on Proxmox — host pressure unknown.</span>
      <a
        href="#settings"
        onClick={(e) => {
          if (onConfigure) {
            e.preventDefault()
            onConfigure()
          }
        }}
      >
        Configure →
      </a>
    </div>
  )
}

function SidebarBar({ model }) {
  const { pool, self } = model
  const total = pool.totalGb || 1
  const pct = (gb) => (gb / total) * 100
  const segs = self.slots.filter((s) => s.bytesGb > 0)
  return (
    <div className="memmap-bar">
      {segs.map((s) => (
        <PctSeg
          key={s.name}
          widthPct={pct(s.bytesGb)}
          color={colorForDevice(s.device)}
          title={`${s.name} · ${fmtGb(s.bytesGb)}`}
        />
      ))}
      {self.otherRamGb > 0 && (
        <PctSeg
          widthPct={pct(self.otherRamGb)}
          color="var(--fg-5)"
          title={`other RAM · ${fmtGb(self.otherRamGb)}`}
        />
      )}
      <PctSeg
        widthPct={Math.max(
          0,
          100 - pct(self.gttUsedGb + self.ramUsedGb + self.npuModelGb),
        )}
        color="var(--bg-4)"
        title="free"
      />
    </div>
  )
}

function HostBar({ model }) {
  const { host, self } = model
  if (host.mode !== 'configured') return null
  const total = host.totalGb || 1
  const pct = (gb) => (gb / total) * 100
  return (
    <div className="memmap-bar memmap-bar-host">
      <PctSeg
        widthPct={pct(self.selfShareGb)}
        color="var(--accent)"
        title={`this hal0 LXC · ${fmtGb(self.selfShareGb)}`}
      />
      <PctSeg
        widthPct={pct(host.othersGb)}
        color="var(--mem-tenant-1, var(--fg-5))"
        title={`other tenants · ${fmtGb(host.othersGb)}`}
      />
      <PctSeg
        widthPct={Math.max(0, 100 - pct(self.selfShareGb + host.othersGb))}
        color="var(--bg-4)"
        title="host free"
      />
    </div>
  )
}

function LegendRow({ swatch, name, sub, sz }) {
  return (
    <div className="ln mono">
      <span className="sw" style={{ background: swatch }} />
      <span className="name">{name}</span>
      {sub && <span className="dim memmap-legend-sub">{sub}</span>}
      <span className="sz">{fmtGb(sz)}</span>
    </div>
  )
}

export function MemoryMap({ variant = 'sidebar', onConfigure }) {
  const model = useMemoryMapModel()
  const { pool, host, self, headroom, loading } = model
  const total = pool.totalGb
  const usedSelf = round1(self.ramUsedGb + self.gttUsedGb + self.npuModelGb)
  const free = Math.max(0, round1(total - usedSelf))

  if (variant === 'expanded') {
    return (
      <div className="card memmap-expanded" data-loading={loading || undefined}>
        <div className="vh">
          <h2>Memory map</h2>
          <span className="mono dim">
            {pool.kind} {fmtGb(total)} · {pool.platformLabel}
            {host.mode === 'configured' && host.totalGb && ` · host ${fmtGb(host.totalGb)}`}
          </span>
        </div>

        {host.mode === 'configured' && (
          <>
            <div className="memmap-h mono">
              <span>host pool</span>
              <span><b>{fmtGb(host.freeGb)}</b> free on host</span>
            </div>
            <HostBar model={model} />
          </>
        )}

        <div className="memmap-h mono">
          <span>inside this hal0</span>
          <span><b>{fmtGb(free)}</b> free in pool</span>
        </div>
        <SidebarBar model={model} />

        <div className="memmap-legend memmap-legend-expanded">
          {self.slots.map((s) => (
            <LegendRow
              key={s.name}
              swatch={colorForDevice(s.device)}
              name={s.name}
              sub={`${s.device}${s.approx ? ' · ≈' : ''}${s.modelId ? ' · ' + s.modelId : ''}`}
              sz={s.bytesGb}
            />
          ))}
          {self.otherRamGb > 0 && (
            <LegendRow swatch="var(--fg-5)" name="other RAM" sz={self.otherRamGb} />
          )}
          <LegendRow swatch="var(--bg-4)" name="free" sz={free} />
          {host.mode === 'configured' &&
            (host.tenants || []).map((t) => (
              <LegendRow
                key={`t-${t.vmid}`}
                swatch="var(--mem-tenant-1, var(--fg-5))"
                name={`${t.name} (${t.type})`}
                sub={`vmid ${t.vmid}`}
                sz={t.memGb}
              />
            ))}
        </div>

        <HeadroomLabel availableGb={headroom.availableGb} limitedBy={headroom.limitedBy} />
        {host.mode === 'detected_unconfigured' && (
          <PveNudge hint={host.hint} onConfigure={onConfigure} />
        )}
      </div>
    )
  }

  // ── sidebar variant ──
  return (
    <div className="side-card memmap-sidebar" data-loading={loading || undefined}>
      <div className="side-card-h">
        <span>Memory map</span>
        <span className="right mono">
          {fmtGb(usedSelf)} / {fmtGb(total)}
        </span>
      </div>
      <div className="side-card-b">
        <div className="memmap">
          {host.mode === 'configured' && (
            <div className="memmap-h mono">
              <span>host {host.tenants?.length ?? 0} tenants</span>
              <span><b>{fmtGb(host.freeGb)}</b> host free</span>
            </div>
          )}
          <div className="memmap-h mono">
            <span>{pool.kind} ram</span>
            <span><b>{fmtGb(free)}</b> free</span>
          </div>
          <SidebarBar model={model} />
          <div className="memmap-legend">
            {self.slots.map((s) => (
              <LegendRow
                key={s.name}
                swatch={colorForDevice(s.device)}
                name={s.name}
                sz={s.bytesGb}
              />
            ))}
            {self.otherRamGb > 0 && (
              <LegendRow swatch="var(--fg-5)" name="other" sz={self.otherRamGb} />
            )}
            <LegendRow swatch="var(--bg-4)" name="free" sz={free} />
          </div>
          <HeadroomLabel
            availableGb={headroom.availableGb}
            limitedBy={headroom.limitedBy}
          />
          {host.mode === 'detected_unconfigured' && (
            <PveNudge hint={host.hint} onConfigure={onConfigure} />
          )}
        </div>
      </div>
    </div>
  )
}

// Window export keeps parity with dashboard.jsx's debug exports.
Object.assign(window, { MemoryMap, useMemoryMapModel })
