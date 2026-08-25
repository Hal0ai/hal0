// #2038 — crash-loop breaker chip, shared by the inference slot cards and the
// slot edit drawer. The classifier (state → tone/label/tooltip) lives in
// slot-status.js so this component and any future surface cannot drift; this
// file owns only the client-side countdown and the colour tokens.
import React from 'react'

import { breakerChip } from './slot-status.js'

const { useEffect, useRef, useState } = React

// tone → the card's colour vocabulary (dashboard.css tokens). Neutral is the
// dashed grey of SlotImageUnknownChip: half-open is "a trial will run", which
// is neither an error nor a warning.
const TONES = {
  warn: {
    color: 'var(--warn)',
    borderColor: 'var(--warn-line)',
    background: 'var(--warn-soft)',
  },
  err: {
    color: 'var(--err)',
    borderColor: 'var(--err-line)',
    background: 'var(--err-soft)',
  },
  neutral: {
    color: 'var(--fg-3)',
    borderStyle: 'dashed',
    borderColor: 'var(--fg-3)',
  },
}

export function SlotBreakerChip({ s }) {
  const b = s?.metadata?.breaker
  // `retry_after_s` is computed when the backend built the snapshot; count
  // down from the moment THIS breaker view arrived. Keyed on the view's
  // content so a fresh poll (new retry_after_s) restarts the clock, while a
  // pure re-render does not. The reset runs in an effect, so the first
  // render after a fresh poll can be up to one frame stale — invisible at
  // 1s label granularity, and it keeps the render pure.
  const viewKey = b ? `${b.state}:${b.retry_after_s}:${b.failures}` : ''
  const receivedAt = useRef(Date.now())
  useEffect(() => {
    receivedAt.current = Date.now()
  }, [viewKey])
  const [, setTick] = useState(0)
  const counting = !!b && (b.state === 'backoff' || b.state === 'parked')
  useEffect(() => {
    if (!counting) return undefined
    const t = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [counting, viewKey])

  const chip = breakerChip(s, (Date.now() - receivedAt.current) / 1000)
  if (!chip) return null
  return (
    <span
      className="tag-chip"
      data-testid={`slot-breaker-${s.name}`}
      title={chip.tooltip}
      style={TONES[chip.tone] || TONES.neutral}
    >
      {chip.label}
    </span>
  )
}
