// hal0 dashboard — telemetry header (design_handoff_telemetry_header).
//
// Full-width combined live-metrics card: four auto-fit cells (Throughput
// hero + spark · GPU semicircle gauge + digits · CPU/Memory gauge + digits
// · NPU AIE occupancy) above a full-width memory "rack ruler". Mounted at
// the top of the Slots page (SlotsView), where it replaces the old
// InferenceHeroBand (iGPU GTT memory map + combined-throughput tile).
//
// Data comes ONLY from the existing hooks; the honesty rules from
// metric-cards.jsx carry over — absent metric → em-dash (never 0%), 404
// endpoint → "source pending", a measured 0.0 tok/s always renders with
// the explicit serving count, and a forced-high GPU util reading is
// captioned "pinned" (util_is_forced_high contract), never silently
// trusted. Column allocation in the NPU cell comes from
// useNpuOccupancy().slots[].cols — never hardcoded.
//
// Styles: overhaul.css (th-*). Owner hues: the :root --npu-s0/s1/s2 token
// sets from npu.css (shared with the slots-page NpuOccupancyCard).

import { useSlots } from '@/api/hooks/useSlots'
import { useHardware } from '@/api/hooks/useHardware'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { useStatsPower } from '@/api/hooks/useStatsPower'
import { useThroughputHistory } from '@/api/hooks/useThroughputHistory'
import { useNpuOccupancy } from '@/api/hooks/useNpuOccupancy'
import { useMemoryMapModel } from './memory-map'
import { slotIndicatorFromPhase } from './slot-status.js'
import { devKind } from '@/lib/deviceMeta'

const { useState: useStateT, useRef: useRefT, useEffect: useEffectT, useMemo: useMemoT } = React

const round1 = (n) => Math.round((n || 0) * 10) / 10
const fmt1 = (n) => (typeof n === 'number' ? n.toFixed(1) : '—')
const isNpuDev = (s) => s.device_class === 'npu' || devKind(s.device) === 'npu'
// Measured tok/s sum over a slot subset — only real positive readings count.
const sumToks = (list) =>
  list
    .map((s) => s?.metrics?.toks)
    .filter((t) => typeof t === 'number' && t > 0)
    .reduce((a, b) => a + b, 0)
// Deterministic per-tile hash → [0,1), stable across renders (mirrors the
// npu-pane pattern — the desync is decorative, never claimed as per-tile load).
const hash01 = (n) => {
  const x = Math.sin(n) * 43758.5453
  return x - Math.floor(x)
}

// Owner hues shared with the slots-page NPU card — tokens live on :root in
// npu.css (npu-pane HUES mirrors this table).
const TH_HUES = [
  { hue: 'var(--npu-s0)', line: 'var(--npu-s0-line)', glow: 'var(--npu-s0-glow)', dim: 'var(--npu-s0-dim)', fg: 'var(--npu-s0-fg)' },
  { hue: 'var(--npu-s1)', line: 'var(--npu-s1-line)', glow: 'var(--npu-s1-glow)', dim: 'var(--npu-s1-dim)', fg: 'var(--npu-s1-fg)' },
  { hue: 'var(--npu-s2)', line: 'var(--npu-s2-line)', glow: 'var(--npu-s2-glow)', dim: 'var(--npu-s2-dim)', fg: 'var(--npu-s2-fg)' },
]

// Semicircle gauge — SVG arc, but the center value is an HTML overlay
// (absolutely positioned flex column, bottom-aligned), NOT SVG <text>.
function SemiGauge({ pct, stroke, value, valueSmall, caption }) {
  const dash = pct == null ? 0 : Math.max(0, Math.min(100, pct))
  return (
    <div className="th-gauge">
      <svg width="118" height="68" viewBox="0 0 100 57">
        <path className="track" d="M8 52 A42 42 0 0 1 92 52" pathLength="100" />
        <path
          className="fill"
          d="M8 52 A42 42 0 0 1 92 52"
          pathLength="100"
          style={{ stroke, strokeDasharray: `${dash} 100` }}
        />
      </svg>
      <div className="th-gauge-c">
        <span className={'th-gauge-v' + (valueSmall ? ' sm' : '')}>{value}</span>
        <span className="th-gauge-cap">{caption}</span>
      </div>
    </div>
  )
}

// Eyebrow row every cell leads with: 6px device dot + uppercase label +
// lowercase sublabel. `live` adds the glow + pulse (NPU only — the pulsing
// dot carries liveness; there is no "active" pill).
function ThEyebrow({ dot, live, label, sub, right }) {
  return (
    <div className="th-eyebrow">
      {dot && <span className={'d' + (live ? ' live' : '')} style={{ background: dot, color: dot }} />}
      <span>{label}</span>
      {sub && <span className="sub">{sub}</span>}
      {right && <span className="right">{right}</span>}
    </div>
  )
}

// Cell 1 · THROUGHPUT — hero number + 20-bucket spark strip. Same gating
// as ThroughputCard2: history pending/empty → "source pending" body, no
// fabricated bars ever. The gpu/npu split in the sub-row is the measured
// per-slot tok/s from the live slot poll.
//
// Persistence: the history endpoint only reports buckets inside its 100s
// window, so on an idle box `samples` empties out and the cell would flap
// back to "source pending". Two-part fix, both honest:
//   1. "source pending" is reserved for a MISSING source (loading / 404 /
//      error — `data` null). A 200 with an empty window is a measurement —
//      "no throughput in the last 100s" — and renders as hero 0.0 with the
//      live serving count, never as a pending gate.
//   2. Real buckets are merged into a module-level cache (keyed by ts) so
//      the spark keeps the last 20 MEASURED buckets on screen after the
//      window slides past them. Every bar is a real reading — the cache
//      only stops bars from vanishing; it never invents them.
const TPS_BUCKET_CACHE = new Map() // ts → sample, module-level so it survives remounts
function mergeTpsCache(samples) {
  for (const s of samples) {
    if (s && typeof s.ts === 'number' && typeof s.total_tps === 'number') {
      TPS_BUCKET_CACHE.set(s.ts, s)
    }
  }
  // keep the cache bounded: newest 20 buckets in ts order
  const kept = [...TPS_BUCKET_CACHE.values()].sort((a, b) => a.ts - b.ts).slice(-20)
  TPS_BUCKET_CACHE.clear()
  for (const s of kept) TPS_BUCKET_CACHE.set(s.ts, s)
  return kept
}

function ThCellThroughput({ slots }) {
  const { data } = useThroughputHistory()
  // Pending ONLY when the source itself is absent (hook returns data=null on
  // loading and on 404/error) — an alive endpoint with an empty window is a
  // real "nothing served lately" reading, not a missing source.
  const sourcePending = !data
  const samples = data?.samples ?? []
  const latest = samples.length > 0 ? samples[samples.length - 1] : null
  const hasLive = latest != null && typeof latest.total_tps === 'number'
  // Empty-but-alive window → measured 0.0 (paired with the serving count
  // below, per the #221 invariant).
  const heroTps = hasLive ? latest.total_tps : sourcePending ? null : 0
  const serving = hasLive
    ? (latest.serving_slots ?? 0)
    : (slots || []).filter((s) => s.state === 'serving').length

  const bars = sourcePending ? [] : mergeTpsCache(samples)
  const maxTps = bars.length > 0 ? Math.max(...bars.map((s) => s.total_tps), 1) : 1

  const npuTps = sumToks((slots || []).filter(isNpuDev))
  const gpuTps = sumToks((slots || []).filter((s) => !isNpuDev(s)))

  return (
    <div className="th-cell th-cell-tps">
      <ThEyebrow label="Throughput" right="tok/s" />
      {sourcePending ? (
        <div className="mc-pending">
          <span className="mc-pending-label">source pending</span>
          <span className="mc-pending-sub">waiting for throughput history</span>
        </div>
      ) : (
        <>
          <div className="th-hero-row">
            <span className={'th-hero' + (heroTps != null ? '' : ' dim')}>{fmt1(heroTps)}</span>
            <div className="th-spark">
              {/* pad on the LEFT so the newest sample stays on the right edge */}
              {bars.length < 20 &&
                Array.from({ length: 20 - bars.length }).map((_, i) => (
                  <i key={`pad-${i}`} className="pad" style={{ height: '2%' }} />
                ))}
              {bars.map((s, i) => (
                <i
                  key={s.ts}
                  style={{
                    height: `${Math.max((s.total_tps / maxTps) * 100, 4)}%`,
                    opacity: i >= bars.length - 4 ? 1 : 0.35 + (i / bars.length) * 0.45,
                  }}
                  title={`${fmt1(s.total_tps)} tok/s`}
                />
              ))}
            </div>
          </div>
          {/* Sub-row ALWAYS renders with a reading (incl. the measured-empty
              0.0) so it is disambiguated by the explicit serving count (#221). */}
          <div className="th-sub">
            gpu <span className="g">{fmt1(gpuTps)}</span> · npu <span className="n">{fmt1(npuTps)}</span> ·{' '}
            <span className="s">{serving} slot{serving !== 1 ? 's' : ''} serving</span>
          </div>
        </>
      )}
    </div>
  )
}

// Cell 2 · GPU — semicircle util gauge + sclk / temp / watts digits.
// Forced-high perf pinning (gpu-compute.service) makes the util % unreliable;
// the gauge caption carries "pinned" so the reading is never silently trusted
// (util_is_forced_high, same contract the old GpuGauge tile honoured).
function ThCellGpu() {
  const hw = useHardware()
  const stats = useStatsHardware()
  const power = useStatsPower()
  const s = stats.data
  const H = hw.data

  const util = typeof s?.gpu_util === 'number' ? s.gpu_util : null
  const pinned = !!s?.util_is_forced_high
  const mhz = s?.gpu_clock_mhz ?? null
  const temp = s?.gpu_temp_c ?? null
  const watts = power.data?.gpu_power_w ?? null
  const sub = H?.gpu ? H.gpu + (H.vulkanCapable ? ' · vulkan' : '') : '—'

  return (
    <div className="th-cell">
      <ThEyebrow dot="var(--dev-vulkan)" label="GPU" sub={sub} />
      <div className="th-row">
        <SemiGauge
          pct={util != null ? util * 100 : null}
          stroke="var(--dev-vulkan)"
          value={util != null ? Math.round(util * 100) + '%' : '—'}
          caption={pinned ? 'util · pinned' : 'util'}
        />
        <div className="th-digits c3">
          <div className="th-digit">
            <div className="v">{mhz != null ? Math.round(mhz) : '—'}</div>
            <div className="cap">MHz sclk</div>
          </div>
          <div className="th-digit">
            <div className={'v' + (temp != null && temp >= 75 ? ' warn' : '')}>
              {temp != null ? Math.round(temp) + '°' : '—'}
            </div>
            <div className="cap">temp C</div>
          </div>
          <div className="th-digit">
            <div className="v sec">{watts != null ? Math.round(watts) : '—'}</div>
            <div className="cap">watts</div>
          </div>
        </div>
      </div>
    </div>
  )
}

// Cell 3 · CPU · MEMORY — sys-ram gauge + cpu util (micro-bar) / temp digits.
function ThCellCpuMem() {
  const hw = useHardware()
  const stats = useStatsHardware()
  const power = useStatsPower()
  const s = stats.data

  const usedGb = s?.ram_used_mb != null ? s.ram_used_mb / 1024 : null
  const totalGb = s?.ram_total_mb != null ? s.ram_total_mb / 1024 : hw.data?.ram?.total || null
  const ramPct = usedGb != null && totalGb ? (usedGb / totalGb) * 100 : null
  const cpuUtil = typeof s?.cpu_util === 'number' ? s.cpu_util : null
  const cpuTemp = power.data?.cpu_temp_c ?? null

  return (
    <div className="th-cell">
      <ThEyebrow dot="var(--dev-cpu)" label="CPU · Memory" sub={hw.data?.cpu || '—'} />
      <div className="th-row">
        <SemiGauge
          pct={ramPct}
          stroke="var(--dev-cpu)"
          valueSmall
          value={usedGb != null ? fmt1(round1(usedGb)) + ' GB' : '—'}
          caption={totalGb ? `sys ram / ${Math.round(totalGb)} GB` : 'sys ram'}
        />
        <div className="th-digits c2">
          <div className="th-digit">
            <div className="v">{cpuUtil != null ? Math.round(cpuUtil * 100) + '%' : '—'}</div>
            <div className="th-microbar">
              <i style={{ width: `${cpuUtil != null ? Math.round(cpuUtil * 100) : 0}%` }} />
            </div>
            <div className="cap">util</div>
          </div>
          <div className="th-digit">
            <div className="v sec">{cpuTemp != null ? Math.round(cpuTemp) + '°' : '—'}</div>
            <div className="cap push">temp C</div>
          </div>
        </div>
      </div>
    </div>
  )
}

// Cell 4 · NPU — compact 4×8 AIE occupancy grid + partition bars + owner
// tags. Column allocation comes from useNpuOccupancy().slots[].cols — the
// grid, bars, tags and caption all derive from that one per-column owner
// array (mirrors the owners[] pattern in npu-pane.jsx). Never hardcoded.
function ThCellNpu({ slots }) {
  const occQ = useNpuOccupancy()
  const stats = useStatsHardware()
  const hw = useHardware()
  const occ = occQ.data || {}
  const rows = occ.rows || 4
  const cols = occ.cols || 8
  const occSlots = occ.slots || []
  const tiles = occ.tiles || rows * cols

  const owners = Array(cols).fill(null)
  occSlots.forEach((sl, idx) => {
    const hue = TH_HUES[idx % TH_HUES.length]
    const o = { name: sl.name, idx, ...hue }
    ;(sl.cols || []).forEach((c) => {
      if (c >= 0 && c < cols) owners[c] = o
    })
  })
  const liveCount = occSlots.filter((sl) => (sl.cols || []).length > 0).length
  const claimedTiles = owners.filter(Boolean).length * rows

  // partition runs — consecutive columns with the same owner (by identity)
  const parts = []
  for (let i = 0; i < cols; ) {
    const o = owners[i]
    let span = 1
    while (i + span < cols && owners[i + span] === o) span++
    parts.push({ start: i, span, owner: o })
    i += span
  }
  // bar spans exactly its claimed columns: cols*12 + (cols-1)*3
  const partW = (span) => span * 12 + (span - 1) * 3

  const npuUtil = typeof stats.data?.npu_util === 'number' ? stats.data.npu_util : null
  const npuTps = sumToks((slots || []).filter(isNpuDev))
  const npuName = hw.data?.npu?.present ? hw.data.npu.name || 'XDNA' : null
  const sub = npuName ? `${npuName}${occSlots.length > 0 ? ' · flm' : ''}` : '—'

  const tileEls = []
  for (let i = 0; i < rows * cols; i++) {
    const o = owners[Math.floor(i / rows)] // column-major fill
    if (o) {
      const dur = 2.2 + hash01(i * 12.9898) * 2.6
      tileEls.push(
        <i
          key={i}
          className="on"
          style={{
            background: o.hue,
            boxShadow: `inset 0 0 0 1px ${o.line}, 0 0 6px -1px ${o.glow}`,
            '--dur': dur.toFixed(2) + 's',
            '--delay': (-hash01(i * 39.346) * dur).toFixed(2) + 's',
          }}
        />
      )
    } else {
      tileEls.push(<i key={i} />)
    }
  }

  return (
    <div className="th-cell">
      <ThEyebrow dot="var(--dev-npu)" live={liveCount > 0} label="NPU" sub={sub} />
      <div className="th-npu">
        <div className="th-aie">
          <div
            className="th-aie-grid"
            style={{ gridTemplateColumns: `repeat(${cols}, 12px)`, gridTemplateRows: `repeat(${rows}, 12px)` }}
          >
            {tileEls}
          </div>
          <div className="th-aie-parts">
            {parts.map((p) => (
              <div key={p.start} className="th-aie-part" style={{ width: partW(p.span) + 'px' }}>
                <span
                  className="br"
                  style={{
                    background: p.owner ? p.owner.hue : 'var(--bg-4)',
                    boxShadow: p.owner ? `0 0 6px -1px ${p.owner.glow}` : 'none',
                  }}
                />
                <span
                  className="pl"
                  style={{ color: p.owner ? (p.owner.idx === 0 ? 'var(--fg-3)' : p.owner.fg) : 'var(--fg-5)' }}
                >
                  {p.owner ? p.owner.name : 'free'} <span className="pc">· {p.span}c</span>
                </span>
              </div>
            ))}
          </div>
        </div>
        <div className="th-npu-right">
          <div className="th-npu-nums">
            <div className="th-npu-num">
              <span className="v npu">{npuUtil != null ? Math.round(npuUtil * 100) + '%' : '—'}</span>
              <span className="u">util</span>
            </div>
            <div className="th-npu-num">
              <span className="v">{npuTps > 0 ? fmt1(npuTps) : '—'}</span>
              <span className="u">tok/s</span>
            </div>
          </div>
          <div className="th-tags">
            {TH_HUES.map((hue, idx) => {
              const o = occSlots[idx]
              return o ? (
                <span
                  key={idx}
                  className="th-tag"
                  style={{ border: `1px solid ${hue.line}`, background: hue.dim, color: hue.fg }}
                >
                  <span className="sw" style={{ background: hue.hue, boxShadow: `inset 0 0 0 1px ${hue.line}` }} />
                  {o.name}
                </span>
              ) : (
                <span key={idx} className="th-tag off" />
              )
            })}
          </div>
          <span className="th-npu-cap">
            {claimedTiles}/{tiles} tiles claimed · {rows}×{cols} AIE-ML · {liveCount} slot
            {liveCount !== 1 ? 's' : ''} live
          </span>
        </div>
      </div>
    </div>
  )
}

// Element width via ResizeObserver — drives "label only when the segment is
// wide enough (~90px)" in the memory bar without clipping half a label
// (same helper as the #1061 dashboard-redesign memory hero).
function useMeasuredWidth() {
  const ref = useRefT(null)
  const [width, setWidth] = useStateT(0)
  useEffectT(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setWidth(e.contentRect.width)
    })
    ro.observe(el)
    setWidth(el.offsetWidth)
    return () => ro.disconnect()
  }, [])
  return [ref, width]
}

const SEG_LABEL_MIN_PX = 90

// Memory rack ruler — full-width second row; replaces and absorbs the old
// memory-map band. All attribution comes from useMemoryMapModel() verbatim
// (pool cap, per-slot bytesGb + Okabe–Ito colors, headroom + limitedBy).
// Bar styling follows the #1061 memory hero: allocations live INSIDE the
// bar as dim-tint segments with a solid top accent stripe, in-segment
// labels only when ≥90 measured px (serving segments add the pulsing dot +
// live tok/s), a 45° striped "system · KV + runtime" block for measured
// GTT use beyond the named model weights, and click → slot. Widths stay
// true to the GB scale (explicit % on border-box segments); the GB tick
// row keeps the ruler's scale readable.
function ThRuler({ slots }) {
  const model = useMemoryMapModel()
  const { pool, self, headroom } = model
  const total = pool.totalGb || 0
  const used = self.modelUsedGb
  const free = Math.max(0, round1(total - used))
  const pct = (gb) => (total > 0 ? (gb / total) * 100 : 0)
  const ticks = total > 0 ? [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(total * f)) : null

  const [barRef, barWidth] = useMeasuredWidth()
  const slotByName = useMemoT(() => {
    const m = {}
    for (const s of slots || []) m[s.name] = s
    return m
  }, [slots])

  const segs = self.slots
    .filter((s) => s.bytesGb > 0)
    .map((s) => {
      const live = slotByName[s.name]
      const ind = live ? slotIndicatorFromPhase(live) : null
      const serving = ind?.cls === 'serving'
      const toks = serving && typeof live?.metrics?.toks === 'number' && live.metrics.toks > 0
        ? live.metrics.toks
        : null
      return { ...s, serving, toks, indLabel: ind ? ind.label : '—' }
    })
  // System block — measured GTT in use beyond the named model weights
  // (KV + runtime + buffers). Honest: only renders when gtt_used reports
  // more than the models hold; never a fabricated split.
  const systemGb = Math.max(0, round1((self.gttUsedGb || 0) - used))
  const barFree = Math.max(0, round1(total - used - systemGb))

  const goSlot = (name) => {
    window.location.hash = '#slots/' + name
  }

  return (
    <div className="th-ruler">
      <div className="th-ruler-h">
        <span>memory · {pool.label}</span>
        <span>
          model <b>{fmt1(used)} GB</b> · free <b>{fmt1(free)} GB</b> · headroom{' '}
          <b className="ok">{fmt1(headroom.availableGb)} GB</b>{' '}
          <span className="lim">— limited by {headroom.limitedBy}</span>
        </span>
      </div>
      <div className="th-ruler-bar" ref={barRef}>
        {segs.map((s) => {
          const w = pct(s.bytesGb)
          const showLabel = (w / 100) * barWidth >= SEG_LABEL_MIN_PX
          return (
            <div
              key={s.name}
              className="th-ruler-seg"
              style={{
                width: w + '%',
                background: `color-mix(in srgb, ${s.color} 18%, transparent)`,
                boxShadow: `inset 0 2px 0 ${s.color}`,
              }}
              title={`${s.name} · ${fmt1(s.bytesGb)} GB · ${s.indLabel}`}
              role="link"
              tabIndex={0}
              onClick={() => goSlot(s.name)}
              onKeyDown={(e) => { if (e.key === 'Enter') goSlot(s.name) }}
            >
              {showLabel && (
                <>
                  <span className="nm" style={{ color: s.color }}>
                    {s.name} · {s.bytesGb.toFixed(1)}G
                  </span>
                  {s.toks != null && (
                    <span className="sub">
                      <span className="dot serving th-ruler-dot" />
                      {Math.round(s.toks)} tok/s
                    </span>
                  )}
                </>
              )}
            </div>
          )
        })}
        {systemGb > 0 && (
          <div
            className="th-ruler-seg th-ruler-seg-system"
            style={{ width: pct(systemGb) + '%' }}
            title={`system · KV + runtime · ${fmt1(systemGb)} GB`}
          >
            {(pct(systemGb) / 100) * barWidth >= SEG_LABEL_MIN_PX && (
              <span className="nm">system · {systemGb.toFixed(1)}G</span>
            )}
          </div>
        )}
        <div className="th-ruler-free">free · {fmt1(barFree)} GB</div>
      </div>
      {ticks && (
        <div className="th-ruler-ticks">
          {ticks.map((t, i) => (
            <span key={i}>
              {t}
              {i === ticks.length - 1 ? ' GB' : ''}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export function TelemetryHeader({ slots: slotsProp }) {
  // Self-contained by default (react-query dedupes the poll); a caller that
  // already holds the slot list can pass it to skip the extra subscription.
  const slotsQuery = useSlots()
  const slots = slotsProp || slotsQuery.data || []
  return (
    <div className="th-card" data-testid="telemetry-header">
      <div className="th-strip">
        <ThCellThroughput slots={slots} />
        <ThCellGpu />
        <ThCellCpuMem />
        <ThCellNpu slots={slots} />
      </div>
      <ThRuler slots={slots} />
    </div>
  )
}

// Window export keeps parity with the other dash modules' debug exports.
Object.assign(window, { TelemetryHeader })
