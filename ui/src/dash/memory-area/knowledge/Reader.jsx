// The reader: the one place a knowledge page or a mental model is read.
//
// Both are text the engine wrote from the bank's facts to answer one question, so both read the
// same way: the title, whether the text is current, the question, the tags that choose its
// facts, how it refreshes, then the text. A page can also be edited (its question and tags); a
// model also shows its history, and any earlier text can be read in place of the current one.
//
// Refresh is asked of the engine and finishes later: the request only queues it. The tab keeps
// watch and passes what it knows here as `run` (starting, running, done, failed, overdue, or
// the reason the request itself failed).
import { useId, useState } from 'react'
import '../memory-area.css'
import {
  useKnowledgePage,
  useMentalModelHistory,
  useMentalModelUpdate,
} from '@/api/hooks/useHindsight'
import {
  historyEntries,
  latestFailureReason,
  pageBody,
  parseTags,
  shortError,
  triggerWords,
} from '../knowledge-pure'
import { EmptyState } from '../shared/EmptyState.jsx'
import { GoLink } from '../shared/GoLink.jsx'
import { NoticeBar } from '../shared/NoticeBar.jsx'
import { ReasonedButton } from '../shared/ReasonedButton.jsx'
import { StatusChip } from '../shared/StatusChip.jsx'
import { formatDate, formatDateTime, formatRelativeTime } from '../shared/format-pure'
import { Markdown } from './Markdown.jsx'

/** `6 h ago (5 Nov 2026, 09:14)`; the date alone once "ago" would no longer help. */
function when(at) {
  const exact = formatDateTime(at)
  if (!exact) return null
  const relative = formatRelativeTime(at)
  return relative && relative !== formatDate(at) ? `${relative} (${exact})` : exact
}

function SkeletonLines({ count = 4 }) {
  return (
    <div className="ma-kn-text" aria-busy="true">
      {Array.from({ length: count }, (_, at) => (
        <span className="ma-skel ma-skel-line" aria-hidden="true" key={at} />
      ))}
      <span className="sr-only">Loading</span>
    </div>
  )
}

function Shell({ titleId, kind, chip, children, testId }) {
  return (
    <article
      className="card ma-card ma-kn-reader"
      aria-labelledby={titleId}
      data-testid={testId ?? 'ma-kn-reader'}
      data-kind={kind ?? undefined}
    >
      <div className="ma-card-head">
        <h2 className="ma-eyebrow">Reader</h2>
        <span className="ma-card-gap" />
        {kind && <span className="ma-card-meta">{kind}</span>}
        {chip}
      </div>
      {children}
    </article>
  )
}

/** The chip in the reader's header. Stale and failed are said by the notice under it instead. */
function headerChip(row, run, hasText) {
  if (run?.phase === 'starting' || run?.phase === 'running') return <StatusChip tone="info">running</StatusChip>
  if (row.failed || row.stale) return null
  if (!row.lastRefreshedAt && !hasText) return <StatusChip tone="neutral">empty</StatusChip>
  return <StatusChip tone="ok">fresh</StatusChip>
}

function StatusNotice({ row, noun, reason }) {
  if (row.failed) {
    const failedOn = formatDateTime(row.lastFailedAt)
    const shownAsOf = formatDate(row.lastRefreshedAt)
    return (
      <NoticeBar inCard tone="err" status="failed" data-testid="ma-kn-notice">
        The last refresh failed{failedOn ? ` on ${failedOn}` : ''}.{' '}
        {shownAsOf ? `Shown as of ${shownAsOf} until a refresh succeeds. ` : 'No refresh has succeeded yet. '}
        Automatic refreshes of this {noun} are paused until one does.
        {reason && (
          <>
            {' '}
            <span data-testid="ma-kn-failure-reason">The engine said: {reason}</span>
          </>
        )}
      </NoticeBar>
    )
  }
  if (row.stale) {
    return (
      <NoticeBar inCard tone="warn" status="stale" data-testid="ma-kn-notice">
        Facts in its scope were saved after its last refresh. A refresh brings this {noun} up to date.
      </NoticeBar>
    )
  }
  return null
}

function Details({ row, trigger }) {
  const refreshed = when(row.lastRefreshedAt)
  const sentences = triggerWords(trigger)
  return (
    <dl className="ma-kv ma-kn-details" data-testid="ma-kn-details">
      <div className="ma-kv-row">
        <dt>Last refreshed</dt>
        <dd className={refreshed ? undefined : 'ma-kv-unknown'} data-testid="ma-kn-refreshed">
          {refreshed ?? 'Never: no refresh has succeeded yet.'}
        </dd>
      </div>
      <div className="ma-kv-row">
        <dt>Question</dt>
        <dd className={row.question ? undefined : 'ma-kv-unknown'} data-testid="ma-kn-question">
          {row.question ?? 'Not reported.'}
        </dd>
      </div>
      <div className="ma-kv-row">
        <dt>Tags</dt>
        <dd className={row.tags.length > 0 ? 'ma-kn-tags' : 'ma-kv-unknown'} data-testid="ma-kn-tags">
          {row.tags.length > 0
            ? row.tags.map((tag) => (
                <span className="chip ma-kn-tag" key={tag}>
                  {tag}
                </span>
              ))
            : 'None: written from every fact in the bank.'}
        </dd>
      </div>
      {sentences.length > 0 && (
        <div className="ma-kv-row">
          <dt>Refreshes</dt>
          <dd data-testid="ma-kn-trigger">{sentences.join(' ')}</dd>
        </div>
      )}
    </dl>
  )
}

function RunStatus({ run, operationsHref }) {
  let message = null
  let link = null
  if (run?.phase === 'done') message = 'The refresh finished.'
  else if (run?.phase === 'failed') message = 'The refresh failed.'
  else if (run?.phase === 'overdue') {
    message = 'The refresh has not finished after 10 min. It is listed under Operations.'
    link = <GoLink href={operationsHref}>Open Operations</GoLink>
  }
  return (
    <>
      {/* Present from the start, so what appears in it later is announced. */}
      <p className="ma-helper ma-kn-status" role="status" data-testid="ma-kn-run-status">
        {message}
        {link && <> {link}</>}
      </p>
      {run?.phase === 'error' && (
        <p className="ma-helper ma-kn-status ma-kn-error" role="alert" data-testid="ma-kn-run-error">
          The refresh did not start. {run.error}
        </p>
      )}
    </>
  )
}

function refreshReason({ blockedReason, modelId, run }) {
  if (blockedReason) return blockedReason
  if (!modelId) return 'This page has no mental model to refresh it from.'
  if (run?.phase === 'starting' || run?.phase === 'running') {
    return 'A refresh is running. This updates when it finishes.'
  }
  return null
}

function PageEditForm({ bank, page, onClose }) {
  const update = useMentalModelUpdate()
  const questionId = useId()
  const tagsId = useId()
  const tagsHelpId = useId()
  const [question, setQuestion] = useState(page.question ?? '')
  const [tags, setTags] = useState(page.tags.join(', '))
  const [problem, setProblem] = useState(null)

  const save = async (event) => {
    event.preventDefault()
    const nextQuestion = question.trim()
    if (!nextQuestion) {
      setProblem('Write the question this page answers.')
      return
    }
    const nextTags = parseTags(tags)
    const body = {}
    if (nextQuestion !== (page.question ?? '')) body.source_query = nextQuestion
    if (nextTags.join('\n') !== page.tags.join('\n')) body.tags = nextTags
    if (Object.keys(body).length === 0) {
      onClose(null)
      return
    }
    setProblem(null)
    try {
      await update.mutateAsync({ bank, id: page.modelId, body })
      // Saving changes what the page is written from, not its text: that takes a refresh.
      onClose('Saved. Refresh the page to rewrite it from the new question and tags.')
    } catch (error) {
      setProblem(`Not saved. ${shortError(error)}`)
    }
  }

  return (
    <form className="ma-kn-form" onSubmit={save} data-testid="ma-kn-edit-form" noValidate>
      <div className="ma-kn-field">
        <label className="ma-eyebrow" htmlFor={questionId}>
          Question
        </label>
        <textarea
          id={questionId}
          className="input ma-kn-textarea"
          rows={4}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
      </div>
      <div className="ma-kn-field">
        <label className="ma-eyebrow" htmlFor={tagsId}>
          Tags, separated by commas
        </label>
        <input
          id={tagsId}
          className="input mono"
          type="text"
          value={tags}
          aria-describedby={tagsHelpId}
          onChange={(event) => setTags(event.target.value)}
        />
        <p className="ma-helper" id={tagsHelpId}>
          Tags choose which facts the page is written from. With no tags it is written from the whole bank.
        </p>
      </div>
      {problem && (
        <p className="ma-helper ma-kn-error" role="alert" data-testid="ma-kn-edit-error">
          {problem}
        </p>
      )}
      <div className="ma-kn-actions">
        <button type="submit" className="btn ghost" disabled={update.isPending} data-testid="ma-kn-edit-save">
          {update.isPending ? 'Saving…' : 'Save'}
        </button>
        <button type="button" className="btn ghost" onClick={() => onClose(null)} data-testid="ma-kn-edit-cancel">
          Cancel
        </button>
      </div>
    </form>
  )
}

function PageReader({ bank, page, run, onRefresh, blockedReason, operationsHref }) {
  const titleId = useId()
  const pageQuery = useKnowledgePage(bank, page.id)
  // Why a refresh failed is in the model's history, and is only asked for when one did.
  const historyQuery = useMentalModelHistory(bank, page.modelId, { enabled: page.failed })
  const [editing, setEditing] = useState(false)
  const [saved, setSaved] = useState(null)

  const loading = pageQuery.data === undefined && !pageQuery.isError
  const body = pageBody(pageQuery.data)
  const reason = page.failed ? latestFailureReason(historyEntries(historyQuery.data?.items)) : null
  const editReason = blockedReason ?? (page.modelId ? null : 'This page has no mental model to edit.')

  let content
  if (loading) content = <SkeletonLines />
  else if (pageQuery.isError && pageQuery.data === undefined) {
    content = (
      <EmptyState
        title="This page did not load"
        action={
          <button type="button" className="btn ghost sm" onClick={() => pageQuery.refetch()}>
            Check again
          </button>
        }
        data-testid="ma-kn-page-error"
      >
        The engine listed the page but did not answer with its text. {shortError(pageQuery.error)}
      </EmptyState>
    )
  } else if (!body) {
    content = (
      <p className="ma-body ma-kn-text" data-testid="ma-kn-no-text">
        No text yet. The first refresh that succeeds writes it.
      </p>
    )
  } else content = <Markdown text={body} data-testid="ma-kn-content" />

  return (
    <Shell titleId={titleId} kind="knowledge page" chip={headerChip(page, run, !!body)}>
      <StatusNotice row={page} noun="page" reason={reason} />
      <div className="ma-kn-reader-top">
        <h3 className="ma-item-title ma-kn-title" id={titleId} data-testid="ma-kn-title">
          {page.name}
        </h3>
        <div className="ma-kn-actions">
          <ReasonedButton
            className="btn ghost sm"
            reason={refreshReason({ blockedReason, modelId: page.modelId, run })}
            onClick={onRefresh}
            data-testid="ma-kn-refresh"
          >
            {run?.phase === 'starting' || run?.phase === 'running' ? 'Refreshing…' : 'Refresh'}
          </ReasonedButton>
          {!editing && (
            <ReasonedButton
              className="btn ghost sm"
              reason={editReason}
              onClick={() => {
                setSaved(null)
                setEditing(true)
              }}
              data-testid="ma-kn-edit"
            >
              Edit
            </ReasonedButton>
          )}
        </div>
        <RunStatus run={run} operationsHref={operationsHref} />
        <p className="ma-helper ma-kn-status" role="status" data-testid="ma-kn-edit-saved">
          {saved}
        </p>
      </div>
      {editing ? (
        <PageEditForm
          bank={bank}
          page={page}
          onClose={(message) => {
            setEditing(false)
            setSaved(message)
          }}
        />
      ) : (
        <Details row={page} trigger={page.trigger} />
      )}
      {content}
    </Shell>
  )
}

/** The mark of a row that opens something. The open row carries the word "open" instead. */
function RowChevron() {
  return (
    <svg
      className="ma-kn-row-chevron"
      aria-hidden="true"
      width="11"
      height="11"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M6 4l4 4-4 4" />
    </svg>
  )
}

function History({ entries, loading, failed, openKey, onOpen }) {
  const headingId = useId()
  let body
  if (loading) body = <SkeletonLines count={2} />
  else if (failed) {
    body = (
      <p className="ma-body ma-kn-text" data-testid="ma-kn-history-error">
        The history did not load.
      </p>
    )
  } else if (entries.length === 0) {
    body = (
      <p className="ma-body ma-kn-text" data-testid="ma-kn-history-empty">
        No earlier text: this model has not been rewritten since it was first written.
      </p>
    )
  } else {
    body = (
      <ul className="ma-kn-history-list" data-testid="ma-kn-history-list">
        <li>
          <button
            type="button"
            className="ma-row"
            aria-current={openKey === null ? 'true' : 'false'}
            onClick={() => onOpen(null)}
            data-testid="ma-kn-history-current"
          >
            <span className="ma-kn-row-main">Current text</span>
            {openKey === null ? <span className="ma-row-open">open</span> : <RowChevron />}
          </button>
        </li>
        {entries.map((entry) => {
          const date = formatDateTime(entry.at) ?? 'date not reported'
          if (entry.kind === 'version') {
            const open = entry.key === openKey
            return (
              <li key={entry.key}>
                <button
                  type="button"
                  className="ma-row"
                  aria-current={open ? 'true' : 'false'}
                  onClick={() => onOpen(entry.key)}
                  data-testid="ma-kn-history-version"
                >
                  <span className="ma-kn-row-main">Text replaced on {date}</span>
                  {open ? <span className="ma-row-open">open</span> : <RowChevron />}
                </button>
              </li>
            )
          }
          // A failed refresh and the first write open nothing: there is no earlier text.
          return (
            <li className="ma-kn-history-note" key={entry.key} data-testid={`ma-kn-history-${entry.kind}`}>
              {entry.kind === 'failure' ? (
                <>
                  <span className="ma-kn-row-main">
                    Refresh failed on {date}
                    {entry.reason && <span className="ma-kn-row-sub">{entry.reason}</span>}
                  </span>
                  <StatusChip tone="err">failed</StatusChip>
                </>
              ) : (
                <span className="ma-kn-row-main">First written on {date}</span>
              )}
            </li>
          )
        })}
      </ul>
    )
  }
  return (
    <section className="ma-kn-history" aria-labelledby={headingId} data-testid="ma-kn-history">
      <h4 className="ma-eyebrow ma-kn-history-head" id={headingId}>
        History
      </h4>
      {body}
    </section>
  )
}

function ModelReader({ bank, model, run, onRefresh, blockedReason, operationsHref }) {
  const titleId = useId()
  const historyQuery = useMentalModelHistory(bank, model.id)
  const [versionKey, setVersionKey] = useState(null)

  const entries = historyEntries(historyQuery.data?.items)
  const version = versionKey ? entries.find((entry) => entry.key === versionKey && entry.kind === 'version') : null
  const reason = model.failed ? latestFailureReason(entries) : null
  const text = version ? version.content : model.content

  return (
    <Shell titleId={titleId} kind="mental model" chip={headerChip(model, run, !!model.content)}>
      <StatusNotice row={model} noun="model" reason={reason} />
      <div className="ma-kn-reader-top">
        <h3 className="ma-item-title ma-kn-title" id={titleId} data-testid="ma-kn-title">
          {model.name}
        </h3>
        <div className="ma-kn-actions">
          <ReasonedButton
            className="btn ghost sm"
            reason={refreshReason({ blockedReason, modelId: model.id, run })}
            onClick={onRefresh}
            data-testid="ma-kn-refresh"
          >
            {run?.phase === 'starting' || run?.phase === 'running' ? 'Refreshing…' : 'Refresh'}
          </ReasonedButton>
        </div>
        <RunStatus run={run} operationsHref={operationsHref} />
      </div>
      <Details row={model} trigger={model.trigger} />
      {version && (
        <p className="ma-helper ma-kn-version-note" data-testid="ma-kn-version-note">
          Showing the text that was replaced on {formatDateTime(version.at) ?? 'a date that was not reported'},
          not the current text.
        </p>
      )}
      {text ? (
        <Markdown text={text} data-testid="ma-kn-content" />
      ) : (
        <p className="ma-body ma-kn-text" data-testid="ma-kn-no-text">
          No text yet. The first refresh that succeeds writes it.
        </p>
      )}
      <History
        entries={entries}
        loading={historyQuery.data === undefined && !historyQuery.isError}
        failed={!!historyQuery.isError && historyQuery.data === undefined}
        openKey={version ? version.key : null}
        onOpen={setVersionKey}
      />
    </Shell>
  )
}

/**
 * @param {{
 *   bank: string,
 *   selection: import('../knowledge-pure').Selection,
 *   loading: boolean,
 *   run?: {phase: string, error?: string},
 *   onRefresh: (target: {modelId: string, name: string, kind: string}) => void,
 *   blockedReason: string|null,
 *   operationsHref: string,
 * }} props `blockedReason` is why nothing can be refreshed now (the engine is not answering).
 */
export function Reader({ bank, selection, loading, run, onRefresh, blockedReason, operationsHref }) {
  const titleId = useId()
  if (loading) {
    return (
      <Shell titleId={titleId} testId="ma-kn-reader-loading">
        <span className="sr-only" id={titleId}>
          Reader
        </span>
        <SkeletonLines count={6} />
      </Shell>
    )
  }
  if (!selection || selection.kind === 'missing') {
    return (
      <Shell titleId={titleId} testId="ma-kn-reader-missing">
        <EmptyState title={<span id={titleId}>Nothing in this bank has that address</span>}>
          The address names a page or mental model that <span className="ma-mono ma-em">{bank}</span> does
          not hold. It may have been deleted. Choose one from the lists.
        </EmptyState>
      </Shell>
    )
  }
  if (selection.kind === 'page') {
    const page = selection.page
    return (
      <PageReader
        key={page.id}
        bank={bank}
        page={page}
        run={run}
        blockedReason={blockedReason}
        operationsHref={operationsHref}
        onRefresh={() => onRefresh({ modelId: page.modelId, name: page.name, kind: 'page' })}
      />
    )
  }
  const model = selection.model
  return (
    <ModelReader
      key={model.id}
      bank={bank}
      model={model}
      run={run}
      blockedReason={blockedReason}
      operationsHref={operationsHref}
      onRefresh={() => onRefresh({ modelId: model.id, name: model.name, kind: 'model' })}
    />
  )
}
