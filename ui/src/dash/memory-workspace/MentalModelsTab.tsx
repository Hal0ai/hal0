// hal0 v3 dashboard — Memory workspace: Mental models tab.
import React, { useState } from 'react'
import { useMemoryEngineMutation, useMemoryEngineQuery } from '@/api/hooks/useMemoryWorkspace'
import { Card, EngineError } from './shared'

interface MentalModel {
  id: string
  name: string
  content?: string | null
  source_query?: string | null
  trigger?: string | null
  is_stale?: boolean
  last_refreshed_at?: string | null
}

export const MentalModelsTab: React.FC<{ bank: string }> = ({ bank }) => {
  const list = useMemoryEngineQuery<{ items: MentalModel[] }>(`v1/default/banks/${encodeURIComponent(bank)}/mental-models`)
  const [selId, setSelId] = useState<string | null>(null)
  const detail = useMemoryEngineQuery<MentalModel>(
    `v1/default/banks/${encodeURIComponent(bank)}/mental-models/${selId}`,
    { enabled: !!selId },
  )
  const history = useMemoryEngineQuery<{ items: unknown[] }>(
    `v1/default/banks/${encodeURIComponent(bank)}/mental-models/${selId}/history`,
    { enabled: !!selId },
  )
  const refresh = useMemoryEngineMutation('post', 'mental-models')

  if (list.isError) return <EngineError error={list.error} what="mental models" onRetry={() => list.refetch()} />
  const items = list.data?.items || []

  return (
    <div className="memws-kb" data-testid="memws-mental-models">
      <Card title="Mental models">
        <ul className="mono small">
          {items.map((mm) => (
            <li key={mm.id}>
              <button className="btn ghost sm" data-testid={`memws-mm-${mm.id}`} onClick={() => setSelId(mm.id)}>
                {mm.name} {mm.is_stale ? <span className="badge badge-warn">stale</span> : null}
              </button>
            </li>
          ))}
        </ul>
      </Card>
      <Card title={detail.data?.name || 'Select a mental model'}>
        {!selId ? (
          <div className="empty mono">Pick a mental model.</div>
        ) : detail.isError ? (
          <EngineError error={detail.error} what="mental model" onRetry={() => detail.refetch()} />
        ) : (
          <>
            <div className="mono small">source query: {detail.data?.source_query || '—'}</div>
            <div className="mono small">trigger: {detail.data?.trigger || '—'}</div>
            <div className="mono small">last refreshed: {detail.data?.last_refreshed_at || '—'}</div>
            <div className="memws-pre">{detail.data?.content || '(no content yet)'}</div>
            <button
              className="btn sm"
              data-testid="memws-mm-refresh"
              disabled={refresh.isPending}
              onClick={() => refresh.mutate({ sub: `v1/default/banks/${bank}/mental-models/${selId}/refresh` })}
            >
              Refresh
            </button>
            <div className="mono small" style={{ marginTop: 10 }}>
              History ({(history.data?.items || []).length})
            </div>
          </>
        )}
      </Card>
    </div>
  )
}

export default MentalModelsTab
