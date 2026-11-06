// hal0 v3 dashboard — Memory workspace: Overview tab.
import React, { useMemo } from 'react'
import { useMemoryEngineQuery, useMemoryLanes } from '@/api/hooks/useMemoryWorkspace'
import {
  fmtAge,
  laneHealthTone,
  normalizeLlmOpStats,
  oldestPendingAge,
  sortBanksByPressure,
  totalQueueDepth,
  type LaneBankRow,
} from './memory-workspace-pure'
import { Card, EngineError } from './shared'

export const OverviewTab: React.FC<{ bank: string }> = ({ bank }) => {
  const lanes = useMemoryLanes()
  const stats = useMemoryEngineQuery<Record<string, unknown>>(`v1/default/banks/${encodeURIComponent(bank)}/stats`)
  const llmStats = useMemoryEngineQuery<unknown>('v1/default/llm-requests/stats')

  const banks: LaneBankRow[] = useMemo(
    () =>
      sortBanksByPressure(
        (lanes.data?.banks || []).map((b) => ({
          bank_id: b.bank_id,
          pending: b.pending ?? 0,
          processing: b.processing ?? 0,
          oldest_pending_age_s: b.oldest_pending_age_s ?? null,
        })),
      ),
    [lanes.data],
  )
  const llmOps = useMemo(() => normalizeLlmOpStats(llmStats.data), [llmStats.data])

  if (lanes.isError) return <EngineError error={lanes.error} what="lanes" onRetry={() => lanes.refetch()} />

  const decision = lanes.data?.decision
  const llm = lanes.data?.llm
  const tone = laneHealthTone(decision?.health)
  const bankErrors = (lanes.data?.banks || []).filter(
    (b): b is LaneBankRow & { error: string } => !!(b as { error?: string }).error,
  )
  const npu = lanes.data?.npu

  return (
    <div className="memws-grid" data-testid="memws-overview">
      <Card title="Decision server" testId="memws-decision-card">
        <div className={`badge badge-${tone}`} data-testid="memws-decision-health">
          {decision?.health || 'unknown'}
        </div>
        <div className="mono small">device: {decision?.device ?? '—'}</div>
        <div className="mono small">requests: {decision?.requests ?? 0}</div>
        <div className="mono small">
          busy: {typeof decision?.busy_s === 'number' ? decision.busy_s.toFixed(1) : (decision?.busy_s ?? 0)}s
        </div>
      </Card>
      <Card title="LLM lane">
        <div className="mono small">model: {llm?.model ?? '—'}</div>
        <div className="mono small">max concurrent: {llm?.max_concurrent ?? '—'}</div>
      </Card>
      {npu ? (
        <Card title="NPU lane" testId="memws-npu-card">
          <pre className="mono small memws-pre">{JSON.stringify(npu, null, 2)}</pre>
        </Card>
      ) : null}
      <Card title="Queue by bank" testId="memws-queue-card">
        <div className="mono small">total in flight: {totalQueueDepth(banks)}</div>
        <div className="mono small">oldest pending: {fmtAge(oldestPendingAge(banks))}</div>
        <table className="tbl mono small">
          <thead>
            <tr><th>bank</th><th>pending</th><th>processing</th><th>oldest</th></tr>
          </thead>
          <tbody>
            {banks.map((b) => (
              <tr key={b.bank_id} data-testid={`memws-queue-row-${b.bank_id}`}>
                <td>{b.bank_id}</td>
                <td>{b.pending}</td>
                <td>{b.processing}</td>
                <td>{fmtAge(b.oldest_pending_age_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {bankErrors.length ? (
          <ul className="mono small mem-error" data-testid="memws-queue-errors">
            {bankErrors.map((b) => (
              <li key={b.bank_id}>{b.bank_id}: {b.error}</li>
            ))}
          </ul>
        ) : null}
      </Card>
      <Card title={`Bank stats — ${bank}`}>
        {stats.isError ? (
          <EngineError error={stats.error} what="bank stats" onRetry={() => stats.refetch()} />
        ) : (
          <pre className="mono small memws-pre">{JSON.stringify(stats.data ?? {}, null, 2)}</pre>
        )}
      </Card>
      <Card title="LLM requests by operation">
        {llmStats.isError ? (
          <EngineError error={llmStats.error} what="llm stats" onRetry={() => llmStats.refetch()} />
        ) : (
          <table className="tbl mono small">
            <thead><tr><th>operation</th><th>count</th></tr></thead>
            <tbody>
              {llmOps.map((o) => (
                <tr key={o.operation}><td>{o.operation}</td><td>{o.count}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}

export default OverviewTab
