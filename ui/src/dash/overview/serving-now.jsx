// hal0 Overview v2 — Serving now: OvServingNow({ onGo }).
//
// The old 13-row slot table as lane-grouped tiles. A lane is a rail group (vulkan / rocm / npu /
// cpu); loaded slots are tiles (state, model, ctx fill, sparkline while serving), stopped ones are
// chips that load on click. The grouping and every figure come from serving-pure.ts.

import { useSlots, useSlotLoad, useSlotUnload } from '@/api/hooks/useSlots'
import { useSystemStats } from '@/api/hooks/useSystemStats'
import { useThroughputHistory } from '@/api/hooks/useThroughputHistory'
import { fmtK } from './overview-pure'
import { laneRows } from './serving-pure'

const { useMemo } = React

const DOT = { serving: 'serving', stale: 'ready', warming: 'ready', error: 'err', offline: '' }

function ServingTile({ tile, onGo, onUnload }) {
  const open = () => onGo('slots/' + tile.name)
  const serving = tile.cls === 'serving'
  const stop = (e) => e.stopPropagation()
  return (
    <div
      className={'ov-tile ' + (serving ? 'serving' : 'ready')}
      tabIndex={0}
      role="link"
      aria-label={tile.name}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && e.target === e.currentTarget) open()
      }}
    >
      <div className="ov-r1">
        <span className={'ov-dot ' + DOT[tile.cls]} />
        <span className="ov-nm">{tile.name}</span>
        {tile.isDefault && <span className="ov-chip amber">default</span>}
        <span className="ov-st">{tile.stateText}</span>
        <span className="ov-acts" onClick={stop}>
          <button type="button" className="ov-btn sm" onClick={() => onUnload(tile.name)}>
            unload
          </button>
        </span>
      </div>
      <div className="ov-model">
        {tile.model || '—'}
        {tile.port != null && <span style={{ color: 'var(--fg-5)' }}> · :{tile.port}</span>}
      </div>
      <div className="ov-r3">
        {tile.ctx ? (
          <span className="ov-ctx">
            ctx
            <span className="ov-track">
              <i style={{ width: tile.ctx.pct + '%', background: tile.ctx.pct > 85 ? 'var(--warn)' : 'var(--fg-3)' }} />
            </span>
            {fmtK(tile.ctx.used)}/{fmtK(tile.ctx.max)}
          </span>
        ) : (
          <span className="ov-ctx">{tile.caption || ''}</span>
        )}
        {tile.spark ? <window.OvSpark values={tile.spark} color="var(--ok)" /> : <span>{tile.memLabel}</span>}
      </div>
    </div>
  )
}

function OvServingNow({ onGo }) {
  const slots = useSlots().data
  const sys = useSystemStats().data
  const history = useThroughputHistory().data
  const load = useSlotLoad()
  const unload = useSlotUnload()
  const lanes = useMemo(
    () => laneRows(Array.isArray(slots) ? slots : [], sys, history?.per_slot),
    [slots, sys, history],
  )

  const up = (l) => l.loaded.filter((t) => t.cls === 'serving' || t.cls === 'stale').length
  const total = lanes.reduce((n, l) => n + l.loaded.length + l.stopped.length, 0)
  const loaded = lanes.reduce((n, l) => n + up(l), 0)
  const onUnload = (name) => {
    if (window.confirm('Unload ' + name + '?')) unload.mutate(name)
  }

  return (
    <window.OvCard
      title="Serving now"
      note={slots ? loaded + '/' + total + ' loaded' : undefined}
      flush
      right={
        <button type="button" className="ov-lnk" onClick={() => onGo('slots')}>
          Manage slots →
        </button>
      }
    >
      {lanes.length === 0 && <div className="ov-empty">{slots ? 'No slots configured' : '—'}</div>}
      {lanes.map((l) => (
        <div className="ov-lane" key={l.key}>
          <div className="ov-lane-h">
            <span className="ov-d" style={{ background: 'var(--dev-' + l.key + ')' }} />
            {l.label}
            <span className="ov-cnt">
              {up(l)} / {l.loaded.length + l.stopped.length} loaded
            </span>
          </div>
          {l.loaded.length > 0 && (
            <div className="ov-tiles">
              {l.loaded.map((t) => (
                <ServingTile key={t.name} tile={t} onGo={onGo} onUnload={onUnload} />
              ))}
            </div>
          )}
          {l.stopped.length > 0 && (
            <div className="ov-stopped">
              <span>stopped</span>
              {l.stopped.map((s) => (
                <button
                  type="button"
                  key={s.name}
                  className="ov-sc"
                  title={s.model || undefined}
                  onClick={() => load.mutate(s.name)}
                >
                  <span className="ov-play">▶</span>
                  {s.name}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </window.OvCard>
  )
}

Object.assign(window, { OvServingNow })
