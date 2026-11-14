// The GGML lane (Slots → NPU → Lanes): the xdna slots (ggml-xdna llama-server on the NPU,
// ADR-0057), one card each. Start/stop/restart in place; create and edit open GgmlSlotDrawer
// in this page. Every readout is a real slot field or `—`.
import { useState } from 'react'
import { useRunnerImagePullJob } from '@/api/hooks/useRunnerImages'
import { useSlotLoad, useSlotRestart, useSlotUnload } from '@/api/hooks/useSlots'
import { isXdnaSlot } from '@/lib/slotModelFit'
import { SlotControls, slotCtrlPhase } from '../inference-pane.jsx'
import { slotIndicatorFromPhase } from '../slot-status.js'
import { GgmlSlotDrawer } from './GgmlSlotDrawer.jsx'
import { ggmlRequantHint } from './ggml-slot-pure'

const num = (v, digits = 0) => (typeof v === 'number' && v > 0 ? (digits ? v.toFixed(digits) : String(Math.round(v))) : null)

function GgmlCard({ slot, hostPmode, onEdit }) {
  const loadMut = useSlotLoad()
  const unloadMut = useSlotUnload()
  const restartMut = useSlotRestart()
  const ind = slotIndicatorFromPhase(slot)
  const up = ['serving', 'ready', 'idle', 'loaded', 'warming', 'stale'].includes(ind.cls)
  const m = slot.metrics || {}
  const tps = num(m.toks)
  const ttft = num(m.ttft)
  const mem = typeof m.mem === 'number' && m.mem > 0 ? (Math.round(m.mem * 10) / 10).toString() : null
  const overrides = Object.keys(slot.tune || {}).length
  const wantsPmode = slot.pmode && hostPmode && slot.pmode !== hostPmode ? slot.pmode : null
  const fitRow = { xdna_path: slot.xdna_path ?? null, reason: slot.xdna_reason || '' }
  const fusedLink = ggmlRequantHint(fitRow)
  const nm = slot.name
  return (
    <div className="npt-gcard" data-testid={`ggml-card-${nm}`}>
      <div className="npt-lane-h">
        <span className={`ldot ${ind.cls}`} />
        <span className="npt-gname">{nm}</span>
        {slot.port ? <span className="npt-sub">:{slot.port}</span> : null}
        <span className="npt-state">{ind.label ?? ind.cls}</span>
        <span className="npt-gsp" />
        <SlotControls
          phase={slotCtrlPhase(slot)}
          onStart={() => loadMut.mutate(nm)}
          onStop={() => unloadMut.mutate(nm)}
          onRestart={() => restartMut.mutate(nm)}
          onLogs={() => window.dispatchEvent(new CustomEvent('hal0:slot-logs', { detail: { name: nm } }))}
          onEdit={() => onEdit(slot)}
        />
      </div>
      <div className="npt-gmodel">{slot.model || '—'}</div>
      <div className="npt-gchips">
        <span
          className={`chip mono npt-fit ${slot.xdna_path || 'none'}`}
          data-testid={`ggml-fit-${nm}`}
          title={slot.xdna_reason || slot.xdna_path || 'no fit data'}
        >
          {slot.xdna_path || '—'}
        </span>
        <span className="chip mono">{slot.profile || '—'}</span>
        {wantsPmode && (
          <span className="chip mono warn" data-testid={`ggml-pmode-${nm}`} title={`host power mode is ${hostPmode}`}>
            wants {wantsPmode}
          </span>
        )}
        <span className="chip mono">{overrides} override{overrides === 1 ? '' : 's'}</span>
      </div>
      <div className="npt-gmx">
        {up && tps ? <><b>{tps}</b> t/s</> : '—'}
        {up && ttft ? ` · ${ttft} ms` : ''}
        {up && mem ? ` · ${mem} GB` : ''}
      </div>
      {fusedLink && (
        <a className="npt-glink" data-testid={`ggml-fused-${nm}`} href="#slots/npu/models">
          Get the fused path
        </a>
      )}
    </div>
  )
}

/**
 * @param {object} props
 * @param {Array<object>} [props.slots]  the page's local slots (xdna ones are picked here)
 * @param {object} [props.occ]  /api/npu/occupancy
 * @param {object} [props.hw]  /api/stats/hardware
 */
export function GgmlLane({ slots = [], occ, hw }) {
  const [drawer, setDrawer] = useState({ open: false, mode: 'create', slot: undefined })
  const pull = useRunnerImagePullJob()
  const xdna = (slots ?? []).filter((s) => isXdnaSlot(s))
  const pulled = occ?.npu_engines?.xdna?.pulled
  const missing = pulled === false
  const pulling = pull.inFlight || pull.state === 'running' || pull.state === 'queued'
  const close = () => setDrawer((d) => ({ ...d, open: false }))
  return (
    <div className="npt-lane npt-lane-ggml" data-testid="ggml-lane">
      <div className="npt-lane-h">
        <div className="npt-lane-ttl">
          <span className="npt-eng ggml">GGML</span>
          <span className="npt-sub">ggml-xdna · llama-server on the NPU</span>
        </div>
        <button
          type="button"
          className="btn primary sm"
          data-testid="ggml-add"
          disabled={missing}
          onClick={() => setDrawer({ open: true, mode: 'create', slot: undefined })}
        >
          + Add GGML slot
        </button>
      </div>
      {missing && (
        <div className="npt-banner" data-testid="ggml-pull-banner">
          <span>The ggml-xdna runner image is not pulled yet.{pulling && pull.pct != null ? ` ${Math.round(pull.pct)}%` : ''}</span>
          <button type="button" className="btn sm" data-testid="ggml-pull" disabled={pulling} onClick={() => Promise.resolve(pull.start('xdna')).catch(() => {})}>
            {pulling ? 'Pulling…' : 'Pull'}
          </button>
          {pull.error && <span className="npt-error-inline" role="alert">{pull.error.message}</span>}
        </div>
      )}
      {xdna.length === 0 ? (
        <p className="npt-hint">No GGML slot yet. A GGML slot runs a plain GGUF model on the NPU.</p>
      ) : (
        <div className="npt-gcards">
          {xdna.map((s) => (
            <GgmlCard
              key={s.name}
              slot={s}
              hostPmode={hw?.npu_pmode ?? null}
              onEdit={(slot) => setDrawer({ open: true, mode: 'edit', slot })}
            />
          ))}
        </div>
      )}
      <GgmlSlotDrawer open={drawer.open} mode={drawer.mode} slot={drawer.slot} onClose={close} />
    </div>
  )
}
