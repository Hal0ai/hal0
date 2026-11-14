// @vitest-environment happy-dom
//
// A finished FLM download flips the catalogue row's `installed` flag, but the NPU tab's catalogue
// query (['slots', 'flm', 'models'], useFlmCatalog) is not under ['models'], so the lane and the
// Models panel kept showing "download" until the 30 s stale time. usePullJob must invalidate it on
// every terminal pull state (SSE terminal event and the cancel POST). Harness mirrors
// pullInvalidatesCapabilities.test.tsx.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    apiPost: () => Promise.resolve({ id: 'job-1' }),
  }
})

type Listener = (evt: MessageEvent) => void

/** Controllable stand-in for EventSource: tests emit named SSE events on it. */
class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  onmessage: Listener | null = null
  onerror: (() => void) | null = null
  closed = false
  listeners: Record<string, Listener[]> = {}
  /** Register the instance so the test can reach it. */
  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  /** Subscribe to a named SSE event, as usePullJob does. */
  addEventListener(type: string, fn: Listener) {
    ;(this.listeners[type] ??= []).push(fn)
  }
  /** Deliver a named SSE event with a JSON payload to its listeners. */
  emit(type: string, data: unknown) {
    const evt = { data: JSON.stringify(data) } as MessageEvent
    for (const fn of this.listeners[type] ?? []) fn(evt)
  }
  /** Mark the stream closed. */
  close() {
    this.closed = true
  }
}
;(globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource

const { usePullJob } = await import('@/api/hooks/useModels')
const FLM_CATALOG_KEY = ['slots', 'flm', 'models']

/** Surfaces usePullJob's latest snapshot to the test. */
function Probe({ onSnapshot }: { onSnapshot: (snap: ReturnType<typeof usePullJob>) => void }) {
  onSnapshot(usePullJob())
  return null
}

/** Mount the probe under a QueryClient that already holds a settled capabilities response. */
function mountProbe() {
  let snapshot!: ReturnType<typeof usePullJob>
  const host = document.createElement('div')
  document.body.appendChild(host)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // The FLM catalogue is cached, as on a mounted NPU tab (useFlmCatalog's key).
  qc.setQueryData(FLM_CATALOG_KEY, { models: [{ model: 'qwen3:8b', installed: false }], probe_ok: true })
  const root = createRoot(host)
  act(() => {
    root.render(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(Probe, {
          onSnapshot: (snap) => {
            snapshot = snap
          },
        }),
      ),
    )
  })
  return { root, qc, get: () => snapshot }
}

/** Has the FLM catalogue been marked stale (it will refetch)? */
function catalogInvalidated(qc: QueryClient): boolean {
  return qc.getQueryState(FLM_CATALOG_KEY)?.isInvalidated === true
}

describe('usePullJob invalidates the FLM catalogue when a pull ends', () => {
  afterEach(() => {
    FakeEventSource.instances.length = 0
    document.body.innerHTML = ''
  })

  it.each(['completed', 'failed', 'cancelled'])('on an SSE %s event', async (terminal) => {
    const probe = mountProbe()
    await act(async () => {
      await probe.get().start('qwen3:8b')
    })
    await act(async () => {
      FakeEventSource.instances[0].emit('progress', { state: 'running', bytes_downloaded: 1 })
    })
    expect(catalogInvalidated(probe.qc)).toBe(false)

    await act(async () => {
      FakeEventSource.instances[0].emit(terminal, { state: terminal })
    })
    expect(catalogInvalidated(probe.qc)).toBe(true)

    act(() => probe.root.unmount())
  })

  it('on cancel()', async () => {
    const probe = mountProbe()
    await act(async () => {
      await probe.get().start('qwen3:8b')
    })
    await act(async () => {
      FakeEventSource.instances[0].emit('progress', { state: 'running' })
    })
    expect(catalogInvalidated(probe.qc)).toBe(false)

    await act(async () => {
      await probe.get().cancel()
    })
    expect(catalogInvalidated(probe.qc)).toBe(true)

    act(() => probe.root.unmount())
  })
})
