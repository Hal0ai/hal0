// hal0 v3 dashboard — MCP page hooks (issue #206).
//
// Wires the read-only `/api/mcp/*` introspection surface — servers,
// clients, catalog — plus the SSE call stream the LiveTimeline ticks
// off. Mutation hooks (install/uninstall/restart/config) hit the
// 501-stub routes; the page surfaces a toast on rejection so the user
// learns the lifecycle work is pending ADR-0013 without the buttons
// going dead.
//
// Mock fallback follows the same pattern as useAgentMcpClients — when
// the backend isn't there (Hal0Error.status === 404 / network error),
// we return baked-in mock shapes so the dashboard renders in dev /
// against a stale build. Forced-mock mode (`VITE_MOCK_HAL0=1`) is
// honoured transparently through the existing mockFetch layer.

import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { api, apiGet, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

// ─── Types ──────────────────────────────────────────────────────────

export type McpServerState = 'running' | 'stopped' | 'failed' | 'installing'

export interface McpServerActivity {
  rpm: number
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
  resources: number
  prompts: number
  activity: McpServerActivity
  connected: string[]
  description?: string
  provider?: string
  // Optional fields the prototype card reads when present.
  since?: string
  url?: string
  clients?: string[]
  env?: Record<string, string>
  progress?: number
  progressLabel?: string
  note?: string
  lastError?: {
    ts?: string
    code?: string
    msg?: string
    attempts?: number
  }
}

export interface McpClient {
  id: string
  name: string
  role: string
  host: string
  since: string | number | null
  connected_to: string[]
  // Optional: the prototype card uses `servers` (alias of connected_to).
  servers?: string[]
  activity?: McpServerActivity
}

export interface McpCatalogItem {
  id?: string
  name: string
  author: string
  verified: boolean
  description: string
  tools: number
  stars?: number
  category: string
}

export interface McpCatalog {
  items: McpCatalogItem[]
  categories: string[]
}

export interface McpCallEvent {
  ts: number
  client: string
  tool: string
  server?: string
}

// ── Manifest resolve (#224) ─────────────────────────────────────────
//
// Shape mirrors `hal0.mcp.manifest.ResolvedManifest`. The InstallDrawer
// reads `name`, `description`, `tools`, `transport`, and the truthiness
// of `env_required` to render the preview card.
export interface ResolvedMcpManifest {
  id: string
  name: string
  description: string
  spec: string
  transport: string
  tools: number
  resources: number
  prompts: number
  env_required: string[]
  source_kind: string
  source_url?: string | null
  author: string
  verified: boolean
}

// ─── Mock fallbacks ─────────────────────────────────────────────────
//
// Shapes mirror what the backend returns (post-normalisation) so a
// switch from mock to live is a no-op for downstream consumers. Kept
// deliberately small — the prototype's elaborate MCP_SERVERS list lives
// in `ui/src/dash/mcp-data.jsx` and the page falls back to that when
// the hook returns no rows.

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

const MOCK_CLIENTS: McpClient[] = []

const MOCK_CATALOG: McpCatalog = {
  items: [
    {
      name: 'filesystem',
      author: 'modelcontextprotocol',
      verified: true,
      description: 'Read, write, and search files inside an allowlisted root.',
      tools: 5,
      stars: 12000,
      category: 'files',
    },
  ],
  categories: [
    'Files',
    'Data',
    'Search',
    'Browser',
    'Comms',
    'Issues',
    'Ops',
    'IoT',
    'Productivity',
  ],
}

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

async function _fetchClients(): Promise<McpClient[]> {
  try {
    const body = await apiGet<{ clients: McpClient[]; count: number } | McpClient[]>(
      ENDPOINTS.mcpClients,
    )
    if (Array.isArray(body)) return body
    if (body && Array.isArray(body.clients)) return body.clients
    return []
  } catch (err) {
    if (err instanceof Hal0Error && (err.status === 404 || err.status === 0)) {
      return MOCK_CLIENTS
    }
    throw err
  }
}

async function _fetchCatalog(): Promise<McpCatalog> {
  try {
    const body = await apiGet<McpCatalog>(ENDPOINTS.mcpCatalog)
    if (body && Array.isArray(body.items)) return body
    return MOCK_CATALOG
  } catch (err) {
    if (err instanceof Hal0Error && (err.status === 404 || err.status === 0)) {
      return MOCK_CATALOG
    }
    throw err
  }
}

const SERVERS_POLL_MS = 5_000
const CLIENTS_POLL_MS = 5_000
const CATALOG_POLL_MS = 30_000

export function useMcpServers(): UseQueryResult<McpServer[]> {
  return useQuery({
    queryKey: ['mcp', 'servers'],
    queryFn: _fetchServers,
    refetchInterval: SERVERS_POLL_MS,
  })
}

export function useMcpClients(): UseQueryResult<McpClient[]> {
  return useQuery({
    queryKey: ['mcp', 'clients'],
    queryFn: _fetchClients,
    refetchInterval: CLIENTS_POLL_MS,
  })
}

export function useMcpCatalog(): UseQueryResult<McpCatalog> {
  return useQuery({
    queryKey: ['mcp', 'catalog'],
    queryFn: _fetchCatalog,
    refetchInterval: CATALOG_POLL_MS,
  })
}

// ─── SSE call stream ────────────────────────────────────────────────
//
// Hook surface matches the prototype's `useLiveCallStream(servers)` —
// returns `{ calls, now }` where `calls` is a serverId → event[] map.
// The 60 s sliding window + opacity decay logic stays in the
// LiveTimeline render code; this hook just maintains the buffer and
// re-renders on each new event.

interface CallStreamState {
  calls: Record<string, McpCallEvent[]>
  now: number
}

const WINDOW_MS = 60_000

export function useMcpCallStream(): CallStreamState {
  const [state, setState] = useState<CallStreamState>({ calls: {}, now: Date.now() })
  const esRef = useRef<EventSource | null>(null)
  const callsRef = useRef<Record<string, McpCallEvent[]>>({})

  useEffect(() => {
    let alive = true
    let tickHandle: ReturnType<typeof setTimeout> | undefined

    // Periodic redraw — drives the LiveTimeline's fade even when no new
    // events arrive. 1 s cadence is coarse enough not to thrash React
    // but fine enough that the opacity decay looks continuous.
    const tick = () => {
      if (!alive) return
      const now = Date.now()
      const next: Record<string, McpCallEvent[]> = {}
      for (const sid of Object.keys(callsRef.current)) {
        next[sid] = callsRef.current[sid].filter((e) => now - e.ts < WINDOW_MS)
      }
      callsRef.current = next
      setState({ calls: next, now })
      tickHandle = setTimeout(tick, 1_000)
    }

    try {
      esRef.current = new EventSource(ENDPOINTS.mcpStream)
    } catch {
      tickHandle = setTimeout(tick, 1_000)
      return () => {
        alive = false
        if (tickHandle) clearTimeout(tickHandle)
      }
    }
    const es = esRef.current

    const handleEvent = (evt: MessageEvent) => {
      try {
        const data = JSON.parse(evt.data)
        const tsRaw = data?.ts
        const ts = typeof tsRaw === 'number' ? tsRaw * 1000 : Date.now()
        const event: McpCallEvent = {
          ts,
          client: String(data?.client ?? 'unknown'),
          tool: String(data?.tool ?? 'call'),
          server: data?.server ?? undefined,
        }
        const sid = event.server || 'unknown'
        const arr = callsRef.current[sid] ? [...callsRef.current[sid]] : []
        arr.push(event)
        callsRef.current = { ...callsRef.current, [sid]: arr }
      } catch {
        // ignore malformed frame
      }
    }

    // Backend emits typed event names — `mcp.tool.invoked`, `mcp.tool.executed`,
    // … — so the EventSource needs to listen on each. We mirror the
    // backend's `_AUDIT_EVENTS` set here; new event types added there
    // need a corresponding `addEventListener` call.
    const eventTypes = [
      'mcp.tool.invoked',
      'mcp.tool.enqueued',
      'mcp.tool.approved',
      'mcp.tool.denied',
      'mcp.tool.executed',
      'mcp.tool.failed',
    ]
    for (const name of eventTypes) {
      es.addEventListener(name, handleEvent as EventListener)
    }
    // Fallback for SDK clients that emit unnamed `message` frames.
    es.onmessage = handleEvent
    es.onerror = () => {
      // Don't tear down — EventSource auto-reconnects, and the ticker
      // keeps the LiveTimeline updating against a stale buffer.
    }

    tickHandle = setTimeout(tick, 1_000)

    return () => {
      alive = false
      if (tickHandle) clearTimeout(tickHandle)
      es.close()
      esRef.current = null
    }
  }, [])

  return state
}

// ─── Mutations ──────────────────────────────────────────────────────
//
// install / uninstall / config-patch are real as of #305. The action
// stub (start/stop/restart) still 501s pending the supervisor
// follow-up; useMcpRestart catches that case + surfaces a toast so the
// button doesn't look broken.

function _toast(verb: string, tone: 'info' | 'warn' | 'err' = 'info'): void {
  if (typeof window !== 'undefined' && (window as any).__hal0Toast) {
    ;(window as any).__hal0Toast(verb, tone)
  }
}

function _invalidator(queryClient: ReturnType<typeof useQueryClient>) {
  return () => {
    queryClient.invalidateQueries({ queryKey: ['mcp', 'servers'] })
    queryClient.invalidateQueries({ queryKey: ['mcp', 'clients'] })
  }
}

export interface McpInstallBody {
  /** Original URL/spec the operator pasted. */
  url?: string
  /** Pre-resolved manifest (round-tripped from `useMcpResolve`). */
  manifest?: ResolvedMcpManifest
}

export function useMcpInstall() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: McpInstallBody) => {
      try {
        const resp = await api(ENDPOINTS.mcpInstall, {
          method: 'POST',
          body: body as unknown as Record<string, unknown>,
          raw: true,
        })
        const name = body.manifest?.name || body.url || 'server'
        _toast(`Installed ${name}`, 'info')
        return resp
      } catch (err) {
        if (err instanceof Hal0Error) {
          _toast(`Install failed — ${err.message}`, 'warn')
        }
        throw err
      }
    },
    onSuccess: _invalidator(qc),
  })
}

export function useMcpUninstall() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      try {
        const resp = await api(ENDPOINTS.mcpServer(id), {
          method: 'DELETE',
          raw: true,
        })
        _toast(`Uninstalled ${id}`, 'info')
        return resp
      } catch (err) {
        if (err instanceof Hal0Error) {
          _toast(`Uninstall failed — ${err.message}`, 'warn')
        }
        throw err
      }
    },
    onSuccess: _invalidator(qc),
  })
}

export function useMcpRestart() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      try {
        return await api(ENDPOINTS.mcpServerAction(id, 'restart'), {
          method: 'POST',
          raw: true,
        })
      } catch (err) {
        if (err instanceof Hal0Error && err.status === 501) {
          _toast(
            'MCP restart pending supervisor follow-up to #305',
            'warn',
          )
          return null
        }
        throw err
      }
    },
    onSuccess: _invalidator(qc),
  })
}

export function useMcpConfigPatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: Record<string, unknown> }) => {
      try {
        const resp = await api(ENDPOINTS.mcpServerConfig(id), {
          method: 'PATCH',
          body,
          raw: true,
        })
        _toast(`Saved ${id} config`, 'info')
        return resp
      } catch (err) {
        if (err instanceof Hal0Error) {
          _toast(`Config save failed — ${err.message}`, 'warn')
        }
        throw err
      }
    },
    onSuccess: _invalidator(qc),
  })
}

// ── Manifest resolver (#224) ────────────────────────────────────────
//
// Query the live `/api/mcp/resolve?url=…` endpoint and surface the
// resolved manifest preview to the InstallDrawer's URL tab. Disabled
// when `url` is empty so the operator only triggers a network call
// once they've pasted something. 30 s cache keeps multiple toggles
// of the same paste cheap.

export function useMcpResolve(url: string | null | undefined) {
  return useQuery({
    queryKey: ['mcp', 'resolve', url || ''],
    queryFn: async (): Promise<ResolvedMcpManifest> => {
      const q = String(url || '').trim()
      if (!q) throw new Error('url is required')
      return await apiGet<ResolvedMcpManifest>(
        `${ENDPOINTS.mcpResolve}?url=${encodeURIComponent(q)}`,
      )
    },
    enabled: !!url && String(url).trim().length > 0,
    retry: false,
    staleTime: 30_000,
  })
}

export function useMcpServerLogs(id: string | null | undefined) {
  return useQuery({
    queryKey: ['mcp', 'logs', id],
    queryFn: async () => {
      if (!id) return { events: [] }
      try {
        return await apiGet<{ server: string; events: any[]; count: number }>(
          ENDPOINTS.mcpServerLogs(id),
        )
      } catch (err) {
        if (err instanceof Hal0Error && (err.status === 404 || err.status === 0)) {
          return { server: id, events: [], count: 0 }
        }
        throw err
      }
    },
    enabled: !!id,
    refetchInterval: 3_000,
  })
}
