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

const MISSION_FIELDS = [
  { key: 'retain_mission', label: 'Retain mission' },
  { key: 'reflect_mission', label: 'Reflect mission' },
  { key: 'observations_mission', label: 'Observations mission' },
] as const

/**
 * Hindsight bank config field for default observation scopes. The platform
 * config PATCH passthrough treats this as an opaque `updates` key, same as
 * `retain_mission`/`reflect_mission` — see
 * src/hal0/memory/spec/templates/shared.json (`default_observation_scopes`:
 * a list of tag-scope entries, each entry a list of tags ANDed together,
 * entries ORed; or a bare string shorthand like `"shared"`). NOTE: the task
 * that requested this named the field `observation_scopes`, but that name
 * is only used for Hindsight *request*-level scoping (retain/consolidate
 * calls); the persisted per-bank config key is `default_observation_scopes`
 * per the platform's own templates and tests — used here, flagged as an
 * open question rather than guessed silently.
 */
const OBSERVATION_SCOPES_KEY = 'default_observation_scopes'

/** Normalize the bank config's `default_observation_scopes` into rows of
 * comma-joined tags for the editable-list UI. Accepts the list-of-lists
 * shape, a bare string shorthand (one row), or absence (empty list). */
export function scopesToRows(value: unknown): string[] {
  if (value == null) return []
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) {
    return value.map((entry) => (Array.isArray(entry) ? entry.join(', ') : String(entry)))
  }
  return []
}

/** Inverse of scopesToRows: comma-joined tag rows -> list-of-lists, dropping
 * blank rows/tags. Empty overall -> empty list (not omitted / not null). */
export function rowsToScopes(rows: string[]): string[][] {
  return rows
    .map((row) => row.split(',').map((t) => t.trim()).filter(Boolean))
    .filter((tags) => tags.length > 0)
}

export const ModelsTab: React.FC<{ bank: string }> = ({ bank }) => {
  const models = useMemoryModels()
  const lanes = useMemoryLanes()
  const config = useMemoryEngineQuery<{ updates?: Record<string, boolean>; [k: string]: unknown }>(
    `v1/default/banks/${encodeURIComponent(bank)}/config`,
  )
  const patchConfig = useMemoryEngineMutation<{ updates: Record<string, unknown> }>('patch', 'config')
  const [toastErr, setToastErr] = React.useState<string | null>(null)
  const [missionDrafts, setMissionDrafts] = React.useState<Record<string, string>>({})
  const [scopeRows, setScopeRows] = React.useState<string[] | null>(null)

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

  const missionValue = (key: string) => missionDrafts[key] ?? (typeof cfg[key] === 'string' ? (cfg[key] as string) : '')
  const saveMission = (key: string) => {
    const value = missionValue(key)
    patchConfig.mutate(
      { sub: `v1/default/banks/${bank}/config`, body: { updates: { [key]: value } } },
      {
        onSuccess: () => setMissionDrafts((d) => { const n = { ...d }; delete n[key]; return n }),
        onError: (e) => setToastErr((e as Error)?.message || `Failed to update ${key}`),
      },
    )
  }

  const effectiveScopeRows = scopeRows ?? scopesToRows(cfg[OBSERVATION_SCOPES_KEY])
  const saveScopes = () => {
    patchConfig.mutate(
      {
        sub: `v1/default/banks/${bank}/config`,
        body: { updates: { [OBSERVATION_SCOPES_KEY]: rowsToScopes(effectiveScopeRows) } },
      },
      {
        onSuccess: () => setScopeRows(null),
        onError: (e) => setToastErr((e as Error)?.message || 'Failed to update observation scopes'),
      },
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
      <Card title={`Bank missions — ${bank}`} testId="memws-missions-card">
        {config.isError ? (
          <EngineError error={config.error} what="bank config" onRetry={() => config.refetch()} />
        ) : (
          <div className="memws-missions">
            {MISSION_FIELDS.map(({ key, label }) => (
              <div key={key} className="memws-mission-field">
                <label htmlFor={`memws-mission-${key}`}>{label}</label>
                <textarea
                  id={`memws-mission-${key}`}
                  data-testid={`memws-mission-${key}`}
                  rows={3}
                  value={missionValue(key)}
                  disabled={patchConfig.isPending}
                  onChange={(e) => setMissionDrafts((d) => ({ ...d, [key]: e.target.value }))}
                />
                <button
                  type="button"
                  className="btn ghost sm"
                  data-testid={`memws-mission-save-${key}`}
                  disabled={patchConfig.isPending}
                  onClick={() => saveMission(key)}
                >
                  Save
                </button>
              </div>
            ))}
          </div>
        )}
      </Card>
      <Card title={`Observation scopes — ${bank}`} testId="memws-observation-scopes-card">
        {config.isError ? (
          <EngineError error={config.error} what="bank config" onRetry={() => config.refetch()} />
        ) : (
          <div className="memws-scopes">
            <ul className="memws-scopes-list">
              {effectiveScopeRows.map((row, i) => (
                <li key={i}>
                  <input
                    type="text"
                    data-testid={`memws-scope-row-${i}`}
                    value={row}
                    placeholder="topic:x, topic:y"
                    disabled={patchConfig.isPending}
                    onChange={(e) => {
                      const next = [...effectiveScopeRows]
                      next[i] = e.target.value
                      setScopeRows(next)
                    }}
                  />
                  <button
                    type="button"
                    className="btn ghost sm"
                    data-testid={`memws-scope-remove-${i}`}
                    disabled={patchConfig.isPending}
                    onClick={() => setScopeRows(effectiveScopeRows.filter((_, j) => j !== i))}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
            <button
              type="button"
              className="btn ghost sm"
              data-testid="memws-scope-add"
              disabled={patchConfig.isPending}
              onClick={() => setScopeRows([...effectiveScopeRows, ''])}
            >
              Add scope
            </button>
            <button
              type="button"
              className="btn primary sm"
              data-testid="memws-scope-save"
              disabled={patchConfig.isPending}
              onClick={saveScopes}
            >
              Save scopes
            </button>
          </div>
        )}
      </Card>
    </div>
  )
}

export default ModelsTab
