// hal0 dashboard — NPU occupancy card.
//
// Replaces the old NpuFlmStack accordion + chat/asr/embed trio picker. One
// full-width card, three regions (per the design handoff "npu_combined"):
//   · LEFT   — duty gauge + two honest readouts (columns claimed, tok/s)
//   · CENTER — live 4×8 AIE-ML occupancy map. The NPU is single-tenant: one
//              FLM process claims the whole 8-column array, so the grid is
//              all-lit-or-all-dark — that IS the visual of the limit.
//   · RIGHT  — one compact card per resident FLM slot: dot · name · model ·
//              inline tok/s · ttft · RAM · column strip · slot controls.
//
// Honesty rules (no fabricated metrics): the NPU exposes no TOPS / power /
// per-tile-utilisation sensor on this kernel. The gauge is driven by the real
// coarse DUTY-cycle (npu_util), throughput by real tok/s, RAM by npu_status,
// columns by the xrt-smi probe (/api/npu/occupancy). The shimmer is cosmetic
// and honours prefers-reduced-motion. Per-tile load is NOT claimed as real.

import { useNpuOccupancy } from '@/api/hooks/useNpuOccupancy'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { useSlotRestart, useSlotUnload, useSlotLoad } from '@/api/hooks/useSlots'
import { SlotControls, slotCtrlPhase } from './inference-pane.jsx'
import { slotIndicatorFromPhase } from './slot-status.js'

// ─── icons (16×16, hal0 thin-line family — ported from the design) ─────────
const NI = ({ d, size = 16, sw = 1.5, children, fill = 'none' }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 16 16"
    fill={fill}
    stroke="currentColor"
    strokeWidth={sw}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    {d ? <path d={d} /> : children}
  </svg>
)
const NIcons = {
  // a tiled die — stands for the AIE tile array
  npu: (
    <NI>
      <rect x="3" y="3" width="10" height="10" rx="1" />
      <path d="M6.3 3v10M9.7 3v10M3 6.3h10M3 9.7h10" sw="1" />
    </NI>
  ),
}
const Ni = ({ name, size = 16 }) =>
  NIcons[name] ? React.cloneElement(NIcons[name], { size }) : null

const toast = (msg, kind = 'info') =>
  typeof window !== 'undefined' && window.__hal0Toast && window.__hal0Toast(msg, kind)

const round1 = (n) => Math.round((n || 0) * 10) / 10
const devKind = (device) => {
  const d = String(device || '').toLowerCase()
  if (d === 'npu') return 'npu'
  if (d.includes('vulkan')) return 'vulkan'
  if (d.includes('rocm') || d.startsWith('gpu')) return 'rocm'
  return d || 'cpu'
}
const isNpuSlot = (s) => s.device_class === 'npu' || devKind(s.device) === 'npu'

// owner hues — assigned by slot index so each resident FLM reads as its own
// owner. Three cohesive accents (deep-indigo → teal → sage); single-tenant NPU
// usually shows just the first. The hue fills the slot's claimed columns in the
// grid AND its mini column strip; glow/line drive the active-tile halo.
const HUES = [
  { hue: 'var(--npu-s0)', glow: 'var(--npu-s0-glow)', line: 'var(--npu-s0-line)', dim: 'var(--npu-s0-dim)' },
  { hue: 'var(--npu-s1)', glow: 'var(--npu-s1-glow)', line: 'var(--npu-s1-line)', dim: 'var(--npu-s1-dim)' },
  { hue: 'var(--npu-s2)', glow: 'var(--npu-s2-glow)', line: 'var(--npu-s2-line)', dim: 'var(--npu-s2-dim)' },
]

// honour reduced-motion → frozen snapshot (no breathing, no glow pulse)
const REDUCE =
  typeof window !== 'undefined' &&
  window.matchMedia &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

const NPU_ROWS = 4
const NPU_COLS = 8

// ─── "aliveness" model ─────────────────────────────────────────────────────
// The claimed columns breathe harder the more the NPU is actually working.
// Blend (per design intent): the coarse duty-cycle sets the baseline energy,
// live tok/s adds transient spikes on top. The aggregate signal is REAL; the
// per-tile variation (phase, jitter) is decorative — there is no per-tile
// counter on this kernel, so we never claim one.
const TPS_CEIL = 60 // tok/s that maps to a full throughput spike
const activityLevel = (dutyPct, tpsSum) => {
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
const tileAnim = (act, base, c, r) => {
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
function Gauge({ pct, label, sub, size = 184 }) {
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
function AieGrid({ owners, available, act = 0, size = 30, cgap = 6, rgap = 5, showHeaders = true, showParts = true }) {
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
      className={'aie' + (available ? '' : ' degraded')}
      style={{ '--aie-w': size + 'px', '--aie-cgap': cgap + 'px', '--aie-rgap': rgap + 'px' }}
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
function TileStrip({ owners, ownerName, act = 0, w = 10, h = 10 }) {
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

// ═══ per-slot card (right rail) ════════════════════════════════════════
function ComboSlot({ slot, occ, owners, hue, handlers, act = 0 }) {
  const ind = slotIndicatorFromPhase(slot)
  const serving = ind.cls === 'serving'
  const m = slot.metrics || {}
  const tps = typeof m.toks === 'number' && m.toks > 0 ? Math.round(m.toks) : null
  const ttft = typeof m.ttft === 'number' && m.ttft > 0 ? Math.round(m.ttft) : null
  const gb = occ && typeof occ.gb === 'number' ? round1(occ.gb) : typeof m.mem === 'number' ? round1(m.mem) : null
  const model = String(slot.model_id || slot.model || occ?.model || '').replace(/-FLM$/, '')
  return (
    <div className={'cslot' + (serving ? '' : ' dim')} style={{ '--slot-hue': hue.hue }}>
      <div className="cslot-top">
        <span className={'ldot ' + (serving ? 'serving' : ind.cls === 'error' ? 'offline' : 'ready')} />
        <span className="nm">{slot.name}</span>
        <span className="md">{model || '—'}</span>
        <span className="grow" />
        <span className="cslot-mx">
          <span className="tps">
            {tps ?? '—'}
            <span className="u">t/s</span>
          </span>
          <span className="sep">·</span>
          <span className={'m' + (ttft ? '' : ' muted')}>{ttft ? ttft + 'ms' : '—'}</span>
          <span className="sep">·</span>
          <span className={'m' + (gb != null ? '' : ' muted')}>
            {gb != null ? gb : '—'}
            <span className="u">GB</span>
          </span>
        </span>
      </div>
      <div className="cslot-row">
        <span className="cslot-strip">
          <TileStrip owners={owners} ownerName={slot.name} act={act} />
        </span>
      </div>
      <div className="cslot-foot">
        <span className="grow" />
        <SlotControls
          phase={slotCtrlPhase(slot)}
          onStart={() => handlers.onStart(slot)}
          onStop={() => handlers.onStop(slot)}
          onRestart={() => handlers.onRestart(slot)}
          onLogs={() => handlers.onLogs(slot)}
          onEdit={() => handlers.onEdit(slot)}
        />
      </div>
    </div>
  )
}

// ═══ the card ══════════════════════════════════════════════════════════
export function NpuOccupancyCard({ slots }) {
  const occQuery = useNpuOccupancy()
  const hw = useStatsHardware()
  const restartMut = useSlotRestart()
  const unloadMut = useSlotUnload()
  const loadMut = useSlotLoad()

  const npuSlots = (slots || []).filter(isNpuSlot)
  if (npuSlots.length === 0) return null

  const occ = occQuery.data || {}
  const occSlots = occ.slots || []
  const occByName = Object.fromEntries(occSlots.map((s) => [s.name, s]))
  const colsAvailable = occ.columns_available !== false
  const colsTotal = occ.cols_total || NPU_COLS
  const colsUsed = occ.cols_used || 0
  const tiles = occ.tiles || NPU_ROWS * NPU_COLS

  // honest live signals
  const dutyPct = typeof hw.data?.npu_util === 'number' ? hw.data.npu_util * 100 : null
  const ramMb = hw.data?.npu_status?.model_mb
  const ramGb = typeof ramMb === 'number' ? round1(ramMb / 1024) : null
  const tpsSum = npuSlots
    .map((s) => s.metrics?.toks)
    .filter((t) => typeof t === 'number' && t > 0)
    .reduce((a, b) => a + b, 0)
  // blended activity (duty floor + tok/s spike) → drives how hard the tiles breathe
  const act = activityLevel(dutyPct, tpsSum)

  // owner descriptor per column (length 8). Built from the occupancy probe so
  // the grid + every slot strip share one source of truth.
  // One owner object per slot, shared by reference across every column it
  // owns — the grid's partition-merge keys off identity, so distinct objects
  // would fragment a single slot into one bracket per column.
  const owners = Array(NPU_COLS).fill(null)
  npuSlots.forEach((s, idx) => {
    const o = occByName[s.name]
    const cols = o?.cols || []
    const ind = slotIndicatorFromPhase(s)
    const hue = HUES[idx % HUES.length]
    const ownerObj = { name: s.name, serving: ind.cls === 'serving', ...hue }
    cols.forEach((c) => {
      if (c >= 0 && c < NPU_COLS) owners[c] = ownerObj
    })
  })

  // fire-and-forget lifecycle (mirrors InferencePane/SlotsView)
  const run = (name, mut, args, okMsg) => {
    mut.mutate(args, {
      onError: (err) => toast(err?.message ? `${name}: ${err.message}` : `${name}: action failed`, 'warn'),
    })
    toast(okMsg, 'info')
  }
  const handlers = {
    onStart: (s) => run(s.name, loadMut, s.name, `Starting ${s.name}…`),
    onStop: (s) => run(s.name, unloadMut, s.name, `Stopping ${s.name}…`),
    onRestart: (s) => run(s.name, restartMut, s.name, `Restarting ${s.name}…`),
    onEdit: (s) => {
      window.location.hash = '#slots/' + s.name
    },
    onLogs: (s) => {
      window.dispatchEvent(new CustomEvent('hal0:slot-logs', { detail: { name: s.name } }))
    },
  }

  const dutySub = colsAvailable
    ? `${colsUsed}/${colsTotal} columns claimed`
    : 'column probe unavailable'

  return (
    <div className="npu-card">
      <div className="wcard">
        <div className="wcard-h">
          <span className="glyph">
            <Ni name="npu" size={15} />
          </span>
          <span className="ttl">NPU occupancy</span>
          <span className="npu-pill">
            <span className="d" />
            XDNA 2 · npu
          </span>
          <span className="grow" />
          <span className="meta">
            <b>{colsUsed * NPU_ROWS}</b>/{tiles} tiles claimed · <b>{NPU_ROWS}×{NPU_COLS}</b> AIE-ML
            {occ.single_tenant !== false && <span className="st"> · single-tenant</span>}
          </span>
        </div>
        <div className="wcard-b">
          <div className="combo">
            <div className="combo-gauge">
              <Gauge pct={dutyPct} label="npu duty" sub={dutySub} />
              <div className="combo-metrics">
                <div className="aie-stat">
                  <div className="sv">
                    {colsUsed}
                    <span className="u">/{colsTotal}</span>
                  </div>
                  <div className="sl">columns</div>
                </div>
                <div className="aie-stat">
                  <div className="sv acc">
                    {tpsSum > 0 ? Math.round(tpsSum) : '—'}
                    <span className="u">tok/s</span>
                  </div>
                  <div className="sl">throughput</div>
                </div>
              </div>
            </div>
            <div className="combo-grid">
              <AieGrid owners={owners} available={colsAvailable} act={act} size={30} cgap={6} rgap={5} />
              <div className="aie-foot">allocated columns — not per-tile utilisation</div>
            </div>
            <div className="combo-slots">
              {npuSlots.map((s, idx) => (
                <ComboSlot
                  key={s.name}
                  slot={s}
                  occ={occByName[s.name]}
                  owners={owners}
                  hue={HUES[idx % HUES.length]}
                  handlers={handlers}
                  act={act}
                />
              ))}
              {colsUsed < colsTotal && (
                <div className="cslot free">
                  <div className="cslot-top">
                    <span className="sw free" />
                    <span className="nm">free</span>
                    <span className="grow" />
                    <span className="md">{colsTotal - colsUsed} columns · idle</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

Object.assign(window, { NpuOccupancyCard })
