// @vitest-environment happy-dom
//
// Persona detail + activate hooks (agent-cards controls, task 13).
//
//   useAgentPersona(agentId, pid)  -> GET  /api/agents/{id}/personas/{pid}
//   usePersonaActivate(agentId)    -> POST /api/agents/{id}/personas/{pid}/activate
//                                     then invalidates ['agents','personas',agentId]
//
// Harness mirrors pullInvalidatesCapabilities.test.tsx: no @testing-library, a
// probe component mounted with createRoot/act under a QueryClientProvider, and
// the api client mocked at its boundary.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn() }
})

const client = await import('@/api/client')
const { useAgentPersona, usePersonaActivate } = await import('@/api/hooks/useAgents')
const { ENDPOINTS } = await import('@/api/endpoints')

type PersonaQuery = ReturnType<typeof useAgentPersona>
type ActivateMutation = ReturnType<typeof usePersonaActivate>

/** Mount `useHook` under a fresh QueryClient and expose its latest result. */
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
    root.render(
      React.createElement(QueryClientProvider, { client: qc }, React.createElement(Probe)),
    )
  })
  return { root, get: () => latest }
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

describe('persona endpoint helpers', () => {
  it('builds the detail and activate paths, encoding both ids', () => {
    expect(ENDPOINTS.agentPersona('hermes', 'coder')).toBe('/api/agents/hermes/personas/coder')
    expect(ENDPOINTS.agentPersonaActivate('hermes', 'coder')).toBe(
      '/api/agents/hermes/personas/coder/activate',
    )
    expect(ENDPOINTS.agentPersona('a/b', 'c d')).toBe('/api/agents/a%2Fb/personas/c%20d')
    expect(ENDPOINTS.agentPersonaActivate('a/b', 'c d')).toBe(
      '/api/agents/a%2Fb/personas/c%20d/activate',
    )
  })
})

describe('persona detail + activate hooks', () => {
  beforeEach(() => {
    vi.mocked(client.apiGet).mockReset()
    vi.mocked(client.apiPost).mockReset()
  })
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('usePersonaActivate posts to the activate route and invalidates personas', async () => {
    vi.mocked(client.apiPost).mockResolvedValue({})
    const qc = newClient()
    // A cached persona list + detail, as on a mounted persona picker.
    qc.setQueryData(['agents', 'personas', 'hermes'], { agent_id: 'hermes', active: null, personas: [] })
    qc.setQueryData(['agents', 'personas', 'hermes', 'coder'], { id: 'coder' })
    qc.setQueryData(['agents', 'personas', 'other'], { agent_id: 'other', active: null, personas: [] })
    const probe = mountHook<ActivateMutation>(() => usePersonaActivate('hermes'), qc)

    await act(async () => {
      await probe.get().mutateAsync('coder')
    })

    expect(client.apiPost).toHaveBeenCalledWith('/api/agents/hermes/personas/coder/activate')
    expect(qc.getQueryState(['agents', 'personas', 'hermes'])?.isInvalidated).toBe(true)
    expect(qc.getQueryState(['agents', 'personas', 'hermes', 'coder'])?.isInvalidated).toBe(true)
    // Another agent's personas are left alone.
    expect(qc.getQueryState(['agents', 'personas', 'other'])?.isInvalidated).toBe(false)

    act(() => probe.root.unmount())
  })

  it('useAgentPersona reads the detail route', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({
      id: 'coder',
      display_name: 'Coder',
      system_prompt: 'p',
      tools_allowed: ['read_file'],
      active: false,
    })
    const probe = mountHook<PersonaQuery>(() => useAgentPersona('hermes', 'coder'), newClient())
    await flush()

    expect(client.apiGet).toHaveBeenCalledWith('/api/agents/hermes/personas/coder')
    expect(probe.get().data?.display_name).toBe('Coder')

    act(() => probe.root.unmount())
  })

  it('useAgentPersona does not fetch without a persona id', async () => {
    const probe = mountHook<PersonaQuery>(() => useAgentPersona('hermes', null), newClient())
    await flush()

    expect(client.apiGet).not.toHaveBeenCalled()
    expect(probe.get().data).toBeUndefined()

    act(() => probe.root.unmount())
  })

  it('useAgentPersona does not fetch when disabled', async () => {
    const probe = mountHook<PersonaQuery>(
      () => useAgentPersona('hermes', 'coder', { enabled: false }),
      newClient(),
    )
    await flush()

    expect(client.apiGet).not.toHaveBeenCalled()

    act(() => probe.root.unmount())
  })
})
