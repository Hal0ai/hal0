// hal0 dashboard — Memory map (sidebar + expanded variants).
//
// Spec: docs/superpowers/specs/2026-05-28-memory-map-redesign-design.md
//
// The model lives in memory-map-pure.ts (buildMemoryMapModel), a pure
// function of the backend memory budget; useMemoryMapModel() below only feeds
// it. The MemoryMap component is a pure renderer. The same component
// drives the compact sidebar widget and the full-width hardware-page
// section via the `variant` prop.

import { useSlots } from '@/api/hooks/useSlots'
import { useHardware } from '@/api/hooks/useHardware'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { useProxmoxSettings } from '@/api/hooks/useProxmoxSettings'
import { buildMemoryMapModel } from './memory-map-pure'

const round1 = (n) => Math.round(n * 10) / 10

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

// Per-slot legend sub-label.
// Container slots: show image tag (truncated at 32 chars) or profile slug —
// more informative than "rocm" which every container shares.
// Un-enriched slots: keep the device token (rocm/vulkan/cpu/npu) as before.
function slotLegendSub(slot) {
  if (slot.isContainer) {
    // Prefer image tag (e.g. "ghcr.io/hal0ai/hal0-runner-standard:2026.10.11")
    // Truncate to last 32 chars so very long registry paths stay readable.
    if (slot.image) {
      const img = String(slot.image)
      return img.length > 32 ? '…' + img.slice(-32) : img
    }
    if (slot.profile) return String(slot.profile)
    return 'container'
  }
  // Legacy / un-enriched path: show device + approx marker.
  return `${slot.device}${slot.approx ? ' · ≈' : ''}`
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
              sub={`${slotLegendSub(s)}${s.modelId ? ' · ' + s.modelId : ''}`}
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
                sub={slotLegendSub(s)}
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
