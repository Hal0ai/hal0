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

import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { apiGet, apiPost, apiPut, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

// ── Types ──────────────────────────────────────────────────────────

export interface AgentRecord {
  name: string
  installed_at: string
  status: string
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
  enqueued_at?: string
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
        const body = await apiGet<MemoryStats>(ENDPOINTS.agentMemoryStats)
        if (body && typeof body.writes === 'number') return body.writes
        if (body && typeof body.items === 'number') return body.items
        return null
      } catch (err) {
        if (_isMissing(err)) {
          _warnMissing(ENDPOINTS.agentMemoryStats, err)
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
  agentStatus: 'running' | 'broken' | 'unknown' | 'not_installed'
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
    if (first.status === 'installed') agentStatus = 'running'
    else if (first.status === 'broken') agentStatus = 'broken'
    else agentStatus = 'unknown'
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
    // TODO endpoints.ts (ui-sweep-b owns) — inline path for now
    mutationFn: ({ pid, body }: { pid: string; body: PersonaUpdateBody }) =>
      apiPut<unknown>(
        `/api/agents/${encodeURIComponent(agentId)}/personas/${encodeURIComponent(pid)}`,
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
