// hal0 dashboard — Dashboard redesign (fixed-band layout, swap-in-place).
//
// Implements design_handoff_dashboard_redesign: replaces the free-form
// customizable grid (DashboardOverhaulView / DashGrid) with a fixed-band
// vertical stack — hero strip · health strip · unified-memory hero ·
// band A (throughput / utilization / requests) · slots · band C
// (activity / services / needs attention). No drag, no resize, no masonry:
// the only customization is swapping which widget occupies a swappable
// cell (per-cell whitelist in useDashLayout CELL_DEFS) and toggling the
// quick-actions strip. Layout persists as { v:3, cells, quickActions }
// via PUT /api/dashboard/layout (fail-soft — see useDashLayout).
//
// Fail-soft contract: any metric a probe can't source renders as "—",
// never a fabricated value (metric-cards.jsx pattern). The Requests
// widget's rollup endpoint is new — it gates to "source pending".
//
// Window-global module: registers DashboardRedesignView on window (the
// #dashboard route in main.jsx). Hooks come in via ES imports, same as
// memory-map.jsx / metric-cards.jsx.

import { useSlots } from '@/api/hooks/useSlots'
import { useHardware } from '@/api/hooks/useHardware'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { useStatsPower } from '@/api/hooks/useStatsPower'
import { useThroughputHistory } from '@/api/hooks/useThroughputHistory'
import { useRequestsRollup } from '@/api/hooks/useRequestsRollup'
import { useServices } from '@/api/hooks/useServices'
import { useConfigUrls } from '@/api/hooks/useConfigUrls'
import { useActivityRecent } from '@/api/hooks/useActivity'
import { useApprovalList } from '@/api/hooks/useAgents'
import { useSlotDrift, useRestartDriftedSlots } from '@/api/hooks/useUpdates'
import {
  useDashLayout,
  useSaveDashLayout,
  reconcile,
  CELL_DEFS,
  CELL_MAP,
  WIDGET_MAP,
} from '@/api/hooks/useDashLayout'
import { slotIndicatorFromPhase, isSlotLive } from './slot-status.js'
import { useMemoryMapModel } from './memory-map'
import { useNotifications } from './notifications.jsx'

const { useState, useEffect, useRef, useMemo, useCallback } = React

// ─── helpers ─────────────────────────────────────────────────────────────────

const round1 = (n) => Math.round(n * 10) / 10
const mbToGb = (mb) => round1((mb || 0) / 1024)
const fmt1 = (n) => (typeof n === 'number' && Number.isFinite(n) ? n.toFixed(1) : '—')
const fmtInt = (n) => (typeof n === 'number' && Number.isFinite(n) ? Math.round(n).toString() : '—')

function fmtGb(gb) {
  if (typeof gb !== 'number' || !Number.isFinite(gb)) return '—'
  return `${gb.toFixed(1)} GB`
}

// Element width via ResizeObserver — drives "label only when the segment is
// wide enough (~90px)" in the memory bar without clipping half a label.
function useMeasuredWidth() {
  const ref = useRef(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setWidth(e.contentRect.width)
    })
    ro.observe(el)
    setWidth(el.offsetWidth)
    return () => ro.disconnect()
  }, [])
  return [ref, width]
}

const toast = (msg, kind = 'info') => window.__hal0Toast && window.__hal0Toast(msg, kind)

// ─── card shell ──────────────────────────────────────────────────────────────
// The redesign card: --bg-1, 1px --line, radius 10px, no shadow; header
// 11px 16px with mono uppercase title, muted note, optional ⇄ control.

function RDCard({ title, count, note, right, swap, children, className, flush }) {
  return (
    <div className={'rd-card' + (className ? ' ' + className : '')}>
      <div className="rd-card-h">
        <span className="rd-card-title mono">{title}</span>
        {count != null && <span className="rd-card-count mono num">{count}</span>}
        <span className="rd-card-spacer" />
        {note && <span className="rd-card-note mono">{note}</span>}
        {right}
        {swap}
      </div>
      <div className={'rd-card-b' + (flush ? ' flush' : '')}>{children}</div>
    </div>
  )
}

// ─── swap-in-place (⇄) ───────────────────────────────────────────────────────
// Every swappable cell ends its header with a ⇄ button. Clicking it (or
// entering customize mode) opens a picker of the widgets allowed IN THAT
// CELL. Swapping replaces the cell's widget; layout never reflows.

function SwapButton({ cellId, current, onSwap }) {
  const [open, setOpen] = useState(false)
  const cell = CELL_MAP[cellId]
  if (!cell || cell.locked) return null

  const names = cell.accepts.map((id) => WIDGET_MAP[id]?.name || id).join(' · ')

  return (
    <span className="rd-swap-wrap">
      <button
        className="rd-swap mono"
        title={`Swap widget — this cell accepts: ${names}`}
        aria-label={`Swap widget (accepts: ${names})`}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >⇄</button>
      {open && (
        <>
          <div className="rd-swap-backdrop" onClick={() => setOpen(false)} />
          <div className="rd-swap-menu" role="menu">
            {cell.accepts.map((id) => {
              const def = WIDGET_MAP[id]
              const built = !!def?.built
              const isCurrent = id === current
              return (
                <button
                  key={id}
                  role="menuitem"
                  className={'rd-swap-item mono' + (isCurrent ? ' current' : '')}
                  disabled={!built}
                  onClick={() => {
                    setOpen(false)
                    if (!isCurrent && built) onSwap(cellId, id)
                  }}
                >
                  <span className="rd-swap-item-name">{def?.name || id}</span>
                  {isCurrent && <span className="rd-swap-item-tag">current</span>}
                  {!built && <span className="rd-swap-item-tag">soon</span>}
                </button>
              )
            })}
          </div>
        </>
      )}
    </span>
  )
}

// ─── attention items (shared: health strip + Needs Attention card) ──────────
// The list now comes from the ONE shared notifications source (notifications.jsx),
// the same hook the topbar bell reads — so the card, the health-strip count, and
// the bell badge can never disagree. `actionableItems` is the actionable subset:
// approvals, error slots, failed downloads, update-available, slot drift, and dev
// messages. In-progress downloads are intentionally excluded (they carry no
// action and live on the bell only).

function useAttentionItems() {
  return useNotifications().actionableItems
}

// ─── hero strip ──────────────────────────────────────────────────────────────

function QuickActions({ slots, onGo }) {
  const urls = useConfigUrls()

  // "restart agent": the default chat slot (falls back to a slot literally
  // named agent/primary). Label carries the real name so the button is honest.
  const agentSlot =
    slots.find((s) => s.isDefault) ||
    slots.find((s) => s.name === 'agent' || s.name === 'primary') ||
    null

  const restartAgent = () => {
    if (!agentSlot) { toast('No default slot to restart', 'warn'); return }
    if (!window.confirm(`Restart slot "${agentSlot.name}"? In-flight requests will drop.`)) return
    window.dispatchEvent(new CustomEvent('hal0:slot-restart', { detail: { name: agentSlot.name } }))
  }

  const evictIdle = () => {
    // Idle = resident but not actively serving (indicator "stale"/ready).
    const idle = slots.filter((s) => isSlotLive(s) && slotIndicatorFromPhase(s).cls === 'stale')
    if (idle.length === 0) { toast('No idle slots to evict', 'info'); return }
    const names = idle.map((s) => s.name).join(', ')
    if (!window.confirm(`Evict ${idle.length} idle slot${idle.length !== 1 ? 's' : ''} (${names})? They reload on next request.`)) return
    for (const s of idle) {
      window.dispatchEvent(new CustomEvent('hal0:slot-stop', { detail: { name: s.name } }))
    }
  }

  const openWebui = () => {
    const url = urls.data?.openwebui
    if (!url) { toast('OpenWebUI URL not available', 'warn'); return }
    window.open(url, '_blank', 'noopener')
  }

  return (
    <div className="rd-qa">
      <button className="btn ghost sm mono" onClick={restartAgent}>
        restart {agentSlot ? agentSlot.name : 'agent'}
      </button>
      <button className="btn ghost sm mono" onClick={() => onGo('models')}>pull model…</button>
      <button className="btn ghost sm mono" onClick={evictIdle}>evict idle</button>
      <button className="btn ghost sm mono" onClick={openWebui}>open webui ↗</button>
    </div>
  )
}

function HeroStrip({ slots, heroTps, layout, swapMode, onToggleSwapMode, onToggleQuickActions, onGo }) {
  const hw = useHardware()
  const hostName = hw.data?.name || null
  const upCount = slots.filter((s) => isSlotLive(s)).length

  return (
    <div className="rd-hero">
      <span className="rd-hero-greet mono">
        steady on <b>{hostName || '—'}</b>
      </span>
      <span className="rd-hero-meta mono">
        {'· '}{upCount} slot{upCount !== 1 ? 's' : ''} up
        {heroTps != null && <>{' · '}{fmtInt(heroTps)} tok/s</>}
      </span>
      <span className="rd-hero-spacer" />
      {swapMode && (
        <button
          className={'btn ghost sm mono rd-qa-toggle' + (layout.quickActions ? ' on' : '')}
          title="Show or hide the quick-actions strip"
          onClick={onToggleQuickActions}
        >
          quick actions: {layout.quickActions ? 'on' : 'off'}
        </button>
      )}
      {layout.quickActions && <QuickActions slots={slots} onGo={onGo} />}
      <span className="rd-hero-div" />
      <button
        className={'btn ghost sm mono' + (swapMode ? ' rd-customize-on' : '')}
        title="Swap widgets in place — layout stays fixed"
        onClick={onToggleSwapMode}
      >
        {swapMode ? 'done' : 'customize'}
      </button>
    </div>
  )
}

// ─── band 0 · health strip ───────────────────────────────────────────────────

function HealthStrip({ slots, heroTps, attentionItems }) {
  const attentionCount = attentionItems.length
  // Compact summary across the broadened set: approvals vs everything else
  // (error slots, failed downloads, update, drift, messages) as "alerts".
  const approvalCount = attentionItems.filter((it) => it.kind === 'approval').length
  const alertCount = attentionCount - approvalCount
  const attnSummary = [
    approvalCount > 0 ? `${approvalCount} approval${approvalCount !== 1 ? 's' : ''}` : null,
    alertCount > 0 ? `${alertCount} alert${alertCount !== 1 ? 's' : ''}` : null,
  ].filter(Boolean).join(' · ')
  const stats = useStatsHardware()
  const st = stats.data

  const readyCount = slots.filter((s) => {
    const cls = slotIndicatorFromPhase(s).cls
    return cls === 'serving' || cls === 'stale'
  }).length

  const ramUsedGb = st?.ram_used_mb != null ? mbToGb(st.ram_used_mb) : null
  const ramTotalGb = st?.ram_total_mb != null ? mbToGb(st.ram_total_mb) : null
  const gpuPct = typeof st?.gpu_util === 'number' ? Math.round(st.gpu_util * 100) : null
  const gpuTemp = typeof st?.gpu_temp_c === 'number' ? Math.round(st.gpu_temp_c) : null

  return (
    <div className="rd-health">
      <div className="rd-health-cell">
        <div className="rd-health-k mono">slots</div>
        <div className="rd-health-v mono num">
          {slots.length ? <>{readyCount}<span className="dim2">/{slots.length}</span> <span className="sub">ready</span></> : '—'}
        </div>
      </div>
      <div className="rd-health-cell">
        <div className="rd-health-k mono">throughput</div>
        <div className="rd-health-v mono num" style={{ color: heroTps != null ? 'var(--accent)' : undefined }}>
          {heroTps != null ? <>{fmtInt(heroTps)} <span className="sub">tok/s</span></> : '—'}
        </div>
      </div>
      <div className="rd-health-cell">
        <div className="rd-health-k mono">unified memory</div>
        <div className="rd-health-v mono num">
          {ramUsedGb != null && ramTotalGb != null
            ? <>{ramUsedGb.toFixed(1)}<span className="dim2">/{Math.round(ramTotalGb)} GB</span></>
            : '—'}
        </div>
      </div>
      <div className="rd-health-cell">
        <div className="rd-health-k mono">igpu</div>
        <div className="rd-health-v mono num">
          {gpuPct != null ? <>{gpuPct}%{gpuTemp != null && <span className="sub"> · {gpuTemp}°C</span>}</> : '—'}
        </div>
      </div>
      <div className="rd-health-cell">
        <div className="rd-health-k mono">needs attention</div>
        {attentionCount > 0 ? (
          <div className="rd-health-v mono num" style={{ color: 'var(--warn)' }}>
            {attentionCount} <span className="sub">{attnSummary}</span>
          </div>
        ) : (
          <div className="rd-health-v mono num" style={{ color: 'var(--fg-4)' }}>
            0 <span className="sub" style={{ color: 'var(--fg-4)' }}>all clear</span>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── unified memory hero ─────────────────────────────────────────────────────
// Slots live INSIDE the bar: one segment per allocation, width = GB/total.
// System + page cache renders as a 45° striped block; on a configured
// Proxmox host an extra striped "proxmox host" block joins and the total
// switches to the host DIMM total.

const SEG_LABEL_MIN_PX = 90

function MemorySegment({ seg, barWidth, totalGb, onGo }) {
  const pct = totalGb > 0 ? (seg.gb / totalGb) * 100 : 0
  if (pct <= 0) return null
  const pxWidth = (pct / 100) * barWidth
  const showLabel = pxWidth >= SEG_LABEL_MIN_PX

  if (seg.kind === 'system') {
    return (
      <div className="rd-mem-seg rd-mem-seg-system" style={{ width: `${pct}%` }} title={seg.tooltip}>
        {showLabel && <span className="rd-mem-seg-name mono">{seg.label}</span>}
      </div>
    )
  }

  return (
    <div
      className="rd-mem-seg"
      style={{ width: `${pct}%`, background: seg.fill, boxShadow: `inset 0 2px 0 ${seg.color}` }}
      title={seg.tooltip}
      role="link"
      tabIndex={0}
      onClick={() => onGo('slots/' + seg.name)}
      onKeyDown={(e) => { if (e.key === 'Enter') onGo('slots/' + seg.name) }}
    >
      {showLabel && (
        <>
          <span className="rd-mem-seg-name mono" style={{ color: seg.color }}>
            {seg.name} · {seg.gb.toFixed(1)}G
          </span>
          {seg.serving && (
            <span className="rd-mem-seg-sub mono num">
              <span className="dot serving rd-mem-dot" />
              {fmtInt(seg.toks)} tok/s
            </span>
          )}
        </>
      )}
    </div>
  )
}

function MemoryHeroCard({ swap, onGo }) {
  const mm = useMemoryMapModel()
  const stats = useStatsHardware()
  const hw = useHardware()
  const slotsQuery = useSlots()
  const slots = slotsQuery.data ?? []
  const slotByName = useMemo(() => {
    const m = {}
    for (const s of slots) m[s.name] = s
    return m
  }, [slots])

  const [barRef, barWidth] = useMeasuredWidth()

  const pveConfigured = mm.host.mode === 'configured'
  const ramTotalGb =
    (stats.data?.ram_total_mb != null ? mbToGb(stats.data.ram_total_mb) : 0) ||
    hw.data?.ram?.total || 0
  const totalGb = pveConfigured ? (mm.host.totalGb || ramTotalGb) : ramTotalGb
  const ramUsedGb = stats.data?.ram_used_mb != null ? mbToGb(stats.data.ram_used_mb) : null

  // Per-slot allocations (BE-METRICS mem_mb, via the shared memory-map
  // model — same colors as the Slots-page map so the two never diverge).
  const allocSegs = mm.self.slots
    .filter((s) => s.bytesGb > 0)
    .map((s) => {
      const live = slotByName[s.name]
      const ind = live ? slotIndicatorFromPhase(live) : null
      const serving = ind?.cls === 'serving'
      return {
        kind: 'slot',
        name: s.name,
        gb: s.bytesGb,
        // s.color is a var(--mem-slot-N) Okabe–Ito token (shared with the
        // Slots-page memory map). Fill = same hue at ~18% alpha.
        color: s.color,
        fill: `color-mix(in srgb, ${s.color} 18%, transparent)`,
        serving,
        toks: serving ? live?.metrics?.toks : null,
        tooltip: `${s.name} · ${fmtGb(s.bytesGb)} · ${ind ? ind.label : '—'}`,
      }
    })

  const allocGb = allocSegs.reduce((acc, s) => acc + s.gb, 0)
  // System + page cache ≈ host RAM in use beyond what loaded models hold.
  const systemGb = ramUsedGb != null ? Math.max(0, round1(ramUsedGb - allocGb)) : null
  const segs = [...allocSegs]
  if (systemGb != null && systemGb > 0) {
    segs.push({
      kind: 'system',
      gb: systemGb,
      label: `system · ${systemGb.toFixed(1)}G`,
      tooltip: `system + page cache · ${fmtGb(systemGb)}`,
    })
  }
  if (pveConfigured && mm.host.othersGb > 0) {
    segs.push({
      kind: 'system',
      gb: mm.host.othersGb,
      label: `proxmox host · ${mm.host.othersGb.toFixed(1)}G`,
      tooltip: `proxmox host (other tenants) · ${fmtGb(mm.host.othersGb)}`,
    })
  }

  const usedGb = segs.reduce((acc, s) => acc + s.gb, 0)
  const freeGb = totalGb > 0 ? Math.max(0, round1(totalGb - usedGb)) : null

  const platform = hw.data?.platform_label || hw.data?.platform || ''
  const noteBits = [
    totalGb ? `${Math.round(totalGb)} GB pool` : null,
    platform || null,
    hw.data?.memoryKind === 'unified' ? 'uma' : null,
    pveConfigured ? 'proxmox' : null,
  ].filter(Boolean)

  return (
    <RDCard
      title="Unified memory"
      note={noteBits.join(' · ') || '—'}
      swap={swap}
      className="rd-mem-card"
    >
      <div className="rd-mem-bar" ref={barRef}>
        {totalGb > 0 && segs.map((seg, i) => (
          <MemorySegment key={seg.kind === 'slot' ? seg.name : `sys-${i}`} seg={seg} barWidth={barWidth} totalGb={totalGb} onGo={onGo} />
        ))}
        <div className="rd-mem-free">
          <span className="mono num">{freeGb != null ? `free · ${fmtGb(freeGb)}` : '—'}</span>
        </div>
      </div>
      <div className="rd-mem-legend">
        {allocSegs.map((s) => (
          <span key={s.name} className="rd-mem-leg mono">
            <i style={{ background: s.color }} />{s.name} {s.gb.toFixed(1)}G
          </span>
        ))}
        {systemGb != null && systemGb > 0 && (
          <span className="rd-mem-leg rd-mem-leg-system mono">
            <i className="striped" />system {systemGb.toFixed(1)}G
          </span>
        )}
        {pveConfigured && mm.host.othersGb > 0 && (
          <span className="rd-mem-leg rd-mem-leg-system mono">
            <i className="striped" />proxmox host {mm.host.othersGb.toFixed(1)}G
          </span>
        )}
        <span className="rd-mem-leg-spacer" />
        <span className="rd-mem-hint mono">click a block → slot</span>
      </div>
    </RDCard>
  )
}

// ─── band A · throughput ─────────────────────────────────────────────────────

function RDThroughputCard({ swap }) {
  const { data, isPending } = useThroughputHistory()
  const samples = data?.samples ?? []
  const latest = samples.length > 0 ? samples[samples.length - 1] : null
  // Null-honoring invariant (#221): a MEASURED 0.0 renders with the serving
  // count; only an empty history renders "—".
  const hasReading = latest != null && typeof latest.total_tps === 'number'
  const heroTps = hasReading ? latest.total_tps : null
  const serving = hasReading ? (latest.serving_slots ?? 0) : null

  const bars = samples.slice(-20)
  const maxTps = bars.length > 0 ? Math.max(...bars.map((s) => s.total_tps), 1) : 1
  const peak = bars.length > 0 ? Math.max(...bars.map((s) => s.total_tps)) : null
  const hotStart = Math.max(0, bars.length - 4)
  const HOT_OPACITY = [0.55, 0.7, 0.85, 1]

  return (
    <RDCard title="Throughput" note="rolling 60s" swap={swap} className="rd-fill">
      <div className="rd-hero-row">
        <span className="rd-hero-num mono num" style={{ color: heroTps != null ? 'var(--accent)' : 'var(--fg-4)' }}>
          {heroTps != null ? fmt1(heroTps) : '—'}
        </span>
        <span className="rd-hero-unit mono">tok/s</span>
      </div>
      <div className="rd-spark">
        {bars.length < 20 && Array.from({ length: 20 - bars.length }).map((_, i) => (
          <i key={`pad-${i}`} className="pad" style={{ height: '2%' }} />
        ))}
        {bars.map((s, i) => {
          const h = Math.max((s.total_tps / maxTps) * 100, 2)
          const hot = i >= hotStart
          return (
            <i
              key={s.ts}
              className={hot ? 'hot' : ''}
              style={{ height: `${h}%`, opacity: hot ? HOT_OPACITY[i - hotStart] : undefined }}
              title={`${fmt1(s.total_tps)} tok/s`}
            />
          )
        })}
      </div>
      <div className="rd-card-foot mono">
        <span>{isPending ? 'source pending' : `${serving} slot${serving !== 1 ? 's' : ''} serving`}</span>
        <span className="num">{peak != null ? `peak ${fmt1(peak)} tok/s` : ''}</span>
      </div>
    </RDCard>
  )
}

// ─── band A · utilization ────────────────────────────────────────────────────

function UtilRow({ dotColor, name, sub, pct, pill, caption }) {
  const pctDisplay = typeof pct === 'number' ? Math.round(pct * 100) : null
  return (
    <div className="rd-util-row">
      <div className="rd-util-top">
        <span className="rd-util-dot" style={{ background: dotColor }} />
        <span className="rd-util-name mono">{name}</span>
        {sub && <span className="rd-util-sub mono">{sub}</span>}
        {pill || (
          <span className="rd-util-pct mono num" style={{ color: pctDisplay != null ? dotColor : 'var(--fg-4)' }}>
            {pctDisplay != null ? `${pctDisplay}%` : '—'}
          </span>
        )}
      </div>
      <div className="rd-util-track">
        {pctDisplay != null && (
          <div className="rd-util-fill" style={{ width: `${pctDisplay}%`, background: dotColor }} />
        )}
      </div>
      {caption && <div className="rd-util-cap mono num">{caption}</div>}
    </div>
  )
}

function RDUtilizationCard({ swap }) {
  const hw = useHardware()
  const stats = useStatsHardware()
  const power = useStatsPower()
  const st = stats.data
  const slotsQuery = useSlots()
  const slots = slotsQuery.data ?? []

  const gpuGhz = st?.gpu_clock_mhz != null ? (st.gpu_clock_mhz / 1000).toFixed(1) : null
  const gpuTemp = st?.gpu_temp_c != null ? Math.round(st.gpu_temp_c) : null
  const gpuW = power.data?.gpu_power_w != null ? Math.round(power.data.gpu_power_w) : null
  const gpuCaption = [
    gpuGhz != null ? `${gpuGhz} GHz` : null,
    gpuTemp != null ? `${gpuTemp}°C` : null,
    gpuW != null ? `${gpuW} W` : null,
  ].filter(Boolean).join(' · ') || (st?.gpu_util == null ? 'pending driver' : null)

  const cores = hw.data?.cores
  const npuStatus = st?.npu_status ?? null
  const npuActive = npuStatus?.ok ?? null
  const npuUtil = st?.npu_util ?? null
  const npuGb = npuStatus?.model_mb ? mbToGb(npuStatus.model_mb) : null
  const npuSlots = slots.filter((s) => (s.device || '') === 'npu' && s.coresident).map((s) => s.name)
  const npuCaption = [
    npuSlots.length > 1 ? `${npuSlots.join(' + ')} coresident` : (npuSlots[0] || null),
    npuGb != null ? `${npuGb.toFixed(1)} GB` : null,
  ].filter(Boolean).join(' · ') || (npuActive == null ? 'pending driver' : null)

  return (
    <RDCard title="Utilization" note="live" swap={swap} className="rd-fill">
      <div className="rd-util-rows">
        <UtilRow
          dotColor="var(--dev-vulkan)"
          name="igpu"
          sub={hw.data?.gpu || null}
          pct={st?.gpu_util ?? null}
          caption={gpuCaption}
        />
        <UtilRow
          dotColor="var(--dev-cpu)"
          name="cpu"
          sub={cores ? `${cores}c · host` : 'host'}
          pct={st?.cpu_util ?? null}
          caption={st?.cpu_util == null ? 'pending driver' : null}
        />
        <UtilRow
          dotColor="var(--dev-npu)"
          name="npu"
          sub="xdna"
          pct={npuUtil}
          pill={npuActive != null ? (
            <span className={'rd-npu-pill mono' + (npuActive ? ' active' : '')}>
              {npuActive ? 'active' : 'idle'}
            </span>
          ) : null}
          caption={npuCaption}
        />
      </div>
    </RDCard>
  )
}

// ─── band A · requests & latency (NEW widget) ───────────────────────────────

function RDRequestsCard({ swap }) {
  const { data, pending } = useRequestsRollup()

  const eps = data?.endpoints ?? []
  const maxCount = eps.length > 0 ? Math.max(...eps.map((e) => e.count), 1) : 1
  const latency = data && (data.p50_ms != null || data.p95_ms != null)
    ? [
        data.p50_ms != null ? `p50 ${Math.round(data.p50_ms)} ms` : null,
        data.p95_ms != null ? `p95 ${Math.round(data.p95_ms)} ms` : null,
      ].filter(Boolean).join(' · ')
    : null

  return (
    <RDCard title="Requests" note="/v1 · last 60s" swap={swap} className="rd-fill">
      <div className="rd-hero-row">
        <span className="rd-hero-num mono num" style={{ color: data?.req_per_min != null ? 'var(--fg)' : 'var(--fg-4)' }}>
          {data?.req_per_min != null ? fmtInt(data.req_per_min) : '—'}
        </span>
        <span className="rd-hero-unit mono">req/min</span>
        {latency && <span className="rd-hero-aside mono num">{latency}</span>}
      </div>
      {eps.length > 0 && (
        <div className="rd-req-rows">
          {eps.map((e, i) => (
            <div key={e.path} className="rd-req-row">
              <div className="rd-req-top">
                <span className="rd-req-path mono">{e.path}</span>
                <span className="rd-req-count mono num">{e.count}</span>
              </div>
              <div className="rd-util-track">
                <div
                  className="rd-util-fill"
                  style={{
                    width: `${Math.max(Math.round((e.count / maxCount) * 100), 2)}%`,
                    background: i === 0 ? 'var(--fg-3)' : 'var(--fg-4)',
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="rd-card-foot rd-foot-bottom mono">
        {pending ? (
          <span>source pending · dispatcher rollup not yet available</span>
        ) : (
          <span>
            {data?.errors != null ? `${data.errors} error${data.errors !== 1 ? 's' : ''}` : '— errors'}
            {data?.dedupe != null && <> · single-flight dedupe {data.dedupe ? 'on' : 'off'}</>}
          </span>
        )}
      </div>
    </RDCard>
  )
}

// ─── band B · slots (locked) ─────────────────────────────────────────────────

function slotMemLabel(slot) {
  if (slot.coresident && !(typeof slot.mem_mb === 'number' && slot.mem_mb > 0)) return 'shared'
  if (typeof slot.mem_mb === 'number' && slot.mem_mb > 0) return `${mbToGb(slot.mem_mb).toFixed(1)} GB`
  return '—'
}

function RDSlotRow({ slot, onGo }) {
  const ind = slotIndicatorFromPhase(slot)
  const isNpu = (slot.device || '') === 'npu'
  const serving = ind.cls === 'serving' && slot.metrics?.toks != null
  const deEmph = ind.cls === 'offline' || ind.cls === 'error'
  const noModel = !slot.model

  return (
    <div
      className="rd-slot-row"
      onClick={() => onGo('slots/' + slot.name)}
      role="link"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter') onGo('slots/' + slot.name) }}
      title={ind.tooltip}
    >
      <span
        className={'sdot ' + ind.cls}
        style={isNpu && ind.cls === 'serving'
          ? { background: 'var(--dev-npu)', boxShadow: '0 0 8px var(--dev-npu)' }
          : undefined}
      />
      <span className="rd-slot-name mono" style={deEmph ? { color: 'var(--fg-3)' } : undefined}>{slot.name}</span>
      <span className="rd-slot-model mono" style={noModel ? { color: 'var(--fg-5)' } : undefined}>
        {slot.model || 'no model configured'}
      </span>
      <span className={'chip mono dev-' + (slot.device || 'cpu').replace('gpu-', '')} style={deEmph ? { opacity: 0.6 } : undefined}>
        {slot.device || 'cpu'}
      </span>
      {slot.isDefault && <span className="chip outlined amber mono">default</span>}
      {slot.coresident && <span className="chip dev-npu mono rd-chip-co">coresident</span>}
      <span className="rd-slot-mem mono num" style={noModel ? { color: 'var(--fg-5)' } : undefined}>{slotMemLabel(slot)}</span>
      <span
        className="rd-slot-state mono num"
        style={{ color: serving ? 'var(--accent)' : deEmph ? 'var(--fg-4)' : 'var(--fg-3)' }}
      >
        {serving ? `${fmtInt(slot.metrics.toks)} tok/s` : ind.label}
      </span>
    </div>
  )
}

function RDSlotsCard({ onGo }) {
  const slotsQuery = useSlots()
  const slots = slotsQuery.data ?? []
  const readyCount = slots.filter((s) => {
    const cls = slotIndicatorFromPhase(s).cls
    return cls === 'serving' || cls === 'stale'
  }).length

  return (
    <RDCard
      title="Slots"
      count={slots.length ? `${readyCount}/${slots.length} ready` : null}
      right={
        <span className="rd-link mono" onClick={() => onGo('slots')} role="link" tabIndex={0}
          onKeyDown={(e) => { if (e.key === 'Enter') onGo('slots') }}>
          Manage slots →
        </span>
      }
      flush
    >
      {slotsQuery.isLoading && !slotsQuery.data ? (
        <div className="rd-empty mono">loading…</div>
      ) : slots.length === 0 ? (
        <div className="rd-empty mono">no slots configured</div>
      ) : (
        slots.map((s) => <RDSlotRow key={s.name} slot={s} onGo={onGo} />)
      )}
    </RDCard>
  )
}

// ─── band C · activity ───────────────────────────────────────────────────────

function activityTs(ts) {
  if (typeof ts === 'number') {
    const d = new Date(ts * 1000)
    return Number.isNaN(d.getTime()) ? '—' : d.toTimeString().slice(0, 8)
  }
  if (typeof ts === 'string') {
    const d = new Date(ts)
    if (!Number.isNaN(d.getTime())) return d.toTimeString().slice(0, 8)
    return ts.length >= 19 ? ts.slice(11, 19) : '—'
  }
  return '—'
}

const ACTIVITY_ROWS = 6

function RDActivityCard({ swap }) {
  // Polled recent records, NOT the SSE stream — see useActivityRecent: the
  // card only needs the latest handful, and an EventSource against a mock /
  // older backend pollutes the console on every dashboard mount.
  const { data: records } = useActivityRecent(50)
  const rows = (records ?? []).slice(0, ACTIVITY_ROWS)

  return (
    <RDCard title="Activity" note="journald · all slots" swap={swap} className="rd-fill" flush>
      <div className="rd-act-rows">
        {rows.length === 0 ? (
          <div className="rd-empty mono">no recent activity</div>
        ) : (
          rows.map((r) => {
            const warn = r.severity === 'warn'
            const err = r.severity === 'error'
            const isUpdate = (r.category || '') === 'update'
            const tagColor = err ? 'var(--err)' : warn ? 'var(--warn)' : isUpdate ? 'var(--accent)' : 'var(--fg-4)'
            const msgColor = err ? 'var(--err)' : warn ? 'var(--warn)' : 'var(--fg-2)'
            return (
              <div key={r.id ?? `${r.ts}-${r.message}`} className="rd-act-row">
                <span className="rd-act-ts mono num">{activityTs(r.ts)}</span>
                <span className="rd-act-tag mono" style={{ color: tagColor }}>[{r.category || r.kind}]</span>
                <span className="rd-act-msg mono" style={{ color: msgColor }}>{r.message}</span>
              </div>
            )
          })
        )}
      </div>
      <div className="rd-act-foot">
        <span
          className="rd-link mono"
          role="button"
          tabIndex={0}
          onClick={() => window.dispatchEvent(new CustomEvent('hal0:open-journal'))}
          onKeyDown={(e) => { if (e.key === 'Enter') window.dispatchEvent(new CustomEvent('hal0:open-journal')) }}
        >
          Open journal →
        </span>
      </div>
    </RDCard>
  )
}

// ─── band C · services ───────────────────────────────────────────────────────

const SERVICE_ORDER = ['openwebui', 'comfyui', 'hermes', 'turnstone', 'n8n']

function RDServicesCard({ swap, onGo }) {
  const svc = useServices()
  const services = svc.data?.services ?? []
  const ordered = [
    ...SERVICE_ORDER.map((id) => services.find((s) => s.id === id)).filter(Boolean),
    ...services.filter((s) => !SERVICE_ORDER.includes(s.id)),
  ].slice(0, 4)

  return (
    <RDCard title="Services" swap={swap} className="rd-fill" flush>
      {svc.pending ? (
        <div className="rd-empty mono">source pending</div>
      ) : ordered.length === 0 ? (
        <div className="rd-empty mono">no companion services</div>
      ) : (
        <div className="rd-svc-grid">
          {ordered.map((s) => {
            const disabled = s.unit_state?.unit_file_state === 'disabled' || (!s.up && !s.managed)
            const dotCls = s.up ? 'ready' : disabled ? 'empty' : 'offline'
            const openable = s.up && s.url
            return (
              <div
                key={s.id}
                className="rd-svc-cell"
                role="link"
                tabIndex={0}
                onClick={() => onGo('services')}
                onKeyDown={(e) => { if (e.key === 'Enter') onGo('services') }}
                title={s.name}
              >
                <div className="rd-svc-top">
                  <span className={'dot ' + dotCls} style={{ width: 6, height: 6 }} />
                  <span className="rd-svc-name mono" style={disabled ? { color: 'var(--fg-3)' } : undefined}>{s.id}</span>
                  {openable && (
                    <span
                      className="rd-svc-open mono"
                      title={`Open ${s.name}`}
                      onClick={(e) => { e.stopPropagation(); window.open(s.url, '_blank', 'noopener') }}
                    >↗</span>
                  )}
                </div>
                <div className="rd-svc-sub mono num" style={disabled ? { color: 'var(--fg-5)' } : undefined}>
                  {disabled ? 'disabled' : (s.detail || '—')}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </RDCard>
  )
}

// ─── band C · needs attention (locked, with inline actions) ─────────────────

function RDAttentionCard({ items }) {
  return (
    <RDCard
      title="Needs attention"
      right={items.length > 0 ? <span className="rd-attn-count mono num">{items.length}</span> : null}
      className="rd-fill"
      flush
    >
      {items.length === 0 ? (
        <div className="rd-empty rd-attn-clear mono">nothing needs you</div>
      ) : (
        <div className="rd-attn-items">
          {items.map((it) => (
            <div key={it.key} className="rd-attn-item">
              <div
                className="rd-attn-eyebrow mono"
                style={{ color: it.tone === 'accent' ? 'var(--accent)' : it.tone === 'err' ? 'var(--err)' : 'var(--warn)' }}
              >
                {it.eyebrow}
              </div>
              <div className="rd-attn-body">{it.body}</div>
              <div className="rd-attn-actions">
                {it.actions.map((a) => (
                  <button
                    key={a.label}
                    className={'btn sm mono' + (a.primary ? '' : ' ghost')}
                    onClick={a.onClick}
                  >
                    {a.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </RDCard>
  )
}

// ─── swapped-in external widgets ─────────────────────────────────────────────
// slottrack / power / quickchat are the already-built board cards from the
// previous dashboard (window globals, DCard shell). They own their full
// card chrome, so the ⇄ control overlays the header corner.

function ExternalWidget({ widgetId, swap }) {
  const globalName = { slottrack: 'SlotTrackCard', power: 'PowerCard', quickchat: 'QuickChatCard' }[widgetId]
  const C = globalName ? window[globalName] : null
  return (
    <div className="rd-ext">
      {typeof C === 'function' ? <C /> : <div className="rd-empty mono">widget unavailable ({widgetId})</div>}
      {swap && <span className="rd-ext-swap">{swap}</span>}
    </div>
  )
}

// ─── cell dispatch ───────────────────────────────────────────────────────────

function CellWidget({ cellId, layout, onSwap, onGo, attentionItems }) {
  const widgetId = layout.cells[cellId]
  const swapNode = <SwapButton cellId={cellId} current={widgetId} onSwap={onSwap} />
  switch (widgetId) {
    case 'memorybar':   return <MemoryHeroCard swap={swapNode} onGo={onGo} />
    case 'throughput':  return <RDThroughputCard swap={swapNode} />
    case 'utilization': return <RDUtilizationCard swap={swapNode} />
    case 'requests':    return <RDRequestsCard swap={swapNode} />
    case 'slots':       return <RDSlotsCard onGo={onGo} />
    case 'activity':    return <RDActivityCard swap={swapNode} />
    case 'services':    return <RDServicesCard swap={swapNode} onGo={onGo} />
    case 'attention':   return <RDAttentionCard items={attentionItems} />
    case 'slottrack':
    case 'power':
    case 'quickchat':   return <ExternalWidget widgetId={widgetId} swap={swapNode} />
    default:            return <div className="rd-empty mono">unknown widget ({widgetId})</div>
  }
}

// ─── post-update slot-drift banner (WS-J, #1111) ──────────────────────────────
//
// After a self-update the slot unit files are re-rendered on disk but the
// running containers are NOT bounced (a restart could kill a mid-inference
// request). This banner surfaces the slots still serving the pre-update
// launch command and offers a one-click restart of ONLY those slots — nothing
// is ever bounced automatically.
function SlotDriftBanner() {
  const drift = useSlotDrift()
  const restart = useRestartDriftedSlots()
  const count = drift.data?.count ?? 0
  if (count <= 0) return null
  const names = (drift.data?.slots ?? []).map((s) => s.slot).filter(Boolean).join(', ')
  const plural = count === 1 ? '' : 's'
  return (
    <div className="banner-stack">
      <div className="banner banner-warn" role="status">
        <span className="banner-ic" aria-hidden="true">↻</span>
        <div className="banner-content">
          <span className="banner-eye">Update · slots need restart</span>
          <span className="banner-heading">{count} slot{plural} need restart</span>
          <span className="banner-body">
            {names ? names + ' — ' : ''}still running the pre-update launch command.
            Restarting briefly interrupts any in-flight request on those slots.
          </span>
          <div className="banner-actions">
            <button
              className="btn ghost sm mono"
              disabled={restart.isPending}
              onClick={() => restart.mutate(undefined)}
            >
              {restart.isPending ? 'restarting…' : 'restart drifted slots'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── view ────────────────────────────────────────────────────────────────────

function DashboardRedesignView({ onGo }) {
  const go = onGo || ((id) => { window.location.hash = '#' + id })

  const layoutQuery = useDashLayout()
  const saveLayout = useSaveDashLayout()
  const layout = useMemo(() => reconcile(layoutQuery.data), [layoutQuery.data])

  const [swapMode, setSwapMode] = useState(false)

  const slotsQuery = useSlots()
  const slots = slotsQuery.data ?? []
  const hw = useHardware()
  const attentionItems = useAttentionItems()

  // Hero/health throughput: latest rolling-60s sample; measured 0 is real.
  const { data: tpHistory } = useThroughputHistory()
  const tpSamples = tpHistory?.samples ?? []
  const tpLatest = tpSamples.length > 0 ? tpSamples[tpSamples.length - 1] : null
  const heroTps = tpLatest != null && typeof tpLatest.total_tps === 'number' ? tpLatest.total_tps : null

  const handleSwap = useCallback((cellId, widgetId) => {
    saveLayout.mutate({ ...layout, cells: { ...layout.cells, [cellId]: widgetId } })
  }, [layout, saveLayout])

  const handleToggleQuickActions = useCallback(() => {
    saveLayout.mutate({ ...layout, quickActions: !layout.quickActions })
  }, [layout, saveLayout])

  // Confirmed-empty install → point at setup instead of an empty board.
  const noSlotsConfigured = Array.isArray(slotsQuery.data) && slotsQuery.data.length === 0
  if (noSlotsConfigured) {
    const hostName = hw.data?.name || (typeof HAL0_DATA !== 'undefined' ? HAL0_DATA.host?.name : '') || '—'
    return (
      <div className="view">
        <div className="dash-empty">
          <div className="dash-empty-glyph"><Wordmark size={56} /></div>
          <h1 className="mono">No models configured yet</h1>
          <p>hal0 is ready, but no slot has a model loaded. Run <span className="mono">hal0 setup</span> in your terminal to install a bundle, or configure slots one at a time.</p>
          <div className="dash-empty-meta mono">
            <span><span style={{ color: 'var(--fg-3)' }}>host</span> {hostName}</span>
            {hw.data?.ram?.total ? <><span style={{ color: 'var(--fg-5)' }}>·</span><span><span style={{ color: 'var(--fg-3)' }}>ram</span> {hw.data.ram.total} GB</span></> : null}
            {hw.data?.npu?.present && <><span style={{ color: 'var(--fg-5)' }}>·</span><span><span style={{ color: 'var(--fg-3)' }}>npu</span> ready</span></>}
          </div>
          <div className="dash-empty-cta">
            <button className="btn ghost lg" onClick={() => go('slots')}>Configure slots</button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={'view rd-view' + (swapMode ? ' rd-swapmode' : '')}>
      <SlotDriftBanner />
      <HeroStrip
        slots={slots}
        heroTps={heroTps}
        layout={layout}
        swapMode={swapMode}
        onToggleSwapMode={() => setSwapMode((m) => !m)}
        onToggleQuickActions={handleToggleQuickActions}
        onGo={go}
      />

      <HealthStrip slots={slots} heroTps={heroTps} attentionItems={attentionItems} />

      <CellWidget cellId="memory" layout={layout} onSwap={handleSwap} onGo={go} attentionItems={attentionItems} />

      <div className="rd-band rd-band-a">
        {['a1', 'a2', 'a3'].map((cellId) => (
          <CellWidget key={cellId} cellId={cellId} layout={layout} onSwap={handleSwap} onGo={go} attentionItems={attentionItems} />
        ))}
      </div>

      <CellWidget cellId="slots" layout={layout} onSwap={handleSwap} onGo={go} attentionItems={attentionItems} />

      <div className="rd-band rd-band-c">
        {['c1', 'c2', 'c3'].map((cellId) => (
          <CellWidget key={cellId} cellId={cellId} layout={layout} onSwap={handleSwap} onGo={go} attentionItems={attentionItems} />
        ))}
      </div>
    </div>
  )
}

Object.assign(window, { DashboardRedesignView })
