// The bank's mental models that are not knowledge pages: standing questions the engine keeps
// an answer ready for. A row opens the model in the reader. "Add a mental model" takes the two
// things a new one needs: a name and the question.
import { useId, useState } from 'react'
import '../memory-area.css'
import { useMentalModelCreate } from '@/api/hooks/useHindsight'
import { shortError } from '../knowledge-pure'
import { EmptyState } from '../shared/EmptyState.jsx'
import { StatusChip } from '../shared/StatusChip.jsx'
import { formatRelativeTime } from '../shared/format-pure'

/**
 * The form a new mental model is added with. Used in the Mental models card and in the empty
 * bank's card.
 *
 * @param {{ bank: string, onClose: (message: string|null) => void }} props `onClose` gets the
 *   line to show once the model is added, or `null` when the form was cancelled.
 */
export function ModelForm({ bank, onClose }) {
  const create = useMentalModelCreate()
  const nameId = useId()
  const questionId = useId()
  const questionHelpId = useId()
  const [name, setName] = useState('')
  const [question, setQuestion] = useState('')
  const [problem, setProblem] = useState(null)

  const submit = async (event) => {
    event.preventDefault()
    if (!name.trim()) {
      setProblem('Give the model a name.')
      return
    }
    if (!question.trim()) {
      setProblem('Write the question it answers.')
      return
    }
    setProblem(null)
    try {
      await create.mutateAsync({ bank, body: { name: name.trim(), source_query: question.trim() } })
      onClose('Added. The engine is writing its first text; it appears here when that finishes.')
    } catch (error) {
      setProblem(`Not added. ${shortError(error)}`)
    }
  }

  return (
    <form className="ma-kn-form" onSubmit={submit} data-testid="ma-kn-model-form" noValidate>
      <div className="ma-kn-field">
        <label className="ma-eyebrow" htmlFor={nameId}>
          Name
        </label>
        <input
          id={nameId}
          className="input"
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <div className="ma-kn-field">
        <label className="ma-eyebrow" htmlFor={questionId}>
          Question
        </label>
        <textarea
          id={questionId}
          className="input ma-kn-textarea"
          rows={3}
          value={question}
          aria-describedby={questionHelpId}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <p className="ma-helper" id={questionHelpId}>
          What should this bank keep an answer ready for? The engine writes the answer from the bank&apos;s
          facts and rewrites it as they change.
        </p>
      </div>
      {problem && (
        <p className="ma-helper ma-kn-error" role="alert" data-testid="ma-kn-model-form-error">
          {problem}
        </p>
      )}
      <div className="ma-kn-actions">
        <button type="submit" className="btn ghost" disabled={create.isPending} data-testid="ma-kn-model-save">
          {create.isPending ? 'Adding…' : 'Add the model'}
        </button>
        <button type="button" className="btn ghost" onClick={() => onClose(null)}>
          Cancel
        </button>
      </div>
    </form>
  )
}

function rowLine(model) {
  const refreshed = formatRelativeTime(model.lastRefreshedAt)
  const parts = [refreshed ? `refreshed ${refreshed}` : 'never refreshed']
  if (model.failed) {
    const failed = formatRelativeTime(model.lastFailedAt)
    parts.push(failed ? `last refresh failed ${failed}` : 'last refresh failed')
  }
  return parts.join(' · ')
}

/**
 * @param {{
 *   bank: string,
 *   models: Array<object>,
 *   status: {meta: string, rowStale: boolean, rowFailed: boolean},
 *   openId: string|null,
 *   onOpen: (id: string) => void,
 *   loading: boolean,
 *   error: unknown,
 *   onRetry: () => void,
 * }} props `error` is set when the list did not load.
 */
export function ModelsCard({ bank, models, status, openId, onOpen, loading, error, onRetry }) {
  const headingId = useId()
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState(null)

  let body
  if (loading) {
    body = (
      <div className="ma-card-body" aria-busy="true" data-testid="ma-kn-models-loading">
        <span className="ma-skel ma-skel-line" aria-hidden="true" />
        <span className="ma-skel ma-skel-line" aria-hidden="true" />
        <span className="sr-only">Loading</span>
      </div>
    )
  } else if (error) {
    body = (
      <EmptyState
        title="The mental models did not load"
        action={
          <button type="button" className="btn ghost sm" onClick={onRetry}>
            Check again
          </button>
        }
        data-testid="ma-kn-models-error"
      >
        {shortError(error)}
      </EmptyState>
    )
  } else if (models.length === 0) {
    body = (
      <p className="ma-body ma-kn-text" data-testid="ma-kn-models-empty">
        No mental models besides the knowledge pages. A mental model is a question this bank keeps an
        answer ready for.
      </p>
    )
  } else {
    body = (
      <ul className="ma-kn-list" data-testid="ma-kn-models">
        {models.map((model) => {
          const open = model.id === openId
          return (
            <li key={model.id}>
              <button
                type="button"
                className="ma-row"
                aria-current={open ? 'true' : 'false'}
                onClick={() => onOpen(model.id)}
                data-testid={`ma-kn-model-${model.id}`}
              >
                <span className="ma-kn-row-main">
                  {model.name}
                  <span className="ma-kn-row-sub">{rowLine(model)}</span>
                </span>
                {status.rowFailed && model.failed && <StatusChip tone="err">failed</StatusChip>}
                {status.rowStale && model.stale && <StatusChip tone="warn">stale</StatusChip>}
                {open ? (
                  <span className="ma-row-open">open</span>
                ) : (
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
                )}
              </button>
            </li>
          )
        })}
      </ul>
    )
  }

  return (
    <section className="card ma-card" aria-labelledby={headingId} data-testid="ma-kn-models-card">
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id={headingId}>
          Mental models
        </h2>
        <span className="ma-card-gap" />
        {!loading && !error && models.length > 0 && (
          <span className="ma-card-meta" data-testid="ma-kn-models-meta">
            {status.meta}
          </span>
        )}
      </div>
      <div className="ma-kn-scroll">{body}</div>
      {adding && (
        <div className="ma-kn-add">
          <ModelForm
            bank={bank}
            onClose={(message) => {
              setAdding(false)
              setAdded(message)
            }}
          />
        </div>
      )}
      <p className="ma-helper ma-kn-added" role="status" data-testid="ma-kn-model-added">
        {added}
      </p>
      {!loading && !error && !adding && (
        <div className="ma-card-foot">
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => {
              setAdded(null)
              setAdding(true)
            }}
            data-testid="ma-kn-model-add"
          >
            Add a mental model
          </button>
        </div>
      )}
    </section>
  )
}
