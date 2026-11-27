// The array band's right half: token rate per NPU lane over 5m/15m/1h (stacked), the NPU duty
// strip under it, and one table row per lane. Hovering a row fades the other lanes in the chart.
// Every figure is a real field or "—": the chart and the req/TTFT columns come from
// /api/stats/activity, "tok/s" from the slot's live metrics, "last used" from occupancy.
import { useMemo, useState } from 'react'
import { useSlotActivity } from '@/api/hooks/useSlotActivity'
import { ageLabel, dutyCells, fmtMs, fmtRate } from '../activity-chart-pure'
import { ActivityChart, ActivityWindowSwitch } from '../ActivityChart.jsx'

const ENGINE_LABEL = { flm: 'FLM', xdna: 'GGML' }

/**
 * @param {object} props
 * @param {import('./npu-activity-pure').NpuLaneRow[]} props.rows  from npuLaneRows (activity left null)
 * @param {Array<{name: string, engine: 'flm' | 'xdna'}>} props.chartLanes  every NPU lane, up or
 *   not: a lane stopped since its traffic still belongs in the window's chart
 * @param {Record<string, {hue: string}>} props.hues  engine → colour tokens
 */
export function NpuActivity({ rows, chartLanes, hues }) {
  const [win, setWin] = useState('15m')
  const [focus, setFocus] = useState(null)
  const actQ = useSlotActivity(win)
  const act = actQ.data

  const lanes = useMemo(
    () => rows.map((r) => ({ ...r, activity: act?.slots?.[r.name] ?? null })),
    [rows, act],
  )
  const hueOf = Object.fromEntries(chartLanes.map((l) => [l.name, hues[l.engine]?.hue ?? 'var(--dev-npu)']))
  // Every NPU lane that moved tokens in the window gets a band, including one stopped since
  // (STT/embed rows move no tokens and stay table-only). GPU slots in the payload never do.
  const series = useMemo(
    () =>
      chartLanes
        .filter((l) => act?.slots?.[l.name]?.tps.some((v) => v > 0))
        .map((l) => ({ key: l.name, values: act.slots[l.name].tps, hue: hues[l.engine]?.hue ?? 'var(--dev-npu)' })),
    [chartLanes, act, hues],
  )
  const duty = dutyCells(act?.fleet?.npu_util)

  return (
    <div className="npt-act" data-testid="npu-activity">
      <div className="act-h">
        <h3 className="act-ttl">Activity</h3>
        <span className="act-note">tok/s per lane, stacked{act ? ` · ${act.bucket_s}s buckets` : ''}</span>
        <span className="act-gap" />
        <ActivityWindowSwitch value={win} onChange={setWin} testid="npu-activity" />
      </div>
      <ActivityChart
        series={series}
        focus={focus}
        window={win}
        testid="npu-activity"
        emptyText={actQ.isError ? 'Activity unavailable.' : actQ.isLoading ? '—' : 'No NPU traffic in this window.'}
        strip={duty ? { cells: duty, color: 'var(--dev-npu)', title: 'NPU duty per bucket (runtime-PM residency)' } : null}
      />

      {lanes.length ? (
        <div className="act-tablewrap">
          <table className="act-table tc-table tc-act" role="table" data-testid="npu-lanes-table">
            <thead role="rowgroup">
              <tr role="row">
                <th scope="col" role="columnheader">Lane</th>
                <th scope="col" role="columnheader">Role · model</th>
                <th scope="col" role="columnheader">Cols</th>
                <th scope="col" role="columnheader" className="num">tok/s</th>
                <th scope="col" role="columnheader" className="num">Peak</th>
                <th scope="col" role="columnheader" className="num">Req</th>
                <th scope="col" role="columnheader" className="num">TTFT p50</th>
                <th scope="col" role="columnheader" className="num">Last used</th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {lanes.map((l) => (
                <tr
                  key={l.name}
                  role="row"
                  data-testid={`npu-lane-row-${l.name}`}
                  className={focus === l.name ? 'on' : undefined}
                  onMouseEnter={() => setFocus(l.name)}
                  onMouseLeave={() => setFocus(null)}
                >
                  <td role="cell" className="tc-name">
                    <span className="sw" style={{ background: hueOf[l.name] }} />
                    {l.name}
                    <small>{ENGINE_LABEL[l.engine]}</small>
                  </td>
                  <td role="cell" className="dim tc-wide" data-label="Role · model">
                    <span className="role">{l.role}</span> · {l.model ?? '—'}
                  </td>
                  <td role="cell" className="dim tc-num" data-label="Cols">{l.cols}</td>
                  <td role="cell" className="num tc-lead" data-label="tok/s" style={l.nowToks ? { color: hueOf[l.name] } : undefined}>
                    {fmtRate(l.nowToks)}
                  </td>
                  <td role="cell" className="num tc-num" data-label="Peak">{l.activity ? fmtRate(l.activity.peak_tps) : '—'}</td>
                  <td role="cell" className="num tc-num" data-label="Req">
                    {l.activity ? l.activity.requests : '—'}
                    {l.activity?.errors ? <span className="err"> · {l.activity.errors} err</span> : null}
                  </td>
                  <td role="cell" className="num tc-num" data-label="TTFT p50">{fmtMs(l.activity?.ttft_p50_ms)}</td>
                  <td role="cell" className="num tc-wide" data-label="Last used">{ageLabel(l.lastUsedAge)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <span className="npt-hint">Nothing holds the NPU. Start a lane below.</span>
      )}
    </div>
  )
}
