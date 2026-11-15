// Pure helpers for the stacked activity charts (NPU array band; meant for the Overview too).
// The chart draws into a fixed 1000×height viewBox stretched to its box, so x is the bucket index
// scaled to 0..1000 and y grows downward from the top.
import type { ActivityWindow } from '@/api/hooks/useSlotActivity'

export const CHART_W = 1000

export interface StackInput {
  key: string
  values: ReadonlyArray<number>
}
export interface StackedPath {
  key: string
  /** Closed band between this series' top and the one below it. */
  area: string
  /** The band's top edge. */
  line: string
}

/** Axis top: the stacked peak plus headroom, rounded up to a step of 10 (5 below 10). */
export function niceMax(peak: number): number {
  if (!(peak > 0)) return 10
  const padded = peak * 1.15
  const step = padded <= 10 ? 5 : 10
  return Math.ceil(padded / step) * step
}

/** Stack the series bottom-up in the given order. Every series must have the same length. */
export function stackSeries(series: ReadonlyArray<StackInput>, height: number): { paths: StackedPath[]; yMax: number } {
  const n = series[0]?.values.length ?? 0
  if (!n) return { paths: [], yMax: niceMax(0) }
  const totals = Array.from({ length: n }, (_, i) => series.reduce((a, s) => a + (s.values[i] || 0), 0))
  const yMax = niceMax(Math.max(...totals))
  const x = (i: number) => (n === 1 ? CHART_W : (i / (n - 1)) * CHART_W).toFixed(1)
  const y = (v: number) => (height - (v / yMax) * height).toFixed(1)
  const cum = new Array<number>(n).fill(0)
  const paths = series.map((s) => {
    const bottom = cum.slice()
    for (let i = 0; i < n; i++) cum[i] += s.values[i] || 0
    let line = `M${x(0)},${y(cum[0])}`
    for (let i = 1; i < n; i++) line += ` L${x(i)},${y(cum[i])}`
    let area = line
    for (let i = n - 1; i >= 0; i--) area += ` L${x(i)},${y(bottom[i])}`
    return { key: s.key, area: `${area} Z`, line }
  })
  return { paths, yMax }
}

const TICKS: Record<ActivityWindow, string[]> = {
  '5m': ['-5m', '-4m', '-3m', '-2m', '-1m', 'now'],
  '15m': ['-15m', '-12m', '-9m', '-6m', '-3m', 'now'],
  '1h': ['-60m', '-48m', '-36m', '-24m', '-12m', 'now'],
}
export function windowTicks(w: ActivityWindow): string[] {
  return TICKS[w]
}

/** Seconds since last use as a short age; "—" when never used. */
export function ageLabel(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec) || sec < 0) return '—'
  if (sec < 5) return 'now'
  if (sec < 60) return `${Math.round(sec)}s`
  if (sec < 3600) return `${Math.round(sec / 60)}m`
  return `${Math.round(sec / 3600)}h`
}

export function fmtRate(v: number | null | undefined): string {
  return v == null || !Number.isFinite(v) ? '—' : v.toFixed(1)
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return '—'
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`
}

/** Duty cells for a strip: opacity per bucket, or null when no bucket has a reading (the strip
 *  then stays hidden rather than drawing an empty row). */
export function dutyCells(duty: ReadonlyArray<number | null> | undefined): Array<{ v: number | null; opacity: number }> | null {
  if (!duty?.length || duty.every((d) => d == null)) return null
  return duty.map((d) => ({ v: d, opacity: d == null ? 0 : 0.1 + 0.9 * Math.max(0, Math.min(1, d)) }))
}
