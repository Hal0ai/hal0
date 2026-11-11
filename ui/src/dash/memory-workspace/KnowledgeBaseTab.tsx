// hal0 v3 dashboard — Memory workspace: Knowledge base tab.
import React, { useMemo, useState } from 'react'
import { useInvalidateMemoryEngine, useMemoryEngineMutation, useMemoryEngineQuery } from '@/api/hooks/useMemoryWorkspace'
import { pageIsStale } from './memory-workspace-pure'
import { Card, EngineError } from './shared'

interface KbNode {
  id: string
  kind: 'folder' | 'page'
  name: string
  children?: KbNode[]
  mental_model_id?: string | null
}

function flattenPages(nodes: KbNode[] | undefined, depth = 0, out: { node: KbNode; depth: number }[] = []) {
  for (const n of nodes || []) {
    out.push({ node: n, depth })
    if (n.children) flattenPages(n.children, depth + 1, out)
  }
  return out
}

export const KnowledgeBaseTab: React.FC<{ bank: string }> = ({ bank }) => {
  const tree = useMemoryEngineQuery<{ roots: KbNode[] }>(`v1/default/banks/${encodeURIComponent(bank)}/knowledge-base/tree`)
  const [pageId, setPageId] = useState<string | null>(null)
  const page = useMemoryEngineQuery<{
    id: string
    name: string
    content?: string | null
    last_refreshed_at?: string | null
    is_stale?: boolean
    source_query?: string | null
    tags?: string[]
    trigger?: string | null
  }>(`v1/default/banks/${encodeURIComponent(bank)}/knowledge-base/pages/${pageId}`, { enabled: !!pageId })

  useInvalidateMemoryEngine()
  const refreshMutation = useMemoryEngineMutation('post', 'knowledge-base')
  const saveMutation = useMemoryEngineMutation('patch', 'knowledge-base')

  const rows = useMemo(() => flattenPages(tree.data?.roots), [tree.data])
  const [draft, setDraft] = useState<{ source_query: string; tags: string; trigger: string } | null>(null)

  if (tree.isError) return <EngineError error={tree.error} what="knowledge-base tree" onRetry={() => tree.refetch()} />

  const openPage = (id: string) => {
    setPageId(id)
    setDraft(null)
  }

  const startEdit = () => {
    if (!page.data) return
    setDraft({
      source_query: page.data.source_query || '',
      tags: (page.data.tags || []).join(', '),
      trigger: page.data.trigger || '',
    })
  }

  const save = () => {
    if (!pageId || !draft) return
    saveMutation.mutate(
      {
        sub: `v1/default/banks/${bank}/knowledge-base/nodes/${pageId}`,
        body: {
          source_query: draft.source_query || null,
          tags: draft.tags.split(',').map((t) => t.trim()).filter(Boolean),
          trigger: draft.trigger || null,
        },
      },
      { onSuccess: () => setDraft(null) },
    )
  }

  return (
    <div className="memws-kb" data-testid="memws-kb">
      <Card title="Pages">
        <ul className="memws-tree mono small" data-testid="memws-kb-tree">
          {rows.map(({ node, depth }) => (
            <li
              key={node.id}
              style={{ paddingLeft: depth * 14 }}
              className={node.kind === 'page' ? 'memws-tree-page' : 'memws-tree-folder'}
            >
              {node.kind === 'page' ? (
                <button
                  className="btn ghost sm"
                  data-testid={`memws-kb-page-${node.id}`}
                  onClick={() => openPage(node.id)}
                >
                  {node.name}
                </button>
              ) : (
                <span>{node.name}/</span>
              )}
            </li>
          ))}
        </ul>
      </Card>
      <Card title={page.data?.name || 'Select a page'}>
        {!pageId ? (
          <div className="empty mono">Pick a page from the tree.</div>
        ) : page.isError ? (
          <EngineError error={page.error} what="page" onRetry={() => page.refetch()} />
        ) : (
          <>
            <div className="mono small" data-testid="memws-kb-meta">
              last refreshed: {page.data?.last_refreshed_at || '—'}
              {pageIsStale(page.data) ? (
                <span className="badge badge-warn" style={{ marginLeft: 8 }}>stale</span>
              ) : null}
            </div>
            <div className="memws-pre" data-testid="memws-kb-content">
              {page.data?.content || <span className="mono small">(no content yet)</span>}
            </div>
            <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
              <button
                className="btn sm"
                data-testid="memws-kb-refresh"
                disabled={refreshMutation.isPending}
                onClick={() =>
                  refreshMutation.mutate({ sub: `v1/default/banks/${bank}/mental-models/${rows.find((r) => r.node.id === pageId)?.node.mental_model_id}/refresh` })
                }
              >
                Refresh
              </button>
              {!draft ? (
                <button className="btn ghost sm" onClick={startEdit} data-testid="memws-kb-edit">
                  Edit definition
                </button>
              ) : null}
            </div>
            {draft ? (
              <div className="memws-form" data-testid="memws-kb-form">
                <label>
                  Source query
                  <textarea
                    value={draft.source_query}
                    onChange={(e) => setDraft({ ...draft, source_query: e.target.value })}
                  />
                </label>
                <label>
                  Tags (comma-separated)
                  <input value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} />
                </label>
                <label>
                  Trigger
                  <input value={draft.trigger} onChange={(e) => setDraft({ ...draft, trigger: e.target.value })} />
                </label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn sm" onClick={save} disabled={saveMutation.isPending} data-testid="memws-kb-save">
                    Save
                  </button>
                  <button className="btn ghost sm" onClick={() => setDraft(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : null}
          </>
        )}
      </Card>
    </div>
  )
}

export default KnowledgeBaseTab
