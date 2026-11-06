// hal0 v3 dashboard — Memory workspace: Models & lanes tab.
import React from 'react'
import { useMemoryEngineMutation, useMemoryEngineQuery, useMemoryLanes, useMemoryModels } from '@/api/hooks/useMemoryWorkspace'
import { Card, EngineError } from './shared'

const DECISION_TOGGLE_KEYS = [
  'recall_decision_model_prune',
  'recall_decision_model_dedup',
  'consolidation_use_decision_model',
  'reflect_use_decision_model',
  'mental_model_delta_use_decision_model',
] as const

export const ModelsTab: React.FC<{ bank: string }> = ({ bank }) => {
  const models = useMemoryModels()
  const lanes = useMemoryLanes()
  const config = useMemoryEngineQuery<{ updates?: Record<string, boolean>; [k: string]: unknown }>(
    `v1/default/banks/${encodeURIComponent(bank)}/config`,
  )
  const patchConfig = useMemoryEngineMutation<{ updates: Record<string, boolean> }>('patch', 'config')
  const [toastErr, setToastErr] = React.useState<string | null>(null)

  if (models.isError) return <EngineError error={models.error} what="models" onRetry={() => models.refetch()} />

  const cfg = (config.data || {}) as Record<string, unknown>
  const npu = lanes.data?.npu

  const field = (info: { provider?: unknown; model?: unknown } | undefined, key: 'provider' | 'model') =>
    info && info[key] != null ? String(info[key]) : '—'

  const toggle = (key: string, checked: boolean) => {
    patchConfig.mutate(
      { sub: `v1/default/banks/${bank}/config`, body: { updates: { [key]: checked } } },
      { onError: (e) => setToastErr((e as Error)?.message || `Failed to update ${key}`) },
    )
  }

  return (
    <div data-testid="memws-models">
      {toastErr ? (
        <div className="toast toast-error" data-testid="memws-toast-error" onClick={() => setToastErr(null)}>
          {toastErr}
        </div>
      ) : null}
      <div className="memws-grid">
        <Card title="Embeddings" testId="memws-embeddings-card">
          <div className="mono small">provider: {field(models.data?.embeddings, 'provider')}</div>
          <div className="mono small">model: {field(models.data?.embeddings, 'model')}</div>
        </Card>
        <Card title="Reranker" testId="memws-reranker-card">
          <div className="mono small">provider: {field(models.data?.reranker, 'provider')}</div>
          <div className="mono small">model: {field(models.data?.reranker, 'model')}</div>
        </Card>
        <Card title="Decision model" testId="memws-decision-model-card">
          <div className="mono small">provider: {field(models.data?.decision, 'provider')}</div>
          <div className="mono small">model: {field(models.data?.decision, 'model')}</div>
          <div className={`badge badge-${models.data?.decision?.reachable ? 'ok' : 'bad'}`} data-testid="memws-decision-reachable">
            {models.data?.decision?.reachable ? 'reachable' : 'unreachable'}
          </div>
        </Card>
        {npu ? (
          <Card title="NPU lane" testId="memws-npu-card">
            <pre className="mono small memws-pre">{JSON.stringify(npu, null, 2)}</pre>
          </Card>
        ) : null}
      </div>
      <Card title={`Decision-model toggles — ${bank}`}>
        {config.isError ? (
          <EngineError error={config.error} what="bank config" onRetry={() => config.refetch()} />
        ) : (
          <ul className="memws-toggles">
            {DECISION_TOGGLE_KEYS.map((key) => (
              <li key={key}>
                <label>
                  <input
                    type="checkbox"
                    data-testid={`memws-toggle-${key}`}
                    checked={!!cfg[key]}
                    disabled={patchConfig.isPending}
                    onChange={(e) => toggle(key, e.target.checked)}
                  />
                  {key}
                </label>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

export default ModelsTab
