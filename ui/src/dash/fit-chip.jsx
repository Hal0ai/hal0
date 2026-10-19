// fit-chip.jsx — the one GTT-fit chip and modality glyphs shared by the slot
// drawer's Model picker (slot-modals.jsx) and the inference card's model
// picker (inference-pane.jsx), so the two can never drift. Verdict word lives
// in the title/aria-label; the chip itself is icon + estimated GB only.
import React from 'react'
import { draftVariantLabel, slotBolt } from './spec-state.js'

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

/** Eye glyph for the `vision` modality: a bare icon, not a chip, so it reads
 * as part of the model's name rather than as another tag. */
export function VisionIcon() {
  return (
    <span className="modality-icon" title="vision" aria-label="vision" role="img">
      <svg {...svgProps}>
        <path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z" />
        <circle cx="8" cy="8" r="1.8" />
      </svg>
    </span>
  )
}

/** Bolt glyph for a model with a paired speculative-decoding drafter (bare
 * icon, like the eye). */
export function DrafterBolt({ variant }) {
  const title = `drafter · ${draftVariantLabel(variant)}`
  return (
    <span
      className="modality-icon"
      title={title}
      aria-label={title}
      role="img"
      data-testid="drafter-bolt"
    >
      <svg {...svgProps}>
        <path d="M9 1.5L3.5 9H8l-1 5.5L12.5 7H8l1-5.5z" />
      </svg>
    </span>
  )
}

/**
 * Modality marker: eye for vision, the plain text tag for the rest, plus a
 * bolt when the model carries a drafter (`draft` = its variant, from
 * `draftTagValue`).
 */
export function ModalityTag({ tag, draft }) {
  if (!tag && !draft) return null
  return (
    <>
      {tag === 'vision' ? <VisionIcon /> : tag ? <span className="chip info">{tag}</span> : null}
      {draft ? <DrafterBolt variant={draft} /> : null}
    </>
  )
}

/**
 * Slots-row bolt after the model name (ADR-0031 §5): lit while the slot
 * drafts, grey when a drafter is paired but speculation is off, amber when
 * the running runner cannot use it. Nothing when the model has no drafter.
 */
export function SlotSpecBolt({ spec, model }) {
  const bolt = slotBolt(spec, model)
  if (!bolt) return null
  return (
    <span
      className={'sl-bolt' + (bolt.state === 'lit' ? '' : ' ' + bolt.state)}
      data-testid="sl-spec-bolt"
      title={bolt.title}
      aria-label={bolt.title}
      role="img"
    >
      <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
        <path d="M9 1.5L3.5 9H8l-1 5.5L12.5 7H8l1-5.5z" />
      </svg>
    </span>
  )
}
