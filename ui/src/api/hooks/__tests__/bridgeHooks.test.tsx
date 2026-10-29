// @vitest-environment happy-dom
//
// Agent-bridge hooks (ADR-0043): bridge clients and the runs they delegated.
//
//   useBridgeClients()      -> GET    /api/bridge/clients            (15 s poll)
//   useBridgeClientCreate() -> POST   /api/bridge/clients            -> {client, key, onboarding}
//   useBridgeClientRotate() -> POST   /api/bridge/clients/{name}/rotate
//   useBridgeClientRevoke() -> DELETE /api/bridge/clients/{name}
//   useBridgeClientScopes() -> PATCH  /api/bridge/clients/{name}
//   useBridgeRuns(params)   -> GET    /api/bridge/runs?client=&status=&limit=
//
// Every client mutation invalidates ['bridge','clients']. The runs poll is 5 s while any run
// is non-terminal and 30 s once all are. Harness mirrors personaHooks.test.tsx.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn(), apiPatch: vi.fn(), apiDelete: vi.fn() }
})

const client = await import('@/api/client')
const bridge = await import('@/api/hooks/useBridge')
const {
  BRIDGE_TERMINAL_STATUSES,
  bridgeDisabled,
  useBridgeClients,
  useBridgeClientCreate,
  useBridgeClientRotate,
  useBridgeClientRevoke,
  useBridgeClientScopes,
  useBridgeRuns,
} = bridge

const CLIENT = {
  name: 'laptop',
  note: '',
  scopes: ['agents', 'memory'],
  created_at: 1_790_000_000,
  created_by: 'api:admin',
  revoked_at: null,
  last_seen_at: null,
}

const run = (status: string, run_id = 'run_1') => ({
  run_id,
  agent: 'hermes',
  profile: null,
  client: 'laptop',
  status,
  created_at: 1_790_000_000,
  updated_at: 1_790_000_005,
  input_preview: 'fix the build',
  output: null,
  error: null,
  usage: null,
  pending: null,
  last_seq: 0,
})

function mountHook<T>(useHook: () => T, qc: QueryClient) {
  let latest!: T
  function Probe() {
    latest = useHook()
    return null
  }
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, React.createElement(Probe)))
  })
  return { root, get: () => latest }
}

async function flush() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
}

const newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } })

/** The poll interval the hook configured, as TanStack would evaluate it for the cached data. */
function intervalOf(qc: QueryClient, key: readonly unknown[]) {
  const query = qc.getQueryCache().find({ queryKey: key })!
  const ri = (query.options as { refetchInterval?: unknown }).refetchInterval
  return typeof ri === 'function' ? (ri as (q: typeof query) => unknown)(query) : ri
}

beforeEach(() => {
  for (const fn of [client.apiGet, client.apiPost, client.apiPatch, client.apiDelete]) {
    vi.mocked(fn).mockReset()
  }
})
afterEach(() => {
  document.body.innerHTML = ''
})

describe('useBridgeClients', () => {
  it('reads the clients route under the bridge/clients key and polls every 15 s', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({ items: [CLIENT] })
    const qc = newClient()
    const probe = mountHook(() => useBridgeClients(), qc)
    await flush()
    expect(client.apiGet).toHaveBeenCalledWith('/api/bridge/clients')
    expect(probe.get().data?.items).toHaveLength(1)
    expect(qc.getQueryData(['bridge', 'clients'])).toBeTruthy()
    expect(intervalOf(qc, ['bridge', 'clients'])).toBe(15_000)
    act(() => probe.root.unmount())
  })
})

describe('client mutations', () => {
  it('create posts {name, note, scopes}, returns the key, and invalidates the list', async () => {
    const minted = {
      client: CLIENT,
      key: 'hal0bk_secretsecret',
      onboarding: {
        lines: [
          {
            server: 'hal0-agents',
            scope: 'agents',
            mcp_url: 'http://hal0/mcp/agents/mcp',
            claude_mcp_add: 'claude mcp add hal0-agents ...',
          },
        ],
      },
    }
    vi.mocked(client.apiPost).mockResolvedValue(minted)
    vi.mocked(client.apiGet).mockResolvedValue({ items: [] })
    const qc = newClient()
    const listProbe = mountHook(() => useBridgeClients(), qc)
    await flush()
    vi.mocked(client.apiGet).mockClear()

    const probe = mountHook(() => useBridgeClientCreate(), qc)
    let result: unknown
    await act(async () => {
      result = await probe.get().mutateAsync({ name: 'laptop', note: 'dev box', scopes: ['agents', 'memory'] })
    })
    await flush()

    expect(client.apiPost).toHaveBeenCalledWith('/api/bridge/clients', {
      name: 'laptop',
      note: 'dev box',
      scopes: ['agents', 'memory'],
    })
    expect((result as typeof minted).key).toBe('hal0bk_secretsecret')
    expect((result as typeof minted).onboarding.lines[0].server).toBe('hal0-agents')
    // the list refetched
    expect(client.apiGet).toHaveBeenCalledWith('/api/bridge/clients')
    act(() => {
      probe.root.unmount()
      listProbe.root.unmount()
    })
  })

  it('rotate posts to the rotate route with the name encoded', async () => {
    vi.mocked(client.apiPost).mockResolvedValue({ client: CLIENT, key: 'k', onboarding: { lines: [] } })
    const qc = newClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    const probe = mountHook(() => useBridgeClientRotate(), qc)
    await act(async () => {
      await probe.get().mutateAsync('laptop')
    })
    expect(client.apiPost).toHaveBeenCalledWith('/api/bridge/clients/laptop/rotate')
    expect(spy).toHaveBeenCalledWith({ queryKey: ['bridge', 'clients'] })
    act(() => probe.root.unmount())
  })

  it('revoke DELETEs the client and scopes PATCHes the replacement list; both invalidate the list', async () => {
    vi.mocked(client.apiDelete).mockResolvedValue({ client: { ...CLIENT, revoked_at: 1 } })
    vi.mocked(client.apiPatch).mockResolvedValue({ client: { ...CLIENT, scopes: ['agents'] } })
    const qc = newClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    const revoke = mountHook(() => useBridgeClientRevoke(), qc)
    const scopes = mountHook(() => useBridgeClientScopes(), qc)
    await act(async () => {
      await revoke.get().mutateAsync('laptop')
      await scopes.get().mutateAsync({ name: 'laptop', scopes: ['agents'] })
    })
    expect(client.apiDelete).toHaveBeenCalledWith('/api/bridge/clients/laptop')
    expect(client.apiPatch).toHaveBeenCalledWith('/api/bridge/clients/laptop', { scopes: ['agents'] })
    expect(spy).toHaveBeenCalledTimes(2)
    expect(spy).toHaveBeenCalledWith({ queryKey: ['bridge', 'clients'] })
    act(() => {
      revoke.root.unmount()
      scopes.root.unmount()
    })
  })
})

describe('useBridgeRuns', () => {
  it('sends only the params that are set', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({ items: [] })
    const probe = mountHook(() => useBridgeRuns({ client: 'laptop', limit: 50 }), newClient())
    await flush()
    expect(client.apiGet).toHaveBeenCalledWith('/api/bridge/runs?client=laptop&limit=50')
    act(() => probe.root.unmount())

    vi.mocked(client.apiGet).mockClear()
    const bare = mountHook(() => useBridgeRuns(), newClient())
    await flush()
    expect(client.apiGet).toHaveBeenCalledWith('/api/bridge/runs')
    act(() => bare.root.unmount())
  })

  it('polls every 5 s while a run is running', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({ items: [run('completed', 'a'), run('running', 'b')] })
    const qc = newClient()
    const probe = mountHook(() => useBridgeRuns({ client: 'laptop' }), qc)
    await flush()
    expect(intervalOf(qc, ['bridge', 'runs', { client: 'laptop' }])).toBe(5_000)
    act(() => probe.root.unmount())
  })

  it('polls every 30 s once every run is terminal, and before any data', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({
      items: [run('completed', 'a'), run('failed', 'b'), run('cancelled', 'c'), run('interrupted', 'd')],
    })
    const qc = newClient()
    const probe = mountHook(() => useBridgeRuns({ client: 'laptop' }), qc)
    expect(intervalOf(qc, ['bridge', 'runs', { client: 'laptop' }])).toBe(30_000)
    await flush()
    expect(intervalOf(qc, ['bridge', 'runs', { client: 'laptop' }])).toBe(30_000)
    act(() => probe.root.unmount())
  })

  it('treats every non-terminal status as live, and unknown as terminal like the backend does', () => {
    for (const s of ['queued', 'running', 'waiting_for_approval', 'waiting_for_clarification', 'stopping']) {
      expect(BRIDGE_TERMINAL_STATUSES.has(s)).toBe(false)
    }
    for (const s of ['completed', 'failed', 'cancelled', 'interrupted', 'unknown']) {
      expect(BRIDGE_TERMINAL_STATUSES.has(s)).toBe(true)
    }
  })

  it('does not hold the 5 s poll open for an orphaned unknown run', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({ items: [run('unknown', 'gone'), run('completed', 'done')] })
    const qc = newClient()
    const probe = mountHook(() => useBridgeRuns({ client: 'laptop' }), qc)
    await flush()
    expect(intervalOf(qc, ['bridge', 'runs', { client: 'laptop' }])).toBe(30_000)
    act(() => probe.root.unmount())
  })
})

describe('bridgeDisabled', () => {
  it('recognises the bridge.disabled code and nothing else', () => {
    expect(bridgeDisabled(new client.Hal0Error('off', { code: 'bridge.disabled', status: 404 }))).toBe(true)
    expect(bridgeDisabled(new client.Hal0Error('dup', { code: 'bridge.client_exists', status: 409 }))).toBe(false)
    expect(bridgeDisabled(new client.Hal0Error('nope', { status: 404 }))).toBe(false)
    expect(bridgeDisabled(new Error('boom'))).toBe(false)
    expect(bridgeDisabled(null)).toBe(false)
    expect(bridgeDisabled(undefined)).toBe(false)
  })
})
