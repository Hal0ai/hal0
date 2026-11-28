// @vitest-environment happy-dom
// The Benchmarks page shows a failed request as a failure, not as "no data" (#4118).
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('../../api/client', () => ({ apiGet: get, apiPost: vi.fn(), apiDelete: vi.fn() }))
vi.mock('../../api/hooks/useUpdates', () => ({ useUpdateState: () => ({ data: null }) }))

const { default: Benchmarks } = await import('../Benchmarks')

type Answers = Record<string, unknown | Error>
let answers: Answers
let root: Root
let host: HTMLDivElement

/** Answer by path prefix; an Error value rejects. Anything unlisted answers empty. */
function serve(next: Answers) {
  answers = next
  get.mockImplementation(async (url: string) => {
    const key = Object.keys(answers).find((k) => url.startsWith(k))
    const value = key ? answers[key] : {}
    if (value instanceof Error) throw value
    return value
  })
}

async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
  }
}

async function open(tab: string) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => root.render(createElement(Benchmarks, { param: tab })))
  await settle()
}

const text = () => host.textContent ?? ''
const retry = () =>
  Array.from(host.querySelectorAll('button')).find((b) => b.textContent === 'Retry') as HTMLButtonElement

beforeEach(() => {
  get.mockReset()
  vi.useRealTimers()
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('Benchmarks load errors (#4118)', () => {
  it('evals: a failed request is an error with the server message, not "no eval records"', async () => {
    serve({ '/api/benchmarks/evals': new Error('sign in to continue') })
    await open('evals')
    expect(text()).toContain('Could not load evals: sign in to continue')
    expect(text()).not.toContain('no eval records yet')
  })

  it('evals: Retry asks again, and an empty answer then shows the empty state', async () => {
    serve({ '/api/benchmarks/evals': new Error('boom') })
    await open('evals')
    serve({ '/api/benchmarks/evals': { models: [] } })
    await act(async () => retry().click())
    await settle()
    expect(text()).not.toContain('Could not load evals')
    expect(text()).toContain('no eval records yet')
  })

  it('runs: a failed request is an error, not "no runs recorded yet"', async () => {
    serve({ '/api/benchmarks/runs': new Error('internal server error') })
    await open('runs')
    expect(text()).toContain('Could not load runs: internal server error')
    expect(text()).not.toContain('no runs recorded yet')
  })

  it('runs: an empty answer still shows the empty state', async () => {
    serve({ '/api/benchmarks/runs': { runs: [] } })
    await open('runs')
    expect(text()).toContain('no runs recorded yet')
    expect(text()).not.toContain('Could not load')
  })

  it('run queue: a failed queue and a failed plan each say so, and the tab does not read 0', async () => {
    serve({
      '/api/benchmarks/queue': new Error('queue unreachable'),
      '/api/benchmarks/plan': new Error('plan failed'),
    })
    await open('queue')
    expect(text()).toContain('Could not load run queue: queue unreachable')
    expect(text()).toContain('Could not load plan: plan failed')
    const tab = Array.from(host.querySelectorAll('[role="tab"]')).find((t) =>
      (t.textContent ?? '').startsWith('Run Queue'),
    )!
    expect(tab.textContent).toContain('!')
    expect(tab.textContent).not.toContain('0')
  })

  it('run queue: a later failed refresh keeps the last good queue and says the refresh failed', async () => {
    serve({ '/api/benchmarks/queue': { items: [{ id: 'a1', label: 'model-x', enqueued: '2026-11-28T10:00:00Z' }], control: { state: 'stopped' } } })
    await open('queue')
    expect(text()).toContain('model-x')
    serve({ '/api/benchmarks/queue': new Error('gone') })
    // The page refreshes the queue on its own every three seconds; wait for one refresh.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 3100))
    })
    await settle()
    expect(text()).toContain('Could not load run queue: gone')
    expect(text()).toContain('model-x')
  }, 10_000)

  it('the server message is text: markup in it is not rendered', async () => {
    serve({ '/api/benchmarks/evals': new Error('<img src=x onerror="window.__pwned=1"><b>bold</b>') })
    await open('evals')
    const row = host.querySelector('[role="alert"]')!
    expect(row.querySelector('img')).toBeNull()
    expect(row.querySelector('b')).toBeNull()
    expect(row.textContent).toContain('<img src=x')
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined()
  })

  it('Retry pressed repeatedly does not stack queue requests', async () => {
    serve({ '/api/benchmarks/queue': new Error('down') })
    await open('queue')
    // The next answer never arrives: every further press must find a request already in flight.
    let pending = 0
    get.mockImplementation((url: string) => {
      if (url.startsWith('/api/benchmarks/queue')) {
        pending += 1
        return new Promise(() => {})
      }
      return Promise.resolve({})
    })
    for (let i = 0; i < 5; i++) await act(async () => retry().click())
    expect(pending).toBe(1)
  })

  it('Retry on a tab cannot be pressed while its request is in flight', async () => {
    serve({ '/api/benchmarks/runs': new Error('down') })
    await open('runs')
    get.mockImplementation(() => new Promise(() => {}))
    await act(async () => retry().click())
    expect(retry()).toBeUndefined()
    expect(text()).toContain('Loading runs')
  })
})
