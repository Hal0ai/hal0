// hal0 dashboard — the dock's events pane: the journal and the activity trail as one timeline,
// with stream / severity / source / slot / search filters. The ticker on the collapsed dock draws
// the same rows (`EventRow`), without the stripe or the detail.

import { useState } from 'react'
import { activityExportUrl } from '@/api/hooks/useActivity'
import { DOCK_FILTERS_DEFAULT, actorLabel, exportFilters, filterEvents, sourceGroups } from './dock-pure'
import { _srcHue, _shortTs, highlightFoot } from '../chrome.jsx'

const STREAM_CHIPS = [
  { key: 'all', label: 'all' },
  { key: 'j', label: 'journal' },
  { key: 'a', label: 'activity' },
]
const SEVERITY_CHIPS = ['all', 'ok', 'info', 'warn', 'error']

// One timeline row. `stripe` and `onClick` are the pane's; the ticker passes neither.
export function EventRow({ e, q = '', stripe = false, open = false, onClick, testid = 'dock-event-row' }) {
  const text = q ? highlightFoot(e.msg, q) : e.msg
  const clickable = !!onClick
  return (
    <div
      className={'foot-line ' + e.level + (clickable ? ' clickable' : '')}
      style={{ '--src': _srcHue(e.source) }}
      data-testid={testid}
      data-stream={e.stream}
      onClick={onClick}
      {...(clickable ? { role: 'button', tabIndex: 0, 'aria-expanded': open, onKeyDown: (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onClick() } } } : {})}
    >
      {stripe && <span className="stripe" />}
      <span className="ts">{_shortTs(e.ts)}</span>
      <span className={'ev-k ' + e.stream} title={e.stream === 'a' ? 'activity' : 'journal'}>{e.stream === 'a' ? 'A' : 'J'}</span>
      <span className={'src ' + e.source}><span className="d" />{e.source}</span>
      <span className="lvl">{e.level}</span>
      <span className="msg">
        {text}
        {e.record && <span className="actor">{actorLabel(e.record.actor)}</span>}
      </span>
    </div>
  )
}

function EventDetail({ record }) {
  const head = [
    record.action,
    record.target,
    record.outcome,
    record.duration_ms != null ? `${record.duration_ms} ms` : null,
  ].filter((p) => p != null && p !== '').join(' · ')
  const hasDiff = record.before || record.after
  return (
    <div className="foot-line-detail" data-testid="dock-event-detail">
      <div>{head}</div>
      {record.error && <div className="err">{record.error}</div>}
      {hasDiff && <pre>{JSON.stringify({ before: record.before, after: record.after }, null, 1)}</pre>}
    </div>
  )
}

function ChipGroup({ testid, label, items, value, onPick }) {
  return (
    <div className="foot-pane-filter mono" data-testid={testid} role="group" aria-label={label}>
      {items.map((it) => (
        <button
          key={it.key}
          onClick={() => onPick(it.key)}
          className={'foot-pane-chip' + (value === it.key ? ' on' : '')}
        >{it.label}</button>
      ))}
    </div>
  )
}

export function EventsPane({ events, filters, onFilters, slotNames = [], disconnected = false }) {
  const [openIds, setOpenIds] = useState(() => new Set())
  const filtered = filterEvents(events, filters)
  const set = (patch) => onFilters({ ...filters, ...patch })
  const toggle = (id) =>
    setOpenIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <div className="foot-pane" data-testid="dock-events">
      <div className="foot-pane-h mono">
        <span>Events</span>
        <span className="ct">· {filtered.length} / {events.length}</span>
        <ChipGroup testid="dock-filter-stream" label="Stream" items={STREAM_CHIPS} value={filters.stream} onPick={(stream) => set({ stream })} />
        <ChipGroup testid="dock-filter-severity" label="Severity" items={SEVERITY_CHIPS.map((k) => ({ key: k, label: k }))} value={filters.severity} onPick={(severity) => set({ severity })} />
        <ChipGroup testid="dock-filter-source" label="Source" items={sourceGroups(events).map((k) => ({ key: k, label: k }))} value={filters.source} onPick={(source) => set({ source })} />
        <select
          className="foot-pane-slot mono"
          data-testid="dock-slot"
          aria-label="Slot"
          value={filters.slot}
          onChange={(e) => set({ slot: e.target.value })}
        >
          <option value="all">All slots</option>
          {slotNames.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <input
          className="foot-pane-search mono"
          value={filters.q}
          onChange={(e) => set({ q: e.target.value })}
          placeholder="search…"
        />
        <span className="foot-pane-right">
          <span style={{ color: disconnected ? 'var(--warn)' : 'var(--ok)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <span className={'dot' + (disconnected ? '' : ' ready')} />
            {disconnected ? 'reconnecting…' : 'follow tail'}
          </span>
          <a href="#logs" className="foot-pane-link">Open full logs →</a>
          <a
            className="foot-pane-link"
            data-testid="dock-export"
            href={activityExportUrl('csv', exportFilters(filters))}
            title="Exports the activity trail with these filters. Journal lines are on the Logs page."
          >Export ↓</a>
        </span>
      </div>
      <div className="foot-pane-body">
        {events.length === 0 ? (
          <div className="foot-pane-empty">No events yet</div>
        ) : filtered.length === 0 ? (
          <div className="foot-pane-empty">
            No events match. <span className="foot-pane-clear" onClick={() => onFilters(DOCK_FILTERS_DEFAULT)}>Clear filters</span>
          </div>
        ) : filtered.map((e) => {
          const open = openIds.has(e.id)
          return (
            <div key={e.id} className="foot-event">
              <EventRow e={e} q={filters.q} stripe open={open} onClick={e.record ? () => toggle(e.id) : undefined} />
              {open && e.record && <EventDetail record={e.record} />}
            </div>
          )
        })}
      </div>
    </div>
  )
}
