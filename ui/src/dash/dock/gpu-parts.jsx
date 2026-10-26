// The pieces the dock's GPU segment and pane share: the button labels, the one-second tick, the
// two confirms (stop while a render runs, hold / keep a pin), the switch and reload step chips and
// the render progress bar. The words, the precedence and the choice of buttons are
// image/status-line-pure.ts's; the step chips and progress figures are
// image/status-line-parts-pure.ts's; what a button calls is image/useStatusLineActions.ts's.
import { useEffect, useReducer } from 'react'
import { ConfirmDialog } from '@/dash/primitives.jsx'
import { clockLabel, renderPct } from '@/dash/image/status-line-parts-pure'

export const LABEL = {
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
export function useTick(active) {
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
export const PIN_CONFIRM = {
  hold: { title: 'Hold image mode?', confirmLabel: 'Hold image mode' },
  keep: { title: 'Keep image mode on?', confirmLabel: 'Keep image mode on' },
}
export const PIN_CONFIRM_BODY =
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

export function Steps({ steps }) {
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

export function RenderRun({ progress, elapsedS }) {
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
