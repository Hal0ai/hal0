// A slot card that image mode has paused: the chip, and the note under the model row with what
// becomes of it and how many chat requests wait. The rule for which card is paused is
// status-line-parts-pure.ts `slotPause`; these two only draw it. Plain dashboard tokens and
// inline styles, like conversion-blocked-chip.jsx: the cards sit outside the Image Gen pane.
import React from 'react'

const INFO = {
  color: 'var(--info)',
  borderColor: 'var(--info-line)',
  background: 'var(--info-soft)',
}

/** The chip. `pause` is `slotPause(...)`'s answer; null draws nothing. */
export function SlotPausedChip({ name, pause }) {
  if (!pause) return null
  return (
    <span className="tag-chip" data-testid={`slot-paused-${name}`} title={pause.detail} style={INFO}>
      {pause.chip}
    </span>
  )
}

/** The note: what becomes of the slot, and the chat-wait line when requests are waiting. */
export function SlotPauseNote({ name, pause }) {
  if (!pause) return null
  return (
    <div
      data-testid={`slot-paused-note-${name}`}
      style={{ fontSize: 11, lineHeight: 1.5, color: 'var(--fg-3)', margin: '2px 0 6px' }}
    >
      <div>{pause.detail}</div>
      {pause.wait && <div>{pause.wait}</div>}
    </div>
  )
}
