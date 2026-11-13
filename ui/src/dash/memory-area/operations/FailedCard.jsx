// The failed operations of the scope, and the two things to do with all of them at once: queue
// them again, or discard them.
//
// Each failed operation is a write an agent sent that was never stored, so neither action is
// offered lightly:
//   - Retry is disabled, with its reason on screen, while storing memories is itself failing:
//     a retry would only fail again.
//   - Discard never acts at once. It opens a confirm in place of the buttons, which says what is
//     lost and that it cannot be undone, and whose other button is named for what is kept.
//
// The run itself (reading the failed operations, then one request each, stopping at the first
// that fails) belongs to the tab, so it keeps going when the scope changes. This card shows it.
import { useEffect, useId, useRef, useState } from 'react'
import '../memory-area.css'
import { bulkText, plural } from '../operations-pure'
import { InlineConfirm, confirmPhrase } from '../shared/InlineConfirm.jsx'
import { ReasonedButton } from '../shared/ReasonedButton.jsx'
import { StatusChip } from '../shared/StatusChip.jsx'

/** Why retry is held back while extraction is the step to fix first. */
export const RETRY_BLOCKED = 'Retrying will fail until storing memories works again.'

const NOUN = { noun: 'failed operations', nounSingular: 'failed operation' }

/**
 * @param {{
 *   count: number|null,
 *   bank: string|null,
 *   blocked: boolean,
 *   healthHref: string,
 *   run: object|null,
 *   onRetryAll: () => void,
 *   onDiscardAll: () => void,
 * }} props `count` is the scope's failed figure from the health answer. `run` is the run in
 *   progress or the last one that finished.
 */
export function FailedCard({ count, bank, blocked, healthHref, run, onRetryAll, onDiscardAll }) {
  const headingId = useId()
  const [confirming, setConfirming] = useState(false)
  const discardButton = useRef(null)
  const returnFocus = useRef(false)

  // Closing the confirm puts focus back on the button that opened it.
  useEffect(() => {
    if (!confirming && returnFocus.current) {
      returnFocus.current = false
      discardButton.current?.focus()
    }
  }, [confirming])

  const failed = typeof count === 'number' ? count : 0
  if (failed === 0 && !run) return null

  const busy = !!run && run.phase !== 'finished'
  const one = failed === 1
  const closeConfirm = () => {
    returnFocus.current = true
    setConfirming(false)
  }

  return (
    <section className="card ma-card" aria-labelledby={headingId} data-testid="ma-ops-failed">
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id={headingId}>
          Failed operations
        </h2>
        <span className="ma-card-gap" />
        {failed > 0 && <StatusChip tone="err">failed</StatusChip>}
      </div>
      <div className="ma-card-body ma-ops-failed">
        {failed > 0 && (
          <div>
            <h3 className="ma-item-title" data-testid="ma-ops-failed-count">
              {plural(failed, NOUN.nounSingular, NOUN.noun)}
              {bank && (
                <>
                  {' '}
                  in <span className="ma-mono">{bank}</span>
                </>
              )}
            </h3>
            <p className="ma-body">
              {one ? 'It is a write' : 'Each one is a write'} an agent sent that was never stored. Retrying
              queues {one ? 'it' : 'them'} again. Discarding removes {one ? 'it' : 'them'} for good.
            </p>
          </div>
        )}

        {failed > 0 && confirming && (
          <InlineConfirm
            verb="Discard"
            {...NOUN}
            count={failed}
            dismissLabel={one ? 'Keep it' : 'Keep them'}
            onConfirm={() => {
              setConfirming(false)
              onDiscardAll()
            }}
            onDismiss={closeConfirm}
            data-testid="ma-ops-discard-confirm"
          >
            {one
              ? 'This is 1 write that was never stored. What the agent sent in it will never become a fact.'
              : `These are ${plural(failed, 'write', 'writes')} that were never stored. What agents sent in them will never become facts.`}{' '}
            Discarding cannot be undone.
          </InlineConfirm>
        )}

        {failed > 0 && !confirming && (
          <div className="ma-ops-actions">
            <ReasonedButton
              className="btn ghost"
              reason={
                blocked ? (
                  <>
                    {RETRY_BLOCKED}{' '}
                    <a className="ma-link" href={healthHref} data-testid="ma-ops-retry-health">
                      Open Health
                    </a>
                  </>
                ) : null
              }
              disabled={busy}
              onClick={onRetryAll}
              data-testid="ma-ops-retry-all"
            >
              Retry all failed
            </ReasonedButton>
            <button
              type="button"
              className="btn danger"
              ref={discardButton}
              disabled={busy}
              onClick={() => setConfirming(true)}
              data-testid="ma-ops-discard"
            >
              {confirmPhrase({ verb: 'Discard', count: failed, ...NOUN })}
            </button>
          </div>
        )}

        {/* In the page from the start and empty until a run begins, so its text is announced. */}
        <p className="ma-ops-run" role="status" data-testid="ma-ops-run" data-phase={run?.phase}>
          {run ? bulkText(run) : ''}
        </p>
      </div>
    </section>
  )
}
