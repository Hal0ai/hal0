// The array band's right half: token rate per NPU lane over 5m/15m/1h (stacked), the NPU duty
// strip under it, and one table row per lane. Hovering a row fades the other lanes in the chart.
// Every figure is a real field or "—": the chart and the req/TTFT columns come from
// /api/stats/activity, "tok/s" from the slot's live metrics, "last used" from occupancy.
import { useMemo, useState } from 'react'
import { ACTIVITY_WINDOWS, useSlotActivity } from '@/api/hooks/useSlotActivity'
import { ageLabel, dutyCells, fmtMs, fmtRate, stackSeries, windowTicks } from '../activity-chart-pure'

const CHART_H = 96
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
  // Every NPU lane that moved tokens in the window gets a band, including one stopped since
  // (STT/embed rows move no tokens and stay table-only).
  const { paths, yMax } = useMemo(() => {
    const charted = chartLanes.filter((l) => act?.slots?.[l.name]?.tps.some((v) => v > 0))
    return stackSeries(charted.map((l) => ({ key: l.name, values: act.slots[l.name].tps })), CHART_H)
  }, [chartLanes, act])
  const hueOf = Object.fromEntries(chartLanes.map((l) => [l.name, hues[l.engine]?.hue ?? 'var(--dev-npu)']))
  const duty = dutyCells(act?.fleet?.npu_util)
  const ticks = windowTicks(win)

  return (
    <div className="npt-act" data-testid="npu-activity">
      <div className="npt-act-h">
        <h3 className="npt-act-ttl">Activity</h3>
        <span className="npt-act-note">
          tok/s per lane, stacked{act ? ` · ${act.bucket_s}s buckets` : ''}
        </span>
        <span className="npt-act-gap" />
        <div className="npt-seg" role="group" aria-label="Time window">
          {ACTIVITY_WINDOWS.map((w) => (
            <button
              key={w}
              type="button"
              aria-pressed={win === w}
              className={win === w ? 'on' : undefined}
              onClick={() => setWin(w)}
              data-testid={`npu-activity-win-${w}`}
            >
              {w}
            </button>
          ))}
        </div>
      </div>

      <div className="npt-act-chart">
        <div className="npt-act-plot">
          <svg viewBox={`0 0 1000 ${CHART_H}`} preserveAspectRatio="none" aria-hidden="true">
            {paths.map((p) => {
              const dim = focus && focus !== p.key
              return (
                <g key={p.key} data-lane={p.key}>
                  <path d={p.area} style={{ fill: hueOf[p.key], fillOpacity: dim ? 0.06 : 0.26 }} />
                  <path d={p.line} style={{ fill: 'none', stroke: hueOf[p.key], strokeOpacity: dim ? 0.2 : 1, strokeWidth: 1.5, vectorEffect: 'non-scaling-stroke' }} />
                </g>
              )
            })}
          </svg>
          {!paths.length && (
            <div className="npt-act-empty" data-testid="npu-activity-empty">
              {actQ.isError ? 'Activity unavailable.' : actQ.isLoading ? '—' : 'No NPU traffic in this window.'}
            </div>
          )}
        </div>
        <div className="npt-act-y" aria-hidden="true">
          <span>{yMax}</span>
          <span>{yMax / 2}</span>
          <span>0</span>
        </div>
      </div>

      {duty && (
        <div className="npt-act-duty" data-testid="npu-activity-duty" title="NPU duty per bucket (runtime-PM residency)">
          {duty.map((c, i) => (
            <span key={i} style={{ opacity: c.opacity }} />
          ))}
        </div>
      )}
      <div className="npt-act-x" aria-hidden="true">
        {ticks.map((t) => (
          <span key={t}>{t}</span>
        ))}
      </div>

      {lanes.length ? (
        <div className="npt-act-tablewrap">
          <table className="npt-act-table" data-testid="npu-lanes-table">
            <thead>
              <tr>
                <th scope="col">Lane</th>
                <th scope="col">Role · model</th>
                <th scope="col">Cols</th>
                <th scope="col" className="num">tok/s</th>
                <th scope="col" className="num">Peak</th>
                <th scope="col" className="num">Req</th>
                <th scope="col" className="num">TTFT p50</th>
                <th scope="col" className="num">Last used</th>
              </tr>
            </thead>
            <tbody>
              {lanes.map((l) => (
                <tr
                  key={l.name}
                  data-testid={`npu-lane-row-${l.name}`}
                  className={focus === l.name ? 'on' : undefined}
                  onMouseEnter={() => setFocus(l.name)}
                  onMouseLeave={() => setFocus(null)}
                >
                  <td>
                    <span className="sw" style={{ background: hueOf[l.name] }} />
                    {l.name}
                    <small>{ENGINE_LABEL[l.engine]}</small>
                  </td>
                  <td className="dim">
                    <span className="role">{l.role}</span> · {l.model ?? '—'}
                  </td>
                  <td className="dim">{l.cols}</td>
                  <td className="num" style={l.nowToks ? { color: hueOf[l.name] } : undefined}>
                    {fmtRate(l.nowToks)}
                  </td>
                  <td className="num">{l.activity ? fmtRate(l.activity.peak_tps) : '—'}</td>
                  <td className="num">
                    {l.activity ? l.activity.requests : '—'}
                    {l.activity?.errors ? <span className="err"> · {l.activity.errors} err</span> : null}
                  </td>
                  <td className="num">{fmtMs(l.activity?.ttft_p50_ms)}</td>
                  <td className="num">{ageLabel(l.lastUsedAge)}</td>
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
