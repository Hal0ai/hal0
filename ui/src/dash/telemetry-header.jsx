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
      {sub && <FieldInfoIcon description={sub} />}
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
            <div className="v">{watts != null ? Math.round(watts) : '—'}</div>
            <div className="cap">watts</div>
          </div>
        </div>
      </div>
    </div>
  )
}

// Cell 3 · CPU · RAM — this box's RAM gauge + cpu util / temp digits.
function ThCellCpuMem() {
  const hw = useHardware()
  const stats = useStatsHardware()
  const power = useStatsPower()
  const s = stats.data
  // RAM is this box's own: used over its limit, both from the backend memory
  // budget (ADR-0039) through the shared memory-map model. The limit is the
  // container's when one caps it, else the machine's total. It is NOT the GPU
  // pool, which can be larger than the RAM the box may use. `mm.ram` is null
  // when the backend could not read RAM: the cell shows no figure then — never
  // "0 / 0", never a division by the limit.
  const mm = useMemoryMapModel()
  const ram = mm.ram

  const usedGb = ram ? ram.usedGb : null
  const ramPct = ram ? (ram.usedGb / ram.limitGb) * 100 : null
  const limitGb = ram ? Math.round(ram.limitGb) : null
  const cpuUtil = typeof s?.cpu_util === 'number' ? s.cpu_util : null
  const cpuTemp = power.data?.cpu_temp_c ?? null

  return (
    <div className="th-cell">
      <ThEyebrow dot="var(--dev-cpu)" label="CPU · RAM" sub={hw.data?.cpu || '—'} />
      <div className="th-row">
        <SemiGauge
          pct={ramPct}
          stroke="var(--dev-cpu)"
          valueSmall
          value={usedGb != null ? fmt1(round1(usedGb)) : '—'}
          caption={
            limitGb == null ? 'RAM' : ram.source === 'container' ? `/ ${limitGb} GB limit` : `/ ${limitGb} GB`
          }
        />
        <div className="th-digits c2">
          <div className="th-digit">
            <div className="v">{cpuUtil != null ? Math.round(cpuUtil * 100) + '%' : '—'}</div>
            <div className="cap">util</div>
          </div>
          <div className="th-digit">
            <div className={'v' + (cpuTemp != null && cpuTemp >= 75 ? ' warn' : '')}>
              {cpuTemp != null ? Math.round(cpuTemp) + '°' : '—'}
            </div>
            <div className="cap">temp C</div>
          </div>
        </div>
      </div>
    </div>
  )
}

// Cell 4 · NPU — semicircle util gauge (purple, mirrors GPU/CPU) + slot
// tags and occupancy caption. The AIE tile grid is replaced by the gauge;
// partition bars are dropped — tags alone identify active slots.
function ThCellNpu({ slots }) {
  const occQ = useNpuOccupancy()
  const stats = useStatsHardware()
  const hw = useHardware()
  const occ = occQ.data || {}
  const occSlots = occ.slots || []
  const liveCount = occSlots.filter((sl) => (sl.cols || []).length > 0).length

  const npuUtil = typeof stats.data?.npu_util === 'number' ? stats.data.npu_util : null
  const npuTps = sumToks((slots || []).filter(isNpuDev))
  const npuName = hw.data?.npu?.present ? hw.data.npu.name || 'XDNA' : null
  const sub = npuName ? `${npuName}${occSlots.length > 0 ? ' · flm' : ''}` : '—'

  return (
    <div className="th-cell">
      <ThEyebrow dot="var(--dev-npu)" live={liveCount > 0} label="NPU" sub={sub} />
      <div className="th-row">
        <SemiGauge
          pct={npuUtil != null ? npuUtil * 100 : null}
          stroke="var(--dev-npu)"
          value={npuUtil != null ? Math.round(npuUtil * 100) + '%' : '—'}
          caption="util"
        />
        <div className="th-digits c2">
          <div className="th-digit">
            <div className="v">{npuTps > 0 ? fmt1(npuTps) : '—'}</div>
            <div className="cap">tok/s</div>
          </div>
          <div className="th-digit">
            <div className="th-tags" style={{ marginTop: 2 }}>
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
          </div>
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
// (the pool and its label, per-slot bytesGb + Okabe–Ito colors, other guests,
// headroom + limitedBy), i.e. from the backend memory budget (ADR-0039).
// Bar styling follows the #1061 memory hero: allocations live INSIDE the
// bar as dim-tint segments with a solid top accent stripe, in-segment
// labels only when ≥90 measured px (serving segments add the pulsing dot +
// live tok/s), a 45° striped "other guests" block for pool usage that is
// neither this box's slots nor the image engine, and click → slot. Widths
// stay true to the GB scale (explicit % on border-box segments); the GB tick
// row keeps the ruler's scale readable.
function ThRuler({ slots }) {
  const model = useMemoryMapModel()
  const { pool, self, headroom, gpu, ram } = model
  const total = pool.totalGb || 0
  const used = self.modelUsedGb
  // Pool usage held by neither this box's slots nor the image engine. Absent
  // (0) with no GPU pool.
  const otherGuestsGb = gpu ? gpu.otherGuestsGb : 0
  // "free": the budget's own figure — the pool less the kernel's live usage,
  // clamped at 0 by the backend. EVERY free label on this ruler (the header and
  // the free block) reads it, so one screen shows one free number. Do NOT
  // re-derive it from the drawn segments: the per-slot attribution can overshoot
  // the kernel counter, and that excess is reported, not drawn — so the label
  // can be larger than the free block's drawn width. With no GPU pool it is
  // this box's free RAM; with neither readable it is unknown.
  const free = gpu ? gpu.freeGb : ram ? ram.freeGb : null
  // The free block's WIDTH is whatever the drawn segments leave (it is flex:1,
  // so never negative). The attribution can overshoot the pool outright; scale
  // the drawn segments down so they never run past 100% of the bar.
  const drawnGb = used + otherGuestsGb
  const fit = total > 0 && drawnGb > total ? total / drawnGb : 1
  const pct = (gb) => (total > 0 ? (gb / total) * 100 * fit : 0)
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
      // Per-slot figures are the backend's attribution, an estimate; the image
      // engine's is the engine's own reading, under its own name.
      const label = s.isImageEngine ? 'image engine' : s.name
      const tip = s.isImageEngine
        ? `${label} · ${fmt1(s.bytesGb)} GB · ${ind ? ind.label : '—'}`
        : `${label} · ${fmt1(s.bytesGb)} GB (estimated) · ${ind ? ind.label : '—'}`
      return { ...s, serving, toks, label, tip }
    })

  // The image engine's segment has no slot page to go to when the engine holds
  // memory but the slot list has no img slot (the model names it 'image engine').
  const goSlot = (s) => {
    if (s.isImageEngine && !slotByName[s.name]) return
    window.location.hash = '#slots/' + s.name
  }

  return (
    <div className="th-ruler">
      <div className="th-ruler-h">
        <span>memory · {pool.label}</span>
        <span>
          model <b>{fmt1(used)} GB</b> · free <b>{free != null ? `${fmt1(free)} GB` : '—'}</b> · headroom{' '}
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
              title={s.tip}
              role="link"
              tabIndex={0}
              onClick={() => goSlot(s)}
              onKeyDown={(e) => { if (e.key === 'Enter') goSlot(s) }}
            >
              {showLabel && (
                <>
                  <span className="nm" style={{ color: s.color }}>
                    {s.label} · {s.bytesGb.toFixed(1)}G
                  </span>
                  {s.toks != null && (
                    <FieldInfoIcon description="<span className=&quot;dot serving th-ruler-dot&quot; />
                      {Math.round(s.toks)} tok/s" />
                  )}
                </>
              )}
            </div>
          )
        })}
        {otherGuestsGb > 0 && (
          <div
            className="th-ruler-seg th-ruler-seg-system"
            style={{ width: pct(otherGuestsGb) + '%' }}
            title={`other guests · ${fmt1(otherGuestsGb)} GB`}
          >
            {(pct(otherGuestsGb) / 100) * barWidth >= SEG_LABEL_MIN_PX && (
              <span className="nm">other guests · {otherGuestsGb.toFixed(1)}G</span>
            )}
          </div>
        )}
        <div className="th-ruler-free">free · {free != null ? `${fmt1(free)} GB` : '—'}</div>
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
        {/* The ruler is a strip item (grid-column set in overhaul.css) so the
            3-column band can pull it up into the blank space beside the
            wrapped NPU cell; at 4/2/1 columns it spans the full width. */}
        <ThRuler slots={slots} />
      </div>
    </div>
  )
}

// Window export keeps parity with the other dash modules' debug exports.
Object.assign(window, { TelemetryHeader })
