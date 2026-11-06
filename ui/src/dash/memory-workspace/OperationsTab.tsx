// hal0 v3 dashboard — Memory workspace: Operations tab.
import React, { useState } from 'react'
import { useMemoryEngineMutation, useMemoryEngineQuery } from '@/api/hooks/useMemoryWorkspace'
import { operationsByStatus, operationStatusCounts, type OperationRow } from './memory-workspace-pure'
import { EngineError } from './shared'

export const OperationsTab: React.FC<{ bank: string }> = ({ bank }) => {
  const list = useMemoryEngineQuery<{ operations: OperationRow[] }>(
    `v1/default/banks/${encodeURIComponent(bank)}/operations?limit=100`,
    { refetchInterval: 10_000 },
  )
  const retry = useMemoryEngineMutation('post', 'operations')
  const [status, setStatus] = useState<string>('all')

  if (list.isError) return <EngineError error={list.error} what="operations" onRetry={() => list.refetch()} />
  const ops = list.data?.operations || []
  const counts = operationStatusCounts(ops)
  const filtered = operationsByStatus(ops, status)

  return (
    <div data-testid="memws-operations">
      <div className="memws-chips">
        {['all', 'pending', 'processing', 'completed', 'failed'].map((s) => (
          <button
            key={s}
            className={`btn ghost sm${status === s ? ' active' : ''}`}
            data-testid={`memws-op-filter-${s}`}
            onClick={() => setStatus(s)}
          >
            {s} {s !== 'all' ? `(${counts[s] || 0})` : `(${ops.length})`}
          </button>
        ))}
      </div>
      <table className="tbl mono small">
        <thead><tr><th>id</th><th>type</th><th>status</th><th>created</th><th></th></tr></thead>
        <tbody>
          {filtered.map((o) => (
            <tr key={o.id} data-testid={`memws-op-row-${o.id}`}>
              <td>{o.id.slice(0, 8)}</td>
              <td>{(o as Record<string, unknown>).operation_type as string}</td>
              <td>{o.status}</td>
              <td>{(o as Record<string, unknown>).created_at as string}</td>
              <td>
                {o.status === 'failed' ? (
                  <button
                    className="btn ghost sm"
                    data-testid={`memws-op-retry-${o.id}`}
                    disabled={retry.isPending}
                    onClick={() => retry.mutate({ sub: `v1/default/banks/${bank}/operations/${o.id}/retry` })}
                  >
                    Retry
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default OperationsTab
