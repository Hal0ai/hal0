// @vitest-environment happy-dom
//
// Runner-image pull terminal-state reconciliation (#2120, runner-images-v3
// Task 7). Bug: when the pull SSE stream dies (network blip, proxy timeout,
// server crash) without ever emitting a terminal payload, useRunnerImagePullJob
// keeps whatever state it last saw — usually "running" — forever. The
// dashboard renders "running — 0/? layers" with no way out even though the
// backend has already recorded the job as failed.
//
// Fix: on EventSource `onerror`, close the stream and reconcile against
// GET /api/runner-images/{id}/pull/status once; a server-side terminal state
// (failed here) must land in the hook's state via the existing applyPayload
// mapping.
//
// This repo has no @testing-library — mount a probe component with
// createRoot/act (mirrors runner-images-view.test.tsx's Task 6 render test)
// and drive a hand-rolled fake EventSource (mirrors that file's apiPost-spy
// idiom for faking the api boundary, extended to apiGet + a controllable
// EventSource class installed on globalThis).
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React

// vi.mock factories are hoisted above imports, so the spies + fixtures they
// close over must be hoisted too (mirrors runner-images-view.test.tsx).
// `apiGetImpl` is a mutable indirection so individual tests can swap in a
// deferred/manually-resolved implementation to control exactly when the
// reconcile GET resolves relative to a concurrent cancel()/start() — that's
// the whole point of the generation-counter fix (fix round 1).
const { apiPostCalls, apiGetCalls, apiGetImpl, STATUS_RESPONSE } = vi.hoisted(() => {
  const STATUS_RESPONSE = {
    state: 'failed',
    error: 'podman exit 125',
    error_code: 'runner_image.pull_failed',
    layers_done: 2,
    layers_total: 5,
  }
  return {
    apiPostCalls: [] as string[],
    apiGetCalls: [] as string[],
    apiGetImpl: { current: (_url: string) => Promise.resolve(STATUS_RESPONSE) as Promise<any> },
    STATUS_RESPONSE,
  }
})

// Mock the api-client boundary (not the hook) — apiPost answers the pull-start
// (and cancel) POST, apiGet answers the reconciliation GET /pull/status the
// fix under test must call. Keep everything else (Hal0Error, etc.) real.
vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    apiPost: (url: string) => {
      apiPostCalls.push(url)
      return Promise.resolve({ id: 'job-1' })
    },
    apiGet: (url: string) => {
      apiGetCalls.push(url)
      return apiGetImpl.current(url)
    },
  }
})

/** A promise the test controls the resolution of — for racing the
 * reconcile GET against a concurrent cancel()/start(). */
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

// A controllable fake EventSource: attachStream() does `new EventSource(url)`
// and wires `.onmessage`/`.onerror` onto the instance. Capture instances so
// the test can reach in and fire `.onerror()` to simulate the stream dying.
class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  onmessage: ((evt: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  close() {
    this.closed = true
  }
}
;(globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource

const { useRunnerImagePullJob } = await import('@/api/hooks/useRunnerImages')

// No renderHook in this repo — a tiny probe component surfaces the hook's
// return value to the test via a callback, same as sibling tests reach into
// real-rendered components rather than a hook-testing utility.
function Probe({ onSnapshot }: { onSnapshot: (snap: ReturnType<typeof useRunnerImagePullJob>) => void }) {
  const snap = useRunnerImagePullJob()
  onSnapshot(snap)
  return null
}

describe('useRunnerImagePullJob — stream-close reconciliation (#2120)', () => {
  afterEach(() => {
    apiPostCalls.length = 0
    apiGetCalls.length = 0
    apiGetImpl.current = (_url: string) => Promise.resolve(STATUS_RESPONSE)
    FakeEventSource.instances.length = 0
    document.body.innerHTML = ''
  })

  function mountProbe() {
    let snapshot!: ReturnType<typeof useRunnerImagePullJob>
    const host = document.createElement('div')
    document.body.appendChild(host)
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
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
    // A getter would be captured by-value the instant a caller destructures
    // it (`const { snapshot } = mountProbe()` freezes on the first render's
    // 'idle' snapshot forever) — return a thunk instead so every call reads
    // the live variable the Probe callback keeps reassigning.
    return { root, get: () => snapshot }
  }

  it('lands failed state when the stream dies without a terminal event', async () => {
    const probe = mountProbe()

    await act(async () => {
      await probe.get().start('rocmfpx-combined')
    })

    // The pull kicked off and attached a (fake) SSE stream; nothing terminal
    // has happened yet.
    expect(apiPostCalls[apiPostCalls.length - 1]).toMatch(/\/pull$/)
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(probe.get().terminal).toBe(false)

    // Stream drops mid-pull, without ever sending a terminal payload.
    await act(async () => {
      FakeEventSource.instances[0].onerror?.()
      // Flush the reconciliation fetch's microtask chain.
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(apiGetCalls[apiGetCalls.length - 1]).toMatch(/\/pull\/status$/)
    expect(probe.get().state).toBe('failed')
    expect(probe.get().terminal).toBe(true)
    expect(probe.get().error?.message).toMatch(/exit 125/)
    expect(probe.get().error?.code).toBe('runner_image.pull_failed')
    // The stream must be torn down, not left dangling.
    expect(FakeEventSource.instances[0].closed).toBe(true)

    act(() => {
      probe.root.unmount()
    })
  })

  // Fix round 1 (Medium gap from review): the reconcile GET is async and can
  // resolve after the job it was launched for stopped being current. Case
  // (a) — cancel-then-resurrect: the user cancels while a reconcile GET
  // triggered by an earlier `onerror` is still in flight; that GET's
  // pre-cancel "running" snapshot must not land afterwards and flip the UI
  // back to running (permanently, since the stream is already closed and
  // nothing else will ever poll again).
  it('does not resurrect a cancelled job with a late pre-cancel reconcile GET', async () => {
    const probe = mountProbe()

    const late = deferred<any>()
    apiGetImpl.current = () => late.promise

    await act(async () => {
      await probe.get().start('rocmfpx-combined')
    })
    expect(probe.get().state).toBe('queued')

    // Stream drops; the reconcile GET fires but hangs (not yet resolved).
    act(() => {
      FakeEventSource.instances[0].onerror?.()
    })

    // User cancels before the reconcile GET comes back.
    await act(async () => {
      await probe.get().cancel()
    })
    expect(probe.get().state).toBe('cancelled')
    expect(probe.get().terminal).toBe(true)

    // The stale reconcile GET now resolves with a pre-cancel "running"
    // snapshot — it must be dropped, not applied.
    await act(async () => {
      late.resolve({ state: 'running', layers_done: 1, layers_total: 5 })
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(probe.get().state).toBe('cancelled')
    expect(probe.get().terminal).toBe(true)

    act(() => {
      probe.root.unmount()
    })
  })

  // Case (b) — cross-job clobber: a new pull starts while an older job's
  // reconcile GET is still in flight; the late response must not overwrite
  // the newer job's state.
  it('does not let a stale job late reconcile clobber a newer job', async () => {
    const probe = mountProbe()

    const staleGet = deferred<any>()
    apiGetImpl.current = () => staleGet.promise

    await act(async () => {
      await probe.get().start('job-a')
    })
    act(() => {
      FakeEventSource.instances[0].onerror?.() // job A's reconcile GET fires and hangs
    })

    // A new pull for a different job starts before job A's GET resolves.
    // `start()` doesn't itself call apiGet, so job A's already-in-flight
    // call still resolves via `staleGet` below regardless of this
    // reassignment — it only guards against a future, unrelated apiGet call
    // hanging the test.
    apiGetImpl.current = () => Promise.resolve({ state: 'running' })
    await act(async () => {
      await probe.get().start('job-b')
    })
    expect(probe.get().imageId).toBe('job-b')
    expect(probe.get().state).toBe('queued')

    // Job A's stale GET now resolves with a terminal snapshot for the OLD
    // job — must not land on job B's state.
    await act(async () => {
      staleGet.resolve({ state: 'failed', error: 'stale job A error', error_code: 'x' })
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(probe.get().imageId).toBe('job-b')
    expect(probe.get().state).toBe('queued')
    expect(probe.get().error).toBeNull()

    act(() => {
      probe.root.unmount()
    })
  })
})
