// @vitest-environment happy-dom
// useSlotLoad / useSlotRestart accept a plain slot name (no body) or `{ name, unloadNpuHolder }`,
// which posts `{ unload_npu_holder: true }` — the opt-in retry for a `slot.npu_conflict` 409.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const calls: Array<{ path: string; init: { method?: string; body?: unknown } }> = []
vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: (path: string, init: { method?: string; body?: unknown }) => (calls.push({ path, init }), Promise.resolve({})) }
})
const hooks = await import('@/api/hooks/useSlots')

type Mutation = { mutateAsync: (v: unknown) => Promise<unknown> }
function mount(useHook: () => Mutation) {
  let m!: Mutation
  const Probe = () => ((m = useHook()), null)
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => root.render(React.createElement(QueryClientProvider, { client: new QueryClient() }, React.createElement(Probe))))
  return () => m
}

describe('slot start variable', () => {
  afterEach(() => {
    calls.length = 0
    document.body.innerHTML = ''
  })
  it.each([
    ['load', hooks.useSlotLoad, '/load'],
    ['restart', hooks.useSlotRestart, '/restart'],
  ])('%s: string posts no body, flag posts unload_npu_holder', async (_k, hook, suffix) => {
    const m = mount(hook as () => Mutation)
    await act(async () => { await m().mutateAsync('npu-gguf') })
    await act(async () => { await m().mutateAsync({ name: 'npu-gguf' }) })
    await act(async () => { await m().mutateAsync({ name: 'npu-gguf', unloadNpuHolder: true }) })
    expect(calls.map((c) => c.path.endsWith(`/npu-gguf${suffix}`))).toEqual([true, true, true])
    expect(calls.map((c) => c.init.body)).toEqual([undefined, undefined, { unload_npu_holder: true }])
  })
})
