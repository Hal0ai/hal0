// hal0 v3 dashboard — bundled-agent + persona/skills catalogues.
//
// Backs the Agent surface (#207, #226, #227). Three read-only queries:
//
//   - useAgents()              → GET /api/agents
//   - useAgentSkills()         → GET /api/agents/skills
//   - useAgentPersonaEnums()   → GET /api/agents/persona-enums
//
// All catalogues are static for v0.3; ``staleTime`` is generous so the
// dashboard doesn't refetch on every tab switch. Re-fetch on
// invalidate (install/uninstall mutations) handles the dynamic case
// for the list endpoint.
//
// Note: the per-agent activity endpoint already lives in this surface
// (/api/agents/{name}/activity) but the Agent Inbox tab still reads
// from HAL0_DATA mock — wiring that lives in a separate follow-up.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

// ── /api/agents ──────────────────────────────────────────────────────

export interface AgentRecord {
  name: string
  shape: string
  state: string
  installed_at?: string | null
  // Manager.as_dict() may attach more keys (driver paths, etc); the
  // dashboard only cares about identity + state for v0.3.
  [key: string]: unknown
}

export interface AgentsResponse {
  agents: AgentRecord[]
  count: number
}

export function useAgents() {
  return useQuery<AgentsResponse>({
    queryKey: ['agents', 'list'],
    queryFn: () => apiGet<AgentsResponse>(ENDPOINTS.agents),
    staleTime: 30_000,
  })
}

// ── /api/agents/skills ───────────────────────────────────────────────

export interface AgentSkill {
  name: string
  cap: string
  policy: 'always' | 'remember' | 'auto' | 'deny'
  src: string
}

export interface AgentSkillsResponse {
  skills: AgentSkill[]
  count: number
}

export function useAgentSkills() {
  return useQuery<AgentSkillsResponse>({
    queryKey: ['agents', 'skills'],
    queryFn: () => apiGet<AgentSkillsResponse>(ENDPOINTS.agentSkills),
    staleTime: 5 * 60_000,
  })
}

// ── /api/agents/persona-enums ────────────────────────────────────────

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
