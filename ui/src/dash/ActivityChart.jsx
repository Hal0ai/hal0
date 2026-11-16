// The stacked activity chart shared by the NPU array band and the Overview's Serving now card:
// a 5m/15m/1h switch, token rate per lane stacked bottom-up, an optional duty strip and the time
// axis. Callers own the data (useSlotActivity), the lane colours and the hover focus; this file
// only draws. Pure maths lives in activity-chart-pure.ts.
import { useMemo } from 'react'
import { ACTIVITY_WINDOWS } from '@/api/hooks/useSlotActivity'
import { stackSeries, windowTicks } from './activity-chart-pure'

const CHART_H = 96

/** Window switch: a button group with aria-pressed. `testid` prefixes each button's test id. */
export function ActivityWindowSwitch({ value, onChange, testid }) {
  return (
    <div className="act-seg" role="group" aria-label="Time window">
      {ACTIVITY_WINDOWS.map((w) => (
        <button
          key={w}
          type="button"
          aria-pressed={value === w}
          className={value === w ? 'on' : undefined}
          onClick={() => onChange(w)}
          data-testid={testid ? `${testid}-win-${w}` : undefined}
        >
          {w}
        </button>
      ))}
    </div>
  )
}

/**
 * @param {object} props
 * @param {Array<{key: string, values: number[], hue: string}>} props.series  bottom-up; only
 *   series that moved tokens should be passed
 * @param {string | null} [props.focus]  key to keep bright; the rest fade
 * @param {string} props.emptyText  shown over an empty plot
 * @param {'5m'|'15m'|'1h'} props.window
 * @param {{cells: Array<{opacity: number}>, color: string, title: string} | null} [props.strip]
 * @param {string} [props.testid]
 */
export function ActivityChart({ series, focus = null, emptyText, window: win, strip = null, testid }) {
  const { paths, yMax } = useMemo(
    () => stackSeries(series.map((s) => ({ key: s.key, values: s.values })), CHART_H),
    [series],
  )
  const hueOf = Object.fromEntries(series.map((s) => [s.key, s.hue]))
  return (
    <>
      <div className="act-chart">
        <div className="act-plot">
          <svg viewBox={`0 0 1000 ${CHART_H}`} preserveAspectRatio="none" aria-hidden="true">
            {paths.map((p) => {
              const dim = focus && focus !== p.key
              return (
                <g key={p.key} data-lane={p.key}>
                  <path d={p.area} style={{ fill: hueOf[p.key], fillOpacity: dim ? 0.06 : 0.26 }} />
                  <path
                    d={p.line}
                    style={{ fill: 'none', stroke: hueOf[p.key], strokeOpacity: dim ? 0.2 : 1, strokeWidth: 1.5, vectorEffect: 'non-scaling-stroke' }}
                  />
                </g>
              )
            })}
          </svg>
          {!paths.length && (
            <div className="act-empty" data-testid={testid ? `${testid}-empty` : undefined}>
              {emptyText}
            </div>
          )}
        </div>
        <div className="act-y" aria-hidden="true">
          <span>{yMax}</span>
          <span>{yMax / 2}</span>
          <span>0</span>
        </div>
      </div>
      {strip && (
        <div className="act-strip" data-testid={testid ? `${testid}-duty` : undefined} title={strip.title}>
          {strip.cells.map((c, i) => (
            <span key={i} style={{ opacity: c.opacity, background: strip.color }} />
          ))}
        </div>
      )}
      <div className="act-x" aria-hidden="true">
        {windowTicks(win).map((t) => (
          <span key={t}>{t}</span>
        ))}
      </div>
    </>
  )
}
