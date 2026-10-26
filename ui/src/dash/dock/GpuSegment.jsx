// The GPU segment of the dock's collapsed row: a coloured rule and dot, the headline, the detail
// and at most two buttons. A render adds a mini progress bar. The state comes from useGpuStatus,
// the calls from useStatusLineActions (Dock owns both, so the confirm dialogs mount once).
//
// It draws with the plain dashboard tokens, not the `--comfy*` accent.
import { KIND_ANNOUNCEMENT } from '@/dash/image/status-line-pure'
import { renderPct } from '@/dash/image/status-line-parts-pure'
import { LABEL } from './gpu-parts.jsx'
import { segmentActions } from './dock-pure'

/**
 * @param {{
 *   gpu: ReturnType<typeof import('./useGpuStatus').useGpuStatus>,
 *   actions: ReturnType<typeof import('@/dash/image/useStatusLineActions').useStatusLineActions>,
 *   onOpen: () => void,
 *   expanded?: boolean,
 * }} props
 */
export function GpuSegment({ gpu, actions, onOpen, expanded = false }) {
  const { view } = gpu
  if (!view) return null
  const pct = view.kind === 'rendering' ? renderPct(gpu.progress) : null
  const buttons = segmentActions(view.actions, gpu.ctx)

  return (
    <div
      className={`gpu-seg gpu-seg-${view.tone}`}
      data-testid="dock-gpu-segment"
      role="group"
      aria-label="GPU status"
    >
      <span className="rule" aria-hidden="true" />
      <span className="dot" aria-hidden="true" />
      {/* The segment's one live region is this hidden phrase, which changes with the kind only: the
          headline counts seconds and steps, and announcing it would read out every tick. It and the
          error alert sit outside the open button, since a button's children are presentational. The error
          is an alert only while the dock is collapsed: expanded, the GPU pane announces it. */}
      <span className="sr-only" aria-live="polite" data-testid="gpu-status-live">
        {KIND_ANNOUNCEMENT[view.kind]}
      </span>
      <div className="txt">
        <button
          type="button"
          className="head"
          title="Open GPU tab"
          data-testid="gpu-status-open"
          onClick={onOpen}
        >
          <span className="head-text" data-testid="gpu-status-head">
            {view.head}
          </span>
        </button>
        {actions.error ? (
          <span
            className="det err"
            role={expanded ? undefined : 'alert'}
            data-testid="gpu-status-detail"
          >
            {actions.error}
          </span>
        ) : (
          view.detail && (
            <span className="det" data-testid="gpu-status-detail">
              {view.detail}
            </span>
          )
        )}
      </div>
      {pct != null && (
        <span className="mini" aria-hidden="true">
          <i style={{ width: `${pct}%` }} />
        </span>
      )}
      {buttons.length > 0 && (
        <span className="acts">
          {buttons.map((a, i) => (
            <button
              key={a}
              type="button"
              className={`btn sm${i === 0 ? '' : ' ghost'}`}
              data-testid={`gpu-status-action-${a}`}
              disabled={actions.pending != null}
              onClick={() => void actions.run(a, gpu.ctx)}
            >
              {LABEL[a]}
            </button>
          ))}
        </span>
      )}
    </div>
  )
}
