// hal0 Overview v2 — the Now band: mode pip, facts line, contextual actions and the unified
// memory bar. Window-global module (OvNowBand); the rules live in now-pure.ts.
//
// The bar is the dashboard's MemoryHeroCard bar restyled: MemorySegment and useMeasuredWidth are
// copied from dashboard-redesign.jsx rather than imported, since that file goes away.

import { useSlots, useSlotLoad } from '@/api/hooks/useSlots'
import { useHardware, hasGpuAccess } from '@/api/hooks/useHardware'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { useStatsPower } from '@/api/hooks/useStatsPower'
import { useArbiter } from '@/api/hooks/useComfyui'
import { useSystemStats } from '@/api/hooks/useSystemStats'
import { gpuUtilReading } from '@/lib/gpuUtil'
import { useMemoryMapModel } from '../memory-map'
import { slotIndicatorFromPhase } from '../slot-status.js'
import { actionFacts } from '@/dash/image/status-line-pure'
import { useStatusLineActions } from '@/dash/image/useStatusLineActions'
import { PinConfirm, ForceStopConfirm } from '../dock/gpu-parts.jsx'
import { nowFacts, modeLabel, contextualActions } from './now-pure'

const { useState, useMemo, useRef, useEffect } = React

const round1 = (n) => Math.round(n * 10) / 10
const fmtInt = (n) => (typeof n === 'number' && Number.isFinite(n) ? Math.round(n).toString() : '—')
const fmtGb = (gb) => (typeof gb === 'number' && Number.isFinite(gb) ? `${gb.toFixed(1)} GB` : '—')
const toast = (msg, kind = 'info') => window.__hal0Toast && window.__hal0Toast(msg, kind)

const SEG_LABEL_MIN_PX = 90

// Element width via ResizeObserver: a segment carries its label only when it is wide enough.
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

function MemorySegment({ seg, barWidth, totalGb, onGo }) {
  const pct = totalGb > 0 ? (seg.gb / totalGb) * 100 : 0
  if (pct <= 0) return null
  const showLabel = (pct / 100) * barWidth >= SEG_LABEL_MIN_PX

  if (seg.kind === 'system') {
    return (
      <div className="ov-mem-seg sys" style={{ width: `${pct}%` }} title={seg.tooltip}>
        {showLabel && <span className="ov-nm">{seg.label}</span>}
      </div>
    )
  }

  const go = () => {
    if (seg.canGo) onGo('slots/' + seg.name)
  }
  return (
    <div
      className="ov-mem-seg"
      style={{ width: `${pct}%`, background: seg.fill, boxShadow: `inset 0 2px 0 ${seg.color}` }}
      title={seg.tooltip}
      role="link"
      tabIndex={0}
      onClick={go}
      onKeyDown={(e) => { if (e.key === 'Enter') go() }}
    >
      {showLabel && (
        <>
          <span className="ov-nm" style={{ color: seg.color }}>{seg.label} · {seg.gb.toFixed(1)}G</span>
          {seg.serving && <span className="ov-sb">● {fmtInt(seg.toks)} tok/s</span>}
        </>
      )}
    </div>
  )
}

function OvNowBand({ onGo }) {
  const mm = useMemoryMapModel()
  const hw = useHardware()
  const slotsQuery = useSlots()
  const slots = slotsQuery.data ?? []
  const stats = useStatsHardware()
  const power = useStatsPower()
  const sys = useSystemStats()
  const arbiterQ = useArbiter()
  const loadMut = useSlotLoad()
  const actions = useStatusLineActions()
  const [barRef, barWidth] = useMeasuredWidth()

  const slotByName = useMemo(() => {
    const m = {}
    for (const s of slots) m[s.name] = s
    return m
  }, [slots])

  // ── the memory bar (same arithmetic as MemoryHeroCard) ──
  const pveConfigured = mm.host.mode === 'configured'
  const totalGb = pveConfigured ? (mm.host.totalGb || mm.pool.totalGb) : mm.pool.totalGb
  const ramUsedGb = mm.ram ? mm.ram.usedGb : null

  const allocSegs = mm.self.slots
    .filter((s) => s.bytesGb > 0)
    .map((s) => {
      const live = slotByName[s.name]
      const ind = live ? slotIndicatorFromPhase(live) : null
      const serving = ind?.cls === 'serving'
      return {
        kind: 'slot',
        name: s.name,
        label: s.isImageEngine ? 'image engine' : s.name,
        canGo: !s.isImageEngine || !!live,
        gb: s.bytesGb,
        color: s.color,
        fill: `color-mix(in srgb, ${s.color} 18%, transparent)`,
        serving,
        toks: serving ? live?.metrics?.toks : null,
        tooltip: `${s.isImageEngine ? 'image engine' : s.name} · ${fmtGb(s.bytesGb)}${s.estimated ? ' (estimated)' : ''} · ${ind ? ind.label : '—'}`,
      }
    })
    .sort((a, b) => b.gb - a.gb)

  const allocGb = allocSegs.reduce((acc, s) => acc + s.gb, 0)
  // On a GPU box the bar is the GPU pool and the striped block is the pool held by other guests;
  // with no GPU pool the bar is this box's RAM and the block is system + page cache.
  const poolBar = !!mm.gpu && !pveConfigured
  const stripedName = poolBar ? 'other guests' : 'system'
  const stripedGb = poolBar
    ? mm.gpu.otherGuestsGb
    : ramUsedGb != null ? Math.max(0, round1(ramUsedGb - allocGb)) : null
  const segs = [...allocSegs]
  if (stripedGb != null && stripedGb > 0) {
    segs.push({
      kind: 'system',
      gb: stripedGb,
      label: stripedName,
      tooltip: `${poolBar ? 'other guests' : 'system + page cache'} · ${fmtGb(stripedGb)}`,
    })
  }
  const hostGb = pveConfigured ? mm.host.othersGb : 0
  if (hostGb > 0) {
    segs.push({ kind: 'system', gb: hostGb, label: 'proxmox host', tooltip: `proxmox host (other tenants) · ${fmtGb(hostGb)}` })
  }
  const usedGb = segs.reduce((acc, s) => acc + s.gb, 0)
  const freeGb = poolBar
    ? mm.gpu.freeGb
    : totalGb > 0 ? Math.max(0, round1(totalGb - usedGb)) : null

  // ── facts / mode / actions ──
  const util = gpuUtilReading(stats.data)
  const facts = nowFacts(slots, sys.data, freeGb ?? null, totalGb > 0 ? totalGb : null, {
    access: hasGpuAccess(hw.data),
    // A pinned counter (forced performance level) says nothing about real load.
    pct: util.pinned ? null : util.pct,
    tempC: power.data?.gpu_temp_c ?? stats.data?.gpu_temp_c ?? null,
    watts: power.data?.gpu_power_w ?? null,
  })
  const arbiter = arbiterQ.data?.arbiter ?? null
  const mode = modeLabel(arbiter)
  const acts = contextualActions(mode.kind, slots)
  const ctx = actionFacts(arbiterQ.data, null)

  const loadSlot = (name) => {
    Promise.resolve(loadMut.mutateAsync(name)).catch((e) => toast(`Could not load ${name}: ${e?.message || e}`, 'err'))
  }
  const onAction = (a) => {
    switch (a.id) {
      case 'hold-image': return void actions.run('hold', ctx)
      case 'back-to-chat': return void actions.run('stop', ctx)
      case 'load-slot': return loadSlot(a.slot)
      case 'agent-chat': return void window.dispatchEvent(new CustomEvent('hal0:toggle-agent-chat'))
    }
  }

  return (
    <section className="ov-card ov-now">
      <div className="ov-now-row">
        <span className={'ov-now-mode' + (mode.kind === 'image' ? ' img' : '')}>
          <span className="ov-d" />{mode.text}
        </span>
        <span className="ov-now-facts">
          <span><b>{facts.loaded}</b> of {facts.total} slots loaded</span>
          {facts.serving > 0 ? (
            <span>
              <b className="hot">{facts.serving} serving · {facts.tps != null ? fmtInt(facts.tps) : '—'} tok/s</b>
              {' '}· {facts.inflight != null ? facts.inflight : '—'} in flight
            </span>
          ) : (
            <span><b>0</b> serving</span>
          )}
          <span>{facts.freeLabel}</span>
          <span>{facts.gpuLabel}</span>
        </span>
        <span className="ov-now-acts">
          {acts.map((a) => (
            <button
              key={a.id}
              type="button"
              className={'ov-btn' + (a.id === 'agent-chat' ? ' primary' : '')}
              disabled={actions.pending != null}
              onClick={() => onAction(a)}
            >
              {a.label}
            </button>
          ))}
        </span>
      </div>
      {actions.error && <div className="ov-now-facts" role="alert">{actions.error}</div>}
      <div className="ov-membar" ref={barRef}>
        {totalGb > 0 && segs.map((seg, i) => (
          <MemorySegment key={seg.kind === 'slot' ? seg.name : `sys-${i}`} seg={seg} barWidth={barWidth} totalGb={totalGb} onGo={onGo} />
        ))}
        <div className="ov-mem-free">
          <span className="ov-nm">{freeGb != null ? `free · ${fmtGb(freeGb)}` : '—'}</span>
        </div>
      </div>
      <div className="ov-mem-leg">
        {allocSegs.map((s) => (
          <span key={s.name}><i style={{ background: s.color }} />{s.label} {s.gb.toFixed(1)}G</span>
        ))}
        {stripedGb != null && stripedGb > 0 && (
          <span><i className="striped" />{stripedName} {stripedGb.toFixed(1)}G</span>
        )}
        {hostGb > 0 && <span><i className="striped" />proxmox host {hostGb.toFixed(1)}G</span>}
        <span className="sp" />
        <span>{totalGb ? `${Math.round(totalGb)} GB pool · ` : ''}click a block → slot</span>
      </div>
      <PinConfirm confirm={actions.confirm} />
      <ForceStopConfirm confirm={actions.confirm} />
    </section>
  )
}

Object.assign(window, { OvNowBand })
