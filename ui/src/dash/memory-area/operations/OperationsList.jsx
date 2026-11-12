// The operations of the scope, newest first: a status filter, the rows, page controls, and
// "Consolidate now" for the chosen bank.
//
// One bank is paged by the engine: the list carries the engine's `total`, and Previous and Next
// ask for the page before and after. Every bank at once is a merge of each bank's newest rows,
// held to a bound and paged here; when older rows exist that the merge does not hold, the card
// says so and says how to reach them (choose a bank).
//
// A row is not a link: it opens nothing. What can be done to a row is a button in it.
import { useEffect, useId, useState } from 'react'
import '../memory-area.css'
import { useConsolidateNow, useOperationLists, useRetryOne } from '../operations-data'
import {
  MERGE_PER_BANK,
  OPERATION_FILTERS,
  PAGE_SIZE,
  banksForFilter,
  clampOffset,
  filterStatus,
  mergeOperationLists,
  nextTryText,
  operationStatus,
  operationType,
  pageWindow,
  plainError,
  plural,
  retriedText,
  shortError,
} from '../operations-pure'
import { EmptyState } from '../shared/EmptyState.jsx'
import { GoLink } from '../shared/GoLink.jsx'
import { ReasonedButton } from '../shared/ReasonedButton.jsx'
import { Segmented } from '../shared/Segmented.jsx'
import { StatusChip } from '../shared/StatusChip.jsx'
import { formatCount, formatDateTime, formatRelativeTime } from '../shared/format-pure'
import { RETRY_BLOCKED } from './FailedCard.jsx'

const EMPTY_WORD = { waiting: 'waiting', running: 'running', failed: 'failed', done: 'finished' }

function Missing() {
  return (
    <span className="ma-missing" role="img" aria-label="not reported">
      –
    </span>
  )
}

function SkeletonRows() {
  return (
    <div className="ma-card-body" aria-busy="true" data-testid="ma-ops-list-loading">
      {Array.from({ length: 5 }, (_, at) => (
        <span className="ma-skel ma-skel-line" aria-hidden="true" key={at} />
      ))}
      <span className="sr-only">Loading</span>
    </div>
  )
}

// A failed row's error: one line, and the whole message behind a control when there is more.
function RowError({ error }) {
  const textId = useId()
  const [open, setOpen] = useState(false)
  return (
    <div className="ma-ops-error">
      <span className="ma-ops-error-text" id={textId} data-testid="ma-ops-error-text">
        {open ? error.full : error.line}
      </span>
      {error.more && (
        <button
          type="button"
          className="btn ghost sm"
          aria-expanded={open}
          aria-controls={textId}
          onClick={() => setOpen((was) => !was)}
          data-testid="ma-ops-error-toggle"
        >
          {open ? 'Show less' : 'Show full error'}
        </button>
      )}
    </div>
  )
}

function Row({ row, now, blocked, blockedId, note, onRetry }) {
  const type = operationType(row)
  const status = operationStatus(row.status)
  const queued = formatRelativeTime(row.created_at, now)
  const error = status.kind === 'failed' ? shortError(row.error_message) : null
  const retried = retriedText(row.retry_count)
  const scheduled = status.kind === 'waiting' || status.kind === 'failed'
  const nextTry = scheduled ? nextTryText(row.next_retry_at, now) : null
  const pending = note?.kind === 'pending'
  return (
    <tr data-op={row.id} data-status={status.kind}>
      <th scope="row" title={type.raw || undefined}>
        {type.label}
      </th>
      <td className="ma-ops-bank">{row.bank}</td>
      <td className="ma-ops-when">
        {queued ? (
          <time dateTime={row.created_at} title={formatDateTime(row.created_at) ?? undefined}>
            {queued}
          </time>
        ) : (
          <Missing />
        )}
      </td>
      <td>
        <div className="ma-ops-status">
          <StatusChip tone={status.tone}>{status.word}</StatusChip>
          {retried && <span className="ma-ops-note">{retried}</span>}
          {nextTry && (
            <span className="ma-ops-note" title={formatDateTime(row.next_retry_at) ?? undefined}>
              {nextTry}
            </span>
          )}
        </div>
        {error && <RowError error={error} />}
        {note && note.kind !== 'pending' && (
          <p className="ma-ops-rownote" role="status" data-testid="ma-ops-row-note" data-kind={note.kind}>
            {note.text}
          </p>
        )}
      </td>
      <td className="ma-ops-act">
        {status.kind === 'failed' && (
          <button
            type="button"
            className="btn ghost sm"
            disabled={blocked || pending}
            aria-describedby={blocked ? blockedId : undefined}
            aria-label={`Retry: ${type.label}, queued ${queued ?? 'at an unknown time'}`}
            onClick={() => onRetry(row)}
            data-testid="ma-ops-retry"
          >
            {pending ? 'Retrying' : 'Retry'}
          </button>
        )}
      </td>
    </tr>
  )
}

function PageControls({ view, onOffset }) {
  if (view.pages <= 1) return null
  return (
    <span className="ma-ops-pages-buttons">
      {view.hasPrev && (
        <button
          type="button"
          className="btn ghost sm"
          onClick={() => onOffset(view.prevOffset)}
          data-testid="ma-ops-prev"
        >
          Previous
        </button>
      )}
      {view.hasNext && (
        <button
          type="button"
          className="btn ghost sm"
          onClick={() => onOffset(view.nextOffset)}
          data-testid="ma-ops-next"
        >
          Next
        </button>
      )}
    </span>
  )
}

function Consolidate({ bank, onChanged }) {
  const consolidate = useConsolidateNow()
  const [message, setMessage] = useState('')
  // What it says belongs to the bank it was said about.
  useEffect(() => setMessage(''), [bank])

  const run = () => {
    setMessage('')
    consolidate.mutate(bank, {
      onSuccess: () => {
        setMessage('Consolidation queued. It appears in this list as an operation.')
        onChanged([bank])
      },
      onError: (error) => setMessage(plainError(error, 'The engine did not accept the request to consolidate.')),
    })
  }

  return (
    <div className="ma-card-foot ma-ops-consolidate" data-testid="ma-ops-consolidate-row">
      <span className="ma-ops-foot-text">
        Consolidation is the engine&apos;s background step that turns stored facts into observations.
      </span>
      <ReasonedButton
        className="btn ghost sm"
        reason={bank ? null : 'Choose a bank to consolidate it.'}
        disabled={consolidate.isPending}
        onClick={run}
        data-testid="ma-ops-consolidate"
      >
        {consolidate.isPending ? 'Queueing' : 'Consolidate now'}
      </ReasonedButton>
      <span className="ma-ops-consolidate-status" role="status" data-testid="ma-ops-consolidate-status">
        {message}
      </span>
    </div>
  )
}

/**
 * @param {{
 *   bank: string|null,
 *   bankIds: string[],
 *   byBank: Array<object>|null,
 *   blocked: boolean,
 *   healthHref: string,
 *   onChanged: (bankIds: string[]) => void,
 * }} props `bank` is the scope: one bank, or null for every bank. `byBank` is the health
 *   answer's per-bank figures, used to leave quiet banks out of a waiting or failed read.
 *   `blocked` holds retry back. `onChanged` re-reads what an action changed.
 */
export function OperationsList({ bank, bankIds, byBank, blocked, healthHref, onChanged }) {
  const headingId = useId()
  const blockedId = useId()
  const [filter, setFilter] = useState('all')
  // The page belongs to the scope and filter it was turned in; a change of either starts over.
  const view = `${bank ?? ''}|${filter}`
  const [paging, setPaging] = useState({ view, offset: 0 })
  const offset = paging.view === view ? paging.offset : 0
  const setOffset = (next) => setPaging({ view, offset: next })
  const [notes, setNotes] = useState({})
  const retry = useRetryOne()

  const merged = bank === null
  const asked = merged ? banksForFilter(filter, bankIds, byBank) : [bank]
  const { entries, refetch } = useOperationLists(asked, {
    status: filterStatus(filter),
    limit: merged ? MERGE_PER_BANK : PAGE_SIZE,
    offset: merged ? 0 : offset,
  })

  const loading = entries.some((entry) => entry.isLoading)
  const failedBanks = entries.filter((entry) => entry.isError)
  const answered = entries.filter((entry) => !entry.isLoading && !entry.isError)
  const allFailed = entries.length > 0 && failedBanks.length === entries.length

  let rows = []
  let page = pageWindow(0, PAGE_SIZE, 0)
  let capped = null
  if (!loading && !allFailed) {
    if (merged) {
      const list = mergeOperationLists(answered)
      page = pageWindow(list.rows.length, PAGE_SIZE, offset)
      rows = list.rows.slice(page.from === 0 ? 0 : page.from - 1, page.to)
      if (list.capped) capped = { shown: list.rows.length, total: list.total }
    } else {
      const entry = answered[0]
      const total = entry?.total ?? offset + (entry?.operations.length ?? 0)
      page = pageWindow(total, PAGE_SIZE, offset)
      rows = (entry?.operations ?? []).map((op) => ({ ...op, bank }))
    }
  }

  // A page that emptied (its rows were retried or discarded) falls back to the last one left.
  const lastOffset = clampOffset(page.total, PAGE_SIZE, offset)
  useEffect(() => {
    if (!loading && !allFailed && offset !== lastOffset) setPaging({ view, offset: lastOffset })
  }, [loading, allFailed, offset, lastOffset, view])

  const retryOne = async (row) => {
    setNotes((was) => ({ ...was, [row.id]: { kind: 'pending' } }))
    try {
      await retry.mutateAsync({ bank: row.bank, id: row.id })
      setNotes((was) => ({ ...was, [row.id]: { kind: 'done', text: 'Queued again.' } }))
      onChanged([row.bank])
    } catch (error) {
      setNotes((was) => ({
        ...was,
        [row.id]: { kind: 'error', text: `Retry failed. ${plainError(error)}` },
      }))
    }
  }

  const now = Date.now()
  const anyFailedRow = rows.some((row) => operationStatus(row.status).kind === 'failed')
  const scope = bank ? <span className="ma-mono ma-em">{bank}</span> : 'any bank'

  let body
  if (loading) {
    body = <SkeletonRows />
  } else if (allFailed) {
    body = (
      <EmptyState
        title="The operations list did not load"
        action={
          <button type="button" className="btn ghost sm" onClick={refetch} data-testid="ma-ops-list-recheck">
            Check again
          </button>
        }
        data-testid="ma-ops-list-error"
      >
        {plainError(failedBanks[0]?.error, 'The engine did not answer the request for its operations.')}
      </EmptyState>
    )
  } else if (rows.length === 0 && filter !== 'all') {
    body = (
      <EmptyState
        title={`No ${EMPTY_WORD[filter]} operations`}
        action={
          <button type="button" className="btn ghost sm" onClick={() => setFilter('all')} data-testid="ma-ops-show-all">
            Show all
          </button>
        }
        data-testid="ma-ops-filter-empty"
      >
        Nothing in {scope} is {EMPTY_WORD[filter]} right now.
      </EmptyState>
    )
  } else if (rows.length === 0) {
    body = (
      <EmptyState
        title="The queue is empty"
        action={<GoLink href={healthHref}>Open Health</GoLink>}
        data-testid="ma-ops-list-empty"
      >
        Operations appear here while they wait or run: a document an agent sent, the engine&apos;s
        background consolidation, a knowledge refresh. One that fails stays listed until you retry or
        discard it.
      </EmptyState>
    )
  } else {
    body = (
      <>
        {blocked && anyFailedRow && (
          <p className="ma-helper ma-ops-blocked" id={blockedId} data-testid="ma-ops-list-blocked">
            {RETRY_BLOCKED}{' '}
            <a className="ma-link" href={healthHref}>
              Open Health
            </a>
          </p>
        )}
        <div className="ma-table-region" role="region" aria-label="Operations table" tabIndex={0}>
          <table className="ma-table ma-ops-table" data-testid="ma-ops-table">
            <thead>
              <tr>
                <th scope="col">Operation</th>
                <th scope="col">Bank</th>
                <th scope="col">Queued</th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <Row
                  key={`${row.bank}/${row.id}`}
                  row={row}
                  now={now}
                  blocked={blocked}
                  blockedId={blockedId}
                  note={notes[row.id]}
                  onRetry={retryOne}
                />
              ))}
            </tbody>
          </table>
        </div>
      </>
    )
  }

  const listed = !loading && !allFailed && rows.length > 0
  return (
    <section className="card ma-card" aria-labelledby={headingId} data-testid="ma-ops-list" data-filter={filter}>
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id={headingId}>
          Operations
        </h2>
        <span className="ma-card-meta">newest first</span>
        <span className="ma-card-gap" />
        <Segmented
          label="Operation status"
          options={OPERATION_FILTERS}
          value={filter}
          onChange={setFilter}
          inset
          data-testid="ma-ops-filter"
        />
      </div>
      {body}
      {listed && (
        <nav className="ma-card-foot ma-ops-pages" aria-label="Operations pages" data-testid="ma-ops-pages">
          <span data-testid="ma-ops-range">
            {formatCount(page.from)}–{formatCount(page.to)} of {plural(page.total, 'operation', 'operations')}
            {page.pages > 1 && ` · page ${formatCount(page.page)} of ${formatCount(page.pages)}`}
          </span>
          <PageControls view={page} onOffset={setOffset} />
        </nav>
      )}
      {listed && capped && (
        <div className="ma-card-foot" data-testid="ma-ops-capped">
          {capped.total === null
            ? `These are the newest ${plural(capped.shown, 'operation', 'operations')} across the banks. Older ones exist.`
            : `These are the newest ${formatCount(capped.shown)} of ${plural(capped.total, 'operation', 'operations')} across the banks.`}{' '}
          Choose a bank to page through all of its operations.
        </div>
      )}
      {!loading && !allFailed && failedBanks.length > 0 && (
        <div className="ma-card-foot" data-testid="ma-ops-banks-unread">
          {plural(failedBanks.length, 'bank', 'banks')} did not answer, so{' '}
          {failedBanks.length === 1 ? 'its operations are' : 'their operations are'} not in this list.
        </div>
      )}
      <Consolidate bank={bank} onChanged={onChanged} />
    </section>
  )
}
