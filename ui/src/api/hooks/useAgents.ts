// hal0 v3 dashboard — bundled-agent rollup hooks (v0.3 PR-6).
//
// Powers the SidebarAgentBlock. Wires the read-only surface the sidebar
// needs to render at a glance:
//
//   - GET /api/agents                              — installed bundled agents
//                                                    (ADR-0004 §2, lifecycle)
//   - GET /api/agents/{id}/personas                — persona list + active id
//                                                    (PR-4 #399, v0.3)
//   - GET /api/agent/approvals                     — pending approvals queue
//                                                    (ADR-0004 §5)
//   - GET /api/agents/skills                       — flat skills list (NEW;
//                                                    expected route, not yet
//                                                    merged in this branch —
//                                                    hook degrades to "—")
//   - GET /api/agents/hermes/memory/stats          — memory writes counter
//                                                    (NEW; not yet merged —
//                                                    hook falls back to
//                                                    GET /api/memory/list
//                                                    item count, then "—")
//   - GET /api/mcp/servers                         — bundled MCP-server state
//                                                    pip (reuses #206 surface)
//
// The state-aware agent cards' overview / repair / gateway-restart / uninstall /
// command-approvals / log-tail hooks sit at the foot of the file.
//
// Polling cadence: 5s refetch + revalidate-on-focus (master plan §2
// state-mgmt policy). Each individual hook can be overridden if a more
// or less frequent cadence makes sense for the specific surface.
//
// Graceful degradation: when a route 404s (older backend or path not
// yet merged) the hook returns `null` for the metric AND logs a single
// `hal0.sidebar.endpoint_missing` warning to the console so the
// operator notices on the network tab. Subsequent 404s on the same
// path are swallowed — we don't want to drown the console on every
// 5s tick.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'
import { apiDelete, apiGet, apiPost, apiPut, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'
import type { CommandApprovalsMode, CommandApprovalsResult } from './useHermesCommandApprovals'
import type { UnitLogs } from './useServices'

// ── Types ──────────────────────────────────────────────────────────

export interface AgentRecord {
  name: string
  installed_at: string
  /** On-disk install state from AgentManager.list() — "installed" / "broken".
   *  NOT liveness: it says a bundle exists, nothing about the systemd unit. */
  status: string
  /** systemd unit liveness from `systemctl is-active hal0-agent@{name}`
   *  (#1459). `true` active, `false` installed-but-down, `null`/absent
   *  unknown — an older backend, no systemd, or an agent with no template
   *  unit. Unknown must NEVER be rendered as healthy. */
  unit_active?: boolean | null
}

export interface AgentList {
  agents: AgentRecord[]
  count: number
}

export interface PersonaSummary {
  id: string
  display_name: string
  description?: string
  active: boolean
}

export interface PersonaList {
  agent_id: string
  active: string | null
  personas: PersonaSummary[]
}

export interface ApprovalEntry {
  id: string
  tool: string
  args: Record<string, unknown>
  client_id?: string
  /** Epoch seconds (float) from ApprovalEntry.as_dict() — NOT an ISO string. */
  enqueued_at?: number
  state?: string
}

export interface ApprovalList {
  approvals: ApprovalEntry[]
}

export interface AgentSkill {
  name: string
  cap?: string
  policy?: string
  src?: string
}

export interface AgentSkillsResponse {
  skills: AgentSkill[]
  count: number
}

export interface MemoryStats {
  writes: number | null
  items?: number
}

// ── 404-once warning helper ────────────────────────────────────────
//
// Sidebar block polls every 5s; a missing route would spew a warning
// every tick. Keep a per-path Set so each path warns exactly once per
// session — that's loud enough to catch in DevTools without crowding
// the console.

const _warnedPaths = new Set<string>()

function _warnMissing(path: string, err: Hal0Error): void {
  if (_warnedPaths.has(path)) return
  _warnedPaths.add(path)
  // eslint-disable-next-line no-console
  console.warn(
    'hal0.sidebar.endpoint_missing',
    JSON.stringify({ path, status: err.status, code: err.code }),
  )
}

function _isMissing(err: unknown): err is Hal0Error {
  return (
    err instanceof Hal0Error &&
    (err.status === 404 || err.status === 0 || err.status === 501)
  )
}

// ── Polling defaults ───────────────────────────────────────────────

const SIDEBAR_POLL_MS = 5_000

// ── /api/agents — installed bundled agents ─────────────────────────

export function useAgents(): UseQueryResult<AgentList> {
  return useQuery({
    queryKey: ['agents', 'list'],
    queryFn: async () => {
      try {
        const body = await apiGet<AgentList>(ENDPOINTS.agents)
        // Tolerate either {agents:[...], count} or [].
        if (Array.isArray(body)) {
          return { agents: body, count: body.length }
        }
        if (body && Array.isArray(body.agents)) {
          return body
        }
        return { agents: [], count: 0 }
      } catch (err) {
        if (_isMissing(err)) {
          _warnMissing(ENDPOINTS.agents, err)
          return { agents: [], count: 0 }
        }
        throw err
      }
    },
    refetchInterval: SIDEBAR_POLL_MS,
    refetchOnWindowFocus: true,
  })
}

// ── POST /api/agents/{id}/restart ──────────────────────────────────
//
// Wraps `systemctl restart <unit>` for a bundled agent. The backend
// returns { status: "restarted" | "restarting" | "error", detail } — a
// non-zero systemctl surfaces as a 500 Hal0Error (caught by the caller
// to render a toast). On success we invalidate both the agents list and
// the slots query: a Hermes bounce re-establishes its slot upstreams, so
// the live telemetry on the card should re-converge promptly.

export interface AgentRestartResult {
  status: 'restarted' | 'restarting' | 'error'
  detail?: string
}

export function useAgentRestart(agentId: string) {
  const qc = useQueryClient()
  return useMutation<AgentRestartResult, Error, void>({
    mutationFn: () => apiPost<AgentRestartResult>(ENDPOINTS.agentRestart(agentId)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agents', 'list'] })
      qc.invalidateQueries({ queryKey: ['slots'] })
    },
  })
}

// ── /api/agents/{id}/personas ──────────────────────────────────────

export function useAgentPersonas(agentId: string | null | undefined): UseQueryResult<PersonaList> {
  return useQuery({
    queryKey: ['agents', 'personas', agentId],
    queryFn: async () => {
      if (!agentId) {
        return { agent_id: '', active: null, personas: [] }
      }
      const path = ENDPOINTS.agentPersonas(agentId)
      try {
        const body = await apiGet<PersonaList>(path)
        return body && Array.isArray(body.personas)
          ? body
          : { agent_id: agentId, active: null, personas: [] }
      } catch (err) {
        if (_isMissing(err)) {
          _warnMissing(path, err)
          return { agent_id: agentId, active: null, personas: [] }
        }
        throw err
      }
    },
    enabled: !!agentId,
    refetchInterval: SIDEBAR_POLL_MS,
    refetchOnWindowFocus: true,
  })
}

// ── /api/agent/approvals (count badge) ─────────────────────────────

export function useAgentApprovalsCount(): UseQueryResult<number> {
  return useQuery({
    queryKey: ['agents', 'approvals', 'count'],
    queryFn: async () => {
      try {
        const body = await apiGet<ApprovalList>(ENDPOINTS.agentApprovals)
        return Array.isArray(body?.approvals) ? body.approvals.length : 0
      } catch (err) {
        if (_isMissing(err)) {
          _warnMissing(ENDPOINTS.agentApprovals, err)
          return 0
        }
        throw err
      }
    },
    refetchInterval: SIDEBAR_POLL_MS,
    refetchOnWindowFocus: true,
  })
}

// ── /api/agents/skills (count rollup) ──────────────────────────────

export function useAgentSkills(): UseQueryResult<number | null> {
  return useQuery<number | null>({
    queryKey: ['agents', 'skills', 'count'],
    queryFn: async () => {
      try {
        const body = await apiGet<AgentSkillsResponse | AgentSkill[]>(
          ENDPOINTS.agentSkills,
        )
        if (Array.isArray(body)) return body.length
        if (body && Array.isArray((body as AgentSkillsResponse).skills)) {
          const r = body as AgentSkillsResponse
          return typeof r.count === 'number' ? r.count : r.skills.length
        }
        return null
      } catch (err) {
        if (_isMissing(err)) {
          _warnMissing(ENDPOINTS.agentSkills, err)
          return null
        }
        throw err
      }
    },
    refetchInterval: SIDEBAR_POLL_MS,
    refetchOnWindowFocus: true,
  })
}

// ── memory writes (best-effort) ────────────────────────────────────
//
// Tries /api/agents/hermes/memory/stats first (the v0.3 design),
// falls back to /api/memory/list count, returns null if both 404.

export function useAgentMemoryWrites(): UseQueryResult<number | null> {
  return useQuery<number | null>({
    queryKey: ['agents', 'memory', 'writes'],
    queryFn: async () => {
      try {
        const body = await apiGet<MemoryStats>(ENDPOINTS.agentMemoryStats('hermes'))
        if (body && typeof body.writes === 'number') return body.writes
        if (body && typeof body.items === 'number') return body.items
        return null
      } catch (err) {
        if (_isMissing(err)) {
          _warnMissing(ENDPOINTS.agentMemoryStats('hermes'), err)
          // Fall through to /api/memory/list count — already wired in
          // useMemory.ts but we hit the raw endpoint here to keep this
          // hook self-contained.
          try {
            const fallback = await apiGet<{ items: unknown[] }>(
              '/api/memory/list?dataset=shared&limit=1',
            )
            return Array.isArray(fallback?.items) ? fallback.items.length : null
          } catch {
            return null
          }
        }
        throw err
      }
    },
    refetchInterval: SIDEBAR_POLL_MS,
    refetchOnWindowFocus: true,
  })
}

// ── MCP-server status pip ──────────────────────────────────────────
//
// Reads the same /api/mcp/servers introspection surface useMcpServers
// (#206) consumes. We only care about hal0-memory + hal0-admin states
// for the sidebar pip; everything else is the MCP page's concern.

export type McpPipState = 'green' | 'yellow' | 'red' | 'unknown'

export interface McpPipRollup {
  state: McpPipState
  servers: { id: string; name: string; state: string }[]
}

interface McpServerRow {
  id?: string
  name?: string
  state?: string
  bundled?: boolean
}

const BUNDLED_MCP_IDS = ['hal0-memory', 'hal0-admin']

function _rollupMcpState(rows: McpServerRow[]): McpPipRollup {
  const filtered = rows.filter(
    (s) =>
      s.bundled === true ||
      (typeof s.id === 'string' && BUNDLED_MCP_IDS.includes(s.id)) ||
      (typeof s.name === 'string' && BUNDLED_MCP_IDS.includes(s.name)),
  )
  if (filtered.length === 0) {
    return { state: 'unknown', servers: [] }
  }
  let anyFailed = false
  let anyStopped = false
  let allRunning = true
  const servers = filtered.map((s) => {
    const state = s.state ?? 'unknown'
    if (state !== 'running') allRunning = false
    if (state === 'failed') anyFailed = true
    if (state === 'stopped') anyStopped = true
    return {
      id: String(s.id ?? s.name ?? '?'),
      name: String(s.name ?? s.id ?? '?'),
      state,
    }
  })
  let state: McpPipState = 'green'
  if (anyFailed) state = 'red'
  else if (anyStopped || !allRunning) state = 'yellow'
  return { state, servers }
}

export function useMcpStatusPip(): UseQueryResult<McpPipRollup> {
  return useQuery<McpPipRollup>({
    queryKey: ['agents', 'mcp', 'pip'],
    queryFn: async () => {
      try {
        const body = await apiGet<{ servers: McpServerRow[] } | McpServerRow[]>(
          ENDPOINTS.mcpServers,
        )
        const rows: McpServerRow[] = Array.isArray(body)
          ? body
          : Array.isArray(body?.servers)
            ? body.servers
            : []
        return _rollupMcpState(rows)
      } catch (err) {
        if (_isMissing(err)) {
          _warnMissing(ENDPOINTS.mcpServers, err)
          return { state: 'unknown', servers: [] }
        }
        throw err
      }
    },
    refetchInterval: SIDEBAR_POLL_MS,
    refetchOnWindowFocus: true,
  })
}

// ── Composed rollup (matches SidebarAgentBlock render contract) ────
//
// One hook the component calls; bundles the underlying queries so the
// caller doesn't juggle five `isLoading` flags. Per-field nullability
// lets the JSX render "—" for missing endpoints without short-circuiting
// the whole block.

export interface SidebarAgentRollup {
  installed: boolean
  agentId: string | null
  agentStatus: 'running' | 'down' | 'broken' | 'unknown' | 'not_installed'
  personaName: string | null
  approvalsPending: number
  skillsCount: number | null
  memoryWrites: number | null
  mcpPip: McpPipRollup
  // Loading flag is true when the FIRST agent.list fetch is still in
  // flight; everything else gates on this so the empty-state CTA
  // doesn't flash before the data arrives.
  loading: boolean
}

export function useSidebarAgentRollup(): SidebarAgentRollup {
  const agents = useAgents()
  const first = agents.data?.agents?.[0]
  const agentId = first?.name ?? null
  const personas = useAgentPersonas(agentId)
  const approvals = useAgentApprovalsCount()
  const skills = useAgentSkills()
  const memory = useAgentMemoryWrites()
  const mcp = useMcpStatusPip()

  const installed = !!first
  let agentStatus: SidebarAgentRollup['agentStatus'] = 'not_installed'
  if (first) {
    // #1459: `installed` is an on-disk fact. Liveness comes from the unit
    // probe the route now runs — and an absent/null probe is UNKNOWN, never
    // "running", so a box where liveness can't be observed can't read green.
    if (first.status === 'broken') agentStatus = 'broken'
    else if (first.status === 'installed') {
      agentStatus =
        first.unit_active === true ? 'running' : first.unit_active === false ? 'down' : 'unknown'
    } else agentStatus = 'unknown'
  }

  const activeId = personas.data?.active
  const activeRow = personas.data?.personas.find((p) => p.id === activeId)
  const personaName =
    activeRow?.display_name ?? (activeId ? activeId : null)

  return {
    installed,
    agentId,
    agentStatus,
    personaName,
    approvalsPending: approvals.data ?? 0,
    skillsCount: skills.data ?? null,
    memoryWrites: memory.data ?? null,
    mcpPip: mcp.data ?? { state: 'unknown', servers: [] },
    loading: agents.isLoading,
  }
}

// ── /api/agent/approvals — full list (for ApprovalModal) ───────────────────

export function useApprovalList(): UseQueryResult<ApprovalList> {
  return useQuery<ApprovalList>({
    queryKey: ['agents', 'approvals', 'list'],
    queryFn: async () => {
      try {
        const body = await apiGet<ApprovalList>(ENDPOINTS.agentApprovals)
        return { approvals: Array.isArray(body?.approvals) ? body.approvals : [] }
      } catch (err) {
        if (_isMissing(err)) {
          _warnMissing(ENDPOINTS.agentApprovals, err)
          return { approvals: [] }
        }
        throw err
      }
    },
    refetchInterval: SIDEBAR_POLL_MS,
    refetchOnWindowFocus: true,
  })
}

export function useApproveApproval() {
  const qc = useQueryClient()
  return useMutation({
    // TODO endpoints.ts (ui-sweep-b owns) — inline paths for now
    mutationFn: (id: string) =>
      apiPost<unknown>(`/api/agent/approvals/${encodeURIComponent(id)}/approve`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents', 'approvals'] }),
  })
}

export function useDenyApproval() {
  const qc = useQueryClient()
  return useMutation({
    // TODO endpoints.ts (ui-sweep-b owns) — inline paths for now
    mutationFn: (id: string) =>
      apiPost<unknown>(`/api/agent/approvals/${encodeURIComponent(id)}/deny`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents', 'approvals'] }),
  })
}

// ── Persona detail + activate ────────────────────────────────────────────────
// GET  /api/agents/{id}/personas/{pid}           — full persona detail
// POST /api/agents/{id}/personas/{pid}/activate  — make it the active persona
// (src/hal0/api/agents/personas.py). Unlike the sidebar rollups above, a 404
// here means the persona is genuinely gone, so the detail query throws and the
// caller renders the error rather than a fabricated empty persona.

export interface PersonaDetail {
  id: string
  display_name: string
  summary: string
  system_prompt: string
  tools_allowed: string[]
  memory_namespace: string
  preferred_upstream: string
  preferred_model: string
  active: boolean
}

export function useAgentPersona(
  agentId: string,
  pid: string | null,
  opts?: { enabled?: boolean },
): UseQueryResult<PersonaDetail> {
  return useQuery<PersonaDetail>({
    // Nested under ['agents','personas',agentId] so activating or editing a
    // persona (both invalidate that prefix) also refreshes the open detail.
    queryKey: ['agents', 'personas', agentId, pid],
    queryFn: () => apiGet<PersonaDetail>(ENDPOINTS.agentPersona(agentId, pid as string)),
    enabled: !!pid && (opts?.enabled ?? true),
  })
}

export function usePersonaActivate(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (pid: string) => apiPost<unknown>(ENDPOINTS.agentPersonaActivate(agentId, pid)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents', 'personas', agentId] }),
  })
}

// ── Persona update mutation ──────────────────────────────────────────────────
// PUT /api/agents/{id}/personas/{pid} — built by backend-dev (#task7).
// 404 degrades gracefully: hook throws so the modal can show an error toast.

// Partial-patch body for PUT /api/agents/{id}/personas/{pid} (PR #736).
// id is immutable; all other fields are optional.
export interface PersonaUpdateBody {
  display_name?: string
  summary?: string
  system_prompt?: string
  tools_allowed?: string[]
  memory_namespace?: string
  preferred_upstream?: string
  preferred_model?: string
  approval?: {
    default_policy?: string
    auto_approve?: string[]
    require_approval?: string[]
  }
}

export function usePersonaUpdate(agentId: string) {
  const qc = useQueryClient()
  return useMutation({
    // Contract: PUT /api/agents/{id}/personas/{pid} → 200 full persona detail
    mutationFn: ({ pid, body }: { pid: string; body: PersonaUpdateBody }) =>
      apiPut<unknown>(
        ENDPOINTS.agentPersona(agentId, pid),
        body as unknown as Record<string, unknown>,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents', 'personas', agentId] }),
  })
}

// ── /api/agents/persona-enums ────────────────────────────────────────
// Merged in from main during the v0.3 integration: needed by flow-modals.jsx.

export interface PersonaTone {
  id: string
  label: string
  desc: string
}

export interface PersonaTool {
  id: string
  label: string
  cap: string
}

export interface PersonaEnumsResponse {
  tones: PersonaTone[]
  tools: PersonaTool[]
}

export function useAgentPersonaEnums(options?: { enabled?: boolean }) {
  return useQuery<PersonaEnumsResponse>({
    queryKey: ['agents', 'persona-enums'],
    queryFn: () => apiGet<PersonaEnumsResponse>(ENDPOINTS.agentPersonaEnums),
    staleTime: 5 * 60_000,
    enabled: options?.enabled ?? true,
  })
}

// ── State-aware agent cards (agent-cards controls, task 15) ──────────────────
//
//   GET    /api/agents/{id}/overview            — one read for a card's face
//   POST   /api/agents/{id}/repair              — queue a repair job
//   POST   /api/agents/{id}/gateway/restart     — bounce the Hermes gateway unit
//   DELETE /api/agents/{id}                     — uninstall
//   PUT    /api/agents/{id}/command-approvals   — Hermes approvals.mode
//   GET    /api/logs?unit=&n=                   — journal tail for the down face
//
// Upgrade is not here: the card calls useComponentConverge and tracks the job
// with useUpdateJob, both republished through agents-hook-bridge.ts.

/** Overview and log-tail poll at 10 s; pass `enabled: false` while the Agents
 *  tab is not shown. */
const OVERVIEW_POLL_MS = 10_000

export type AgentPhase =
  | 'working'
  | 'broken'
  | 'down'
  | 'degraded'
  | 'serving'
  | 'ready'
  | 'installed'
  | 'not_installed'
  | 'unknown'

/** The one-click fix, if any, a degraded row offers. */
export type AgentFix =
  | 'restart_gateway'
  | 'repair'
  | 'resync_approvals'
  | 'cli'
  | 'none'

export interface AgentDegradedRow {
  reason: string
  detail?: string
  fix: AgentFix
  command?: string | null
}

export interface AgentDoctorRow {
  id: string
  /** `unreadable`: the check could not read its source (the gateway unit row
   *  on a box where the unit file is not readable to the daemon). */
  status: 'ok' | 'warn' | 'fail' | 'absent' | 'unreadable'
  detail?: string
}

export interface AgentProvisionPhase {
  name: string
  status: string
  at?: string | null
  failure_count?: number
  reason?: string | null
}

export interface AgentOverview {
  name: string
  /** "daemon" (Hermes) or "cli" (Pi). */
  kind: string
  installed: boolean
  /** On-disk install state ("installed" / "broken"); liveness is `units`. */
  status?: string
  unit_active?: boolean | null
  /** Body-level timestamp the server stamps on the read. */
  generated_at?: string
  /** The newest queued/running converge:hermes or repair:hermes job. */
  job?: { id: string; kind?: string; state: string; [key: string]: unknown } | null
  /** Server-computed face. The UI upgrades `ready` to `serving` when a slot is
   *  actually bound, exactly as `_derive` does. */
  phase?: AgentPhase
  degraded?: AgentDegradedRow[]
  doctor?: AgentDoctorRow[]
  /** Hermes: live/previous/pin/component. Pi: installed/pin. Not installed:
   *  only `pin` appears at the top level (below). */
  version?: {
    live?: Record<string, unknown> | null
    previous?: Record<string, unknown> | null
    installed?: Record<string, unknown> | null
    pin?: Record<string, unknown> | null
    component?: { installed?: unknown; pinned?: unknown; status?: string; [key: string]: unknown }
    update_available?: boolean
    [key: string]: unknown
  }
  /** Not-installed agents: the pin the install would get. */
  pin?: Record<string, unknown>
  units?: {
    agent?: 'active' | 'inactive' | 'failed' | 'unknown' | string
    gateway?: 'active' | 'inactive' | 'failed' | 'unknown' | 'absent' | string
    /** Whether the gateway unit is enabled; null when it cannot be told. */
    gateway_enabled?: boolean | null
    [key: string]: unknown
  }
  api_server?: { endpoint?: string; healthy?: boolean; profiles?: string[]; [key: string]: unknown }
  platforms?: string[]
  provision?: {
    present?: boolean
    hal0_version?: string | null
    hermes_version?: string | null
    completed_at?: string | null
    /** When provision.json was written, and whether it predates the live build. */
    reported_at?: string | null
    stale?: boolean
    live_built_at?: string | null
    phases?: AgentProvisionPhase[]
    ok?: number
    warn?: number
    fail?: number
    primary_model?: string | null
    delegation_model?: string | null
    chat_slot_count?: number | null
    mcp_server_count?: number | null
    memory_provider?: string | null
    persona?: string | null
    terminal_tool?: 'on' | 'off' | null
    approvals_mode?: string | null
    [key: string]: unknown
  }
  approvals?: {
    mode?: string | null
    effective?: string | null
    /** null when Hermes is not provisioned (nothing rendered to compare). */
    in_sync?: boolean | null
    pending?: number
    [key: string]: unknown
  }
  /** Hermes: {available, writes, last_write}. Pi: {mcp_entry, hindsight_config,
   *  hindsight_api_reachable}. */
  memory?: Record<string, unknown>
  links?: { desktop?: string | null; [key: string]: unknown }
  /** Pi only. */
  profile?: {
    home?: string
    profiled_at?: string | null
    home_readable?: boolean
    checks?: { id: string; ok: boolean | null; detail?: string }[]
    [key: string]: unknown
  }
  /** Pi only: the verbs to show as copyable commands. */
  commands?: { install?: string; upgrade?: string; uninstall?: string; [key: string]: unknown }
  [key: string]: unknown
}

/**
 * GET /api/agents/{name}/overview. `null` (never a throw) on 404/501/0 so a
 * daemon that predates the route renders the old card; one warning per path.
 * Pass `{ enabled: false }` to hold the poll while the Agents tab is hidden.
 */
export function useAgentOverview(
  name: string,
  opts?: { enabled?: boolean },
): UseQueryResult<AgentOverview | null> {
  return useQuery<AgentOverview | null>({
    queryKey: agentOverviewKey(name),
    queryFn: async () => {
      const path = ENDPOINTS.agentOverview(name)
      try {
        const body = await apiGet<AgentOverview>(path)
        return body && typeof body === 'object' ? body : null
      } catch (err) {
        if (_isMissing(err)) {
          _warnMissing(path, err)
          return null
        }
        throw err
      }
    },
    enabled: opts?.enabled !== false,
    // A poll that fails is retried by the next tick, not by backoff on top.
    retry: false,
    refetchInterval: OVERVIEW_POLL_MS,
    refetchOnWindowFocus: true,
  })
}

/** The query key `useAgentOverview(name)` caches under. */
export function agentOverviewKey(name: string) {
  return ['agents', 'overview', name] as const
}

/**
 * Read `name`'s overview with `?refresh=1` and put it in the cache. The route
 * keeps its slow probes (gateway, API server, degraded reasons, approvals, Pi's
 * version and checks) for 30 s, so a plain refetch right after an action can
 * answer with the state from before it; a refresh rebuilds them. A plain poll
 * still in flight would land after this with the older answer, so it is
 * cancelled first. Resolves with what was cached: `null` on 404/501/0, as the
 * hook does; any other error rejects and leaves the cache alone.
 */
export async function refreshAgentOverview(
  qc: QueryClient,
  name: string,
): Promise<AgentOverview | null> {
  const path = ENDPOINTS.agentOverview(name)
  let data: AgentOverview | null
  try {
    const body = await apiGet<AgentOverview>(`${path}?refresh=1`)
    data = body && typeof body === 'object' ? body : null
  } catch (err) {
    if (!_isMissing(err)) throw err
    _warnMissing(path, err)
    data = null
  }
  await qc.cancelQueries({ queryKey: agentOverviewKey(name), exact: true })
  qc.setQueryData(agentOverviewKey(name), data)
  return data
}

/** The queued job snapshot the repair route returns; feed `id` to useUpdateJob. */
export interface AgentJobSnapshot {
  id: string
  state?: string
  phase?: string
  [key: string]: unknown
}

/** POST /api/agents/{name}/repair — returns the queued job. */
export function useAgentRepair(name: string) {
  const qc = useQueryClient()
  return useMutation<AgentJobSnapshot, Error, void>({
    mutationFn: () => apiPost<AgentJobSnapshot>(ENDPOINTS.agentRepair(name)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agents'] })
      qc.invalidateQueries({ queryKey: ['updates'] })
    },
  })
}

/** POST /api/agents/{name}/gateway/restart — result typed like the agent restart's. */
export function useAgentGatewayRestart(name: string) {
  const qc = useQueryClient()
  return useMutation<AgentRestartResult, Error, void>({
    mutationFn: () => apiPost<AgentRestartResult>(ENDPOINTS.agentGatewayRestart(name)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agents'] })
    },
  })
}

export interface AgentUninstallResidual {
  path: string
  reason: string
}

/**
 * Outcome of DELETE /api/agents/{name}. The route answers 200 `{name, status}`
 * for `uninstalled` / `not_installed`, but a partial removal is a 207 whose
 * body is the error envelope (`{error:{code, message, details}}`), and
 * `client.ts` only throws on a non-2xx, so the hook lifts `details` out and
 * returns one shape. `removed` is whether the call found anything to remove;
 * `residual` lists what the daemon user could not delete.
 */
export type AgentUninstallResult =
  | { name: string; status: 'uninstalled' | 'not_installed' }
  | {
      name: string
      status: 'partially_uninstalled'
      removed: boolean
      residual: AgentUninstallResidual[]
    }

interface UninstallBody {
  name?: string
  status?: string
  error?: {
    details?: {
      name?: string
      status?: string
      removed?: boolean
      residual?: AgentUninstallResidual[]
    }
  }
}

function _normaliseUninstall(name: string, body: UninstallBody | null): AgentUninstallResult {
  const details = body?.error?.details
  if (details?.status === 'partially_uninstalled') {
    return {
      name: details.name ?? name,
      status: 'partially_uninstalled',
      removed: details.removed === true,
      residual: Array.isArray(details.residual) ? details.residual : [],
    }
  }
  if (body?.status === 'uninstalled' || body?.status === 'not_installed') {
    return { name: body.name ?? name, status: body.status }
  }
  throw new Error(`unexpected response uninstalling ${name}`)
}

/** DELETE /api/agents/{name} — idempotent; a cli-kind agent answers 400
 *  `agent.cli_kind_local_only` (its profile is removed from the CLI). A 207
 *  partial removal resolves with `status: 'partially_uninstalled'`. The agents
 *  queries refresh whatever the outcome: a call that rejects (an unreadable
 *  body, a timeout) may still have removed the agent. */
export function useAgentUninstall(name: string) {
  const qc = useQueryClient()
  return useMutation<AgentUninstallResult, Error, void>({
    mutationFn: async () =>
      _normaliseUninstall(name, await apiDelete<UninstallBody | null>(ENDPOINTS.agentUninstall(name))),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['agents'] })
    },
  })
}

/** PUT /api/agents/{name}/command-approvals — re-renders Hermes's config and
 *  restarts the unit when it was running; the result carries `restart` and
 *  `message` for the toast.
 *
 *  The result is the new approvals status, so it is written straight into the
 *  Settings query (as useSetHermesCommandApprovals does) and into the cached
 *  overview's `approvals`. The overview itself is left out of the invalidation:
 *  a plain refetch would answer from the route's 30 s cache with the old mode;
 *  the card follows up with refreshAgentOverview instead. */
export function useCommandApprovalsMode(name: string) {
  const qc = useQueryClient()
  return useMutation<CommandApprovalsResult, Error, CommandApprovalsMode>({
    mutationFn: (mode) =>
      apiPut<CommandApprovalsResult>(ENDPOINTS.agentCommandApprovals(name), { mode }),
    onSuccess: (res) => {
      qc.setQueryData(['agent-command-approvals', name], res)
      qc.setQueryData<AgentOverview | null>(agentOverviewKey(name), (old) =>
        old
          ? {
              ...old,
              approvals: {
                ...(old.approvals ?? {}),
                mode: res.mode,
                effective: res.effective,
                in_sync: res.in_sync,
              },
            }
          : old,
      )
      const overview = agentOverviewKey(name)
      qc.invalidateQueries({
        queryKey: ['agents'],
        predicate: (q) => !overview.every((part, i) => q.queryKey[i] === part),
      })
    },
  })
}

/** What POST /api/agents/hermes/reconcile-upstreams answers: `ok` plus the
 *  sync report (src/hal0/agents/hermes_upstreams.py sync_hermes_upstreams). */
export interface HermesReconcileResult {
  ok: boolean
  keys_written?: unknown
  profiles_wired?: string[]
  [key: string]: unknown
}

/** POST /api/agents/hermes/reconcile-upstreams — re-mirror the Hermes
 *  gateway's profiles into the upstream registry (Open WebUI reads them). */
export function useHermesReconcileUpstreams() {
  const qc = useQueryClient()
  return useMutation<HermesReconcileResult, Error, void>({
    mutationFn: () => apiPost<HermesReconcileResult>(ENDPOINTS.agentHermesReconcileUpstreams),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agents'] })
    },
  })
}

/** GET /api/logs (src/hal0/api/routes/logs.py list_logs): the same body the
 *  Services drawer reads. `hint` is set when the journal was unreadable or
 *  empty and says why. */
export type UnitLogTail = UnitLogs

/**
 * Journal tail for the `down` face. `null` on 404/501/0 (older daemon, or no
 * such unit route), same once-per-path warning as the other agent reads. Polls
 * every 10 s while `enabled`; a blank unit never fetches.
 */
export function useUnitLogTail(
  unit: string,
  n = 120,
  enabled = true,
): UseQueryResult<UnitLogTail | null> {
  return useQuery<UnitLogTail | null>({
    queryKey: ['agents', 'logs', unit, n],
    queryFn: async () => {
      const path = ENDPOINTS.logsUnit(unit, n)
      try {
        const body = await apiGet<UnitLogTail>(path)
        return body && Array.isArray(body.lines) ? body : null
      } catch (err) {
        if (_isMissing(err)) {
          _warnMissing(path, err)
          return null
        }
        throw err
      }
    },
    enabled: enabled && !!unit,
    // A poll that fails is retried by the next tick, not by backoff on top.
    retry: false,
    refetchInterval: OVERVIEW_POLL_MS,
  })
}
