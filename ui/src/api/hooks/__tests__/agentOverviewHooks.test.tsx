// @vitest-environment happy-dom
//
// Overview, repair, gateway-restart, uninstall, command-approvals and log-tail
// hooks plus the window bridge (agent-cards controls, task 15).
//
//   useAgentOverview(name, opts)   -> GET    /api/agents/{id}/overview
//   useAgentRepair(name)           -> POST   /api/agents/{id}/repair
//   useAgentGatewayRestart(name)   -> POST   /api/agents/{id}/gateway/restart
//   useAgentUninstall(name)        -> DELETE /api/agents/{id}
//   useCommandApprovalsMode(name)  -> PUT    /api/agents/{id}/command-approvals
//   useUnitLogTail(unit, n, on)    -> GET    /api/logs?unit=&n=
//
// Harness mirrors personaHooks.test.tsx: no @testing-library, a probe component
// mounted with createRoot/act under a QueryClientProvider, and the api client
// mocked at its boundary. Agent names are unique per test where a 404 is
// involved because the once-per-path warning set is module state.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn(), apiPut: vi.fn(), apiDelete: vi.fn() }
})

const client = await import('@/api/client')
const {
  useAgentOverview,
  useAgentRepair,
  useAgentGatewayRestart,
  useAgentUninstall,
  useCommandApprovalsMode,
  useUnitLogTail,
  refreshAgentOverview,
} = await import('@/api/hooks/useAgents')
const { ENDPOINTS } = await import('@/api/endpoints')

/** Mount `useHook` under `qc` and expose its latest result. */
function mountHook<T>(useHook: () => T, qc: QueryClient) {
  let latest!: T
  function Probe() {
    latest = useHook()
    return null
  }
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const render = () =>
    root.render(
      React.createElement(QueryClientProvider, { client: qc }, React.createElement(Probe)),
    )
  act(render)
  return { root, get: () => latest, rerender: () => act(render) }
}

/**
 * Let in-flight query promises settle and React flush the resulting state.
 * One macrotask is not enough under a loaded full-suite run (react-query hops
 * through several promise ticks before it notifies), so wait a few.
 */
async function flush() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
}

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

/** The refetchInterval a mounted query registered, resolved if it is a function. */
function pollMs(qc: QueryClient): unknown {
  const query = qc.getQueryCache().getAll()[0]
  const opts = query?.observers[0]?.options
  const interval = opts?.refetchInterval
  return typeof interval === 'function' ? interval(query) : interval
}

describe('agent control endpoint helpers', () => {
  it('builds the overview, repair, gateway-restart, uninstall and converge paths', () => {
    expect(ENDPOINTS.agentOverview('hermes')).toBe('/api/agents/hermes/overview')
    expect(ENDPOINTS.agentRepair('hermes')).toBe('/api/agents/hermes/repair')
    expect(ENDPOINTS.agentGatewayRestart('hermes')).toBe('/api/agents/hermes/gateway/restart')
    expect(ENDPOINTS.agentUninstall('hermes')).toBe('/api/agents/hermes')
    expect(ENDPOINTS.componentConverge('hermes')).toBe('/api/updates/components/hermes/converge')
    expect(ENDPOINTS.agentOverview('a/b')).toBe('/api/agents/a%2Fb/overview')
    expect(ENDPOINTS.agentUninstall('a/b')).toBe('/api/agents/a%2Fb')
    expect(ENDPOINTS.componentConverge('a/b')).toBe('/api/updates/components/a%2Fb/converge')
  })
})

describe('agent overview, control and log hooks', () => {
  beforeEach(() => {
    vi.mocked(client.apiGet).mockReset()
    vi.mocked(client.apiPost).mockReset()
    vi.mocked(client.apiPut).mockReset()
    vi.mocked(client.apiDelete).mockReset()
  })
  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  // ── useAgentOverview ──────────────────────────────────────────────

  it('useAgentOverview reads the overview route and returns the body', async () => {
    const body = { name: 'hermes', kind: 'daemon', installed: true, phase: 'ready' }
    vi.mocked(client.apiGet).mockResolvedValue(body)
    const probe = mountHook(() => useAgentOverview('hermes'), newClient())
    await flush()

    expect(client.apiGet).toHaveBeenCalledWith('/api/agents/hermes/overview')
    expect(probe.get().data).toEqual(body)

    act(() => probe.root.unmount())
  })

  it('useAgentOverview returns null on 404 and warns once per path', async () => {
    vi.mocked(client.apiGet).mockRejectedValue(new client.Hal0Error('nf', { status: 404, code: 'not_found' }))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const qc = newClient()
    const probe = mountHook(() => useAgentOverview('hermes-nf'), qc)
    await flush()

    expect(probe.get().data).toBeNull()
    expect(probe.get().isError).toBe(false)
    expect(warn).toHaveBeenCalledTimes(1)
    expect(warn.mock.calls[0]?.[0]).toBe('hal0.sidebar.endpoint_missing')
    expect(String(warn.mock.calls[0]?.[1])).toContain('/api/agents/hermes-nf/overview')

    // A re-render and a forced refetch hit the same 404 and stay quiet.
    probe.rerender()
    await act(async () => {
      await qc.refetchQueries({ queryKey: ['agents'] })
    })
    await flush()
    expect(probe.get().data).toBeNull()
    expect(client.apiGet).toHaveBeenCalledTimes(2)
    expect(warn).toHaveBeenCalledTimes(1)

    act(() => probe.root.unmount())
  })

  it.each([501, 0])('useAgentOverview returns null on status %i without throwing', async (status) => {
    vi.mocked(client.apiGet).mockRejectedValue(new client.Hal0Error('gone', { status, code: 'x' }))
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const probe = mountHook(() => useAgentOverview(`hermes-${status}`), newClient())
    await flush()

    expect(probe.get().data).toBeNull()
    expect(probe.get().isError).toBe(false)

    act(() => probe.root.unmount())
  })

  it('refreshAgentOverview reads ?refresh=1 and writes the overview cache', async () => {
    const stale = { name: 'hermes', kind: 'daemon', installed: true, phase: 'degraded' }
    const fresh = { name: 'hermes', kind: 'daemon', installed: true, phase: 'ready' }
    const qc = newClient()
    qc.setQueryData(['agents', 'overview', 'hermes'], stale)
    vi.mocked(client.apiGet).mockResolvedValue(fresh)

    let result: unknown
    await act(async () => {
      result = await refreshAgentOverview(qc, 'hermes')
    })

    expect(client.apiGet).toHaveBeenCalledWith('/api/agents/hermes/overview?refresh=1')
    expect(result).toEqual(fresh)
    expect(qc.getQueryData(['agents', 'overview', 'hermes'])).toEqual(fresh)
  })

  it('refreshAgentOverview cancels a plain poll still in flight so it cannot land after', async () => {
    const qc = newClient()
    // The mounted hook's first (plain) read never resolves: it is the stale
    // poll that would otherwise overwrite the refreshed body.
    vi.mocked(client.apiGet).mockImplementationOnce(() => new Promise(() => {}))
    const probe = mountHook(() => useAgentOverview('hermes-race'), qc)
    await flush()
    const fresh = { name: 'hermes-race', kind: 'daemon', installed: true, phase: 'ready' }
    vi.mocked(client.apiGet).mockResolvedValue(fresh)
    const cancel = vi.spyOn(qc, 'cancelQueries')

    await act(async () => {
      await refreshAgentOverview(qc, 'hermes-race')
    })
    await flush()

    expect(cancel).toHaveBeenCalledWith({ queryKey: ['agents', 'overview', 'hermes-race'], exact: true })
    expect(probe.get().data).toEqual(fresh)

    act(() => probe.root.unmount())
  })

  it('refreshAgentOverview caches null on a 404, and a 5xx rejects without touching the cache', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const qc = newClient()
    vi.mocked(client.apiGet).mockRejectedValueOnce(new client.Hal0Error('nf', { status: 404, code: 'not_found' }))
    await act(async () => {
      expect(await refreshAgentOverview(qc, 'hermes-refresh-nf')).toBeNull()
    })
    expect(qc.getQueryData(['agents', 'overview', 'hermes-refresh-nf'])).toBeNull()

    const kept = { name: 'hermes', kind: 'daemon', installed: true, phase: 'ready' }
    qc.setQueryData(['agents', 'overview', 'hermes'], kept)
    vi.mocked(client.apiGet).mockRejectedValueOnce(new client.Hal0Error('boom', { status: 500, code: 'internal' }))
    await act(async () => {
      await expect(refreshAgentOverview(qc, 'hermes')).rejects.toThrow('boom')
    })
    expect(qc.getQueryData(['agents', 'overview', 'hermes'])).toEqual(kept)
  })

  it('useAgentOverview surfaces a real server error instead of hiding it', async () => {
    vi.mocked(client.apiGet).mockRejectedValue(new client.Hal0Error('boom', { status: 500, code: 'internal' }))
    const probe = mountHook(() => useAgentOverview('hermes-500'), newClient())
    await flush()

    expect(probe.get().isError).toBe(true)
    expect(probe.get().data).toBeUndefined()

    act(() => probe.root.unmount())
  })

  it('useAgentOverview polls every 10 s and refetches on window focus', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({ name: 'hermes' })
    const qc = newClient()
    const probe = mountHook(() => useAgentOverview('hermes'), qc)
    await flush()

    const query = qc.getQueryCache().getAll()[0]
    const opts = query?.observers[0]?.options
    expect(opts?.refetchInterval).toBe(10_000)
    expect(opts?.refetchOnWindowFocus).toBe(true)

    act(() => probe.root.unmount())
  })

  it('useAgentOverview does not fetch when disabled, and fetches when enabled is omitted', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({ name: 'hermes' })
    const off = mountHook(() => useAgentOverview('hermes', { enabled: false }), newClient())
    await flush()
    expect(client.apiGet).not.toHaveBeenCalled()
    act(() => off.root.unmount())

    const on = mountHook(() => useAgentOverview('hermes', {}), newClient())
    await flush()
    expect(client.apiGet).toHaveBeenCalledTimes(1)
    act(() => on.root.unmount())
  })

  // ── mutations ─────────────────────────────────────────────────────

  it('useAgentRepair posts and invalidates agents and updates', async () => {
    vi.mocked(client.apiPost).mockResolvedValue({ id: 'r1', state: 'queued' })
    const qc = newClient()
    qc.setQueryData(['agents', 'overview', 'hermes'], { name: 'hermes' })
    qc.setQueryData(['updates', 'state'], {})
    qc.setQueryData(['slots'], [])
    const inv = vi.spyOn(qc, 'invalidateQueries')
    const probe = mountHook(() => useAgentRepair('hermes'), qc)

    let job: unknown
    await act(async () => {
      job = await probe.get().mutateAsync()
    })

    expect(client.apiPost).toHaveBeenCalledWith('/api/agents/hermes/repair')
    expect(job).toEqual({ id: 'r1', state: 'queued' })
    expect(inv).toHaveBeenCalledWith({ queryKey: ['agents'] })
    expect(inv).toHaveBeenCalledWith({ queryKey: ['updates'] })
    expect(qc.getQueryState(['agents', 'overview', 'hermes'])?.isInvalidated).toBe(true)
    expect(qc.getQueryState(['updates', 'state'])?.isInvalidated).toBe(true)

    act(() => probe.root.unmount())
  })

  it('useAgentGatewayRestart posts to the gateway route and invalidates agents', async () => {
    vi.mocked(client.apiPost).mockResolvedValue({ status: 'restarted' })
    const qc = newClient()
    const inv = vi.spyOn(qc, 'invalidateQueries')
    const probe = mountHook(() => useAgentGatewayRestart('hermes'), qc)

    await act(async () => {
      await probe.get().mutateAsync()
    })

    expect(client.apiPost).toHaveBeenCalledWith('/api/agents/hermes/gateway/restart')
    expect(inv).toHaveBeenCalledWith({ queryKey: ['agents'] })

    act(() => probe.root.unmount())
  })

  it('useAgentUninstall deletes the agent and invalidates agents', async () => {
    vi.mocked(client.apiDelete).mockResolvedValue({ name: 'hermes', status: 'uninstalled' })
    const qc = newClient()
    const inv = vi.spyOn(qc, 'invalidateQueries')
    const probe = mountHook(() => useAgentUninstall('hermes'), qc)

    let result: unknown
    await act(async () => {
      result = await probe.get().mutateAsync()
    })

    expect(client.apiDelete).toHaveBeenCalledWith('/api/agents/hermes')
    expect(result).toEqual({ name: 'hermes', status: 'uninstalled' })
    expect(inv).toHaveBeenCalledWith({ queryKey: ['agents'] })

    act(() => probe.root.unmount())
  })

  it('useAgentUninstall passes not_installed through', async () => {
    vi.mocked(client.apiDelete).mockResolvedValue({ name: 'hermes', status: 'not_installed' })
    const probe = mountHook(() => useAgentUninstall('hermes'), newClient())

    let result: unknown
    await act(async () => {
      result = await probe.get().mutateAsync()
    })

    expect(result).toEqual({ name: 'hermes', status: 'not_installed' })

    act(() => probe.root.unmount())
  })

  it('useAgentUninstall lifts a 207 partial removal out of the error envelope', async () => {
    // client.ts throws only on !res.ok, so a 207 resolves with the envelope as
    // its body: {error: {code, message, details: {name, status, removed, residual}}}.
    const residual = [{ path: '/var/lib/hal0/agents/hermes/venv', reason: 'Permission denied' }]
    vi.mocked(client.apiDelete).mockResolvedValue({
      error: {
        code: 'agent.uninstall_incomplete',
        message: 'could not remove 1 entry',
        details: { name: 'hermes', status: 'partially_uninstalled', removed: true, residual },
      },
    })
    const qc = newClient()
    const inv = vi.spyOn(qc, 'invalidateQueries')
    const probe = mountHook(() => useAgentUninstall('hermes'), qc)

    let result: unknown
    await act(async () => {
      result = await probe.get().mutateAsync()
    })

    expect(result).toEqual({
      name: 'hermes',
      status: 'partially_uninstalled',
      removed: true,
      residual,
    })
    // The agent is partly gone, so the lists still refresh.
    expect(inv).toHaveBeenCalledWith({ queryKey: ['agents'] })

    act(() => probe.root.unmount())
  })

  it('useAgentUninstall rejects a body it cannot read instead of reporting success', async () => {
    vi.mocked(client.apiDelete).mockResolvedValue(null)
    const qc = newClient()
    const inv = vi.spyOn(qc, 'invalidateQueries')
    const probe = mountHook(() => useAgentUninstall('hermes'), qc)

    await act(async () => {
      await expect(probe.get().mutateAsync()).rejects.toThrow('unexpected response uninstalling hermes')
    })

    expect(inv).not.toHaveBeenCalled()

    act(() => probe.root.unmount())
  })

  it('useCommandApprovalsMode puts the mode, writes the result into both caches, and refetches all but the overview', async () => {
    const applied = {
      mode: 'manual',
      effective: 'manual',
      in_sync: true,
      changed: true,
      restart: 'restarted',
      message: 'applied',
    }
    vi.mocked(client.apiPut).mockResolvedValue(applied)
    const qc = newClient()
    qc.setQueryData(['agents', 'overview', 'hermes'], {
      name: 'hermes',
      kind: 'daemon',
      installed: true,
      approvals: { mode: 'smart', effective: 'smart', in_sync: false, pending: 2 },
    })
    const inv = vi.spyOn(qc, 'invalidateQueries')
    const probe = mountHook(() => useCommandApprovalsMode('hermes'), qc)

    let result: unknown
    await act(async () => {
      result = await probe.get().mutateAsync('manual')
    })

    // The full apply result, so the card can toast `restart` and `message`.
    expect(result).toEqual(applied)

    expect(client.apiPut).toHaveBeenCalledWith('/api/agents/hermes/command-approvals', {
      mode: 'manual',
    })
    // The Settings query and the card's overview read the new mode at once.
    expect(qc.getQueryData(['agent-command-approvals', 'hermes'])).toEqual(applied)
    expect(
      (qc.getQueryData(['agents', 'overview', 'hermes']) as { approvals: unknown }).approvals,
    ).toEqual({ mode: 'manual', effective: 'manual', in_sync: true, pending: 2 })

    // Every agents query refetches except this agent's overview, whose plain
    // read would come from the route's cache with the old mode.
    expect(inv).toHaveBeenCalledTimes(1)
    const filter = inv.mock.calls[0]?.[0] as unknown as {
      queryKey: unknown[]
      predicate: (q: { queryKey: unknown[] }) => boolean
    }
    expect(filter.queryKey).toEqual(['agents'])
    expect(filter.predicate({ queryKey: ['agents', 'overview', 'hermes'] })).toBe(false)
    expect(filter.predicate({ queryKey: ['agents', 'overview', 'pi'] })).toBe(true)
    expect(filter.predicate({ queryKey: ['agents', 'personas', 'hermes'] })).toBe(true)
    expect(filter.predicate({ queryKey: ['agents', 'list'] })).toBe(true)

    act(() => probe.root.unmount())
  })

  it('a failed repair rejects and leaves the caches alone', async () => {
    vi.mocked(client.apiPost).mockRejectedValue(
      new client.Hal0Error('busy', { status: 409, code: 'agent.busy' }),
    )
    const qc = newClient()
    const inv = vi.spyOn(qc, 'invalidateQueries')
    const probe = mountHook(() => useAgentRepair('hermes'), qc)

    await act(async () => {
      await expect(probe.get().mutateAsync()).rejects.toMatchObject({ status: 409 })
    })

    expect(inv).not.toHaveBeenCalled()

    act(() => probe.root.unmount())
  })

  // ── useUnitLogTail ────────────────────────────────────────────────

  it('useUnitLogTail reads the journal tail, 10 s poll', async () => {
    const body = { unit: 'hal0-agent@hermes', lines: ['a', 'b'], count: 2 }
    vi.mocked(client.apiGet).mockResolvedValue(body)
    const qc = newClient()
    const probe = mountHook(() => useUnitLogTail('hal0-agent@hermes', 40, true), qc)
    await flush()

    expect(client.apiGet).toHaveBeenCalledWith('/api/logs?unit=hal0-agent%40hermes&n=40')
    expect(probe.get().data).toEqual(body)
    expect(pollMs(qc)).toBe(10_000)

    act(() => probe.root.unmount())
  })

  it('useUnitLogTail returns null on 404 without throwing', async () => {
    vi.mocked(client.apiGet).mockRejectedValue(new client.Hal0Error('nf', { status: 404, code: 'not_found' }))
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const probe = mountHook(() => useUnitLogTail('hal0-agent@nf', 10, true), newClient())
    await flush()

    expect(probe.get().data).toBeNull()
    expect(probe.get().isError).toBe(false)

    act(() => probe.root.unmount())
  })

  it('useUnitLogTail does not fetch when disabled or without a unit', async () => {
    const off = mountHook(() => useUnitLogTail('hal0-agent@hermes', 40, false), newClient())
    await flush()
    act(() => off.root.unmount())

    const blank = mountHook(() => useUnitLogTail('', 40, true), newClient())
    await flush()
    act(() => blank.root.unmount())

    expect(client.apiGet).not.toHaveBeenCalled()
  })
})

describe('agents hook bridge', () => {
  it('publishes every hook the state-aware cards read on window, by identity', async () => {
    await import('@/dash/agents/agents-hook-bridge')
    const agents = await import('@/api/hooks/useAgents')
    const { useSlots } = await import('@/api/hooks/useSlots')
    const { useCapabilities } = await import('@/api/hooks/useCapabilities')
    const { useComponentConverge } = await import('@/api/hooks/useComponents')
    const { useConfigUrls } = await import('@/api/hooks/useConfigUrls')
    const { useUpdateJob } = await import('@/api/hooks/useUpdates')
    const w = window as unknown as Record<string, unknown>
    const expected: Record<string, unknown> = {
      // Already published before this task: must survive the extension.
      __hal0UseAgents: agents.useAgents,
      __hal0UseAgentRestart: agents.useAgentRestart,
      __hal0UseSlots: useSlots,
      // New.
      __hal0UseAgentOverview: agents.useAgentOverview,
      __hal0UseAgentRepair: agents.useAgentRepair,
      __hal0UseAgentGatewayRestart: agents.useAgentGatewayRestart,
      __hal0UseAgentUninstall: agents.useAgentUninstall,
      __hal0UseCommandApprovalsMode: agents.useCommandApprovalsMode,
      __hal0UseUnitLogTail: agents.useUnitLogTail,
      __hal0UseAgentPersonas: agents.useAgentPersonas,
      __hal0UsePersonaActivate: agents.usePersonaActivate,
      __hal0UseComponentConverge: useComponentConverge,
      __hal0UseUpdateJob: useUpdateJob,
      __hal0UseConfigUrls: useConfigUrls,
      __hal0UseCapabilities: useCapabilities,
      __hal0UseAgentApprovalsCount: agents.useAgentApprovalsCount,
      // Not a hook: the cards call it with their query client after an action.
      __hal0RefreshAgentOverview: agents.refreshAgentOverview,
    }
    for (const [name, hook] of Object.entries(expected)) {
      expect(typeof hook, `${name} source`).toBe('function')
      expect(w[name], name).toBe(hook)
    }
    expect(w.__hal0AgentArt).toMatchObject({ hermes: expect.any(String), pi: expect.any(String) })
  })
})
