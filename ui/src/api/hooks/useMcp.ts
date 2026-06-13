// hal0 dashboard — MCP servers hook.
//
// Reads the read-only `GET /api/mcp/servers` introspection surface that backs
// the MCP section of the Connections view (connections-overhaul). The old
// standalone MCP page (clients ribbon, catalog/install drawer, SSE call
// stream, lifecycle mutations) was removed, so only the server list survives
// here. `useMcpStatusPip` in useAgents.ts reads the same endpoint for the
// sidebar pip.
//
// Mock fallback: when the backend isn't reachable (Hal0Error.status 404/0)
// we return baked-in server shapes so the dashboard still renders in dev /
// against a stale build. Forced-mock mode (`VITE_MOCK_HAL0=1`) is honoured
// transparently through the existing mockFetch layer.

import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { apiGet, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

// ─── Types ──────────────────────────────────────────────────────────

export type McpServerState = 'running' | 'stopped' | 'failed' | 'installing'

export interface McpServerActivity {
  rpm: number
}

// Per-tool detail surfaced by GET /api/mcp/servers (connections-overhaul).
// The Connections page renders this as a capability / blast-radius manifest:
// what an agent wired to this server could actually do. `gated` is the hal0
// approval policy (does invoking it land in the owner-approval queue); the
// read_only / destructive / idempotent / open_world hints are the advisory
// MCP annotations declared by the server. Hints are null when the server
// declares none for that tool.
export interface McpTool {
  name: string
  description: string
  /** One-line `name?: type, …` signature, or "—" when the tool takes no args. */
  args: string
  read_only: boolean | null
  destructive: boolean | null
  idempotent: boolean | null
  open_world: boolean | null
  gated: boolean
}

export interface McpServer {
  id: string
  name: string
  bundled: boolean
  state: McpServerState
  transport: string
  connect_url: string
  pid: number | null
  version: string
  tools: number
  /** Live per-tool detail for bundled servers; [] for registry-only installs. */
  tool_details?: McpTool[]
  resources: number
  prompts: number
  activity: McpServerActivity
  connected: string[]
  description?: string
  provider?: string
}

// ─── Mock fallback ──────────────────────────────────────────────────
//
// Server shapes only (counts, no tool_details — those come from live FastMCP
// introspection). Used when the backend is unreachable in dev.

const MOCK_SERVERS: McpServer[] = [
  {
    id: 'hal0-admin',
    name: 'hal0-admin',
    bundled: true,
    state: 'running',
    transport: 'streamable-http',
    connect_url: `${typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8080'}/mcp/admin`,
    pid: null,
    version: '0.3.0',
    tools: 19,
    resources: 0,
    prompts: 0,
    activity: { rpm: 0 },
    connected: [],
    description: 'hal0 bundled admin MCP server (FastMCP, streamable-http).',
    provider: 'hal0',
  },
  {
    id: 'hal0-memory',
    name: 'hal0-memory',
    bundled: true,
    state: 'running',
    transport: 'streamable-http',
    connect_url: `${typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8080'}/mcp/memory`,
    pid: null,
    version: '0.3.0',
    tools: 4,
    resources: 0,
    prompts: 0,
    activity: { rpm: 0 },
    connected: [],
    description: 'hal0 bundled memory MCP server (FastMCP, streamable-http).',
    provider: 'hal0',
  },
]

async function _fetchServers(): Promise<McpServer[]> {
  try {
    const body = await apiGet<{ servers: McpServer[]; count: number } | McpServer[]>(
      ENDPOINTS.mcpServers,
    )
    if (Array.isArray(body)) return body
    if (body && Array.isArray(body.servers)) return body.servers
    return []
  } catch (err) {
    if (err instanceof Hal0Error && (err.status === 404 || err.status === 0)) {
      return MOCK_SERVERS
    }
    throw err
  }
}

const SERVERS_POLL_MS = 5_000

export function useMcpServers(): UseQueryResult<McpServer[]> {
  return useQuery({
    queryKey: ['mcp', 'servers'],
    queryFn: _fetchServers,
    refetchInterval: SERVERS_POLL_MS,
  })
}
