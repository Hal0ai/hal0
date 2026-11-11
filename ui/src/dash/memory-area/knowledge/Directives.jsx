// The directives of the bank the scope bar names: standing instructions the engine gives to
// every agent that uses the bank.
//
// Each row is a directive as the engine holds it: its name, its text, its priority and whether
// it is active. A row can be switched on and off, edited in place, and deleted. Delete asks
// first, in the row, naming the directive; the other button keeps it. Add opens the same form
// as Edit, with the three fields the engine takes for a new one: name, text and priority.
import { useId, useState } from 'react'
import '../memory-area.css'
import {
  useDirectiveCreate,
  useDirectiveDelete,
  useDirectiveUpdate,
  useDirectives,
} from '@/api/hooks/useHindsight'
import {
  EMPTY_DIRECTIVE,
  directiveBody,
  directiveDraft,
  orderDirectives,
  shortError,
} from '../knowledge-pure'
import { EmptyState } from '../shared/EmptyState.jsx'
import { InlineConfirm } from '../shared/InlineConfirm.jsx'
import { formatCount } from '../shared/format-pure'

function DirectiveForm({ initial, submitLabel, busy, onSubmit, onCancel, testId }) {
  const nameId = useId()
  const textId = useId()
  const priorityId = useId()
  const priorityHelpId = useId()
  const [draft, setDraft] = useState(initial)
  const [problem, setProblem] = useState(null)
  const set = (key) => (event) => setDraft((d) => ({ ...d, [key]: event.target.value }))

  const submit = async (event) => {
    event.preventDefault()
    const checked = directiveBody(draft)
    if (!checked.ok) {
      setProblem(checked.reason)
      return
    }
    setProblem(null)
    try {
      await onSubmit(checked.body)
    } catch (error) {
      setProblem(`Not saved. ${shortError(error)}`)
    }
  }

  return (
    <form className="ma-kn-form" onSubmit={submit} data-testid={testId} noValidate>
      <div className="ma-kn-field">
        <label className="ma-eyebrow" htmlFor={nameId}>
          Name
        </label>
        <input id={nameId} className="input" type="text" value={draft.name} onChange={set('name')} />
      </div>
      <div className="ma-kn-field">
        <label className="ma-eyebrow" htmlFor={textId}>
          Text given to agents
        </label>
        <textarea
          id={textId}
          className="input ma-kn-textarea"
          rows={3}
          value={draft.content}
          onChange={set('content')}
        />
      </div>
      <div className="ma-kn-field ma-kn-field-narrow">
        <label className="ma-eyebrow" htmlFor={priorityId}>
          Priority
        </label>
        <input
          id={priorityId}
          className="input mono"
          type="number"
          step="1"
          inputMode="numeric"
          value={draft.priority}
          aria-describedby={priorityHelpId}
          onChange={set('priority')}
        />
        <p className="ma-helper" id={priorityHelpId}>
          A whole number. Higher numbers are given to agents first.
        </p>
      </div>
      {problem && (
        <p className="ma-helper ma-kn-error" role="alert" data-testid="ma-kn-directive-form-error">
          {problem}
        </p>
      )}
      <div className="ma-kn-actions">
        <button type="submit" className="btn ghost" disabled={busy} data-testid="ma-kn-directive-save">
          {busy ? 'Saving…' : submitLabel}
        </button>
        <button type="button" className="btn ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

function DirectiveRow({ bank, directive }) {
  const update = useDirectiveUpdate()
  const remove = useDirectiveDelete()
  const [mode, setMode] = useState('read') // 'read' | 'edit' | 'confirm'
  const [problem, setProblem] = useState(null)
  const [gone, setGone] = useState(false)
  const active = directive.is_active !== false

  const toggle = async () => {
    setProblem(null)
    try {
      await update.mutateAsync({ bank, id: directive.id, body: { is_active: !active } })
    } catch (error) {
      setProblem(`Not changed. ${shortError(error)}`)
    }
  }

  const confirmDelete = async () => {
    setProblem(null)
    try {
      await remove.mutateAsync({ bank, id: directive.id })
      // It is deleted. The list is refetched after this; until it arrives the row is not
      // drawn, so the question cannot be answered twice.
      setGone(true)
    } catch (error) {
      setMode('read')
      setProblem(`Not deleted. ${shortError(error)}`)
    }
  }

  if (gone) return null

  if (mode === 'edit') {
    return (
      <li className="ma-kn-directive" data-testid={`ma-kn-directive-${directive.id}`} data-mode="edit">
        <DirectiveForm
          initial={directiveDraft(directive)}
          submitLabel="Save"
          busy={update.isPending}
          testId="ma-kn-directive-edit-form"
          onCancel={() => setMode('read')}
          onSubmit={async (body) => {
            await update.mutateAsync({ bank, id: directive.id, body })
            setMode('read')
          }}
        />
      </li>
    )
  }

  return (
    <li className="ma-kn-directive" data-testid={`ma-kn-directive-${directive.id}`} data-mode={mode}>
      <div className="ma-kn-directive-main">
        <span className="ma-kn-directive-name" data-testid="ma-kn-directive-name">
          {directive.name}
        </span>
        <p className="ma-kn-directive-text" data-testid="ma-kn-directive-text">
          {directive.content}
        </p>
        <span className="ma-kn-directive-meta">
          <span data-testid="ma-kn-directive-priority">
            priority {formatCount(directive.priority ?? 0)}
          </span>
          {(directive.tags ?? []).map((tag) => (
            <span className="chip ma-kn-tag" key={tag}>
              {tag}
            </span>
          ))}
        </span>
      </div>
      {mode === 'confirm' ? (
        <div className="ma-kn-directive-confirm">
          <InlineConfirm
            verb="Delete"
            noun={`the directive “${directive.name}”`}
            dismissLabel="Keep it"
            busy={remove.isPending}
            onConfirm={confirmDelete}
            onDismiss={() => setMode('read')}
            data-testid="ma-kn-directive-confirm"
          >
            Agents that use this bank stop being given it. This cannot be undone.
          </InlineConfirm>
        </div>
      ) : (
        <div className="ma-kn-directive-actions">
          <label className="ma-kn-check">
            <input
              type="checkbox"
              checked={active}
              disabled={update.isPending}
              onChange={toggle}
              aria-label={`Active: ${directive.name}`}
              data-testid="ma-kn-directive-active"
            />
            <span>Active</span>
          </label>
          <button
            type="button"
            className="btn ghost sm"
            aria-label={`Edit the directive ${directive.name}`}
            onClick={() => {
              setProblem(null)
              setMode('edit')
            }}
            data-testid="ma-kn-directive-edit"
          >
            Edit
          </button>
          <button
            type="button"
            className="btn ghost sm"
            aria-label={`Delete the directive ${directive.name}`}
            onClick={() => {
              setProblem(null)
              setMode('confirm')
            }}
            data-testid="ma-kn-directive-delete"
          >
            Delete
          </button>
        </div>
      )}
      {problem && (
        <p className="ma-helper ma-kn-error ma-kn-directive-problem" role="alert" data-testid="ma-kn-directive-error">
          {problem}
        </p>
      )}
    </li>
  )
}

/** @param {{ bank: string }} props */
export function Directives({ bank }) {
  const headingId = useId()
  const query = useDirectives(bank)
  const create = useDirectiveCreate()
  const [adding, setAdding] = useState(false)

  const loading = query.data === undefined && !query.isError
  const failed = !!query.isError && query.data === undefined
  const items = orderDirectives(query.data?.items)
  const on = items.filter((d) => d.is_active !== false).length

  let body
  if (loading) {
    body = (
      <div className="ma-card-body" aria-busy="true" data-testid="ma-kn-directives-loading">
        <span className="ma-skel ma-skel-line" aria-hidden="true" />
        <span className="ma-skel ma-skel-line" aria-hidden="true" />
        <span className="sr-only">Loading</span>
      </div>
    )
  } else if (failed) {
    body = (
      <EmptyState
        title="The directives did not load"
        action={
          <button type="button" className="btn ghost sm" onClick={() => query.refetch()}>
            Check again
          </button>
        }
        data-testid="ma-kn-directives-error"
      >
        {shortError(query.error)}
      </EmptyState>
    )
  } else if (items.length === 0 && !adding) {
    body = (
      <EmptyState title="No directives for this bank" data-testid="ma-kn-directives-empty">
        A directive is a standing instruction given to every agent that uses this bank, such as “never
        repeat a password”. Add one above.
      </EmptyState>
    )
  } else {
    body = (
      <ul className="ma-kn-directives" data-testid="ma-kn-directives-list">
        {items.map((directive) => (
          <DirectiveRow bank={bank} directive={directive} key={directive.id} />
        ))}
      </ul>
    )
  }

  return (
    <section className="card ma-card" aria-labelledby={headingId} data-testid="ma-kn-directives">
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id={headingId}>
          Directives
        </h2>
        <span className="ma-card-gap" />
        {!loading && !failed && (
          <span className="ma-card-meta" data-testid="ma-kn-directives-meta">
            <span className="ma-mono">{bank}</span> · {formatCount(items.length)}{' '}
            {items.length === 1 ? 'directive' : 'directives'}
            {items.length > 0 && `, ${formatCount(on)} active`}
          </span>
        )}
        {!adding && !failed && !loading && (
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => setAdding(true)}
            data-testid="ma-kn-directive-add"
          >
            Add a directive
          </button>
        )}
      </div>
      {adding && (
        <div className="ma-kn-add">
          <DirectiveForm
            initial={EMPTY_DIRECTIVE}
            submitLabel="Add the directive"
            busy={create.isPending}
            testId="ma-kn-directive-add-form"
            onCancel={() => setAdding(false)}
            onSubmit={async (fields) => {
              await create.mutateAsync({ bank, body: fields })
              setAdding(false)
            }}
          />
        </div>
      )}
      {body}
    </section>
  )
}
