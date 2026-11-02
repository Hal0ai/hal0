// @vitest-environment happy-dom
//
// The slot drawer's halogen memory readout rides GET /api/slots/{name}/resolved
// (`['slot-resolved', name]`), and its verdict flips on the slot's state: a
// loaded slot reads "loaded", an unloaded one is judged against the free pool.
// So a load, unload, restart or swap must mark that query stale, or a drawer
// left open across one keeps the old verdict until its staleTime runs out.
//
// Harness mirrors pullInvalidatesCapabilities.test.tsx: no @testing-library, a
// probe component mounted with createRoot/act, the api client mocked at its
// boundary.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: () => Promise.resolve({}) }
})

const hooks = await import('@/api/hooks/useSlots')

type Mutation = { mutateAsync: (vars: never) => Promise<unknown> }

/** Mounts `useHook` under a QueryClient holding a fresh slot list and resolved answer. */
function mount(useHook: () => Mutation) {
  let mutation!: Mutation
  function Probe() {
    mutation = useHook()
    return null
  }
  const host = document.createElement('div')
  document.body.appendChild(host)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  qc.setQueryData(['slots'], [])
  qc.setQueryData(['slot-resolved', 'flash'], { name: 'flash', argv: null })
  const root = createRoot(host)
  act(() => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, React.createElement(Probe)))
  })
  return { root, qc, mutation: () => mutation }
}

const stale = (qc: QueryClient, key: unknown[]) => qc.getQueryState(key)?.isInvalidated === true

describe('slot lifecycle mutations invalidate the resolved readout', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it.each([
    ['useSlotLoad', hooks.useSlotLoad, 'flash'],
    ['useSlotUnload', hooks.useSlotUnload, 'flash'],
    ['useSlotRestart', hooks.useSlotRestart, 'flash'],
    ['useSlotSwap', hooks.useSlotSwap, { name: 'flash', model_id: 'm' }],
    // A Save that moves the profile, a tune ctx_size or vision moves resident_mb.
    ['useSlotEdit', hooks.useSlotEdit, { name: 'flash', body: { profile: 'p' } }],
  ])('%s', async (_label, useHook, vars) => {
    const probe = mount(useHook as () => Mutation)
    expect(stale(probe.qc, ['slot-resolved', 'flash'])).toBe(false)
    await act(async () => {
      await probe.mutation().mutateAsync(vars as never)
    })
    expect(stale(probe.qc, ['slots'])).toBe(true)
    expect(stale(probe.qc, ['slot-resolved', 'flash'])).toBe(true)
    act(() => probe.root.unmount())
  })
})
