// SERVER ▸ Security — Hermes command approvals.
//
// Switches Hermes's own approvals.mode between smart and manual through
// GET/PUT /api/agents/hermes/command-approvals (hal0.api.agents.command_approvals).
// The PUT re-renders Hermes's config and restarts hal0-agent@hermes; on failure
// it restores the previous state, so the control just re-reads and shows the
// error. An approvals.mode in overrides.yaml wins over the setting, and the
// panel says so instead of presenting the setting as what is in effect.
//
// Not hal0's MCP approval queue (AgentApprovalRow / /api/agent/approvals).
//
// Split in two so the view renders in a server-side unit test without React
// Query: HermesCommandApprovalsView (props in) + HermesCommandApprovalsPanel
// (hooks).
import {
  useHermesCommandApprovals,
  useSetHermesCommandApprovals,
} from '@/api/hooks/useHermesCommandApprovals'
import {
  COMMAND_APPROVALS_INTRO,
  COMMAND_APPROVALS_MODES,
  canReapply,
  commandApprovalsNotices,
  commandApprovalsSaveResult,
} from './commandApprovals.js'

export function HermesCommandApprovalsView({
  status = undefined,
  loading = false,
  error = null,
  pending = false,
  saveError = null,
  saved = null,
  onSelect = undefined,
}) {
  const selected = status?.mode
  const notices = commandApprovalsNotices(status)
  // The current mode stays clickable while config.yaml has drifted from it,
  // so "pick it again" (the drift notice) actually re-applies.
  const reapply = canReapply(status)
  const help = COMMAND_APPROVALS_MODES.find((m) => m.id === selected)?.help

  return (
    <>
      <h3 style={{ margin: '0 0 6px', fontSize: 13 }}>Hermes command approvals</h3>
      <div className="s-panel" data-testid="cmd-approvals" style={{ marginBottom: 16 }}>
        <div className="s-row" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 14, padding: '13px 14px' }}>
          <div>
            <div style={{ fontSize: 13, color: 'var(--fg)' }}>
              Who approves flagged commands
              {status?.source === 'default' && (
                <span className="chip mono" style={{ marginLeft: 8, fontSize: 10, color: 'var(--fg-4)', borderColor: 'var(--line)', background: 'var(--bg-2)' }}>
                  default
                </span>
              )}
            </div>
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-5)', marginTop: 3, lineHeight: 1.55, maxWidth: 460 }}>
              {COMMAND_APPROVALS_INTRO}
              {help && (
                <>
                  <br />
                  <span data-testid="cmd-approvals-help" style={{ color: 'var(--fg-3)' }}>{help}</span>
                </>
              )}
            </div>
          </div>
          <div role="radiogroup" aria-label="Hermes command approvals" style={{ display: 'inline-flex', gap: 6, flexShrink: 0 }}>
            {COMMAND_APPROVALS_MODES.map((m) => {
              const on = selected === m.id
              return (
                <button
                  key={m.id}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  className={on ? 'btn sm' : 'btn ghost sm'}
                  data-testid={'cmd-approvals-' + m.id}
                  title={m.help}
                  disabled={loading || !!error || pending || (on && !reapply)}
                  onClick={() => onSelect && onSelect(m.id)}
                >
                  {pending && !on ? 'Applying…' : m.label}
                </button>
              )
            })}
          </div>
        </div>
        {error && (
          <div data-testid="cmd-approvals-load-error" className="mono err" style={{ fontSize: 10.5, padding: '0 14px 12px', lineHeight: 1.55 }}>
            Could not read the setting: {error}
          </div>
        )}
        {notices.map((n) => (
          <div
            key={n.testid}
            data-testid={n.testid}
            className="mono"
            style={{ fontSize: 10.5, color: n.tone === 'warn' ? 'var(--warn)' : 'var(--fg-4)', padding: '0 14px 12px', lineHeight: 1.55 }}
          >
            {n.text}
          </div>
        ))}
        {saveError && (
          <div data-testid="cmd-approvals-save-error" className="mono err" style={{ fontSize: 10.5, padding: '0 14px 12px', lineHeight: 1.55 }}>
            {saveError}
          </div>
        )}
        {!saveError && saved && (
          <div
            data-testid="cmd-approvals-saved"
            data-tone={saved.tone}
            className="mono"
            style={{ fontSize: 10.5, color: saved.tone === 'warn' ? 'var(--warn)' : 'var(--ok)', padding: '0 14px 12px', lineHeight: 1.55 }}
          >
            {saved.text}
          </div>
        )}
      </div>
    </>
  )
}

export function HermesCommandApprovalsPanel() {
  const q = useHermesCommandApprovals()
  const set = useSetHermesCommandApprovals()
  return (
    <HermesCommandApprovalsView
      status={q.data}
      loading={q.isLoading}
      error={q.isError ? (q.error?.message || 'request failed') : null}
      pending={set.isPending}
      saveError={set.isError ? (set.error?.message || 'could not apply the change') : null}
      saved={set.isSuccess ? commandApprovalsSaveResult(set.data) : null}
      onSelect={(mode) => set.mutate(mode)}
    />
  )
}
