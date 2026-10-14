// hal0 v3 dashboard — Hermes command approvals (Settings ▸ Security).
//
// Hermes's own gate on dangerous terminal commands / execute_code scripts
// (Hermes's `approvals.mode`):
//   smart  — a call to the hal0/agent model approves a flagged command first;
//            a failure or any answer but APPROVE falls back to asking a human.
//   manual — a human approves every flagged command.
//
// NOT the hal0 MCP approval queue (/api/agent/approvals, AgentApprovalRow),
// which gates hal0 MCP tool calls.
//
// Backend: hal0.api.agents.command_approvals. The PUT persists the setting,
// re-renders Hermes's config and restarts hal0-agent@hermes when it is running;
// on any failure it restores the previous state and returns an error envelope.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPut } from '../client'
import { ENDPOINTS } from '../endpoints'

export type CommandApprovalsMode = 'smart' | 'manual'

export interface CommandApprovalsStatus {
  agent_id: string
  /** The operator setting (default when unset or invalid). */
  mode: CommandApprovalsMode
  /** What Hermes is configured to use; an overrides.yaml value can be anything. */
  effective: string
  source: 'default' | 'setting' | 'overrides'
  default: CommandApprovalsMode
  allowed: CommandApprovalsMode[]
  invalid_value: string | null
  /** The seed TOML exists but could not be read; the stored choice is unknown. */
  seed_unreadable: boolean
  seed_path: string
  overrides_path: string
  /** approvals.mode in Hermes's config.yaml now; null when not provisioned. */
  rendered: string | null
  in_sync: boolean | null
}

export interface CommandApprovalsResult extends CommandApprovalsStatus {
  changed: boolean
  restart:
    | 'restarted'
    | 'skipped-inactive'
    | 'skipped-overridden'
    | 'skipped-not-installed'
    | 'not-needed'
    /** hal0 could not tell whether Hermes is running; restart it to apply. */
    | 'unknown'
    /** The restart did not finish in time; whether Hermes is running is unknown. */
    | 'timeout'
  message: string
}

const AGENT = 'hermes'
const QUERY_KEY = ['agent-command-approvals', AGENT] as const

/** GET /api/agents/hermes/command-approvals */
export function useHermesCommandApprovals() {
  return useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => apiGet<CommandApprovalsStatus>(ENDPOINTS.agentCommandApprovals(AGENT)),
    retry: false,
  })
}

/** PUT /api/agents/hermes/command-approvals {mode} */
export function useSetHermesCommandApprovals() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (mode: CommandApprovalsMode) =>
      apiPut<CommandApprovalsResult>(ENDPOINTS.agentCommandApprovals(AGENT), { mode }),
    onSuccess: (data) => {
      qc.setQueryData(QUERY_KEY, data)
    },
    // A failed apply was rolled back server-side; re-read so the control
    // reflects the restored state rather than the optimistic click.
    onError: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })
}
