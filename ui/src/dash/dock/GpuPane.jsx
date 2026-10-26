// The dock's GPU tab: what the GPU is doing, every button that fits, and the facts behind the line.
//
// Left, the status line's own words and buttons (the same ones the Image Gen pane draws) plus the
// extras that are always there: restart, the engine log and the editor. Right, the arbiter, the
// engine and the pool as plain rows. The state comes from useGpuStatus and the calls from
// useStatusLineActions (Dock owns both, so the confirm dialogs mount once). The segment's hidden
// phrase is the dock's one live region: this pane adds none.
//
// It draws with the plain dashboard tokens, not the `--comfy*` accent.
import { useState } from 'react'
import { LABEL, RenderRun, Steps } from './gpu-parts.jsx'
import { LogsModal, PILL } from '@/dash/image/EngineCard.jsx'
import { gateRow, queueSummary } from '@/dash/image/engine-pure'
import { costLine } from '@/dash/image/status-line-pure'
import { useGenerateState } from '@/dash/image/generate-store'
import { slotIndicatorFromPhase } from '@/dash/slot-status.js'

const fmtGb = (gb) => String(Number(gb.toFixed(1)))

// `HH:MM:SS` of an epoch-ms time, the journal rows' style.
const shortTime = (at) => new Date(at).toTimeString().slice(0, 8)

/**
 * @param {{
 *   gpu: ReturnType<typeof import('./useGpuStatus').useGpuStatus>,
 *   actions: ReturnType<typeof import('@/dash/image/useStatusLineActions').useStatusLineActions>,
 * }} props
 */
export function GpuPane({ gpu, actions }) {
  const [logsOpen, setLogsOpen] = useState(false)
  const lastImage = useGenerateState().results[0]
  const { view, env, status, memory } = gpu
  if (!view) return null

  const arbiter = env?.arbiter
  const imageMode = arbiter?.mode === 'img'
  const unloadSet = arbiter?.unload_set ?? null
  const pool = memory?.gpu ?? null

  // What image mode frees: the GB of the slots it paused, as the memory budget has them.
  const freedGb = (pool?.this_box ?? [])
    .filter((row) => unloadSet?.includes(row.slot))
    .reduce((sum, row) => sum + row.gb, 0)
  const cost = imageMode
    ? `Image mode frees ${fmtGb(freedGb)} GB of the GPU pool. Paused slots reload about 10 s after the last image unless pinned.`
    : costLine(unloadSet, memory, arbiter).join(' ')

  // A button that could only do nothing is not shown.
  const buttons = view.actions.filter(
    (a) => !(a === 'open_editor' && !gpu.editorUrl) && !(a === 'cancel' && !gpu.ctx.onCancel),
  )
  const extras = ['restart_engine', 'open_editor'].filter(
    (a) => !view.actions.includes(a) && !(a === 'open_editor' && !gpu.editorUrl),
  )
  const actionButton = (a, cls) => (
    <button
      key={a}
      type="button"
      className={`btn sm${cls ? ` ${cls}` : ''}`}
      data-testid={`gpu-status-action-${a}`}
      disabled={actions.pending != null}
      onClick={() => void actions.run(a, gpu.ctx)}
    >
      {LABEL[a]}
    </button>
  )
  const primary = (a) => (a === 'hold' || a === 'keep' ? 'primary' : a === 'stop' ? 'warn' : '')

  const gate = status?.gate
  const gateInfo = gateRow(gate)
  const gateTone = gate?.state === 'stale' ? 'warn' : gate?.state === 'no_credential' ? 'err' : ''
  const waiting = arbiter?.llm_waiters?.count ?? 0
  const configured = (gpu.slots ?? []).filter((s) => !!(s.model || s.model_default))

  const rows = [
    ['mode', imageMode ? `image${arbiter?.pinned ? ' · pinned' : ''}` : 'chat'],
    ['phase', arbiter?.phase ?? '—'],
    [
      'engine',
      status ? `${PILL[status.engine] ?? status.engine ?? '—'}${gate ? ` · gate ${gate.version_seen ?? gate.state}` : ''}` : '—',
      gateTone,
      gateInfo?.hint,
    ],
    ['queue', status?.queue ? queueSummary(status.queue).line : '—'],
    [
      'chat waiting',
      waiting > 0 ? `${waiting} request${waiting === 1 ? '' : 's'} · cap 2:00` : 'none',
      waiting > 0 ? 'warn' : '',
    ],
    [
      'gpu pool',
      pool
        ? `${fmtGb(pool.total_gb - pool.used_gb)} GB free of ${pool.total_gb}${imageMode ? ` · ${fmtGb(freedGb)} GB freed` : ''}`
        : '—',
    ],
  ]

  return (
    <div className="gp" data-testid="dock-gpu">
      <div className="gp-main">
        <div className="gp-head">
          <span className={`dotc tone-${view.tone}`} aria-hidden="true" />
          {view.head}
        </div>
        {view.detail && <div className="gp-det">{view.detail}</div>}
        <Steps steps={gpu.steps} />
        {view.kind === 'rendering' && gpu.progress && <RenderRun progress={gpu.progress} elapsedS={gpu.elapsedS} />}
        <div className="gp-acts">
          {buttons.map((a, i) => actionButton(a, i === 0 ? primary(a) : `ghost ${primary(a)}`.trim()))}
          <span className="gp-sep" aria-hidden="true" />
          {extras.includes('restart_engine') && actionButton('restart_engine', 'ghost')}
          <button
            type="button"
            className="btn sm ghost"
            data-testid="gpu-status-logs"
            onClick={() => setLogsOpen(true)}
          >
            Logs
          </button>
          {extras.includes('open_editor') && actionButton('open_editor', 'ghost')}
        </div>
        {actions.error && (
          <div className="gp-error" role="alert">
            {actions.error}
          </div>
        )}
        {cost && <div className="gp-cost">{cost}</div>}
      </div>
      <div className="gp-facts">
        <dl data-testid="gpu-facts">
          {rows.map(([label, value, tone, hint]) => (
            <Fact key={label} label={label} tone={tone} title={hint}>
              {value}
            </Fact>
          ))}
          <Fact label="slots">
            {configured.length === 0
              ? '—'
              : configured.map((s, i) => {
                  const paused = imageMode && unloadSet?.includes(s.name)
                  return (
                    <span key={s.name ?? i} className="gp-slot">
                      <span className={`pip ${paused ? 'paused' : slotIndicatorFromPhase(s).cls}`} aria-hidden="true" />
                      {s.name}
                    </span>
                  )
                })}
          </Fact>
          <Fact label="last image · this browser">
            {lastImage ? `${shortTime(lastImage.at)} · ${lastImage.model} · ${lastImage.size}` : '—'}
          </Fact>
        </dl>
      </div>
      <LogsModal open={logsOpen} onClose={() => setLogsOpen(false)} container={status?.container?.name} />
    </div>
  )
}

function Fact({ label, tone, title, children }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className={tone || undefined} title={title || undefined}>
        {children}
      </dd>
    </>
  )
}
