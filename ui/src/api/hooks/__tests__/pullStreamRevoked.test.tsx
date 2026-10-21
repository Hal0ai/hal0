// @vitest-environment happy-dom
//
// #2824: a key rotation / session expiry ends pull SSE streams with a final
// `event: revoked` frame (or an `error`) while the server-side pull keeps
// running. usePullJob and useSlotImagePull must close the stream and
// reconcile through the pull-status GET; a 401 there surfaces a signed-out
// error instead of a frozen bar (model) or a false "failed" (slot image).
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const apiGetMock = vi.fn()
vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    apiGet: (...a: unknown[]) => apiGetMock(...a),
    apiPost: () => Promise.resolve({ id: 'job-1' }),
    api: () => Promise.resolve({ image: 'img:1' }),
  }
})
vi.mock('../../client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    apiGet: (...a: unknown[]) => apiGetMock(...a),
    apiPost: () => Promise.resolve({ id: 'job-1' }),
    api: () => Promise.resolve({ image: 'img:1' }),
  }
})

type Listener = (evt: MessageEvent) => void

class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  onmessage: Listener | null = null
  onerror: (() => void) | null = null
  closed = false
  listeners: Record<string, Listener[]> = {}
  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  addEventListener(type: string, fn: Listener) {
    ;(this.listeners[type] ??= []).push(fn)
  }
  emit(type: string, data: unknown) {
    const evt = { data: JSON.stringify(data) } as MessageEvent
    for (const fn of this.listeners[type] ?? []) fn(evt)
  }
  close() {
    this.closed = true
  }
}
;(globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource

const { Hal0Error } = await import('@/api/client')
const { usePullJob } = await import('@/api/hooks/useModels')
const { useSlotImagePull } = await import('@/api/hooks/useSlots')

let root: ReturnType<typeof createRoot> | null = null

function mount<T>(useHook: () => T) {
  const ref: { current: T } = { current: undefined as T }
  const qc = new QueryClient()
  const invalidate = vi.spyOn(qc, 'invalidateQueries')
  function Probe() {
    ref.current = useHook()
    return null
  }
  const el = document.createElement('div')
  root = createRoot(el)
  act(() => {
    root!.render(
      <QueryClientProvider client={qc}>
        <Probe />
      </QueryClientProvider>,
    )
  })
  return { ref, invalidate }
}

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve() })

beforeEach(() => {
  FakeEventSource.instances = []
  apiGetMock.mockReset()
})
afterEach(() => {
  act(() => root?.unmount())
  root = null
})

const unauth = () => new Hal0Error('auth required', { code: 'auth.required', status: 401 })

describe('usePullJob stream end (#2824)', () => {
  for (const how of ['revoked', 'error'] as const) {
    it(`${how}: closes the stream and applies the status GET result`, async () => {
      apiGetMock.mockResolvedValue({ state: 'completed', bytes_downloaded: 10, bytes_total: 10 })
      const { ref } = mount(usePullJob)
      await act(async () => { await ref.current.start('m1') })
      const es = FakeEventSource.instances[0]
      await act(async () => {
        if (how === 'revoked') es.emit('revoked', { reason: 'credential revoked' })
        else es.onerror?.()
      })
      await flush()
      expect(es.closed).toBe(true)
      expect(apiGetMock).toHaveBeenCalledWith('/api/models/m1/pull/status')
      expect(ref.current.state).toBe('completed')
    })
  }

  it('401 on the status GET surfaces a signed-out error and rechecks auth', async () => {
    apiGetMock.mockRejectedValue(unauth())
    const { ref, invalidate } = mount(usePullJob)
    await act(async () => { await ref.current.start('m1') })
    await act(async () => { FakeEventSource.instances[0].onerror?.() })
    await flush()
    expect(ref.current.error?.code).toBe('auth.required')
    expect(ref.current.state).toBe('queued')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['auth-status'] })
  })

  it('non-401 GET failure keeps the last known state without an error', async () => {
    apiGetMock.mockRejectedValue(new Error('network'))
    const { ref } = mount(usePullJob)
    await act(async () => { await ref.current.start('m1') })
    await act(async () => { FakeEventSource.instances[0].onerror?.() })
    await flush()
    expect(ref.current.error).toBeNull()
  })

  it('a late reconcile for a reset job is ignored', async () => {
    let resolve!: (v: unknown) => void
    apiGetMock.mockReturnValue(new Promise((r) => { resolve = r }))
    const { ref } = mount(usePullJob)
    await act(async () => { await ref.current.start('m1') })
    await act(async () => { FakeEventSource.instances[0].onerror?.() })
    act(() => ref.current.reset())
    await act(async () => { resolve({ state: 'completed' }) })
    expect(ref.current.state).toBe('idle')
  })
})

describe('useSlotImagePull stream end (#2824)', () => {
  for (const how of ['revoked', 'error'] as const) {
    it(`${how}: a still-running pull is not marked failed`, async () => {
      apiGetMock.mockResolvedValue({ slot_name: 's1', state: 'pulling', layer: 2, total_layers: 5 })
      const { ref } = mount(useSlotImagePull)
      await act(async () => { await ref.current.start('s1') })
      const es = FakeEventSource.instances[0]
      await act(async () => {
        if (how === 'revoked') es.emit('revoked', { reason: 'credential revoked' })
        else es.onerror?.()
      })
      await flush()
      expect(es.closed).toBe(true)
      expect(apiGetMock).toHaveBeenCalledWith('/api/slots/s1/pull/status')
      expect(ref.current.state).toBe('pulling')
      expect(ref.current.layer).toBe(2)
      expect(ref.current.error).toBeNull()
    })
  }

  it('reconcile reporting failed marks it failed', async () => {
    apiGetMock.mockResolvedValue({ state: 'failed', error: 'boom' })
    const { ref } = mount(useSlotImagePull)
    await act(async () => { await ref.current.start('s1') })
    await act(async () => { FakeEventSource.instances[0].onerror?.() })
    await flush()
    expect(ref.current.state).toBe('failed')
    expect(ref.current.error).toBe('boom')
  })

  it('401 on the status GET shows signed-out, not failed', async () => {
    apiGetMock.mockRejectedValue(unauth())
    const { ref, invalidate } = mount(useSlotImagePull)
    await act(async () => { await ref.current.start('s1') })
    await act(async () => { FakeEventSource.instances[0].onerror?.() })
    await flush()
    expect(ref.current.state).toBe('pulling')
    expect(ref.current.error).toMatch(/signed out/i)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['auth-status'] })
  })
})
