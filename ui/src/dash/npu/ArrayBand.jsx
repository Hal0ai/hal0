// The NPU array band (Slots → NPU → Lanes, top): duty gauge, claimed columns, throughput and the
// 4×8 AIE grid. Every readout is a real field or "—"; per-tile load is never claimed — the grid
// shows ALLOCATED columns. FLM columns take the NPU hue, ggml-xdna the npu-s1 hue.
import { useMemo } from 'react'
import { Gauge, AieGrid, HUES, NPU_COLS, activityLevel } from './aie-grid.jsx'
import { arrayHolders, arrayOwners, arrayShared } from './array-pure'
import { npuHolders, npuToks } from './npu-pure'

const ENGINE_HUE = {
  flm: { hue: 'var(--dev-npu)', glow: 'var(--npu-s0-glow)', line: 'var(--npu-s0-line)', dim: 'var(--npu-s0-dim)', fg: 'var(--npu-s0-fg)' },
  xdna: HUES[1],
}
const ENGINE_LABEL = { flm: 'FLM', xdna: 'GGML' }

export function ArrayBand({ occ, hw, slots }) {
  const owners = useMemo(() => arrayOwners(occ?.slots, slots), [occ?.slots, slots])
  // One painted object per holder (identity drives the grid's partition merge).
  const painted = useMemo(() => {
    const seen = new Map()
    return owners.map((o) => {
      if (!o) return null
      if (!seen.has(o)) seen.set(o, { name: o.name, serving: o.serving, ...ENGINE_HUE[o.engine] })
      return seen.get(o)
    })
  }, [owners])
  const holders = useMemo(() => arrayHolders(occ?.slots, slots), [occ?.slots, slots])
  const shared = arrayShared(holders)

  const colsAvailable = occ?.columns_available !== false
  const colsTotal = occ?.cols_total || NPU_COLS
  const colsUsed = typeof occ?.cols_used === 'number' ? occ.cols_used : null
  const dutyPct = typeof hw?.npu_util === 'number' ? hw.npu_util * 100 : null
  const tps = npuToks(slots)
  const act = activityLevel(dutyPct, tps ?? 0)

  // Banners: the server's shared warning, and a host power mode that is not turbo while a GGML
  // slot is up. A null pmode is unknown, never guessed.
  const sharedWarning = typeof occ?.shared_warning === 'string' && occ.shared_warning ? occ.shared_warning : null
  const pmode = typeof hw?.npu_pmode === 'string' && hw.npu_pmode ? hw.npu_pmode : null
  const xdnaUp = npuHolders(occ?.slots).some((s) => s.engine === 'xdna')
  const pmodeWarn = xdnaUp && pmode && pmode !== 'turbo' ? pmode : null

  return (
    <>
    {sharedWarning && (
      <div className="npt-banner" role="status" data-testid="npu-shared-warning">{sharedWarning}</div>
    )}
    {pmodeWarn && (
      <div className="npt-banner" role="status" data-testid="npu-pmode-banner">
        <span>
          Host power mode is {pmodeWarn}. GGML slots run best on turbo (about +15% prefill, +20% decode over the default power
          mode); hal0 cannot set it from the container.
        </span>
        <a className="npt-glink" href="#slots/npu/engines" data-testid="npu-pmode-fix">How to fix</a>
      </div>
    )}
    <div className="npt-band" data-testid="npu-array-band">
      <Gauge pct={dutyPct} label="npu duty" sub={colsAvailable ? `${colsUsed ?? '—'}/${colsTotal} columns` : 'column probe unavailable'} size={150} />
      <div className="npt-band-stats">
        <div className="aie-stat">
          <div className="sv">
            {colsAvailable && colsUsed != null ? colsUsed : '—'}
            <span className="u">/{colsTotal}</span>
          </div>
          <div className="sl">columns</div>
        </div>
        <div className="aie-stat">
          <div className="sv acc">
            {tps ?? '—'}
            <span className="u">tok/s</span>
          </div>
          <div className="sl">throughput</div>
        </div>
      </div>
      <div className="npt-band-grid">
        <AieGrid owners={painted} available={colsAvailable} act={act} size={26} cgap={5} rgap={4} shared={shared} />
        <div className="aie-foot" data-testid="npu-array-foot">
          {shared ? 'shared · time-sliced — allocated columns, not per-tile utilisation' : 'allocated columns — not per-tile utilisation'}
        </div>
        <div className="npt-holders" data-testid="npu-holders">
          {holders.length ? (
            holders.map((h) => (
              <span className="npt-holder" key={h.name}>
                <span className="sw" style={{ background: ENGINE_HUE[h.engine].hue }} />
                {h.name}
                <small>{ENGINE_LABEL[h.engine]}</small>
              </span>
            ))
          ) : (
            <span className="npt-hint">Nothing holds the NPU. Start a lane below.</span>
          )}
        </div>
      </div>
    </div>
    </>
  )
}
