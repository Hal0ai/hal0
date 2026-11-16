// Models → FLM: the FastFlowLM catalogue (GET /api/slots/flm/models), read from the same hook and
// row builder the FLM lane's model dropdown uses. Each row shows the name, family, roles, size,
// an audio flag and whether it is installed; a model that is not installed has a Download button
// (the same pull the lane starts when you pick one). An installed model has Remove, which asks
// inline (naming the space it frees) and then runs `flm remove` (DELETE /api/slots/flm/models/
// {tag}); it is disabled, with the reason as its tooltip, while an FLM role uses the model.
// Which model the FLM process serves is still chosen on the lane (Slots → NPU → Lanes).
import { useState } from 'react'
import './npu/npu-tab.css'
import { useFlmCatalog, useRemoveFlmModel } from '@/api/hooks/useFlmCatalog'
import { usePullJob } from '@/api/hooks/useModels'
import { flmCatalogRows, formatFlmSize } from './npu/npu-models-pure'

export function FlmModelsTab() {
  const q = useFlmCatalog()
  const pull = usePullJob()
  const remove = useRemoveFlmModel()
  const [confirming, setConfirming] = useState(null)
  const rows = flmCatalogRows(q.data?.models)
  const removingId = remove.isPending ? remove.variables : null
  const errorId = remove.isError ? remove.variables : null
  const doneId = remove.isSuccess ? remove.variables : null

  return (
    <div className="mdl-list" style={{ marginTop: 18 }} data-testid="flm-models-tab">
      <p className="npt-hint" data-testid="flm-models-note">
        FastFlowLM models for the NPU. Pick which one the FLM process serves on the{' '}
        <a href="#slots/npu/lanes" data-testid="flm-models-lane-link">NPU lanes</a> tab.
      </p>
      {q.isLoading && <p className="npt-hint" data-testid="flm-models-loading">Loading…</p>}
      {q.isError && <p className="npt-hint" role="alert" data-testid="flm-models-error">Could not read the FLM catalogue.</p>}
      {q.data && !q.data.probe_ok && rows.length === 0 && (
        <p className="npt-hint" data-testid="flm-models-empty">FLM is not reachable on this box, so its catalogue is empty.</p>
      )}
      {pull.error && <p className="npt-hint" role="alert" data-testid="flm-models-pull-error">{pull.error.message}</p>}
      {rows.map((r) => {
        const pulling = pull.inFlight && pull.modelId === r.id
        const inUse = r.inUseBy.length > 0
        const usedBy = r.inUseBy.map((u) => `${u.role} on ${u.slot}`).join(', ')
        const size = formatFlmSize(r.sizeBytes)
        return (
          <div key={r.id} className="npt-mrow" data-testid={`flm-model-${r.id}`}>
            <span className="npt-mname mono">{r.id}</span>
            {r.family && <span className="chip mono">{r.family}</span>}
            <span className="chip mono">{r.caps.join(' · ')}</span>
            <span className="chip mono" data-testid={`flm-size-${r.id}`}>{size ?? '—'}</span>
            {r.audio && <span className="chip info" title="also takes audio input">audio</span>}
            <span className={`chip ${r.installed ? 'ok' : 'amber'}`} data-testid={`flm-state-${r.id}`}>
              {r.installed ? (inUse ? 'installed · in use' : 'installed') : pulling ? `downloading${pull.pct != null ? ` ${Math.round(pull.pct)}%` : ''}` : 'not installed'}
            </span>
            {!r.installed && (
              <button type="button" className="btn sm" data-testid={`flm-download-${r.id}`} disabled={pull.inFlight} onClick={() => pull.start(r.id)}>
                Download
              </button>
            )}
            {r.installed && confirming !== r.id && (
              <button
                type="button"
                className="btn sm"
                data-testid={`flm-remove-${r.id}`}
                disabled={inUse || removingId != null || pulling}
                title={inUse ? `In use (${usedBy}). Switch that role to another model on the NPU lanes first.` : undefined}
                onClick={() => {
                  remove.reset()
                  setConfirming(r.id)
                }}
              >
                {removingId === r.id ? 'Removing…' : 'Remove'}
              </button>
            )}
            {r.installed && confirming === r.id && (
              <span className="npt-hint" data-testid={`flm-remove-confirm-${r.id}`}>
                Remove {r.id} from disk{size ? ` and free ${size}` : ''}?{' '}
                <button
                  type="button"
                  className="btn sm danger"
                  data-testid={`flm-remove-yes-${r.id}`}
                  onClick={() => {
                    setConfirming(null)
                    remove.mutate(r.id)
                  }}
                >
                  Remove
                </button>{' '}
                <button type="button" className="btn sm" data-testid={`flm-remove-no-${r.id}`} onClick={() => setConfirming(null)}>
                  Cancel
                </button>
              </span>
            )}
            {errorId === r.id && (
              <span className="npt-hint" role="alert" data-testid={`flm-remove-error-${r.id}`}>{remove.error?.message ?? 'Remove failed.'}</span>
            )}
            {doneId === r.id && remove.data?.warnings?.length > 0 && (
              <span className="npt-hint" data-testid={`flm-remove-warning-${r.id}`}>{remove.data.warnings.join(' ')}</span>
            )}
          </div>
        )
      })}
    </div>
  )
}
