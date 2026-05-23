// hal0 v3 dashboard — per-agent MCP allow-list hooks (ADR-0013 §8).
//
// v0.3 alpha is read-only per ADR-0013 §8; v0.3 stable will add a PUT
// hook + an MCPServerConfig editor. The endpoint shape mirrors the
// schemas in src/hal0/config/schema.py (see #287 + #293).
//
// The list endpoint returns every agent that has a TOML on disk under
// /etc/hal0/agents/<name>.toml; the per-agent endpoint returns that
// agent's full AgentConfig + a live health dot per server.
//
// Until the backend route ships, the hooks fall back to a baked-in
// mock so the dashboard panel renders without 404s. The mock matches
// the ADR-0013 §2 worked example (Hermes with hal0-admin + hal0-memory
// + filesystem + opt-in github).

import { useQuery } from '@tanstack/react-query'
import { apiGet, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

export type ToolClassification = 'allow' | 'gated' | 'blocked'

export interface ToolPolicy {
  allow: string[]
  gated: string[]
  blocked: string[]
}

export interface MCPClientAuth {
  kind: 'none' | 'bearer-from-env'
  env: string | null
  /** Presence of the token at startup — surfaced without ever rendering the value. */
  tokenStatus: 'present' | 'missing' | 'not-needed'
}

export interface MCPClientServer {
  name: string
  url: string | null
  enabled: boolean
  builtin: boolean
  auth: MCPClientAuth
  tools: ToolPolicy
  /** Live ping dot — green = reachable, yellow = degraded, red = unreachable. */
  health: 'green' | 'yellow' | 'red' | 'unknown'
}

export interface AgentMCPClientView {
  name: string
  display: string
  workspace: string
  servers: MCPClientServer[]
}

export interface AgentMCPClientList {
  agents: AgentMCPClientView[]
}

// ── Mock fallback (matches ADR-0013 §2 worked example) ─────────────

const MOCK_LIST: AgentMCPClientList = {
  agents: [
    {
      name: 'hermes',
      display: 'Hermes-Agent',
      workspace: '/var/lib/hal0/agents/hermes/workspace',
      servers: [
        {
          name: 'hal0-admin',
          url: null,
          enabled: true,
          builtin: true,
          auth: { kind: 'none', env: null, tokenStatus: 'not-needed' },
          tools: { allow: [], gated: [], blocked: [] },
          health: 'green',
        },
        {
          name: 'hal0-memory',
          url: null,
          enabled: true,
          builtin: true,
          auth: { kind: 'none', env: null, tokenStatus: 'not-needed' },
          tools: { allow: [], gated: [], blocked: [] },
          health: 'green',
        },
        {
          name: 'filesystem',
          url: 'stdio:///usr/lib/hal0/mcp/filesystem-server',
          enabled: true,
          builtin: false,
          auth: { kind: 'none', env: null, tokenStatus: 'not-needed' },
          tools: {
            allow: ['read_file', 'list_directory', 'search_files'],
            gated: ['write_file'],
            blocked: [],
          },
          health: 'green',
        },
        {
          name: 'github',
          url: 'https://api.github.com/mcp',
          enabled: false,
          builtin: false,
          auth: {
            kind: 'bearer-from-env',
            env: 'HAL0_AGENT_HERMES_GITHUB_TOKEN',
            tokenStatus: 'missing',
          },
          tools: {
            allow: ['list_issues', 'get_pr', 'search_code'],
            gated: ['create_pr', 'post_issue_comment'],
            blocked: ['delete_repo', 'delete_branch'],
          },
          health: 'unknown',
        },
      ],
    },
  ],
}

async function fetchOrMock(): Promise<AgentMCPClientList> {
  try {
    return await apiGet<AgentMCPClientList>(ENDPOINTS.agentMcpClients)
  } catch (err) {
    if (err instanceof Hal0Error && err.status === 404) {
      // Backend route lands as a v0.3 follow-up; until then the
      // dashboard renders the mock so the read-only alpha works
      // against #287 builds + a stale Hermes install.
      return MOCK_LIST
    }
    throw err
  }
}

const POLL_MS = 30_000

export function useAgentMcpClients() {
  return useQuery({
    queryKey: ['agents', 'mcp', 'clients'],
    queryFn: fetchOrMock,
    refetchInterval: POLL_MS,
  })
}
