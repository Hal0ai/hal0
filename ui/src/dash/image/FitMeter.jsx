// The fit meter: two bars, the GPU pool and RAM · this box, each with a dashed ghost of what the
// model needs, and the server's verdict beside them. Both figures (`render_free_gb`,
// `ram_free_gb`) are what a render can use once image mode has paused the chat slots, so both
// read "free for a render". On a bare-metal unified-memory host the RAM figure counts the memory
// the paused slots give back, and can be above what the box reports free right now (#3072).
//
// The Models cards and the Generate tab's fit panel both draw it. The verdict is always the
// server's (`fit.verdict`, `fit.reason`); the UI never judges a fit. What it computes is display
// only: "{spare} GB to spare" is a subtraction of two server figures (`fitSpareGb`) and each
// ghost's width is the need as a share of what is free. Neither decides anything.
//
// The bars are the top bar's memory ruler's own (`th-ruler-bar`, `th-ruler-seg`); only the
// dashed ghost (`fm-need`) is new (image.css).
import './image.css'
import { fitSpareGb } from './image-models-pure'

// 47.9 -> "47.9", 12 -> "12": the server rounds to one decimal already.
const gb = (n) => String(Number(Number(n).toFixed(1)))

// How much of the bar the need takes: a share of what is free, never past the whole bar. No
// free memory at all reads as full.
const share = (need, free) => (free > 0 ? Math.min(1, Math.max(0, need / free)) : need > 0 ? 1 : 0)

function Bar({ kind, label, need, estimated, free, freeLabel, suffix }) {
  const pct = Math.round(share(need, free) * 100)
  return (
    <div className="fm-row" data-testid={`fit-bar-${kind}${suffix}`}>
      <div className="th-ruler-h fm-h">
        <span>{label}</span>
        <span>
          needs <b>{gb(need)} GB{estimated ? ' est.' : ''}</b> · <b>{gb(free)} GB</b> {freeLabel}
        </span>
      </div>
      <div className="th-ruler-bar fm-bar">
        <div className="th-ruler-seg fm-need" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

const VERDICT = {
  fits: { cls: 'ok' },
  tight: { cls: 'warn' },
  wont_fit: { cls: 'err' },
}

const verdictText = (fit) =>
  fit.verdict === 'fits'
    ? `Fits · ${gb(fitSpareGb(fit))} GB to spare`
    : fit.verdict === 'tight'
      ? 'Tight'
      : "Won't fit"

/**
 * @param {object} props
 * @param {import('@/api/hooks/useComfyui').Fit | null | undefined} props.fit
 *   The server's verdict for one render; null when the box's budget is unknown.
 * @param {boolean} props.engineAvailable
 *   False on a host with no ROCm lane: the verdict is not shown then, whatever `fit` holds.
 * @param {string} [props.size]
 *   The size the verdict was judged at (`/fit` answers it, after clamping).
 * @param {string} [props.requestedSize]
 *   The size the caller asked for; "judged at {size}" shows only when the two differ.
 * @param {boolean} [props.perImage]
 *   More than one image is asked for: the verdict is for one, and the meter says so.
 * @param {string} [props.id]
 *   Suffix for the test ids when one screen draws several meters.
 */
export function FitMeter({ fit, engineAvailable, size, requestedSize, perImage = false, id }) {
  // No verdict, no chip: never "Won't fit" for a budget nobody measured or an engine that is off.
  if (!fit || !engineAvailable) return null
  const suffix = id ? `-${id}` : ''
  const judgedElsewhere = !!size && !!requestedSize && size !== requestedSize
  const tone = VERDICT[fit.verdict]?.cls ?? ''
  return (
    <div className="fm" data-testid={`fit-meter${suffix}`} data-verdict={fit.verdict}>
      <Bar
        kind="gpu"
        label="GPU pool"
        need={fit.need.gpu_gb}
        estimated={fit.need.gpu_estimated}
        free={fit.render_free_gb}
        freeLabel="free for a render"
        suffix={suffix}
      />
      <Bar
        kind="ram"
        label="RAM · this box"
        need={fit.need.ram_gb}
        estimated={fit.need.ram_estimated}
        free={fit.ram_free_gb}
        freeLabel="free for a render"
        suffix={suffix}
      />
      <div className="fm-verdict">
        <span className={`chip ${tone}`} data-testid={`fit-chip${suffix}`}>
          {verdictText(fit)}
        </span>
        {judgedElsewhere && <span className="fm-judged">judged at {size}</span>}
        {perImage && (
          <span className="fm-judged" data-testid={`fit-per-image${suffix}`}>
            judged for one image
          </span>
        )}
      </div>
      {/* The server's sentence, as given: it names the budget that decided the verdict. */}
      {fit.reason && (
        <div className="fm-reason" data-testid={`fit-reason${suffix}`}>
          {fit.reason}
        </div>
      )}
    </div>
  )
}
