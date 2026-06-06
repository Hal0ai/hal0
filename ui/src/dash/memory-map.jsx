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

// Categorical per-slot palette (CSS custom props defined in dashboard.css).
// Each loaded model slot gets its OWN stable colour so co-resident models
// are distinguishable in the bar + legend — device colour alone collapses
// several GPU slots into a single hue.
const SLOT_PALETTE = [
  'var(--mem-slot-1)',
  'var(--mem-slot-2)',
  'var(--mem-slot-3)',
  'var(--mem-slot-4)',
  'var(--mem-slot-5)',
  'var(--mem-slot-6)',
  'var(--mem-slot-7)',
  'var(--mem-slot-8)',
]

// Deterministic, render-stable colour for a slot: sort live slot names and
// index by position into the palette. A given slot name always maps to the
// same colour within a render set, and the swatch matches its bar segment.
function assignSlotColors(slotNames) {
  const sorted = [...slotNames].sort()
  const map = new Map()
  sorted.forEach((name, i) => {
    map.set(name, SLOT_PALETTE[i % SLOT_PALETTE.length])
  })
  return map
}

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

// True iff at least one live slot carries the BE-METRICS `mem_mb`
// contract field. When present we attribute REAL per-slot resident
// model + KV memory; when wholly absent (backend not yet deployed) we
// fall back to the legacy GTT-split / NPU-divide attribution so the map
// keeps rendering instead of crashing.
function hasMemMb(liveSlots) {
  return liveSlots.some(
    (s) => typeof s.mem_mb === 'number' && Number.isFinite(s.mem_mb),
  )
}

// Preferred path: each slot gets its OWN reported resident memory
// (model weights + KV cache). No equal-splitting, no host RAM folding —
// just the real bytes the loaded model holds, per slot, as its own
// colored segment.
function attributeByMemMb({ liveSlots }) {
  return liveSlots.map((s) => {
    const device = deviceFor(s)
    const mb = typeof s.mem_mb === 'number' && Number.isFinite(s.mem_mb) ? s.mem_mb : 0
    return { slot: s, device, bytesGb: mbToGb(mb), approx: false }
  })
}

// Legacy fallback (used ONLY when no slot reports mem_mb). Divides the
// live GTT pool across GPU slots by a fake equal weight and splits NPU
// model memory evenly. Marked `approx` because the per-slot figure is
// estimated, not measured.
function attributeFallback({ liveSlots, gttUsedGb, npuModelGb }) {
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

  // Pool total = the real ceiling for GPU model loads. On UMA (Strix Halo)
  // that is the GTT cap (amdgpu.gttsize, ~80 GiB — live as
  // stats.gpu_vram_total_mb), NOT the full unified RAM (128 GiB): models can't
  // actually allocate past the GTT window, so headroom must be measured
  // against it. Prefer the live GTT cap; fall back to the static unified/ram
  // probe when the live value is unavailable (keeps non-UMA + test mocks sane).
  const rawHw = hw.data || {}
  const ramTotalGb = rawHw.ram?.total ?? 0
  const gttCapGb = mbToGb(stats.data?.gpu_vram_total_mb || stats.data?.gtt_total_mb || 0)
  const unifiedFromProbe = mbToGb(rawHw.unified_memory_mb || 0)
  const unifiedGb =
    gttCapGb ||
    unifiedFromProbe ||
    ramTotalGb ||
    mbToGb(stats.data?.ram_total_mb || 0)
  const platformLabel = rawHw.platform_label || rawHw.platform || ''
  const memoryKind = rawHw.memoryKind === 'unified' ? 'unified' : 'system'
  // On UMA the pool ceiling is the GTT cap, not the whole unified RAM
  // (see unifiedGb above). Don't render the raw 'unified' kind — it reads
  // as "unified 80GB" and misleads. Label it as the GPU/GTT pool instead.
  const poolLabel = memoryKind === 'unified' ? 'GPU pool (GTT)' : 'system'

  const ramUsedGb = mbToGb(stats.data?.ram_used_mb || 0)
  const gttUsedGb = mbToGb(
    stats.data?.gtt_used_mb ?? stats.data?.vram_used_mb ?? 0,
  )
  const npuModelGb = mbToGb(stats.data?.npu_status?.model_mb || 0)

  const liveSlots = slots.filter((s) => LIVE_STATES.has((s.state || '').toLowerCase()))

  // Prefer the BE-METRICS `mem_mb` contract (real per-slot resident
  // model + KV memory). Fall back to the legacy GTT-split / NPU-divide
  // attribution only when NO slot reports mem_mb, so the map never
  // crashes against a pre-deploy backend.
  const usingMemMb = hasMemMb(liveSlots)
  const attributed = usingMemMb
    ? attributeByMemMb({ liveSlots })
    : attributeFallback({ liveSlots, gttUsedGb, npuModelGb })

  // Model memory = sum of each loaded model's resident bytes. This is the
  // figure the map's primary "used" reflects — NOT host system RAM.
  const modelUsedGb = round1(
    attributed.reduce((acc, a) => acc + (a.bytesGb || 0), 0),
  )

  // Host-pressure share for the Proxmox block only. With real per-slot
  // mem_mb we report the measured model footprint; in the legacy fallback
  // we keep the old ram+gtt+npu sum so the host bar stays populated.
  const selfShareGb = usingMemMb
    ? modelUsedGb
    : round1(ramUsedGb + gttUsedGb + npuModelGb)

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
  // Pool free = unified cap minus model memory actually held. Host free
  // can be the tighter constraint when running on Proxmox. The running
  // LXC's cgroup memory cap is a third candidate (issue #372): when
  // BELOW the current min(pool, host) it becomes the binding constraint.
  // When unlimited / unreadable, cgroup_max_mb is null and the cgroup
  // branch is a no-op.
  const poolHeadroom = unifiedGb - modelUsedGb
  let limitedBy = 'pool'
  let candidate = poolHeadroom
  if (host.mode === 'configured' && host.freeGb < candidate) {
    candidate = host.freeGb
    limitedBy = 'host'
  }
  // cgroup branch: treat the running cgroup's memory.max as a third
  // ceiling. Subtract the same modelUsedGb the pool already accounts
  // for — the cgroup cap is a hard absolute, not a "free" figure.
  const cgroupMaxMb = rawHw.cgroup_max_mb
  if (cgroupMaxMb && Number.isFinite(cgroupMaxMb)) {
    const cgroupFreeGb = mbToGb(cgroupMaxMb) - modelUsedGb
    if (cgroupFreeGb < candidate) {
      candidate = cgroupFreeGb
      limitedBy = 'cgroup'
    }
  }
  const availableGb = Math.max(0, round1(candidate - SAFETY_MARGIN_GB))

  return {
    pool: { totalGb: unifiedGb, kind: memoryKind, label: poolLabel, platformLabel },
    host,
    self: {
      // Model memory the map renders against the pool. `modelUsedGb` is
      // the headline "used" figure; host system RAM lives in `host` only.
      modelUsedGb,
      selfShareGb,
      // Retained for the host bar (selfShareGb) + legacy diagnostics.
      ramUsedGb,
      gttUsedGb,
      npuModelGb,
      slots: (() => {
        const colorMap = assignSlotColors(attributed.map((a) => a.slot.name))
        return attributed.map((a) => ({
          name: a.slot.name,
          device: a.device,
          color: colorMap.get(a.slot.name),
          bytesGb: a.bytesGb,
          modelId: a.slot.model || '',
          approx: a.approx,
        }))
      })(),
    },
    headroom: { availableGb, limitedBy },
    loading: hw.isLoading || stats.isLoading || slotsQ.isLoading || pveSettings.isLoading,
  }
}

// ── Render helpers ─────────────────────────────────────────────────────

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

// Model-memory bar: one colored segment per loaded model (real bytes),
// then the remaining pool free. Host system RAM is intentionally NOT
// folded in here — this bar is about model memory vs the unified pool.
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
          color={s.color}
          title={`${s.name} · ${fmtGb(s.bytesGb)}`}
        />
      ))}
      <PctSeg
        widthPct={Math.max(0, 100 - pct(self.modelUsedGb))}
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
  // Headline "used" = model memory held by loaded models (+ KV), NOT host
  // system RAM. Host pressure is a separate secondary block (expanded).
  const usedModel = self.modelUsedGb
  const free = Math.max(0, round1(total - usedModel))

  if (variant === 'expanded') {
    return (
      <div className="card memmap-expanded" data-loading={loading || undefined}>
        <div className="vh">
          <h2>Memory map</h2>
          <span className="mono dim">
            {pool.label} {fmtGb(total)} · {pool.platformLabel}
            {host.mode === 'configured' && host.totalGb && ` · host ${fmtGb(host.totalGb)}`}
          </span>
        </div>

        {/* Primary section: MODEL memory vs the unified pool. */}
        <div className="memmap-h mono">
          <span>model memory</span>
          <span><b>{fmtGb(free)}</b> free in pool</span>
        </div>
        <SidebarBar model={model} />

        <div className="memmap-legend memmap-legend-expanded">
          {self.slots.map((s) => (
            <LegendRow
              key={s.name}
              swatch={s.color}
              name={s.name}
              sub={`${s.device}${s.approx ? ' · ≈' : ''}${s.modelId ? ' · ' + s.modelId : ''}`}
              sz={s.bytesGb}
            />
          ))}
          <LegendRow swatch="var(--bg-4)" name="free" sz={free} />
        </div>

        <HeadroomLabel availableGb={headroom.availableGb} limitedBy={headroom.limitedBy} />

        {/* Secondary section: HOST pressure (Proxmox). Clearly separate —
            this is about system RAM across all LXCs/VMs, not model memory. */}
        {host.mode === 'configured' && (
          <div className="memmap-host-section">
            <div className="memmap-h mono">
              <span>host pressure</span>
              <span><b>{fmtGb(host.freeGb)}</b> free on host</span>
            </div>
            <HostBar model={model} />
            <div className="memmap-legend memmap-legend-expanded">
              {(host.tenants || []).map((t) => (
                <LegendRow
                  key={`t-${t.vmid}`}
                  swatch="var(--mem-tenant-1, var(--fg-5))"
                  name={`${t.name} (${t.type})`}
                  sub={`vmid ${t.vmid}`}
                  sz={t.memGb}
                />
              ))}
            </div>
          </div>
        )}
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
          {pool.label} · {fmtGb(usedModel)} / {fmtGb(total)}
        </span>
      </div>
      <div className="side-card-b">
        <div className="memmap">
          {/* Sidebar = MODEL memory only. Host pressure lives in the
              expanded (hardware-page) variant, not here. */}
          <div className="memmap-h mono">
            <span>model memory</span>
            <span><b>{fmtGb(free)}</b> free</span>
          </div>
          <SidebarBar model={model} />
          <div className="memmap-legend">
            {self.slots.map((s) => (
              <LegendRow
                key={s.name}
                swatch={s.color}
                name={s.name}
                sub={s.device}
                sz={s.bytesGb}
              />
            ))}
            <LegendRow swatch="var(--bg-4)" name="free" sz={free} />
          </div>
          {/* Proxmox host-pressure nudge intentionally omitted from the
              sidebar — it lives only in the expanded (hardware-page)
              variant where the Configure → affordance has room to land.
              The compact sidebar stays model-memory only. */}
        </div>
      </div>
    </div>
  )
}

// Window export keeps parity with dashboard.jsx's debug exports.
Object.assign(window, { MemoryMap, useMemoryMapModel })
