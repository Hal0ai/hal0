// The slot rail: a compact roster of every slot, grouped by device, beside the Inference pane on
// the Slots page. A row reads like the overview's slot row (dot, name, model, device chip, memory,
// state) and jumps to the slot's card in the pane; the dock's hal0:slot-event flashes the row of
// the slot an event is about. The rules (grouping, order, counts, footer) live in slot-rail-pure.ts.

import React from 'react'
import { useSlots } from '@/api/hooks/useSlots'
import { useArbiter } from '@/api/hooks/useComfyui'
import { useMemoryBudget } from '@/api/hooks/useStatsHardware'
import { slotMemLabel } from './dashboard-redesign.jsx'
import { RAIL_GROUPS, railCounts, railFooter, railRows, stateCell } from './slot-rail-pure'

const { useEffect, useMemo, useRef } = React

const FLASH_MS = 1600

// querySelector wants an escaped value; CSS.escape is missing in a few embedded runtimes.
const cssEscape = (s) => (typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&'))

export function SlotRail({ onNew }) {
  const slotsQuery = useSlots()
  const arbiter = useArbiter().data?.arbiter ?? null
  const budget = useMemoryBudget().data

  const slots = useMemo(() => slotsQuery.data ?? [], [slotsQuery.data])
  const imageMode = arbiter?.mode === 'img'
  const unloadSet = arbiter?.unload_set
  const rows = useMemo(
    () => railRows(slots, unloadSet ?? [], imageMode, Date.now(), slotMemLabel),
    [slots, unloadSet, imageMode],
  )
  const counts = railCounts(rows)
  const poolFreeGb = budget?.gpu ? Math.max(0, budget.gpu.total_gb - budget.gpu.used_gb) : null
  const foot = railFooter(slots, rows, poolFreeGb)

  // One timer per flashing element, so a second event restarts the 1.6 s window.
  const timers = useRef(new Map())
  const asideRef = useRef(null)
  const namesRef = useRef(new Set())
  namesRef.current = new Set(slots.map((s) => s.name))

  const flash = (el, key) => {
    if (!el) return
    const prev = timers.current.get(key)
    if (prev != null) clearTimeout(prev)
    el.classList.remove('flash')
    void el.offsetWidth // reflow, so the animation starts over
    el.classList.add('flash')
    timers.current.set(
      key,
      setTimeout(() => {
        el.classList.remove('flash')
        timers.current.delete(key)
      }, FLASH_MS),
    )
  }

  useEffect(() => {
    const onEvent = (e) => {
      const name = e.detail?.slot
      // The dispatcher also names non-slot targets (a model, a service); only real slots flash.
      if (!name || !namesRef.current.has(name)) return
      flash(asideRef.current?.querySelector(`[data-slot="${cssEscape(name)}"]`), 'row:' + name)
    }
    window.addEventListener('hal0:slot-event', onEvent)
    const live = timers.current
    return () => {
      window.removeEventListener('hal0:slot-event', onEvent)
      live.forEach((t) => clearTimeout(t))
      live.clear()
    }
  }, [])

  const goToCard = (name) => {
    const card = document.querySelector(`[data-testid="infer-slot-${cssEscape(name)}"]`)
    if (!card) return
    card.scrollIntoView({ block: 'nearest' })
    flash(card, 'card:' + name)
  }

  return (
    <aside className="slot-rail" data-testid="slot-rail" aria-label="Slots" ref={asideRef}>
      <div className="rail-h">
        <span className="eyebrow mono">SLOTS</span>
        <span className="rail-count mono" data-testid="slot-rail-count">
          <b>{counts.ready}</b> / {counts.total} ready
        </span>
        <button type="button" className="btn sm" data-testid="slot-rail-new" onClick={onNew}>
          + New slot
        </button>
      </div>
      <div className="rail-body">
        {RAIL_GROUPS.map((g) => {
          const groupRows = rows.filter((r) => r.group === g.key)
          if (groupRows.length === 0) return null
          const c = counts.byGroup[g.key]
          return (
            <div key={g.key} className="rail-group">
              <div className="rail-grp" style={{ '--gc': `var(--dev-${g.key})` }}>
                <span className="gdot" />
                <span className="gl mono">{g.label}</span>
                <span className="gc mono">
                  {c.up} / {c.total} up
                </span>
              </div>
              {groupRows.map((r) => {
                const cell = stateCell(r)
                const chip = (r.device || 'cpu').replace('gpu-', '')
                return (
                  <div
                    key={r.name}
                    className={'srow' + (r.cls === 'offline' || r.cls === 'error' ? ' off' : '')}
                    data-testid="slot-rail-row"
                    data-slot={r.name}
                    role="button"
                    tabIndex={0}
                    title={r.tooltip}
                    onClick={() => goToCard(r.name)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        goToCard(r.name)
                      }
                    }}
                  >
                    <span className={'sdot ' + r.cls + (r.group === 'npu' ? ' npu' : '')} />
                    <span className="nm">
                      <span className="name mono">{r.name}</span>
                      <span className={'chip mono dev-' + chip}>{r.device}</span>
                    </span>
                    <span className={'st mono' + (cell.tone ? ' ' + cell.tone : '')}>{cell.text}</span>
                    <span className="mdl mono">{r.model || 'no model configured'}</span>
                    <span className="mem mono">{r.mem}</span>
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
      <div className="rail-f mono" data-testid="slot-rail-footer">
        <span>loaded {foot.loadedGb.toFixed(1)} GB</span>
        {foot.poolFreeGb != null && <span>pool {foot.poolFreeGb.toFixed(1)} GB free</span>}
        {foot.paused > 0 && (
          <span className="paused-note">{foot.paused} paused for image mode</span>
        )}
      </div>
    </aside>
  )
}
