// hal0 Overview v2 — Usage card: window picker over GET /api/stats.
//
// Heroes (tokens, decode tok/s, TTFT), a per-bucket token bar strip, and a
// per-model table. The strip needs the optional `buckets` list; without it the
// card still renders fully and the strip stays empty.
import { useStatsSummary } from '@/api/hooks/useStatsSummary'
import { usageModel } from './usage-pure'

const { useState, useCallback } = React

export const WINDOW_KEY = 'hal0.overview.usageWindow'
const WINDOWS = ['1h', '24h', '7d']

function readWindow() {
  try {
    const v = localStorage.getItem(WINDOW_KEY)
    if (WINDOWS.includes(v)) return v
  } catch {
    // storage blocked: fall through to the default
  }
  return '24h'
}

const fmtInt = (n) => (typeof n === 'number' ? n.toLocaleString('en-US') : '—')
const plural = (n, w) => `${fmtInt(n)} ${w}${n === 1 ? '' : 's'}`

const BAR_W = 420
const BAR_H = 44
const BAR_GAP = 2

function UsageBars({ values }) {
  const n = values.length
  const max = Math.max(...values, 1)
  const bw = n > 0 ? (BAR_W - BAR_GAP * (n - 1)) / n : 0
  return (
    <svg className="ov-usage-spk" viewBox={`0 0 ${BAR_W} ${BAR_H}`} preserveAspectRatio="none" role="img" aria-label="tokens per bucket">
      {values.map((v, i) => {
        const bh = Math.max((v / max) * (BAR_H - 4), v > 0 ? 3 : 1.5)
        return (
          <rect
            key={i}
            x={(i * (bw + BAR_GAP)).toFixed(1)}
            y={(BAR_H - bh).toFixed(1)}
            width={bw.toFixed(1)}
            height={bh.toFixed(1)}
            rx="1.5"
            fill={v > 0 ? 'var(--accent)' : 'var(--bg-4)'}
            opacity={i >= n - 1 ? 1 : v > 0 ? 0.75 : 1}
          >
            <title>{fmtInt(v)} tokens</title>
          </rect>
        )
      })}
    </svg>
  )
}

export function OvUsage({ onGo }) {
  const [win, setWin] = useState(readWindow)
  const stats = useStatsSummary(win)
  const m = usageModel(stats.data, win)

  const pick = useCallback((w) => {
    setWin(w)
    try {
      localStorage.setItem(WINDOW_KEY, w)
    } catch {
      // storage blocked: the choice still applies for this session
    }
  }, [])

  const tabs = (
    <span className="ov-tabs" role="group" aria-label="window">
      {WINDOWS.map((w) => (
        <button key={w} type="button" data-win={w} aria-pressed={win === w} onClick={() => pick(w)}>
          {w}
        </button>
      ))}
    </span>
  )

  const prefillSub = m.prefill === '—' ? 'prefill —' : `prefill ${fmtInt(Number(m.prefill))} tok/s`
  const reqSub = m.requests === null ? '—' : `${plural(m.requests, 'request')} · ${plural(m.errors, 'error')}`

  return (
    <OvCard
      title="Usage"
      note={`/v1 · last ${win}`}
      right={tabs}
      foot={
        <>
          <span>from the request-metric table (stats overview) · 60 s dispatcher rollups stay in the dock</span>
          <span style={{ flex: 1 }} />
          <button type="button" className="ov-lnk" onClick={() => onGo('benchmarks')}>
            Benchmarks →
          </button>
        </>
      }
    >
      <div className="ov-heroes">
        <div className="ov-hero">
          <span className="ov-v accent">{m.tokens}</span>
          <span className="ov-k">tokens generated</span>
          <span className="ov-s">{reqSub}</span>
        </div>
        <div className="ov-hero">
          <span className="ov-v">{m.decode}</span>
          <span className="ov-k">decode tok/s · avg</span>
          <span className="ov-s">{prefillSub}</span>
        </div>
        <div className="ov-hero">
          <span className="ov-v">{m.ttft}</span>
          <span className="ov-k">ttft · p50</span>
          <span className="ov-s">p95 {m.p95}</span>
        </div>
      </div>
      <UsageBars values={m.buckets} />
      {m.buckets.length > 0 && <div className="ov-card-n" style={{ marginTop: -6 }}>{m.bucketNote}</div>}
      {m.empty && <div className="ov-empty">no requests in the last {win}</div>}
      {m.rows.length > 0 && (
        <div className="ov-tscroll">
          <table className="ov-mtable">
            <thead>
              <tr>
                <th>model</th>
                <th>requests</th>
                <th>ttft p50</th>
                <th>decode tok/s</th>
                <th>draft accept</th>
                <th>errors</th>
              </tr>
            </thead>
            <tbody>
              {m.rows.map((r) => (
                <tr key={r.key}>
                  <td className="m" title={r.label}>{r.label}</td>
                  <td>
                    {r.req}
                    <span className="ov-bar"><i style={{ width: `${Math.round(r.share)}%` }} /></span>
                  </td>
                  <td>{r.ttft}</td>
                  <td>{r.tps}</td>
                  <td>{r.accept}</td>
                  <td className={r.errors ? '' : 'ok'}>{r.errors}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </OvCard>
  )
}

Object.assign(window, { OvUsage })
