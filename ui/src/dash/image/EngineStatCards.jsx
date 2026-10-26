// The Engine tab's queue card. It reads /status as given: the queue's counts and hal0's own render.
import { QUEUE_EMPTY, queueSummary } from './engine-pure'
import { clockLabel, renderPct } from './status-line-parts-pure'

/** @param {{ status: import('@/api/hooks/useComfyui').ComfyuiStatus | undefined }} props */
export function QueueCard({ status }) {
  // A body without a queue (an older backend) has no counts to draw: not "0 running".
  if (!status?.queue) return null
  const { line, empty } = queueSummary(status.queue)
  const progress = status.progress
  const pct = renderPct(progress)
  return (
    <section className="ie-card" data-testid="queue-card" aria-label="ComfyUI queue">
      <header className="ie-card-h">
        <span className="ie-title">Queue</span>
        <span className="ie-grow" />
        <span className="ie-sub" data-testid="queue-summary">
          {line}
        </span>
      </header>
      <div className="ie-card-b">
        {progress && (
          <div className="ie-prog" data-testid="queue-progress">
            <div className="ie-prog-row">
              <b>{progress.model}</b>
              {progress.step != null && progress.total != null ? (
                <span>
                  step {progress.step}/{progress.total}
                </span>
              ) : null}
              {progress.node ? <span className="ie-dim">{progress.node}</span> : null}
              <span className="ie-dim">{clockLabel(progress.elapsed_s)}</span>
            </div>
            {pct != null && (
              <div
                className="ie-bar"
                role="progressbar"
                aria-label="Render progress"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={pct}
              >
                <i style={{ width: `${pct}%` }} />
              </div>
            )}
          </div>
        )}
        {empty && !progress && (
          <div className="ie-muted" data-testid="queue-empty">
            {QUEUE_EMPTY}
          </div>
        )}
      </div>
    </section>
  )
}
