// "Set up image generation": the block that heads the Models tab while nothing renderable is
// installed and no install is running. The recommended model as the large card with the reason
// it was recommended, one button to install it, two lighter alternatives, and a way down to the
// full list.
//
// When the server says the recommended model won't fit this box (the picker and the fit check can
// disagree: #3076) the large card is the largest model that does, and the recommended one is the
// first of the small cards, with the server's reason.
import './image.css'
import { FitMeter } from './FitMeter'
import { stateAction } from './image-models-pure'
import { Badges } from './ImageModelCard'
import { downloadGb, gbLabel, modelBadges, perUnit } from './models-panel-pure'

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
  const { recommended, lead, others } = picks
  // The recommended model does not lead: the server says it won't fit and another model does.
  const passedOver = lead.id !== recommended.id
  return (
    <section className="imp-first" data-testid="imp-first-run">
      <h2>Set up image generation</h2>
      <p className="imp-first-lead">
        {passedOver
          ? 'No image model is installed yet. The recommended model does not fit this box right now, so the first pick is the largest one that does.'
          : 'No image model is installed yet. Install one to start generating; the recommended model is the one chosen for this box.'}
      </p>

      <div
        className="imp-first-big"
        data-testid={passedOver ? 'imp-first-run-alternative' : 'imp-first-run-recommended'}
      >
        <div className="imp-card-h">
          <span className="imp-name">{lead.label}</span>
          <Badges labels={modelBadges(lead, null)} />
          <span className="imp-meta">{metaOf(lead)}</span>
          {/* The reason is the recommended model's: it says nothing of the one that took its place. */}
          {reason && !passedOver && <span className="imp-first-reason">{reason}</span>}
          <div className="imp-actions" style={{ marginTop: 8 }}>
            <button className="btn" onClick={() => onInstall(lead)}>
              {passedOver ? 'Install' : 'Install recommended —'} {lead.label} (~{gbLabel(downloadGb(lead))} GB)
            </button>
          </div>
        </div>
        <FitMeter fit={lead.fit} engineAvailable id={`fr-${lead.id}`} />
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
                  {m.id === recommended.id && <Badges labels={modelBadges(m, null)} />}
                  <span className="imp-meta">{metaOf(m)}</span>
                  {passedOver && m.id === recommended.id && (
                    <span className="imp-first-wont" data-testid="imp-first-run-wont-fit">
                      {/* The server's sentence, as given. */}
                      Won't fit{m.fit?.reason ? ` · ${m.fit.reason}` : ''}
                    </span>
                  )}
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
