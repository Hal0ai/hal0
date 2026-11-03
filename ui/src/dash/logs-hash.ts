// hal0 dashboard — `#logs?unit=<unit>` deep link (hal0#2931).
//
// The agent card's Unit logs control opens `#logs?unit=hal0-agent@hermes.service`.
// No React, no API imports, so vitest exercises these without a DOM.
// Unknown query keys are ignored; the backend (`GET /api/logs`) owns the
// check that a unit is one of hal0's own.

/** The `unit` asked for by a `#logs?unit=...` hash, or null. */
export function parseLogsUnit(hash: string): string | null {
  const raw = hash.replace(/^#/, '')
  const qIdx = raw.indexOf('?')
  if (qIdx === -1) return null
  if (raw.slice(0, qIdx) !== 'logs') return null
  const unit = new URLSearchParams(raw.slice(qIdx + 1)).get('unit')
  return unit && unit.trim() ? unit.trim() : null
}

/** The slot a `hal0-slot@<name>[.service]` unit belongs to, or null.
 *  Slot units are already served by the page's slot picker. */
export function slotForUnit(unit: string): string | null {
  const m = /^hal0-slot@(.+?)(?:\.service)?$/.exec(unit)
  return m ? m[1] : null
}

/** The hash that opens the Logs page on `unit`. */
export function logsHashForUnit(unit: string): string {
  return `#logs?unit=${encodeURIComponent(unit)}`
}
