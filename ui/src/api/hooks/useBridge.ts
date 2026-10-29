// hal0 dashboard — agent-bridge hooks (ADR-0043).
//
// The bridge lets a coding agent on another machine (a "bridge client") delegate work to hal0's
// Hermes through the hal0-agents MCP server, and read and write hal0's memory. The operator
// mints a client's key here; hal0 stores only a hash of it. Backend: src/hal0/api/routes/bridge.py
// (clients) and the bridge service (runs).
//
//   GET    /api/bridge/clients               every client, revoked ones flagged
//   POST   /api/bridge/clients               add a client; the response carries the key once
//   POST   /api/bridge/clients/{name}/rotate replace the key; the old one dies at once
//   DELETE /api/bridge/clients/{name}        revoke the key (the name stays taken)
//   PATCH  /api/bridge/clients/{name}        replace the client's scopes
//   GET    /api/bridge/runs                  runs delegated through the bridge
//
//   GET    /api/bridge/me                    the caller's own record (an admin session sees `admin`)
//
// While `[bridge].enabled` is false, listing and revoking still work, and adding, rotating and
// re-scoping answer 404 `bridge.disabled` (see `bridgeDisabled`), and so do `/me` and `/runs`.
// `/runs` also answers 503 `bridge.unavailable` when the bridge service is down.
//
// The key reaches the page exactly once, in the create/rotate mutation result. Those two
// mutations use `gcTime: 0` so the result is dropped from the mutation cache the moment nothing
// observes it; callers still `reset()` the mutation when they close the result view.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPatch, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

export type BridgeScope = 'agents' | 'memory' | 'admin'
export type BridgeServer = 'hal0-agents' | 'hal0-memory' | 'hal0-admin'

/** The MCP server a scope reaches. The scope chips and the onboarding lines use these names. */
export const BRIDGE_SCOPE_SERVER: Record<BridgeScope, BridgeServer> = {
  agents: 'hal0-agents',
  memory: 'hal0-memory',
  admin: 'hal0-admin',
}

export interface BridgeClient {
  name: string
  note: string
  scopes: BridgeScope[]
  /** Epoch seconds. */
  created_at: number
  created_by: string
  /** Epoch seconds, or null while the key is live. */
  revoked_at: number | null
  /** Epoch seconds the key last authenticated. The server updates it at most every 60 s. */
  last_seen_at: number | null
}

export interface BridgeOnboardingLine {
  server: BridgeServer
  scope: BridgeScope
  mcp_url: string
  /** A `claude mcp add …` command for this server, with the key in it. */
  claude_mcp_add: string
}

/** What add and rotate answer: the client, its key (shown once), and one line per scope. */
export interface BridgeMinted {
  client: BridgeClient
  key: string
  onboarding: { lines: BridgeOnboardingLine[] }
}

export type BridgeRunStatus =
  | 'queued'
  | 'running'
  | 'waiting_for_approval'
  | 'waiting_for_clarification'
  | 'stopping'
  | 'unknown'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

/**
 * A run that will not change again. Mirrors the backend's `TERMINAL_STATUSES`
 * (src/hal0/bridge/store.py): `unknown` is terminal there too — it marks a run Hermes no longer
 * knows, and the watcher publishes it as `agents.run_failed`. Everything else is live (queued,
 * running, waiting, …).
 */
export const BRIDGE_TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'completed',
  'failed',
  'cancelled',
  'interrupted',
  'unknown',
])

export interface BridgeRun {
  run_id: string
  /**
   * The agent that ran it, already carrying its profile: `hermes`, or `hermes/<profile>` for a
   * named one. Show this as it is; do not append `profile` to it.
   */
  agent: string
  /** The Hermes profile the run used (`default` for the default one); never null. */
  profile: string
  client: string
  status: BridgeRunStatus
  /** Epoch seconds. */
  created_at: number
  updated_at: number
  input_preview: string
  output: string | null
  error: string | null
  usage: Record<string, unknown> | null
  pending: unknown
  last_seq: number
}

export interface BridgeRunsParams {
  client?: string
  status?: string
  limit?: number
}

const CLIENTS_KEY = ['bridge', 'clients'] as const

const ME_KEY = ['bridge', 'me'] as const

const CLIENTS_POLL_MS = 15_000
const ME_STALE_MS = 60_000
const RUNS_LIVE_POLL_MS = 5_000
const RUNS_IDLE_POLL_MS = 30_000

/** True for the 404 `bridge.disabled` that add, rotate and re-scope answer while the bridge is off. */
export function bridgeDisabled(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { code?: unknown }).code === 'bridge.disabled'
  )
}

/** 5 s while any run can still change, else 30 s (and before the first answer). */
export function bridgeRunsPollMs(items: readonly BridgeRun[] | undefined): number {
  const live = (items ?? []).some((r) => !BRIDGE_TERMINAL_STATUSES.has(r.status))
  return live ? RUNS_LIVE_POLL_MS : RUNS_IDLE_POLL_MS
}

/**
 * Whether the bridge is on, read off `GET /api/bridge/me`. The route answers 404
 * `bridge.disabled` exactly when `[bridge].enabled` was off at boot and 200 for an admin session
 * otherwise, so it tells the card up front, before anyone tries to add a client. Only a restart
 * changes the answer, so a `bridge.disabled` is not retried or polled; any other failure (a
 * sign-in prompt, a blip) is not "disabled" and the card keeps offering its actions.
 */
export function useBridgeMe() {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: () => apiGet<{ name: string; tier: string }>(ENDPOINTS.bridgeMe),
    retry: (count, error) => !bridgeDisabled(error) && count < 1,
    staleTime: ME_STALE_MS,
  })
}

export function useBridgeClients() {
  return useQuery({
    queryKey: CLIENTS_KEY,
    queryFn: () => apiGet<{ items: BridgeClient[] }>(ENDPOINTS.bridgeClients),
    refetchInterval: CLIENTS_POLL_MS,
  })
}

function useInvalidateClients() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: CLIENTS_KEY })
}

export interface BridgeClientCreateInput {
  name: string
  note: string
  scopes: BridgeScope[]
}

export function useBridgeClientCreate() {
  const invalidate = useInvalidateClients()
  return useMutation({
    mutationFn: (input: BridgeClientCreateInput) =>
      apiPost<BridgeMinted>(ENDPOINTS.bridgeClients, {
        name: input.name,
        note: input.note,
        scopes: input.scopes,
      }),
    onSuccess: invalidate,
    gcTime: 0,
  })
}

export function useBridgeClientRotate() {
  const invalidate = useInvalidateClients()
  return useMutation({
    mutationFn: (name: string) => apiPost<BridgeMinted>(ENDPOINTS.bridgeClientRotate(name)),
    onSuccess: invalidate,
    gcTime: 0,
  })
}

export function useBridgeClientRevoke() {
  const invalidate = useInvalidateClients()
  return useMutation({
    mutationFn: (name: string) => apiDelete<{ client: BridgeClient }>(ENDPOINTS.bridgeClient(name)),
    onSuccess: invalidate,
  })
}

export function useBridgeClientScopes() {
  const invalidate = useInvalidateClients()
  return useMutation({
    mutationFn: ({ name, scopes }: { name: string; scopes: BridgeScope[] }) =>
      apiPatch<{ client: BridgeClient }>(ENDPOINTS.bridgeClient(name), { scopes }),
    onSuccess: invalidate,
  })
}

function runsPath(params: BridgeRunsParams): string {
  const q = new URLSearchParams()
  if (params.client) q.set('client', params.client)
  if (params.status) q.set('status', params.status)
  if (params.limit != null) q.set('limit', String(params.limit))
  const qs = q.toString()
  return qs ? `${ENDPOINTS.bridgeRuns}?${qs}` : ENDPOINTS.bridgeRuns
}

export function useBridgeRuns(params: BridgeRunsParams = {}) {
  return useQuery({
    queryKey: ['bridge', 'runs', params] as const,
    queryFn: () => apiGet<{ items: BridgeRun[] }>(runsPath(params)),
    refetchInterval: (query) => bridgeRunsPollMs(query.state.data?.items),
  })
}
