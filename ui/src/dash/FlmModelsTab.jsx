// Models → FLM: the FastFlowLM catalogue (GET /api/slots/flm/models), read from the same hook and
// row builder the FLM lane's model dropdown uses. Each row shows the name, family, roles, size,
// an audio flag and whether it is installed; a model that is not installed has a Download button
// (the same pull the lane starts when you pick one). FLM has no per-model remove hook, so
// installed rows carry no remove action. Which model the FLM process serves is still chosen on
// the lane (Slots → NPU → Lanes).
import './npu/npu-tab.css'
import { useFlmCatalog } from '@/api/hooks/useFlmCatalog'
import { usePullJob } from '@/api/hooks/useModels'
import { flmCatalogRows, formatFlmSize } from './npu/npu-models-pure'

export function FlmModelsTab() {
  const q = useFlmCatalog()
  const pull = usePullJob()
  const rows = flmCatalogRows(q.data?.models)

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
        return (
          <div key={r.id} className="npt-mrow" data-testid={`flm-model-${r.id}`}>
            <span className="npt-mname mono">{r.id}</span>
            {r.family && <span className="chip mono">{r.family}</span>}
            <span className="chip mono">{r.caps.join(' · ')}</span>
            <span className="chip mono" data-testid={`flm-size-${r.id}`}>{formatFlmSize(r.sizeBytes) ?? '—'}</span>
            {r.audio && <span className="chip info" title="also takes audio input">audio</span>}
            <span className={`chip ${r.installed ? 'ok' : 'amber'}`} data-testid={`flm-state-${r.id}`}>
              {r.installed ? 'installed' : pulling ? `downloading${pull.pct != null ? ` ${Math.round(pull.pct)}%` : ''}` : 'not installed'}
            </span>
            {!r.installed && (
              <button type="button" className="btn sm" data-testid={`flm-download-${r.id}`} disabled={pull.inFlight} onClick={() => pull.start(r.id)}>
                Download
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}
