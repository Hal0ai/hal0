// The Generate tab's results: a grid of the last 12 images, newest first. Each tile has Open,
// Download and Reuse seed. The results live in generate-store.ts (they outlive the panel).
import './image-generate.css'
import { isReusableSeed } from './generate-pure'

const TOO_LARGE =
  'This seed is too large to reuse exactly: a browser keeps whole numbers exact only up to 2^53, ' +
  'so sending it back would render a different image.'

// `/api/images/cache/<stem>.png` -> `<stem>.png`
const fileName = (url) => url.split('/').pop() || 'image.png'

/**
 * @param {object} props
 * @param {import('./generate-store').GenerateResult[]} props.results   newest first
 * @param {(seed: number) => void} props.onReuseSeed   lock the seed field to this seed
 */
export function GenerateResults({ results, onReuseSeed }) {
  if (results.length === 0) return null
  return (
    <section className="gen-results" data-testid="gen-results" aria-label="Results">
      <h3 className="gen-results-h">
        Results <span className="ct">· {results.length}</span>
      </h3>
      <div className="gen-grid">
        {results.map((r) => (
          <figure className="gen-tile" data-testid="gen-tile" key={r.url}>
            <img src={r.url} alt={r.prompt} loading="lazy" />
            <figcaption>
              <span className="gen-tile-meta">
                {[r.model, r.size, isReusableSeed(r.seed) ? `seed ${r.seed}` : null].filter(Boolean).join(' · ')}
              </span>
              <div className="gen-tile-actions">
                <a
                  className="btn ghost sm"
                  data-testid="gen-open"
                  href={r.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open
                </a>
                <a className="btn ghost sm" data-testid="gen-download" href={r.url} download={fileName(r.url)}>
                  Download
                </a>
                {r.seed != null && (
                  <button
                    type="button"
                    className="btn ghost sm"
                    data-testid="gen-reuse"
                    disabled={!isReusableSeed(r.seed)}
                    title={isReusableSeed(r.seed) ? 'Render with this seed again' : TOO_LARGE}
                    onClick={() => onReuseSeed(r.seed)}
                  >
                    Reuse seed
                  </button>
                )}
              </div>
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  )
}
