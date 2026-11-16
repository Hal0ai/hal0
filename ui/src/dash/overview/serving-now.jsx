// hal0 Overview v2 — Serving now: OvServingNow({ onGo }).
//
// Top: token rate per slot over 5m/15m/1h, stacked and coloured by device lane, with a GPU-busy
// strip (ActivityChart, shared with the NPU band). Below: one table, a section per lane (vulkan /
// rocm / npu / cpu); loaded slots (any state but stopped) are rows, stopped ones are chips that
// load on click. Hovering a row fades the other slots in the chart. Grouping and per-slot figures
// come from serving-pure.ts; windowed figures (peak, requests, TTFT) from /api/stats/activity.

import { useSlots, useSlotLoad, useSlotUnload } from '@/api/hooks/useSlots'
import { useSystemStats } from '@/api/hooks/useSystemStats'
import { useSlotActivity } from '@/api/hooks/useSlotActivity'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { dutyCells, fmtMs, fmtRate } from '../activity-chart-pure'
import { ActivityChart, ActivityWindowSwitch } from '../ActivityChart.jsx'
import { fmtK } from './overview-pure'
import { laneHues, laneRows } from './serving-pure'

const { useMemo, useState } = React

const DOT = { serving: 'serving', stale: 'ready', warming: 'ready', error: 'err', offline: '' }
const COLS = 8

function SlotRow({ tile, hue, activity, focused, onFocus, onGo, onUnload, onLogs }) {
  const open = () => onGo('slots/' + tile.name)
  const stop = (e) => e.stopPropagation()
  return (
    <tr
      className={'ov-srow ' + tile.cls + (focused ? ' on' : '')}
      data-testid={`ov-slot-row-${tile.name}`}
      onClick={open}
      onMouseEnter={() => onFocus(tile.name)}
      onMouseLeave={() => onFocus(null)}
    >
      <td>
        <span className="sw" style={{ background: hue }} />
        <button type="button" className="ov-nm" onClick={(e) => (stop(e), open())}>
          {tile.name}
        </button>
        {tile.isDefault && <span className="ov-chip amber">default</span>}
      </td>
      <td className="ov-mdl" title={tile.model ? tile.model + (tile.port != null ? ` · :${tile.port}` : '') : undefined}>
        {tile.model || '—'}
      </td>
      <td className="ov-st">
        <span className={'ov-dot ' + DOT[tile.cls]} />
        {tile.stateLabel}
        {tile.inflight ? <span className="ov-infl"> · {tile.inflight} in flight</span> : null}
      </td>
      <td className="num" style={tile.toks ? { color: hue } : undefined}>
        {tile.toks ? fmtRate(tile.toks) : '—'}
      </td>
      <td className="num">{activity ? fmtRate(activity.peak_tps) : '—'}</td>
      <td className="num">
        {activity ? activity.requests : '—'}
        {activity?.errors ? <span className="err"> · {activity.errors} err</span> : null}
      </td>
      <td className="num">{fmtMs(activity?.ttft_p50_ms)}</td>
      <td className="ov-ctxc">
        {tile.ctx ? (
          <span className="ov-ctx" title={`${tile.ctx.used} / ${tile.ctx.max} tokens`}>
            <span className="ov-track">
              <i style={{ width: tile.ctx.pct + '%', background: tile.ctx.pct > 85 ? 'var(--warn)' : 'var(--fg-3)' }} />
            </span>
            {fmtK(tile.ctx.used)}/{fmtK(tile.ctx.max)}
          </span>
        ) : (
          <span className="ov-cap">{tile.caption || tile.memLabel || ''}</span>
        )}
        {/* Shown on row hover/focus over the ctx cell, so they take no column of their own. */}
        <span className="ov-acts" onClick={stop}>
          <button type="button" className="ov-btn sm" onClick={() => onLogs(tile.name)}>
            logs
          </button>
          <button type="button" className="ov-btn sm" onClick={() => onUnload(tile.name)}>
            unload
          </button>
        </span>
      </td>
    </tr>
  )
}

export function OvServingNow({ onGo }) {
  const slots = useSlots().data
  const sys = useSystemStats().data
  const hw = useStatsHardware().data
  const load = useSlotLoad()
  const unload = useSlotUnload()
  const [win, setWin] = useState('15m')
  const [focus, setFocus] = useState(null)
  const act = useSlotActivity(win)
  const activity = act.data

  const lanes = useMemo(() => laneRows(Array.isArray(slots) ? slots : [], sys, undefined), [slots, sys])
  const hues = useMemo(() => laneHues(lanes), [lanes])
  // Every configured slot that moved tokens in the window gets a band, stopped ones included (a
  // slot unloaded since its traffic still belongs in the window). Lane order, bottom-up.
  const series = useMemo(() => {
    const names = lanes.flatMap((l) => [...l.loaded.map((t) => t.name), ...l.stopped.map((s) => s.name)])
    return names
      .filter((n) => activity?.slots?.[n]?.tps.some((v) => v > 0))
      .map((n) => ({ key: n, values: activity.slots[n].tps, hue: hues[n] }))
  }, [lanes, activity, hues])
  // A GPU pinned to a forced performance level reads busy all the time (util_is_forced_high), so
  // the strip would claim load that is not there: leave it out.
  const gpuForced = hw?.util_is_forced_high === true
  const gpuCells = gpuForced ? null : dutyCells(activity?.fleet?.gpu_util)

  const total = lanes.reduce((n, l) => n + l.loaded.length + l.stopped.length, 0)
  const loaded = lanes.reduce((n, l) => n + l.loaded.length, 0)
  // Same hand-off as the command palette: route to Slots, then ask its log drawer to open (the short
  // delay covers SlotsView's listener not being mounted yet).
  const onLogs = (name) => {
    onGo('slots')
    setTimeout(() => window.dispatchEvent(new CustomEvent('hal0:slot-logs', { detail: { name } })), 60)
  }
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
      {lanes.length > 0 && (
        <>
          <div className="ov-sact" data-testid="ov-activity">
            <div className="act-h">
              <span className="act-note">
                tok/s per slot, stacked{activity ? ` · ${activity.bucket_s}s buckets` : ''}
                {gpuCells ? ' · strip: GPU busy' : ''}
                {gpuForced ? ' · GPU clock pinned, busy not shown' : ''}
              </span>
              <span className="act-gap" />
              <ActivityWindowSwitch value={win} onChange={setWin} testid="ov-activity" />
            </div>
            <ActivityChart
              series={series}
              focus={focus}
              window={win}
              testid="ov-activity"
              emptyText={act.isError ? 'Activity unavailable.' : act.isLoading ? '—' : 'No traffic in this window.'}
              strip={gpuCells ? { cells: gpuCells, color: 'var(--fg-2)', title: 'GPU busy per bucket' } : null}
            />
          </div>
          <div className="act-tablewrap">
            <table className="act-table ov-stable">
              <thead>
                <tr>
                  <th scope="col">Slot</th>
                  <th scope="col">Model</th>
                  <th scope="col">State</th>
                  <th scope="col" className="num">tok/s</th>
                  <th scope="col" className="num">Peak</th>
                  <th scope="col" className="num">Req</th>
                  <th scope="col" className="num">TTFT p50</th>
                  <th scope="col">Ctx</th>
                </tr>
              </thead>
              {lanes.map((l) => (
                <tbody className="ov-lane" key={l.key} data-testid={`ov-lane-${l.key}`}>
                  <tr className="ov-lane-h">
                    <th scope="rowgroup" colSpan={COLS}>
                      <span className="ov-d" style={{ background: 'var(--dev-' + l.key + ')' }} />
                      {l.label}
                      <span className="ov-cnt">
                        {l.loaded.length} / {l.loaded.length + l.stopped.length} loaded
                      </span>
                    </th>
                  </tr>
                  {l.loaded.map((t) => (
                    <SlotRow
                      key={t.name}
                      tile={t}
                      hue={hues[t.name]}
                      activity={activity?.slots?.[t.name] ?? null}
                      focused={focus === t.name}
                      onFocus={setFocus}
                      onGo={onGo}
                      onUnload={onUnload}
                      onLogs={onLogs}
                    />
                  ))}
                  {l.stopped.length > 0 && (
                    <tr className="ov-stopped-row">
                      <td colSpan={COLS}>
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
                      </td>
                    </tr>
                  )}
                </tbody>
              ))}
            </table>
          </div>
        </>
      )}
    </window.OvCard>
  )
}

Object.assign(window, { OvServingNow })
