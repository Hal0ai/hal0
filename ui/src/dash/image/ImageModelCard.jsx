// One model on the Models tab: what it is, whether it fits this box, how far its install is,
// and the buttons that fit its state.
//
// Everything arrives as props from ImageModelsPanel, which owns the data and the mutations. The
// install-lifecycle button (Install, Cancel or Remove) is `stateAction`'s; the verdict is the
// server's, drawn by FitMeter. The panel renders inside the pane's `.comfy-page` wrapper.
import './image.css'
import { isImageJobActive } from '@/api/hooks/imageJobState'
import { FitMeter } from './FitMeter'
import { jobProgress, stateAction } from './image-models-pure'
import { fmtEta, gbLabel, gibOf, modelBadges, perUnit } from './models-panel-pure'

const BADGE_TONE = {
  Recommended: 'amber',
  Default: 'ok',
  'Editor only': '',
  Large: 'warn',
  'Non-commercial': 'warn',
}

export function Badges({ labels }) {
  if (labels.length === 0) return null
  return (
    <div className="imp-badges">
      {labels.map((label) => (
        <span key={label} className={`chip ${BADGE_TONE[label] ?? ''}`}>
          {label}
        </span>
      ))}
    </div>
  )
}

// What the suggestion button says. An installed model is used (it opens on Generate); one that
// is not installed is installed, and the button says so and how much it still has to fetch
// (`missing_gb`, or the model's whole size when the row carries no missing figure).
function suggestionLabel(alt) {
  if (alt.state === 'complete') return `Use ${alt.label}`
  return `Install ${alt.label} · ${gbLabel(alt.missing_gb ?? alt.approx_gb)} GB to download`
}

// "0.9 / 3.7 GB" (GiB, the unit of `approx_gb`), then the speed and time left once two polls
// have shown them. A job queued behind another reads "Queued".
function Progress({ id, job, rate }) {
  const pct = Math.round(jobProgress(job) * 100)
  const total = job.bytes_total > 0 ? ` / ${gibOf(job.bytes_total)}` : ''
  return (
    <div className="imp-prog" data-testid={`imp-progress-${id}`}>
      <div
        className="imp-prog-bar"
        role="progressbar"
        aria-label="Download progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <i style={{ width: `${pct}%` }} />
      </div>
      <div className="imp-prog-t">
        <span>
          {job.state === 'queued' && job.bytes_downloaded === 0
            ? 'Queued'
            : `${gibOf(job.bytes_downloaded)}${total} GB`}
        </span>
        {rate && (
          <span>
            {rate.mbPerS.toFixed(1)} MB/s · {fmtEta(rate.etaS)} left
          </span>
        )}
      </div>
    </div>
  )
}

/**
 * @param {object} props
 * @param {import('@/api/hooks/useComfyui').ComfyuiCatalogModel} props.model
 * @param {import('@/api/hooks/useComfyui').ImageJob | null} props.job   the freshest job for it
 * @param {{ mbPerS: number, etaS: number } | undefined} props.rate   from `useJobRates`
 * @param {boolean} props.engineAvailable
 * @param {boolean} props.isDefault   the img capability's selected model
 * @param {import('@/api/hooks/useComfyui').ComfyuiCatalogModel | null} props.alternative
 *   what to offer in place of this model when the server says it won't fit
 * @param {string | undefined} props.installError   the server's message for a refused install
 * @param {{
 *   onInstall: (m: object) => void, onCancel: (m: object) => void, onRemove: (m: object) => void,
 *   onGenerate: (m: object) => void, onOpenEditor: (m: object) => void,
 *   onSetDefault: (m: object) => void, onUseAlternative: (m: object) => void,
 * }} props.actions
 * @param {boolean} [props.settingDefault]
 */
export function ImageModelCard({
  model,
  job,
  rate,
  engineAvailable,
  isDefault,
  alternative,
  installError,
  actions,
  settingDefault = false,
}) {
  const complete = model.state === 'complete'
  const running = isImageJobActive(job)
  const act = stateAction(model, job, engineAvailable)
  const onState = () => {
    if (act.id === 'install') actions.onInstall(model)
    else if (act.id === 'cancel') actions.onCancel(model)
    else actions.onRemove(model)
  }
  const failed = job?.state === 'failed' && !complete && !!job.error
  const meta = [
    model.license,
    `${model.approx_gb} GB`,
    `~${model.est_seconds} s per ${perUnit(model.task)}`,
  ].filter(Boolean)

  return (
    <article
      className="imp-card"
      data-testid={`imp-card-${model.id}`}
      data-complete={complete ? 'true' : 'false'}
    >
      <div className="imp-card-h">
        <span className="imp-name">{model.label}</span>
        <Badges labels={modelBadges(model, isDefault ? model.id : null)} />
        <span className="imp-meta">{meta.join(' · ')}</span>
      </div>

      <FitMeter fit={model.fit} engineAvailable={engineAvailable} id={model.id} />

      {alternative && (
        <div className="imp-suggest" data-testid={`imp-suggest-${model.id}`}>
          <button className="btn sm" onClick={() => actions.onUseAlternative(alternative)}>
            {suggestionLabel(alternative)}
          </button>
          {alternative.state === 'complete' && <span>installed</span>}
        </div>
      )}

      {running && <Progress id={model.id} job={job} rate={rate} />}

      {failed && (
        <div className="imp-err" role="alert" data-testid={`imp-job-error-${model.id}`}>
          <span>{job.error}</span>
          <button className="btn ghost sm" onClick={() => actions.onInstall(model)} disabled={!engineAvailable}>
            Retry
          </button>
        </div>
      )}
      {installError && !complete && (
        <div className="imp-err" role="alert" data-testid={`imp-install-error-${model.id}`}>
          {installError}
        </div>
      )}

      <div className="imp-actions">
        <button
          className={'btn sm' + (act.id === 'remove' ? ' danger' : act.id === 'cancel' ? ' ghost' : '')}
          disabled={act.disabled}
          onClick={onState}
        >
          {act.label}
        </button>
        <span className="grow" />
        {complete && model.api_renderable && (
          <>
            {!isDefault && (
              <button className="btn ghost sm" disabled={settingDefault} onClick={() => actions.onSetDefault(model)}>
                Set as default
              </button>
            )}
            <button className="btn sm" disabled={!engineAvailable} onClick={() => actions.onGenerate(model)}>
              Generate
            </button>
          </>
        )}
        {complete && !model.api_renderable && (
          <button className="btn ghost sm" disabled={!engineAvailable} onClick={() => actions.onOpenEditor(model)}>
            Open in ComfyUI
          </button>
        )}
      </div>
    </article>
  )
}
