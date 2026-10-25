// "Set up image generation": the block that heads the Models tab while nothing renderable is
// installed and no install is running. The recommended model as the large card with the reason
// it was recommended, one button to install it, two lighter alternatives, and a way down to the
// full list.
import './image.css'
import { FitMeter } from './FitMeter'
import { stateAction } from './image-models-pure'
import { Badges } from './ImageModelCard'
import { modelBadges, perUnit } from './models-panel-pure'

const metaOf = (m) =>
  [m.license, `${m.approx_gb} GB`, `~${m.est_seconds} s per ${perUnit(m.task)}`].filter(Boolean).join(' · ')

/**
 * @param {object} props
 * @param {import('./models-panel-pure').FirstRunPicks} props.picks
 * @param {string | null} props.reason   `recommendationReason(...)`, or null
 * @param {string | null} props.error    the server's message for a refused install
 * @param {(m: object) => void} props.onInstall
 * @param {() => void} props.onBrowse    scrolls to the full list below
 */
export function ImageFirstRun({ picks, reason, error, onInstall, onBrowse }) {
  const { recommended, others } = picks
  return (
    <section className="imp-first" data-testid="imp-first-run">
      <h2>Set up image generation</h2>
      <p className="imp-first-lead">
        No image model is installed yet. Install one to start generating; the recommended model is the
        one chosen for this box.
      </p>

      <div className="imp-first-big" data-testid="imp-first-run-recommended">
        <div className="imp-card-h">
          <span className="imp-name">{recommended.label}</span>
          <Badges labels={modelBadges(recommended, null)} />
          <span className="imp-meta">{metaOf(recommended)}</span>
          {reason && <span className="imp-first-reason">{reason}</span>}
          <div className="imp-actions" style={{ marginTop: 8 }}>
            <button className="btn" onClick={() => onInstall(recommended)}>
              Install recommended — {recommended.label} (~{recommended.approx_gb} GB)
            </button>
          </div>
        </div>
        <FitMeter fit={recommended.fit} engineAvailable id={`fr-${recommended.id}`} />
      </div>

      {error && (
        <div className="imp-err" role="alert" data-testid="imp-first-run-error">
          {error}
        </div>
      )}

      {others.length > 0 && (
        <div className="imp-first-small">
          {others.map((m) => {
            const act = stateAction(m, null, true)
            return (
              <div key={m.id} className="imp-card" data-testid={`imp-first-run-small-${m.id}`}>
                <div className="imp-card-h">
                  <span className="imp-name">{m.label}</span>
                  <span className="imp-meta">{metaOf(m)}</span>
                </div>
                <div className="imp-actions">
                  <button className="btn ghost sm" onClick={() => onInstall(m)}>
                    {act.label}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div className="imp-first-foot">
        <button className="btn ghost sm" onClick={onBrowse}>
          Browse all models
        </button>
      </div>
    </section>
  )
}
