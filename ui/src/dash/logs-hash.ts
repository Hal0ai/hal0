// hal0 dashboard — `#logs?unit=<unit>` deep link (hal0#2931).
//
// App (main.jsx `parseRoute`) owns parsing the hash query and hands `unit` to
// LogsView. No React, no API imports, so vitest exercises this without a DOM.
// The backend (`GET /api/logs`) owns the check that a unit is one of hal0's own.

/** The slot a `hal0-slot@<name>[.service]` unit belongs to, or null.
 *  Slot units are already served by the page's slot picker. */
export function slotForUnit(unit: string): string | null {
  const m = /^hal0-slot@(.+?)(?:\.service)?$/.exec(unit)
  return m ? m[1] : null
}
