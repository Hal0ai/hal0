// ADR-0029 — "Conversion blocked" badge, shared by the slot cards and the slot
// edit drawer. The classifier lives in slot-status.js (conversionBlockedChip);
// this file owns only the markup and colour tokens.
import React from 'react'

import { conversionBlockedChip } from './slot-status.js'

const WARN = {
  color: 'var(--warn)',
  borderColor: 'var(--warn-line)',
  background: 'var(--warn-soft)',
}

/** The compact chip. Renders nothing for a slot the conversion did not block. */
export function ConversionBlockedChip({ s }) {
  const chip = conversionBlockedChip(s)
  if (!chip) return null
  return (
    <span
      className="tag-chip"
      data-testid={`slot-conversion-blocked-${s.name}`}
      title={chip.tooltip}
      style={WARN}
    >
      {chip.label}
    </span>
  )
}

/** The full explanation, for the drawer: reason, the pre-conversion note and the fix. */
export function ConversionBlockedDetail({ s }) {
  const chip = conversionBlockedChip(s)
  if (!chip) return null
  return (
    <div
      className="hint"
      data-testid="slot-conversion-blocked-detail"
      style={{
        marginTop: 6,
        padding: '8px 10px',
        borderRadius: 'var(--rad-sm)',
        border: '1px solid var(--warn-line)',
        background: 'var(--warn-soft)',
        color: 'var(--warn)',
      }}
    >
      <div>
        <b>Conversion blocked.</b>
        {chip.reason ? ` ${chip.reason}` : ''}
      </div>
      {chip.running && (
        <div data-testid="slot-conversion-blocked-running">
          Still running its pre-conversion tune: it works, but needs fixing.
        </div>
      )}
      {chip.fix && (
        <div className="mono" data-testid="slot-conversion-blocked-fix" style={{ marginTop: 4 }}>
          {chip.fix}
        </div>
      )}
    </div>
  )
}
