// The GPU status line: what the GPU is doing, what an image costs chat, and the buttons that fit.
//
// Board A: a coloured left rule and dot, the headline, the detail, the actions on the right. A
// render adds a progress bar and an elapsed timer; a switch and a reload add their step chips.
// The words, the precedence and the choice of buttons are status-line-pure.ts's; the step chips,
// progress figures and slot-card note are status-line-parts-pure.ts's; what a button calls is
// useStatusLineActions.ts's. This file merges the responses and draws.
//
// It draws with the plain dashboard tokens, not the `--comfy*` accent.
import { useEffect, useReducer } from 'react'
import './image-status-line.css'
import { useArbiter, useComfyui, useComfyuiModels } from '@/api/hooks/useComfyui'
import { useMemoryBudget } from '@/api/hooks/useStatsHardware'
import { useSlots } from '@/api/hooks/useSlots'
import { ConfirmDialog } from '@/dash/primitives.jsx'
import { KIND_ANNOUNCEMENT, actionFacts, statusLine } from './status-line-pure'
import { clockLabel, reloadSteps, renderPct, switchSteps } from './status-line-parts-pure'
import { useDismissedRefusalAt, useStatusLineActions } from './useStatusLineActions'
import { useEditorAccess } from './useEditorAccess'

const LABEL = {
  hold: 'Hold image mode',
  cancel: 'Cancel',
  cancel_render: 'Cancel render',
  keep: 'Keep image mode',
  unpin: 'Unpin',
  stop: 'Stop image mode',
  open_editor: 'Open editor',
  dismiss: 'Dismiss',
  restart_engine: 'Restart engine',
}

// Re-render once a second while a clock is on screen (the grace countdown, the render's elapsed
// time); the polls alone move every one to five seconds.
function useTick(active) {
  const [, bump] = useReducer((n) => n + 1, 0)
  useEffect(() => {
    if (!active) return undefined
    const id = setInterval(bump, 1000)
    return () => clearInterval(id)
  }, [active])
}

/**
 * The confirm that asks before stopping image mode drops a running render. Mount one wherever
 * `useStatusLineActions().run('stop', ...)` can be called with `renderRunning`.
 * @param {{ confirm: { open: boolean, accept: () => void, decline: () => void } }} props
 */
export function ForceStopConfirm({ confirm }) {
  return (
    <ConfirmDialog
      open={confirm.open && confirm.kind === 'stop'}
      destructive
      onCancel={confirm.decline}
      onConfirm={confirm.accept}
      title="Stop image mode while a render runs?"
      message="A render is running. Stopping now cancels it, drops any prompts queued behind it and reloads the paused GPU slots."
      footerNote="The render in progress is lost."
      confirmLabel="Stop and cancel render"
    />
  )
}

// What Hold and Keep ask. A pin stops GPU chat (503 `gpu.image_mode`) until someone unpins, which
// neither button's name nor the line beside it says.
const PIN_CONFIRM = {
  hold: { title: 'Hold image mode?', confirmLabel: 'Hold image mode' },
  keep: { title: 'Keep image mode on?', confirmLabel: 'Keep image mode on' },
}
const PIN_CONFIRM_BODY =
  'GPU chat requests (Open WebUI, agents, the API) are refused until you unpin. The paused GPU slots stay paused.'

/**
 * The confirm that asks before Hold or Keep pins image mode. Mount one wherever
 * `useStatusLineActions().run('hold' | 'keep')` can be called.
 * @param {{ confirm: { open: boolean, kind: string | null, accept: () => void, decline: () => void } }} props
 */
export function PinConfirm({ confirm }) {
  const copy = PIN_CONFIRM[confirm.kind]
  return (
    <ConfirmDialog
      open={confirm.open && copy != null}
      onCancel={confirm.decline}
      onConfirm={confirm.accept}
      title={copy?.title ?? ''}
      message={PIN_CONFIRM_BODY}
      confirmLabel={copy?.confirmLabel ?? 'Confirm'}
    />
  )
}

function Steps({ steps }) {
  if (steps.length === 0) return null
  return (
    <ol className="isl-steps">
      {steps.map((s) => (
        <li key={s.key} className={`isl-step ${s.state}`} data-testid="image-status-step" data-state={s.state}>
          {s.label}
        </li>
      ))}
    </ol>
  )
}

function RenderRun({ progress, elapsedS }) {
  const pct = renderPct(progress)
  return (
    <div className="isl-run">
      {pct != null && (
        <div
          className="isl-bar"
          role="progressbar"
          aria-label="Render progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
        >
          <i style={{ width: `${pct}%` }} />
        </div>
      )}
      <span className="isl-elapsed mono" data-testid="image-status-elapsed">
        {clockLabel(elapsedS)}
      </span>
    </div>
  )
}

/**
 * @param {{
 *   ownRun?: boolean,
 *   onCancelOwnRun?: () => void,
 * }} props `ownRun`: a Generate run of this browser is in flight (it started the switch in
 *   progress, so only it can cancel one); `onCancelOwnRun` aborts that run.
 */
export function ImageStatusLine({ ownRun = false, onCancelOwnRun }) {
  const arbiterQ = useArbiter()
  // The pane is mounted, so /status polls at its active cadence, faster while a run is in flight.
  const statusQ = useComfyui({ active: true, busy: ownRun })
  const memory = useMemoryBudget().data
  const engineAvailable = useComfyuiModels().data?.engine?.available
  const slots = useSlots().data
  // Where the editor cannot be opened from this page there is no link to open (editor-reach.ts).

  const editorUrl = useEditorAccess().url
  const dismissedAt = useDismissedRefusalAt()

  const env = arbiterQ.data
  const status = statusQ.data
  const facts = actionFacts(env, status)
  useTick(env?.arbiter?.phase === 'grace' || facts.renderRunning)

  const nowS = Date.now() / 1000
  // `restore_at` is on the server's clock and the browser's can be off: the countdown is taken on
  // the server's (`skewMs` is read off each /arbiter answer's Date header, 0 when it has none).
  // The render time below stays on the browser's clock, both ends of it being the browser's.
  const skewS = (env?.skewMs ?? 0) / 1000
  const view = statusLine(env, status, memory, {
    slots,
    dismissedAt,
    now: nowS - skewS,
    engineAvailable,
    ownRun,
  })
  // A failed action's message goes when the line's state changes (the kind), not when its text
  // does: the headline counts seconds and steps.
  const actions = useStatusLineActions({ resetKey: view?.kind ?? null })
  if (!view) return null

  const { kind } = view
  const progress = status?.progress ?? null
  // `elapsed_s` is as of the last poll; carry it on from there.
  const elapsedS = progress
    ? progress.elapsed_s + Math.max(0, statusQ.dataUpdatedAt ? nowS - statusQ.dataUpdatedAt / 1000 : 0)
    : 0
  const steps =
    kind === 'switching'
      ? switchSteps(env?.arbiter?.phase)
      : kind === 'reloading'
        ? reloadSteps(env?.arbiter?.unload_set, slots)
        : []

  const ctx = { ...facts, editorUrl, onCancel: onCancelOwnRun }
  // A button that could only do nothing is not shown.
  const buttons = view.actions.filter(
    (a) => !(a === 'open_editor' && !editorUrl) && !(a === 'cancel' && !onCancelOwnRun),
  )

  return (
    <div
      className={`isl isl-${view.tone}`}
      data-testid="image-status-line"
      data-tone={view.tone}
      role="group"
      aria-label="GPU status"
    >
      <span className="isl-dot" aria-hidden="true" />
      <div className="isl-main">
        {/* The headline counts seconds and steps: announcing it would read out every tick. The
            line's one live region is this hidden phrase, which changes with the kind only. */}
        <span className="sr-only" aria-live="polite" data-testid="image-status-live">
          {KIND_ANNOUNCEMENT[kind]}
        </span>
        <div className="isl-head" data-testid="image-status-head">
          {view.head}
        </div>
        {view.detail && (
          <div className="isl-detail" data-testid="image-status-detail">
            {view.detail}
          </div>
        )}
        {kind === 'rendering' && progress && <RenderRun progress={progress} elapsedS={elapsedS} />}
        <Steps steps={steps} />
        {actions.error && (
          <div className="isl-error" role="alert">
            {actions.error}
          </div>
        )}
      </div>
      {buttons.length > 0 && (
        <div className="isl-actions">
          {buttons.map((a, i) => (
            <button
              key={a}
              type="button"
              className={`btn sm${i === 0 ? '' : ' ghost'}`}
              data-testid={`image-status-action-${a}`}
              disabled={actions.pending != null}
              onClick={() => void actions.run(a, ctx)}
            >
              {LABEL[a]}
            </button>
          ))}
        </div>
      )}
      <ForceStopConfirm confirm={actions.confirm} />
      <PinConfirm confirm={actions.confirm} />
    </div>
  )
}
