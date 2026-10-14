// Pure copy + state helpers for the Hermes command approvals panel
// (HermesCommandApprovalsPanel.jsx). Kept JSX-free so vitest can import them.

export const COMMAND_APPROVALS_MODES = [
  {
    id: 'smart',
    label: 'Smart',
    help:
      'A call to your hal0/agent model approves a flagged command first. If that call fails or answers anything but APPROVE, Hermes falls back to asking you.',
  },
  {
    id: 'manual',
    label: 'Manual',
    help: 'You approve every flagged command yourself.',
  },
]

export const COMMAND_APPROVALS_INTRO =
  'Hermes asks before it runs a dangerous terminal command or an execute_code script. ' +
  'This only matters while the Hermes terminal tool is on. ' +
  "It is separate from hal0's approval queue for gated MCP tool calls (hal0 agent approvals)."

/**
 * Notices to render under the control, derived from GET /command-approvals.
 * Each is `{ tone: 'warn' | 'muted', text, testid }`.
 */
export function commandApprovalsNotices(status) {
  if (!status) return []
  const out = []
  if (status.source === 'overrides') {
    out.push({
      tone: 'warn',
      testid: 'cmd-approvals-overridden',
      text: `In effect: ${status.effective}. ${status.overrides_path} sets approvals.mode, which wins over this setting. Remove it there to use the choice above.`,
    })
  }
  if (status.seed_unreadable) {
    out.push({
      tone: 'warn',
      testid: 'cmd-approvals-unreadable',
      text: `${status.seed_path} could not be read, so the stored setting is unknown and the default (${status.default}) is in use. Fix the file or run sudo hal0 agent reprovision hermes --repair.`,
    })
  }
  if (status.invalid_value) {
    out.push({
      tone: 'warn',
      testid: 'cmd-approvals-invalid',
      text: `The stored value "${status.invalid_value}" is invalid, so the default (${status.default}) is used. Pick a mode to fix it.`,
    })
  }
  if (status.in_sync === false) {
    out.push({
      tone: 'warn',
      testid: 'cmd-approvals-drift',
      text:
        status.source === 'overrides'
          ? `Hermes's config.yaml says "${status.rendered}", not "${status.effective}" from overrides.yaml. Run sudo hal0 agent reprovision hermes to re-apply it.`
          : `Hermes's config.yaml says "${status.rendered}", not "${status.effective}". Pick ${status.effective} again to re-apply it.`,
    })
  }
  if (status.rendered == null) {
    out.push({
      tone: 'muted',
      testid: 'cmd-approvals-not-provisioned',
      text: 'Hermes is not provisioned on this box yet; the setting applies when it is.',
    })
  }
  return out
}

/** True when re-picking the current mode re-applies it (config.yaml drifted). */
export function canReapply(status) {
  return !!status && status.in_sync === false && status.source !== 'overrides'
}

/** Result line after a save: `{ tone: 'ok' | 'warn', text }`, or null. */
export function commandApprovalsSaveResult(result) {
  if (!result || !result.message) return null
  const uncertain = result.restart === 'unknown' || result.restart === 'timeout'
  return { tone: uncertain ? 'warn' : 'ok', text: result.message }
}
