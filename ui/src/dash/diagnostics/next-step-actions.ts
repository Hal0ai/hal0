// hal0 dashboard — NextStep -> UI action mapping (D6 remediation chips).
//
// `src/hal0/health_report.py` emits `NextStep{kind, label, target}` rows —
// the same three fields whichever check emitted them (see that module's
// "shared remediation commands" section, the one owner for the literal
// command strings below). A `command` step is always safe to copy; a small
// allowlisted subset also maps onto an EXISTING typed mutation so the panel
// can offer "Run" instead of "copy, open a terminal, paste" — restarting one
// of the systemd units `hal0.api.routes.installer._REPAIRABLE_UNITS` already
// allows (`useServiceRepair`), or restarting one named runner slot
// (`useSlotRestart`, `POST /api/slots/{name}/restart`).
//
// Deliberately NOT string-sniffing every possible command: an unmapped
// `command` step still copies fine, it just has no "Run" button. Adding a
// new mapping here always pairs with adding the matching literal in
// health_report.py's remediation-command section — one owner per string.

export type NextStepKind = 'command' | 'manual' | 'doc'

export interface NextStep {
  kind: NextStepKind
  label: string
  target: string
}

export type NextStepAction =
  | { kind: 'serviceRestart'; unit: string }
  | { kind: 'slotRestart'; slot: string }

// command target (verbatim, as printed) -> systemd unit `useServiceRepair`
// restarts. Mirrors `hal0.api.routes.installer._REPAIRABLE_UNITS` (the
// bare-name half — the mutation appends `.service`/uses the literal unit).
const SERVICE_RESTART_UNITS: Readonly<Record<string, string>> = {
  'systemctl restart hal0-api': 'hal0-api.service',
  'systemctl restart hindsight-api': 'hindsight-api.service',
  'systemctl restart hal0-openwebui': 'hal0-openwebui.service',
  'systemctl restart hal0-agent@hermes': 'hal0-agent@hermes.service',
}

const SLOT_RESTART_RE = /^hal0 slot restart (\S+)$/

/** `null` when the step has no matching typed action — copy-only. */
export function actionForNextStep(step: Pick<NextStep, 'kind' | 'target'>): NextStepAction | null {
  if (step.kind !== 'command') return null
  const unit = SERVICE_RESTART_UNITS[step.target]
  if (unit) return { kind: 'serviceRestart', unit }
  const m = SLOT_RESTART_RE.exec(step.target)
  if (m) return { kind: 'slotRestart', slot: m[1] }
  return null
}

/** True when `target` is an absolute URL (`doc` steps may be either an
 *  external https://hal0.dev/... link or an in-app `/docs/...` / `#...` route). */
export function isExternalDocTarget(target: string): boolean {
  return /^https?:\/\//i.test(target)
}

/** Open a `doc` step's target the way the rest of the dashboard opens links:
 *  a new tab for an external URL, an in-app route change for a hash/path. */
export function openDocTarget(target: string): void {
  if (typeof window === 'undefined') return
  if (isExternalDocTarget(target)) {
    window.open(target, '_blank', 'noopener')
    return
  }
  if (target.startsWith('#')) {
    window.location.hash = target.slice(1)
    return
  }
  // A docs-site path (e.g. "/docs/operate/services/#mdns-discovery-toggle")
  // — the dashboard doesn't serve docs itself, so this always opens externally.
  window.open(`https://hal0.dev${target}`, '_blank', 'noopener')
}
