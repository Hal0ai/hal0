// The GGML lane (Slots → NPU → Lanes): the xdna slots (ggml-xdna llama-server on the NPU,
// ADR-0057), one card each. Start/stop/restart in place; create and edit open GgmlSlotDrawer
// in this page. Every readout is a real slot field or `—`.
import { useMemo, useState } from 'react'
import { useRunnerImagePullJob } from '@/api/hooks/useRunnerImages'
import { useModels } from '@/api/hooks/useModels'
import { useSlotEdit, useSlotUnload } from '@/api/hooks/useSlots'
import { useXdnaFit } from '@/api/hooks/useXdnaFit'
import { isXdnaSlot } from '@/lib/slotModelFit'
import { SlotControls, slotCtrlPhase } from '../inference-pane.jsx'
import { slotButtonPhase, slotIndicatorFromPhase } from '../slot-status.js'
import { GgmlSlotDrawer } from './GgmlSlotDrawer.jsx'
import { GgmlModelPicker } from './GgmlModelPicker.jsx'
import { GGML_IMAGE_MISSING, GGML_REQUANT_CMD, ggmlPerOpLine, ggmlPickerRows, xdnaBlocker } from './ggml-slot-pure'
import { copyRequantFix } from './XdnaFitChips.jsx'
import { useNpuConflictStart } from './useNpuConflictStart.jsx'

const num = (v, digits = 0) => (typeof v === 'number' && v > 0 ? (digits ? v.toFixed(digits) : String(Math.round(v))) : null)

function GgmlCard({ slot, hostPmode, pickerRows, onEdit }) {
  const start = useNpuConflictStart()
  const unloadMut = useSlotUnload()
  const editMut = useSlotEdit()
  const warn = (msg) => {
    if (typeof window !== 'undefined' && window.__hal0Toast) window.__hal0Toast(msg, 'warn')
  }
  const startOrWarn = (kind) =>
    start.run(slot.name, kind).catch((e) => warn(e?.message ? `${slot.name}: ${e.message}` : `${slot.name}: action failed`))
  const ind = slotIndicatorFromPhase(slot)
  const up = ['serving', 'ready', 'idle', 'loaded', 'warming', 'stale'].includes(ind.cls)
  const m = slot.metrics || {}
  const tps = num(m.toks)
  const ttft = num(m.ttft)
  const mem = typeof m.mem === 'number' && m.mem > 0 ? (Math.round(m.mem * 10) / 10).toString() : null
  const knobs = Object.keys(slot.tune || {}).length
  const wantsPmode = slot.pmode && hostPmode && slot.pmode !== hostPmode ? slot.pmode : null
  const fitRow = slot.xdna_path === undefined ? null : { xdna_path: slot.xdna_path, reason: slot.xdna_reason || '' }
  const path = fitRow?.xdna_path ?? null
  const perOp = ggmlPerOpLine(fitRow)
  const blocker = xdnaBlocker(fitRow)
  const nm = slot.name
  const cur = slot.model_id || slot.model || ''
  // Swap the bound model; a running slot restarts onto it (a per-op / FLM conflict opens the confirm).
  const swap = async (id) => {
    try {
      await editMut.mutateAsync({ name: nm, body: { model: { default: id } } })
      if (slotButtonPhase(slot) !== 'off') await start.run(nm, 'restart')
    } catch (e) {
      warn(e?.message ? `${nm}: ${e.message}` : `${nm}: model change failed`)
    }
  }
  return (
    <div className="npt-gcard" data-testid={`ggml-card-${nm}`}>
      <div className="npt-ghead">
        <span className={`ldot ${ind.cls}`} />
        <span className="npt-gname">{nm}</span>
        {slot.port ? <span className="npt-gport">:{slot.port}</span> : null}
        <span className="npt-gstate">{ind.label ?? ind.cls}</span>
        <span className="npt-gsp" />
        <SlotControls
          phase={slotCtrlPhase(slot)}
          onStart={() => startOrWarn('load')}
          onStop={() => unloadMut.mutate(nm)}
          onRestart={() => startOrWarn('restart')}
          onLogs={() => window.dispatchEvent(new CustomEvent('hal0:slot-logs', { detail: { name: nm } }))}
          onEdit={() => onEdit(slot)}
        />
      </div>
      <div className="npt-gmodelrow">
        <GgmlModelPicker slotName={nm} cur={cur} curLabel={slot.model || cur} rows={pickerRows} disabled={editMut.isPending} onSwap={swap} />
      </div>
      <div className="npt-gchips">
        <span className={`chip mono ${path === 'fused' ? 'ok' : path === 'per-op' ? 'amber' : ''}`} data-testid={`ggml-${nm}-fit`} data-fit={path || 'none'} title={fitRow?.reason || 'no fit data'}>
          {path || '—'}
        </span>
        <span className="chip mono">{slot.profile || 'xdna'}</span>
        {wantsPmode ? (
          <span className="chip mono amber" data-testid={`ggml-pmode-${nm}`} title={`host power mode is ${hostPmode}`}>
            wants {wantsPmode}
          </span>
        ) : knobs > 0 ? (
          <span className="chip mono" data-testid={`ggml-knobs-${nm}`}>{knobs} knob{knobs === 1 ? '' : 's'}</span>
        ) : null}
        {blocker?.fix && (
          <button type="button" className="chip mono amber npt-fix" data-testid={`ggml-${nm}-fix`} title={`Copy: ${GGML_REQUANT_CMD}`} onClick={copyRequantFix}>
            copy fix
          </button>
        )}
      </div>
      {perOp && <div className="npt-gwhy" data-testid={`ggml-${nm}-why`} title={fitRow?.reason}>{perOp}</div>}
      <div className="npt-gmx" data-testid={`ggml-mx-${nm}`}>
        {up ? (
          tps || ttft || mem ? (
            <>
              {tps && <span><b>{tps}</b> t/s</span>}
              {ttft && <span>{ttft} ms ttft</span>}
              {mem && <span>{mem} GB</span>}
            </>
          ) : (
            '—'
          )
        ) : (
          'stopped'
        )}
      </div>
      {start.dialog}
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
  const modelsQ = useModels()
  const fitQ = useXdnaFit()
  const pickerRows = useMemo(() => ggmlPickerRows(modelsQ.data, fitQ.data), [modelsQ.data, fitQ.data])
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
      </div>
      {missing && (
        <div className="npt-banner" data-testid="ggml-pull-banner">
          <span>{GGML_IMAGE_MISSING}{pulling && pull.pct != null ? ` ${Math.round(pull.pct)}%` : ''}</span>
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
              pickerRows={pickerRows}
              onEdit={(slot) => setDrawer({ open: true, mode: 'edit', slot })}
            />
          ))}
        </div>
      )}
      <button
        type="button"
        className="npt-gadd"
        data-testid="ggml-add"
        disabled={missing}
        title={missing ? GGML_IMAGE_MISSING : undefined}
        onClick={() => setDrawer({ open: true, mode: 'create', slot: undefined })}
      >
        + Add GGML slot
      </button>
      <p className="npt-gfoot">Each GGML slot is its own llama-server process on the NPU. You can run several, beside FLM or without it.</p>
      <GgmlSlotDrawer open={drawer.open} mode={drawer.mode} slot={drawer.slot} onClose={close} />
    </div>
  )
}
