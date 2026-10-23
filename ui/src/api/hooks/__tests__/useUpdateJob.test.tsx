// @vitest-environment happy-dom
//
// useUpdateJob(jobId, opts?) -> { job, terminal, lost }
//
// Polls GET /api/updates/status/{id} every 1.5 s until the job is applied or
// failed. It also gives up, with `lost: true`, when the server no longer knows
// the job (404) or, only when the caller sets `staleAfterMs`, when a queued or
// running snapshot has gone quiet for longer than that.
//
// Harness mirrors agentOverviewHooks.test.tsx: no @testing-library, a probe
// component mounted with createRoot/act, the api client mocked at its boundary.
// Timers and the clock are faked so the 1.5 s poll is stepped, not waited on.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn() }
})

const client = await import('@/api/client')
const { useUpdateJob } = await import('@/api/hooks/useUpdates')
const { ENDPOINTS } = await import('@/api/endpoints')
type UpdateJob = import('@/api/hooks/useUpdates').UpdateJob

const NOW = new Date('2026-10-23T12:00:00Z')
const nowS = () => Date.now() / 1000
const MIN = 60_000

function snap(over: Partial<UpdateJob> = {}): UpdateJob {
  return {
    id: 'job-1',
    state: 'running',
    channel: 'stable',
    version: null,
    created_at: nowS(),
    updated_at: nowS(),
    error: null,
    ...over,
  }
}

type Props = { jobId: string | null; opts?: { staleAfterMs?: number } }

function mount(initial: Props) {
  let latest!: ReturnType<typeof useUpdateJob>
  function Probe(props: Props) {
    latest = useUpdateJob(props.jobId, props.opts)
    return null
  }
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const render = (props: Props) => act(() => root.render(React.createElement(Probe, props)))
  render(initial)
  return { get: () => latest, rerender: render, unmount: () => act(() => root.unmount()) }
}

/** Let the pending status read settle. */
const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
/** Step the clock `ms` and let each poll that fires settle. */
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

describe('useUpdateJob', () => {
  const apiGet = client.apiGet as unknown as ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    vi.setSystemTime(NOW)
    apiGet.mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('a 404 from the status route ends the loop as lost, for every caller', async () => {
    apiGet.mockRejectedValue(new client.Hal0Error('no such job', { status: 404 }))
    const h = mount({ jobId: 'job-1' })
    await settle()
    expect(apiGet).toHaveBeenCalledWith(ENDPOINTS.updateStatus('job-1'))
    expect(h.get()).toMatchObject({ terminal: true, lost: true })

    await advance(10_000)
    expect(apiGet).toHaveBeenCalledTimes(1)
    h.unmount()
  })

  it('a job that was running and then 404s keeps its last snapshot and is lost', async () => {
    apiGet.mockResolvedValueOnce(snap({ state: 'running' }))
    apiGet.mockRejectedValue(new client.Hal0Error('no such job', { status: 404 }))
    const h = mount({ jobId: 'job-1' })
    await settle()
    expect(h.get()).toMatchObject({ terminal: false, lost: false })
    expect(h.get().job?.state).toBe('running')

    await advance(1500)
    expect(h.get()).toMatchObject({ terminal: true, lost: true })
    expect(h.get().job?.state).toBe('running')
    h.unmount()
  })

  it('a snapshot stale beyond staleAfterMs is lost', async () => {
    apiGet.mockResolvedValue(snap({ state: 'running', updated_at: nowS() - 51 * 60 }))
    const h = mount({ jobId: 'job-1', opts: { staleAfterMs: 50 * MIN } })
    await settle()
    expect(h.get()).toMatchObject({ terminal: true, lost: true })

    await advance(10_000)
    expect(apiGet).toHaveBeenCalledTimes(1)
    h.unmount()
  })

  it('a queued snapshot goes stale too', async () => {
    apiGet.mockResolvedValue(snap({ state: 'queued', updated_at: nowS() - 90 * 60 }))
    const h = mount({ jobId: 'job-1', opts: { staleAfterMs: 50 * MIN } })
    await settle()
    expect(h.get().lost).toBe(true)
    h.unmount()
  })

  it('a fresh snapshot with staleAfterMs set keeps polling until it goes quiet', async () => {
    // Updated 10 minutes before the clock starts, with a 12 minute window.
    apiGet.mockResolvedValue(snap({ state: 'running', updated_at: nowS() - 10 * 60 }))
    const h = mount({ jobId: 'job-1', opts: { staleAfterMs: 12 * MIN } })
    await settle()
    expect(h.get()).toMatchObject({ terminal: false, lost: false })

    await advance(1500)
    expect(apiGet).toHaveBeenCalledTimes(2)
    expect(h.get().lost).toBe(false)

    // Three minutes on, the same snapshot is 13 minutes old.
    await advance(3 * MIN)
    expect(h.get()).toMatchObject({ terminal: true, lost: true })
    h.unmount()
  })

  it('a stale snapshot without the option keeps polling', async () => {
    apiGet.mockResolvedValue(snap({ state: 'running', updated_at: nowS() - 5 * 3600 }))
    const h = mount({ jobId: 'job-1' })
    await settle()
    expect(h.get()).toMatchObject({ terminal: false, lost: false })

    await advance(1500 * 4)
    expect(apiGet.mock.calls.length).toBeGreaterThanOrEqual(5)
    expect(h.get()).toMatchObject({ terminal: false, lost: false })
    h.unmount()
  })

  it('applied is terminal and not lost, even when it is old', async () => {
    apiGet.mockResolvedValue(snap({ state: 'applied', updated_at: nowS() - 5 * 3600 }))
    const h = mount({ jobId: 'job-1', opts: { staleAfterMs: 50 * MIN } })
    await settle()
    expect(h.get()).toMatchObject({ terminal: true, lost: false })
    expect(h.get().job?.state).toBe('applied')

    await advance(10_000)
    expect(apiGet).toHaveBeenCalledTimes(1)
    h.unmount()
  })

  it('failed is terminal and not lost', async () => {
    apiGet.mockResolvedValue(snap({ state: 'failed', error: 'boom' }))
    const h = mount({ jobId: 'job-1' })
    await settle()
    expect(h.get()).toMatchObject({ terminal: true, lost: false })
    h.unmount()
  })

  it('any other error keeps polling, so a restarting hal0-api can come back', async () => {
    apiGet.mockRejectedValueOnce(new client.Hal0Error('bad gateway', { status: 502 }))
    apiGet.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    apiGet.mockResolvedValue(snap({ state: 'applied' }))
    const h = mount({ jobId: 'job-1' })
    await settle()
    expect(h.get()).toMatchObject({ terminal: false, lost: false })

    await advance(1500)
    expect(h.get()).toMatchObject({ terminal: false, lost: false })

    await advance(1500)
    expect(h.get()).toMatchObject({ terminal: true, lost: false })
    expect(apiGet).toHaveBeenCalledTimes(3)
    h.unmount()
  })

  it('a new job id starts clean: the last job being lost is not this one', async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === ENDPOINTS.updateStatus('job-gone')) {
        throw new client.Hal0Error('no such job', { status: 404 })
      }
      return snap({ id: 'job-2', state: 'running' })
    })
    const h = mount({ jobId: 'job-gone' })
    await settle()
    expect(h.get().lost).toBe(true)

    // The very render that carries the new id already reads as not lost.
    h.rerender({ jobId: 'job-2' })
    expect(h.get()).toMatchObject({ job: null, terminal: false, lost: false })
    await settle()
    expect(h.get().job?.id).toBe('job-2')
    expect(h.get()).toMatchObject({ terminal: false, lost: false })

    h.rerender({ jobId: null })
    expect(h.get()).toMatchObject({ job: null, terminal: false, lost: false })
    h.unmount()
  })

  it('stops polling on unmount', async () => {
    apiGet.mockResolvedValue(snap({ state: 'running' }))
    const h = mount({ jobId: 'job-1' })
    await settle()
    h.unmount()
    const calls = apiGet.mock.calls.length
    await advance(10_000)
    expect(apiGet).toHaveBeenCalledTimes(calls)
  })
})
