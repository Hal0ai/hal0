// hal0 v3 dashboard — Memory workspace: Directives tab.
import React, { useState } from 'react'
import { useMemoryEngineMutation, useMemoryEngineQuery } from '@/api/hooks/useMemoryWorkspace'
import { EngineError } from './shared'

interface Directive {
  id: string
  name: string
  content: string
  priority: number
  is_active: boolean
  tags: string[]
}

const emptyDirectiveDraft = { name: '', content: '', priority: 0, is_active: true, tags: '' }

export const DirectivesTab: React.FC<{ bank: string }> = ({ bank }) => {
  const list = useMemoryEngineQuery<{ items: Directive[] }>(`v1/default/banks/${encodeURIComponent(bank)}/directives`)
  const create = useMemoryEngineMutation('post', 'directives')
  const update = useMemoryEngineMutation('patch', 'directives')
  const del = useMemoryEngineMutation('delete', 'directives')
  const [editingId, setEditingId] = useState<string | 'new' | null>(null)
  const [draft, setDraft] = useState(emptyDirectiveDraft)

  if (list.isError) return <EngineError error={list.error} what="directives" onRetry={() => list.refetch()} />
  const items = list.data?.items || []

  const beginEdit = (d?: Directive) => {
    if (d) {
      setEditingId(d.id)
      setDraft({ name: d.name, content: d.content, priority: d.priority, is_active: d.is_active, tags: d.tags.join(', ') })
    } else {
      setEditingId('new')
      setDraft(emptyDirectiveDraft)
    }
  }

  const submit = () => {
    const body = {
      name: draft.name,
      content: draft.content,
      priority: Number(draft.priority) || 0,
      is_active: draft.is_active,
      tags: draft.tags.split(',').map((t) => t.trim()).filter(Boolean),
    }
    if (editingId === 'new') {
      create.mutate({ sub: `v1/default/banks/${bank}/directives`, body }, { onSuccess: () => setEditingId(null) })
    } else if (editingId) {
      update.mutate(
        { sub: `v1/default/banks/${bank}/directives/${editingId}`, body },
        { onSuccess: () => setEditingId(null) },
      )
    }
  }

  return (
    <div data-testid="memws-directives">
      <button className="btn sm" data-testid="memws-directive-new" onClick={() => beginEdit()}>
        + New directive
      </button>
      <table className="tbl mono small">
        <thead><tr><th>name</th><th>priority</th><th>active</th><th>tags</th><th></th></tr></thead>
        <tbody>
          {items.map((d) => (
            <tr key={d.id} data-testid={`memws-directive-row-${d.id}`}>
              <td>{d.name}</td>
              <td>{d.priority}</td>
              <td>{d.is_active ? 'yes' : 'no'}</td>
              <td>{d.tags.join(', ')}</td>
              <td>
                <button className="btn ghost sm" onClick={() => beginEdit(d)}>Edit</button>
                <button
                  className="btn ghost sm"
                  onClick={() => del.mutate({ sub: `v1/default/banks/${bank}/directives/${d.id}` })}
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editingId ? (
        <div className="memws-form" data-testid="memws-directive-form">
          <label>Name<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
          <label>Content<textarea value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} /></label>
          <label>Priority<input type="number" value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) })} /></label>
          <label>
            <input type="checkbox" checked={draft.is_active} onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} /> Active
          </label>
          <label>Tags<input value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} /></label>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn sm" data-testid="memws-directive-save" onClick={submit}>Save</button>
            <button className="btn ghost sm" onClick={() => setEditingId(null)}>Cancel</button>
          </div>
        </div>
      ) : null}
    </div>
  )
}

export default DirectivesTab
