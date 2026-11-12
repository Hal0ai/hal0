// The top of Memory ▸ Operations: why the queue is not moving (when extraction is the cause),
// the all-clear (when nothing waits and nothing failed), the figures, and where they sit by bank.
//
// Every figure here is the health route's (`GET /api/memory/health`), so this tab and Health
// cannot disagree. Nothing is added up in the browser.
import { useId } from 'react'
import '../memory-area.css'
import { memoryTabHref, serializeMemoryRoute } from '../memory-route-pure'
import {
  bankBreakdown,
  durationParts,
  plural,
  spreadText,
} from '../operations-pure'
import { GoLink } from '../shared/GoLink.jsx'
import { StatStrip } from '../shared/StatStrip.jsx'
import { StatusChip } from '../shared/StatusChip.jsx'
import { formatCount, formatDateTime, formatDuration, formatRelativeTime } from '../shared/format-pure'

const BACKGROUND_STEP = "the engine's background step"

/** A count in a table cell: the number, or the "not reported" mark. Never an unreported zero. */
function Figure({ value }) {
  const text = formatCount(value)
  if (text === null) {
    return (
      <span className="ma-missing" role="img" aria-label="not reported">
        –
      </span>
    )
  }
  return text
}

const Code = ({ children }) => <span className="ma-mono ma-em">{children}</span>

// What is wrong with extraction, as the sentences of the cause card.
function CauseText({ cause }) {
  const slot = cause.slot ? (
    <>
      the <Code>{cause.slot}</Code> slot
    </>
  ) : (
    'the extraction model'
  )
  if (cause.reason === 'slot_unresolved') {
    return <>Storing a memory runs on {slot}, and that slot has no model. </>
  }
  if (cause.reason === 'llm_unreachable') {
    return <>Storing a memory runs on {slot}, and it is not accepting connections. </>
  }
  if (cause.reason === 'recent_failures' && cause.failures !== null) {
    return (
      <>
        {plural(cause.failures, 'attempt', 'attempts')} to store a memory failed in the last {cause.window}.{' '}
      </>
    )
  }
  return <>The step that turns what agents send into facts is not working. </>
}

// The engine's own words for the most frequent failure, on a line of their own: the message is
// shown as it is, so it is not made part of a sentence.
function CauseReason({ cause }) {
  if (!cause.top) return null
  return (
    <p className="ma-helper ma-ops-reason" data-testid="ma-ops-cause-reason">
      Most frequent reason, seen {plural(cause.top.count, 'time', 'times')}:{' '}
      <span className="ma-mono ma-em">{cause.top.message}</span>
      {cause.otherMessages > 0 && (
        <> · {plural(cause.otherMessages, 'other reason', 'other reasons')} seen</>
      )}
    </p>
  )
}

/**
 * The cause, first: shown while extraction is the step that has to be fixed before anything in
 * the queue can move. It offers no retry; the fix is on Health.
 */
export function CauseCard({ cause, route }) {
  const headingId = useId()
  return (
    <section className="card ma-card" aria-labelledby={headingId} data-testid="ma-ops-cause">
      <div className="ma-lead-row">
        <div className="ma-lead-main">
          <span className="ma-eyebrow ma-eyebrow-block">Why the queue is not moving</span>
          <h2 className="ma-lead" id={headingId}>
            New memories are failing to be stored
          </h2>
          <p className="ma-body" data-testid="ma-ops-cause-text">
            <CauseText cause={cause} />
            Until that is fixed, operations wait here and a retry would fail.
          </p>
          <CauseReason cause={cause} />
          <div className="ma-ops-lead-link">
            <GoLink href={memoryTabHref('health', route)} data-testid="ma-ops-cause-health">
              Open Health
            </GoLink>
          </div>
        </div>
        <div className="ma-lead-side">
          <StatusChip tone="err">failing</StatusChip>
        </div>
      </div>
    </section>
  )
}

/** The all-clear: nothing waits, nothing failed, and what will show up here when there is work. */
export function ClearCard({ bank, lastCompletedAt }) {
  const headingId = useId()
  const now = Date.now()
  const when = formatRelativeTime(lastCompletedAt, now)
  return (
    <section className="card ma-card" aria-labelledby={headingId} data-testid="ma-ops-clear">
      <div className="ma-lead-row">
        <div className="ma-lead-main">
          <span className="ma-eyebrow ma-eyebrow-block">Queue</span>
          <h2 className="ma-lead" id={headingId}>
            Nothing is waiting. Nothing has failed.
          </h2>
          <p className="ma-body">
            {bank ? (
              <>
                Every operation queued for <Code>{bank}</Code> has been worked through.
              </>
            ) : (
              'Every operation agents queued has been worked through.'
            )}
            {when && (
              <>
                {' '}
                The last one finished{' '}
                <time
                  className="ma-em"
                  dateTime={lastCompletedAt}
                  title={formatDateTime(lastCompletedAt) ?? undefined}
                  data-testid="ma-ops-last-completed"
                >
                  {when}
                </time>
                .
              </>
            )}{' '}
            Operations appear below while they wait or run: a document an agent sent, the engine&apos;s
            background consolidation, a knowledge refresh. One that fails stays listed until you retry or
            discard it.
          </p>
        </div>
        <div className="ma-lead-side">
          <StatusChip tone="ok">done</StatusChip>
        </div>
      </div>
    </section>
  )
}

function oldestCell(figures) {
  const base = { id: 'oldest', label: 'Oldest waiting' }
  if (figures.waiting === 0) return { ...base, value: 'none', helper: 'nothing is waiting' }
  const parts = durationParts(figures.oldestWaitingS)
  if (parts) return { ...base, value: parts.value, unit: parts.unit, helper: 'in the queue' }
  // Something waits, and none of it is due: the engine put each one off to a later try.
  if ((figures.waiting ?? 0) > 0) return { ...base, value: null, helper: 'none is due yet' }
  return { ...base, value: null }
}

/**
 * The five figures of the scope, and in words what they leave out when the health read was cut
 * short.
 *
 * @param {{ figures: object, report: object|null, bank: string|null, loading: boolean }} props
 *   `report` is null while there is no health answer.
 */
export function QueueStrip({ figures, report, bank, loading }) {
  const queue = report?.queue ?? null
  const operations = (n) => (n === 1 ? 'operation' : 'operations')
  const facts = (n) => (n === 1 ? 'fact' : 'facts')
  const cells = [
    {
      id: 'waiting',
      label: 'Waiting',
      value: figures.waiting,
      unit: operations(figures.waiting),
      helper: bank === null ? spreadText(queue, 'waiting') : null,
    },
    {
      id: 'failed',
      label: 'Failed',
      value: figures.failed,
      unit: operations(figures.failed),
      helper: bank === null ? spreadText(queue, 'failed') : null,
    },
    oldestCell(figures),
    {
      id: 'consolidation-waiting',
      label: 'Waiting on consolidation',
      value: figures.consolidationPending,
      unit: facts(figures.consolidationPending),
      helper: BACKGROUND_STEP,
    },
    {
      id: 'consolidation-failed',
      label: 'Failed consolidation',
      value: figures.consolidationFailed,
      unit: facts(figures.consolidationFailed),
      helper: BACKGROUND_STEP,
    },
  ]
  return (
    <>
      <StatStrip
        label={bank === null ? 'Queue summary, all banks' : `Queue summary, ${bank}`}
        cells={cells}
        loading={loading}
        data-testid="ma-ops-strip"
      />
      {!loading && report && (report.partial || report.truncated) && (
        <div className="ma-notice" data-testid="ma-ops-incomplete">
          <p className="ma-notice-text ma-notice-quiet">
            {report.partial && (
              <span data-testid="ma-ops-partial">
                Some banks did not answer in time, so these figures cover only the banks that did. The real
                numbers may be higher.{' '}
              </span>
            )}
            {report.truncated && (
              <span data-testid="ma-ops-truncated">
                A bank holds more operations than were read. The counts are exact; the oldest waiting time
                and the reasons for failure come from the part that was read.
              </span>
            )}
          </p>
        </div>
      )}
    </>
  )
}

/**
 * Where the queue sits, bank by bank. A bank with nothing waiting and nothing failed is counted
 * in one line under the table, not listed. A bank's name opens the list for that bank.
 */
export function BreakdownCard({ queue }) {
  const headingId = useId()
  const { rows, quiet } = bankBreakdown(queue)
  if (rows.length === 0) return null
  const anyMissing = rows.some((row) => row.waiting === null || row.failed === null)
  return (
    <section className="card ma-card" aria-labelledby={headingId} data-testid="ma-ops-banks">
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id={headingId}>
          Queue by bank
        </h2>
        <span className="ma-card-gap" />
        <span className="ma-card-meta">a bank opens its operations</span>
      </div>
      <div className="ma-table-region" role="region" aria-label="Queue by bank table" tabIndex={0}>
        <table className="ma-table ma-ops-banks-table">
          <thead>
            <tr>
              <th scope="col">Bank</th>
              <th scope="col" className="ma-num">
                Waiting
              </th>
              <th scope="col" className="ma-num">
                Failed
              </th>
              <th scope="col" className="ma-num">
                Oldest waiting
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.bank_id} data-bank={row.bank_id}>
                <th scope="row">
                  <a className="ma-link" href={serializeMemoryRoute({ tab: 'operations', bank: row.bank_id })}>
                    {row.bank_id}
                  </a>
                </th>
                <td className="ma-num" data-col="waiting">
                  <Figure value={row.waiting} />
                </td>
                <td className="ma-num" data-col="failed">
                  <Figure value={row.failed} />
                </td>
                <td className="ma-num" data-col="oldest">
                  {row.waiting === 0 ? (
                    <span className="sr-only">nothing waiting</span>
                  ) : (
                    (formatDuration(row.oldest_waiting_s) ?? <Figure value={null} />)
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {quiet > 0 && (
        <div className="ma-card-foot" data-testid="ma-ops-banks-quiet">
          {plural(quiet, 'bank has', 'banks have')} nothing waiting or failed.
        </div>
      )}
      {anyMissing && (
        <div className="ma-card-foot" data-testid="ma-ops-banks-foot">
          – means the engine did not report a figure
        </div>
      )}
    </section>
  )
}
