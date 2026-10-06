// @vitest-environment happy-dom
//
// #1974: a finished FLM pull resets the backend's FLM-image probe, but
// useCapabilities() only polls while `backends_settled === false`. Once a
// response said `true`, nothing refetched it after a pull, so a mounted
// capabilities view kept hiding NPU until remount. usePullJob must invalidate
// the capabilities query on every terminal pull state (SSE terminal event and
// the cancel POST); the refetch then sees `backends_settled: false` and the
// 2 s poll resumes until the re-probe lands.
//
// Harness mirrors src/dash/__tests__/runner-images-pull-terminal.test.tsx:
// no @testing-library, a probe component mounted with createRoot/act, the api
// client mocked at its boundary, and a hand-rolled fake EventSource.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
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

const { usePullJob } = await import('@/api/hooks/useModels')
const { CAPABILITIES_QUERY_KEY } = await import('@/api/hooks/useCapabilities')

function Probe({ onSnapshot }: { onSnapshot: (snap: ReturnType<typeof usePullJob>) => void }) {
  onSnapshot(usePullJob())
  return null
}

function mountProbe() {
  let snapshot!: ReturnType<typeof usePullJob>
  const host = document.createElement('div')
  document.body.appendChild(host)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // A settled capabilities response is already cached, as on a mounted page.
  qc.setQueryData(CAPABILITIES_QUERY_KEY, { backends: [], backends_settled: true })
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

function capabilitiesInvalidated(qc: QueryClient): boolean {
  return qc.getQueryState(CAPABILITIES_QUERY_KEY)?.isInvalidated === true
}

describe('usePullJob invalidates capabilities when a pull ends (#1974)', () => {
  afterEach(() => {
    FakeEventSource.instances.length = 0
    document.body.innerHTML = ''
  })

  it.each(['completed', 'failed', 'cancelled'])('on an SSE %s event', async (terminal) => {
    const probe = mountProbe()
    await act(async () => {
      await probe.get().start('qwen3:1.7b')
    })
    expect(capabilitiesInvalidated(probe.qc)).toBe(false)

    await act(async () => {
      FakeEventSource.instances[0].emit('progress', { state: 'running', bytes_downloaded: 1 })
    })
    expect(capabilitiesInvalidated(probe.qc)).toBe(false)

    await act(async () => {
      FakeEventSource.instances[0].emit(terminal, { state: terminal })
    })
    expect(capabilitiesInvalidated(probe.qc)).toBe(true)

    act(() => probe.root.unmount())
  })

  it('on cancel()', async () => {
    const probe = mountProbe()
    await act(async () => {
      await probe.get().start('qwen3:1.7b')
    })
    await act(async () => {
      FakeEventSource.instances[0].emit('progress', { state: 'running' })
    })
    expect(capabilitiesInvalidated(probe.qc)).toBe(false)

    await act(async () => {
      await probe.get().cancel()
    })
    expect(capabilitiesInvalidated(probe.qc)).toBe(true)

    act(() => probe.root.unmount())
  })
})
