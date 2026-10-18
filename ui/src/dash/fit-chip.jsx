// fit-chip.jsx — the one GTT-fit chip and modality glyphs shared by the slot
// drawer's Model picker (slot-modals.jsx) and the inference card's model
// picker (inference-pane.jsx), so the two can never drift. Verdict word lives
// in the title/aria-label; the chip itself is icon + estimated GB only.
import React from 'react'

const svgProps = {
  width: 11,
  height: 11,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': 'true',
  focusable: 'false',
}

const GLYPH = {
  ok: <path d="M3 8l3 3 7-7" />,
  warn: <path d="M8 3v6M8 12.5v0.01" />,
  err: <path d="M4 4l8 8M12 4l-8 8" />,
}

const VERDICTS = {
  fits: { tone: 'ok', word: 'fits' },
  tight: { tone: 'warn', word: 'tight' },
  exceeds: { tone: 'err', word: "won't fit" },
  exceeds_total: { tone: 'err', word: "won't fit" },
}

/** Icon + `~N GB` chip. Unknown verdicts render nothing (warn-never-block). */
export function FitChip({ verdict, neededMb }) {
  const v = VERDICTS[verdict]
  if (!v) return null
  const gb = Math.round((neededMb ?? 0) / 1024)
  const title = `${v.word} · needs ~${gb} GB`
  return (
    <span className={`chip fit-chip ${v.tone}`} title={title} aria-label={title} data-fit={v.tone}>
      <svg {...svgProps}>{GLYPH[v.tone]}</svg>
      <span>~{gb} GB</span>
    </span>
  )
}

/** Eye glyph for the `vision` modality. */
export function VisionIcon() {
  return (
    <span className="chip info modality-icon" title="vision" aria-label="vision" role="img">
      <svg {...svgProps}>
        <path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z" />
        <circle cx="8" cy="8" r="1.8" />
      </svg>
    </span>
  )
}

/** Modality marker: eye for vision, the plain text tag for the rest. */
export function ModalityTag({ tag }) {
  if (!tag) return null
  if (tag === 'vision') return <VisionIcon />
  return <span className="chip info">{tag}</span>
}
