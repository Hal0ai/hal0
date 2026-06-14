// hal0 dashboard overhaul — W4: metric cards
//
// Window-global module. Exports ThroughputCard2 and UtilizationCard onto
// window — these names are wired by the grid worker (W3) via window globals.
//
// ThroughputCard2 — §2a history endpoint, hero number + spark-bar strip.
//   Gate: if useThroughputHistory pending/empty → "source pending" body.
//   NO fabricated bars ever.
//
// UtilizationCard — §2b hardware stats: iGPU (gpu_util) + CPU (cpu_util,
//   optional) + NPU (npu_status.ok active/idle pill; npu_util optional).
//   Absent metrics render as em-dash, never 0%.
//
// Styles: appended to overhaul.css under /* ─── metric cards (W4) ─── */
// Import order: after cards-shell.jsx (DCard/StatusDot on window).

import { useThroughputHistory } from '@/api/hooks/useThroughputHistory'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'


// ─── helpers ─────────────────────────────────────────────────────────────────

const fmt1 = (n) => (typeof n === 'number' ? n.toFixed(1) : '—')
const pct  = (v) => (typeof v === 'number' ? `${Math.round(v * 100)}%` : null)

// ─── ThroughputCard2 ─────────────────────────────────────────────────────────
//
// Props: none (self-contained, pulls useThroughputHistory).
// Exported name: ThroughputCard2 (W3 wires window.ThroughputCard2).

function ThroughputCard2() {
  const { data, isPending } = useThroughputHistory()

  // Samples oldest → newest (already ordered by backend).
  const samples = data?.samples ?? []
  const latest  = samples.length > 0 ? samples[samples.length - 1] : null
  // heroTps null == "no signal" (empty history) → renders "—". A MEASURED
  // value (incl. 0) is a real reading → renders the number. To keep a
  // measured 0.0 from reading as fake activity, the "<n> slots serving"
  // subrow is GUARANTEED to render whenever heroTps is a real reading (#221
  // null-honoring invariant). When latest exists, serving_slots is a real
  // count (default 0), so the two always agree.
  const hasReading = latest != null && typeof latest.total_tps === 'number'
  const heroTps = hasReading ? latest.total_tps : null
  const serving = hasReading ? (latest.serving_slots ?? 0) : null

  // Spark-bar: use up to last 20 samples.
  const bars = samples.slice(-20)
  const maxTps = bars.length > 0
    ? Math.max(...bars.map(s => s.total_tps), 1)
    : 1

  // Most-recent 4 bars get .hot (amber-yellow --yellow).
  const hotStart = Math.max(0, bars.length - 4)

  if (isPending) {
    return (
      <DCard title="THROUGHPUT" note="tok/s">
        <div className="mc-pending">
          <span className="mc-pending-label">source pending</span>
          <span className="mc-pending-sub">waiting for throughput history</span>
        </div>
      </DCard>
    )
  }

  return (
    <DCard title="THROUGHPUT" note="tok/s">
      {/* Hero number */}
      <div className="mc-hero-row">
        <span className="mc-hero-num mono">
          {heroTps != null ? fmt1(heroTps) : '—'}
        </span>
        <span className="mc-hero-unit">tok/s</span>
      </div>

      {/* Spark-bar strip */}
      <div className="mc-spark">
        {bars.map((s, i) => {
          const h = maxTps > 0 ? (s.total_tps / maxTps) * 100 : 0
          const isHot = i >= hotStart
          return (
            <i
              key={s.ts}
              className={'mc-spark-bar' + (isHot ? ' hot' : '')}
              style={{ height: `${Math.max(h, 2)}%` }}
              title={`${fmt1(s.total_tps)} tok/s`}
            />
          )
        })}
        {/* Pad left with empty bars if fewer than 20 samples */}
        {bars.length < 20 && Array.from({ length: 20 - bars.length }).map((_, i) => (
          <i key={`pad-${i}`} className="mc-spark-bar pad" style={{ height: '2%' }} />
        ))}
      </div>

      {/* Sub-row — ALWAYS rendered alongside a real heroTps reading so a
          measured 0.0 is disambiguated by the explicit serving count and
          never reads as fake activity. */}
      {hasReading && (
        <div className="mc-sub-row mono">
          <span className="mc-sub-serving">
            {serving} slot{serving !== 1 ? 's' : ''} serving
          </span>
        </div>
      )}
    </DCard>
  )
}

// ─── UtilizationCard ─────────────────────────────────────────────────────────
//
// Three rows: iGPU / CPU / NPU.
// Data from useStatsHardware (gpu_util, cpu_util [optional], npu_status).
// Absent metrics = em-dash + "pending driver" caption — never fake 0%.

function UtilCard_Row({ label, sublabel, pctValue, color, note }) {
  // pctValue: float 0-1, or null if absent.
  const pctDisplay = pct(pctValue)

  return (
    <div className="uc-row">
      <div className="uc-row-top">
        {/* Colored dot */}
        <span className="uc-dot" style={{ background: color }} />
        <span className="uc-label mono">{label}</span>
        <span className="uc-sublabel mono">{sublabel}</span>
        <span className="uc-pct mono" style={{ color: pctDisplay ? color : 'var(--fg-4)' }}>
          {pctDisplay ?? '—'}
        </span>
      </div>
      {/* Thin progress bar — only when real value present */}
      <div className="uc-track">
        {pctDisplay && (
          <div
            className="uc-fill"
            style={{
              width: `${Math.round((pctValue ?? 0) * 100)}%`,
              background: color,
            }}
          />
        )}
      </div>
      {note && <div className="uc-note mono">{note}</div>}
    </div>
  )
}

function UtilizationCard() {
  const hw = useStatsHardware()
  const stats = hw.data

  // iGPU: gpu_util (float 0-1). Present in existing endpoint.
  const gpuUtil = stats?.gpu_util ?? null

  // CPU: cpu_util — NEW field (§2b), optional until backend ships.
  // Plain JS property access — absent key returns undefined, ?? null gates it.
  const cpuUtil = stats?.cpu_util ?? null

  // NPU: npu_status.ok → active/idle pill.
  // npu_util: optional (§2b), only render if key present.
  const npuStatus = stats?.npu_status ?? null
  const npuUtil   = stats?.npu_util ?? null   // optional §2b — undefined until backend exposes it
  const npuActive = npuStatus?.ok ?? null

  return (
    <DCard title="UTILIZATION">
      <div className="uc-rows">
        {/* iGPU row */}
        <UtilCard_Row
          label="iGPU"
          sublabel="Vulkan"
          pctValue={gpuUtil}
          color="var(--dev-vulkan)"
          note={gpuUtil == null ? 'pending driver' : null}
        />

        {/* CPU row */}
        <UtilCard_Row
          label="CPU"
          sublabel="host"
          pctValue={cpuUtil}
          color="var(--dev-cpu)"
          note={cpuUtil == null ? 'pending driver' : null}
        />

        {/* NPU row — active/idle pill; bar only if npu_util appears */}
        <div className="uc-row uc-row-npu">
          <div className="uc-row-top">
            <span className="uc-dot" style={{ background: 'var(--dev-npu)' }} />
            <span className="uc-label mono">NPU</span>
            <span className="uc-sublabel mono">XDNA</span>
            {npuUtil != null ? (
              <span className="uc-pct mono" style={{ color: 'var(--dev-npu)' }}>
                {Math.round(npuUtil * 100)}%
              </span>
            ) : (
              <span className="uc-pct mono" style={{ color: 'var(--fg-4)' }}>—</span>
            )}
          </div>
          {/* Progress bar: only when npu_util present */}
          <div className="uc-track">
            {npuUtil != null && (
              <div
                className="uc-fill"
                style={{
                  width: `${Math.round(npuUtil * 100)}%`,
                  background: 'var(--dev-npu)',
                }}
              />
            )}
          </div>
          {/* Status pill + caption */}
          <div className="uc-npu-status mono">
            {npuActive === null ? (
              <span className="uc-note">pending driver</span>
            ) : (
              <>
                <span className={'uc-pill' + (npuActive ? ' active' : ' idle')}>
                  {npuActive ? 'active' : 'idle'}
                </span>
                {npuUtil != null ? (
                  // Real npu_util present → the % is a residency reading.
                  <span className="uc-note">active residency</span>
                ) : (
                  <span className="uc-note">% pending driver</span>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </DCard>
  )
}

Object.assign(window, { ThroughputCard2, UtilizationCard })
