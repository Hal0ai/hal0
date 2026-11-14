// The AIE tile grid, its owner hues and the duty gauge. Moved out of npu-pane.jsx so the NPU tab
// and the old occupancy card share one copy (npu-pane.jsx re-exports until it is removed).
// Per-tile load is never claimed: the shimmer is cosmetic, the aggregate level is real.

// owner hues — assigned by slot index so each resident FLM reads as its own
// owner. Three cohesive accents (deep-indigo → teal → sage); single-tenant NPU
// usually shows just the first. The hue fills the slot's claimed columns in the
// grid AND its mini column strip; glow/line drive the active-tile halo.
export const HUES = [
  { hue: 'var(--npu-s0)', glow: 'var(--npu-s0-glow)', line: 'var(--npu-s0-line)', dim: 'var(--npu-s0-dim)', fg: 'var(--npu-s0-fg)' },
  { hue: 'var(--npu-s1)', glow: 'var(--npu-s1-glow)', line: 'var(--npu-s1-line)', dim: 'var(--npu-s1-dim)', fg: 'var(--npu-s1-fg)' },
  { hue: 'var(--npu-s2)', glow: 'var(--npu-s2-glow)', line: 'var(--npu-s2-line)', dim: 'var(--npu-s2-dim)', fg: 'var(--npu-s2-fg)' },
]

// honour reduced-motion → frozen snapshot (no breathing, no glow pulse)
const REDUCE =
  typeof window !== 'undefined' &&
  window.matchMedia &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

export const NPU_ROWS = 4
export const NPU_COLS = 8

// ─── "aliveness" model ─────────────────────────────────────────────────────
// The claimed columns breathe harder the more the NPU is actually working.
// Blend (per design intent): the coarse duty-cycle sets the baseline energy,
// live tok/s adds transient spikes on top. The aggregate signal is REAL; the
// per-tile variation (phase, jitter) is decorative — there is no per-tile
// counter on this kernel, so we never claim one.
const TPS_CEIL = 60 // tok/s that maps to a full throughput spike
export const activityLevel = (dutyPct, tpsSum) => {
  const duty = Math.max(0, Math.min(1, (dutyPct || 0) / 100))
  const tps = Math.max(0, Math.min(1, (tpsSum || 0) / TPS_CEIL))
  return Math.max(0, Math.min(1, 0.5 * duty + 0.7 * tps))
}
// deterministic per-tile hash → [0,1), stable across renders (no reshuffle)
const hash01 = (n) => {
  const x = Math.sin(n) * 43758.5453
  return x - Math.floor(x)
}
// map activity (0..1) + a tile's base opacity → the CSS-animation custom props
// for that tile. Anchors: 0% solid + faint breath · 25% ≈ ±20% swing / 10–20
// pulses-per-min · 80%+ wider swing / ≈30–45 ppm, randomised per tile.
export const tileAnim = (act, base, c, r) => {
  const amp = Math.min(0.45, 0.05 + 0.6 * act) // opacity swing ±amp
  const ppm = 3 + 46 * act // pulses per 60s
  const baseDur = 60 / ppm // seconds per pulse
  const spread = 0.3 + 0.5 * act // per-tile duration jitter widens with load
  const s1 = hash01(c * 12.9898 + r * 78.233)
  const s2 = hash01(c * 39.346 + r * 11.135)
  const dur = baseDur * (1 - spread / 2 + s1 * spread)
  return {
    '--lo': Math.max(0.06, base - amp).toFixed(3),
    '--hi': Math.min(1, base + amp).toFixed(3),
    '--dur': dur.toFixed(2) + 's',
    '--delay': (-s2 * dur).toFixed(2) + 's', // negative → tiles start mid-cycle, desynced
    '--gmax': (4 + 11 * act).toFixed(1) + 'px', // glow blur grows with load
  }
}

// ═══ RADIAL GAUGE (270° sweep) ═════════════════════════════════════════
export function Gauge({ pct, label, sub, size = 184 }) {
  const sw = Math.round(size * 0.065)
  const r = size / 2 - sw - 4
  const cx = size / 2
  const cy = size / 2
  const C = 2 * Math.PI * r
  const sweep = 0.75 // 270°
  const arc = C * sweep
  const p = Math.max(0, Math.min(1, (pct || 0) / 100))
  const fill = p * arc
  return (
    <div className="gauge" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle
          className="gtrack"
          cx={cx}
          cy={cy}
          r={r}
          strokeWidth={sw}
          strokeDasharray={`${arc} ${C}`}
          strokeLinecap="round"
          transform={`rotate(135 ${cx} ${cy})`}
        />
        <circle
          className="gfill"
          cx={cx}
          cy={cy}
          r={r}
          strokeWidth={sw}
          strokeDasharray={`${fill} ${C}`}
          transform={`rotate(135 ${cx} ${cy})`}
        />
      </svg>
      <div className="gc">
        <div className="pct">
          {pct == null ? '—' : Math.round(pct)}
          {pct != null && <span className="s">%</span>}
        </div>
        <div className="lbl">{label}</div>
        {sub ? <div className="sub">{sub}</div> : null}
      </div>
    </div>
  )
}

// ═══ AIE TILE GRID — the shared spatial primitive (4 rows × 8 columns) ══
// `owners` is a length-8 array: owner descriptor per column (or null = free).
// `available` false → degraded (probe couldn't read columns); grid greys.
export function AieGrid({ owners, available, act = 0, size = 30, cgap = 6, rgap = 5, showHeaders = true, showParts = true, shared = false }) {
  const cols = Array.from({ length: NPU_COLS }, (_, c) => c)
  const rows = Array.from({ length: NPU_ROWS }, (_, r) => r)
  // partition runs — collapse consecutive columns with the same owner
  const parts = []
  let i = 0
  while (i < NPU_COLS) {
    const o = owners[i]
    let span = 1
    while (i + span < NPU_COLS && owners[i + span] === o) span++
    parts.push({ start: i, span, owner: o })
    i += span
  }
  return (
    <div
      className={'aie' + (available ? '' : ' degraded') + (shared ? ' shared' : '')}
      style={{ '--aie-w': size + 'px', '--aie-cgap': cgap + 'px', '--aie-rgap': rgap + 'px' }}
      data-testid="aie-grid"
      data-shared={shared ? 'true' : 'false'}
    >
      {showHeaders && (
        <div className="aie-colhdr">
          {cols.map((c) => (
            <span className="cn" key={c}>
              {c}
            </span>
          ))}
        </div>
      )}
      <div className="aie-grid">
        {cols.map((c) => {
          const owner = owners[c]
          return (
            <div className="aie-col" key={c}>
              {rows.map((r) => {
                const serving = owner && owner.serving
                // solid base fill — the colour IS the claim (was a near-invisible
                // 0.2/0.5); the animation breathes around this base.
                const base = !owner ? 0 : serving ? 0.82 : 0.5
                const cls = 'aie-tile' + (owner ? (serving ? ' active' : ' claimed') : '')
                const style = owner
                  ? {
                      '--tile-hue': owner.hue,
                      '--tile-glow': owner.glow,
                      '--tile-line': owner.line,
                      '--base': base,
                      ...(REDUCE ? {} : tileAnim(act, base, c, r)),
                    }
                  : null
                return (
                  <div className={cls} key={r} style={style}>
                    <span className="core" style={{ background: owner ? owner.hue : undefined }} />
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
      {showParts && (
        <div className="aie-parts">
          {parts.map((p) => (
            <div
              className={'aie-part' + (p.owner ? '' : ' free')}
              key={p.start}
              style={{ gridColumn: `span ${p.span}`, '--part-hue': p.owner ? p.owner.hue : undefined }}
            >
              <span className="br" />
              <span className="pl">
                {p.owner ? p.owner.name : 'free'}
                <span className="pc">· {p.span}c</span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// mini single-row strip of the 8 columns, filtered to one owner (slot rail)
export function TileStrip({ owners, ownerName, act = 0, w = 10, h = 10 }) {
  const cols = Array.from({ length: NPU_COLS }, (_, c) => c)
  return (
    <div className="tstrip" style={{ '--ts-w': w + 'px', '--ts-h': h + 'px' }}>
      {cols.map((c) => {
        const o = owners[c]
        const mine = o && o.name === ownerName
        const serving = mine && o.serving
        const base = !mine ? 0 : serving ? 0.85 : 0.5
        const cls = 't' + (mine ? (serving ? ' active' : ' claimed') : '')
        const style = mine
          ? {
              background: o.hue,
              '--tile-glow': o.glow,
              '--base': base,
              ...(REDUCE ? {} : tileAnim(act, base, c, 0)),
            }
          : null
        return <span className={cls} key={c} style={style} />
      })}
    </div>
  )
}

