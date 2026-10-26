// @vitest-environment happy-dom
//
// The GPU segment of the status dock (useGpuStatus + GpuSegment inside Dock). The hooks are mocked
// at the module boundary, the way image-status-line.test.tsx mocks them: what is pinned here is
// what the collapsed row shows, what each button calls and what the segment hides, not the
// polling (imageHooks.test.tsx has that).
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { React: typeof React }).React = React
// primitives.jsx (Banner, Modal) reads `Icons` off the window at render time.
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type Json = Record<string, unknown>

const { box, calls } = vi.hoisted(() => ({
  box: {
    arbiter: undefined as unknown,
    status: undefined as unknown,
    memory: undefined as unknown,
    models: undefined as unknown,
    slots: [] as unknown[],
    urls: undefined as unknown,
    fail: undefined as Error | undefined,
    // The unpin: held until `pinGate` resolves, and/or failing with `pinFail`.
    pinGate: undefined as Promise<void> | undefined,
    pinFail: undefined as Error | undefined,
    // When /status last answered (ms); the line carries the render time on from there.
    statusUpdatedAt: undefined as number | undefined,
  },
  calls: [] as [string, unknown][],
}))

const record = (name: string) => async (body?: unknown) => {
  calls.push([name, body])
  if (box.fail) throw box.fail
  return {}
}

vi.mock('@/api/hooks/useComfyui', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useArbiter: () => ({ data: box.arbiter }),
  useComfyui: () => ({ data: box.status, dataUpdatedAt: box.statusUpdatedAt ?? Date.now() }),
  useComfyuiModels: () => ({ data: box.models }),
  useComfyuiSwitchover: () => ({ mutateAsync: record('switchover') }),
  useComfyuiPin: () => ({
    mutateAsync: async (body?: unknown) => {
      calls.push(['pin', body])
      await box.pinGate
      if (box.pinFail) throw box.pinFail
      if (box.fail) throw box.fail
      return {}
    },
  }),
  useComfyuiRenderCancel: () => ({ mutateAsync: record('render-cancel') }),
  useComfyuiRestart: () => ({ mutateAsync: record('restart') }),
}))
vi.mock('@/api/hooks/useStatsHardware', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useMemoryBudget: () => ({ data: box.memory }),
}))
vi.mock('@/api/hooks/useConfigUrls', () => ({ useConfigUrls: () => ({ data: box.urls }) }))
// The slot card test renders SlotCards, which wants the full slots module.
vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: box.slots, isLoading: false }),
  useSlotLoad: () => ({ mutate: () => {} }),
  useSlotUnload: () => ({ mutate: () => {} }),
  useSlotSwap: () => ({ mutate: () => {} }),
  useSlotRestart: () => ({ mutate: () => {} }),
  useSlotEdit: () => ({ isPending: false, mutate: () => {}, mutateAsync: async () => {} }),
}))
vi.mock('@/api/hooks/useModelsFeasibility', () => ({
  useModelsFeasibility: () => ({ data: undefined, mutate: () => {} }),
}))
// What the Dock itself reads besides the GPU state: quiet defaults.
vi.mock('@/api/hooks/useRuntime', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useRuntimeRollup: () => ({ status: 'up', ready: 2, total: 2 }),
  useHealthSystem: () => ({ data: { status: 'ok', checks: {} }, isError: false }),
}))
vi.mock('@/api/hooks/useServicesHealth', () => ({
  useServicesHealth: () => ({ services: [], pending: false, error: false }),
}))
vi.mock('@/api/hooks/useLogs', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useLogsStream: () => ({ ring: [], disconnected: false }),
}))
vi.mock('@/api/hooks/useModels', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  usePullsList: () => ({ jobs: [], hasActive: false }),
  useClearPullJob: () => ({ mutate: () => {} }),
}))
vi.mock('@/api/hooks/useUpdates', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useUpdateState: () => ({ data: undefined }),
}))
vi.mock('@/api/hooks/useProfiles', () => ({ useProfiles: () => ({ data: [] }) }))
vi.mock('@/api/hooks/useRuntimes', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useSystemInfo: () => ({ data: { backends: {} } }),
}))

const { Dock } = await import('../dock/Dock.jsx')
const { clearDismissedRefusal } = await import('../image/useStatusLineActions')
const { useImageRunStore } = await import('@/stores/useImageRunStore')

// ── fixtures ─────────────────────────────────────────────────────────────────

const arbiterBlock = (over: Json = {}) => ({
  mode: 'llm',
  pinned: false,
  saved_llm_slots: [],
  switching: false,
  image_jobs: 0,
  phase: null,
  restore_at: null,
  handoff_until: null,
  drain_in_flight: 0,
  unload_set: ['brain', 'coder'],
  llm_waiters: { count: 0, oldest_s: null },
  ...over,
})

const envelope = (over: Json = {}, refusal: Json | null = null) => ({
  mode: null,
  arbiter: arbiterBlock(over),
  switchover: { active: false, target: null, error: null },
  gate_refusal: refusal,
})

const statusBody = (over: Json = {}) => ({
  queue: { running: 0, pending: 0 },
  progress: null,
  gate: { state: 'current', version_seen: '1' },
  gate_refusal: null,
  ...over,
})

const memoryBody = (gpu: Json | null = {}) => ({
  gpu:
    gpu === null
      ? null
      : {
          kind: 'shared',
          total_gb: 96,
          used_gb: 40,
          this_box: [
            { slot: 'brain', gb: 20.2, state: 'ready' },
            { slot: 'coder', gb: 9.1, state: 'ready' },
          ],
          image_engine_gb: 0,
          image_engine_slot: 'img',
          image_engine_estimated: false,
          other_guests_gb: 0,
          attribution_excess_gb: 0,
          free_gb: 56,
          ...gpu,
        },
  ram: { limit_gb: 64, used_gb: 20, free_gb: 44, source: 'container' },
  render_free_gb: 50,
})

// ── harness ──────────────────────────────────────────────────────────────────

const roots: { root: Root; host: HTMLElement }[] = []
const renderers = new Map<HTMLElement, (el: React.ReactElement) => void>()

function mount(el: React.ReactElement) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const render = (next: React.ReactElement) =>
    act(() => {
      root.render(<QueryClientProvider client={client}>{next}</QueryClientProvider>)
    })
  render(el)
  roots.push({ root, host })
  renderers.set(host, render)
  return host
}

// Render again into a mounted host with whatever the mocks hold now (a poll landing).
const rerender = (host: HTMLElement, el: React.ReactElement) => renderers.get(host)!(el)

const byId = (testid: string) => document.querySelector(`[data-testid="${testid}"]`) as HTMLElement | null
const button = (label: string, within: ParentNode = document) =>
  Array.from(within.querySelectorAll('button')).find((b) => b.textContent?.trim() === label) as
    | HTMLButtonElement
    | undefined

// The confirm dialog: its buttons live in the foot, the line's own buttons are outside it.
const foot = (label: string) => {
  const modalFoot = document.querySelector('.modal-foot')
  return modalFoot ? button(label, modalFoot) : undefined
}
const dialogTitle = () => document.querySelector('.modal-shell h2')?.textContent
const dialogBody = () => document.querySelector('.modal-body')?.textContent

// A pin refuses GPU chat until someone unpins, so Hold and Keep ask first (fixed copy).
const PIN_BODY =
  'GPU chat requests (Open WebUI, agents, the API) are refused until you unpin. The paused GPU slots stay paused.'

async function click(el: Element | null | undefined) {
  expect(el, 'element to click').toBeTruthy()
  await act(async () => {
    el!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}

const DISMISS_KEY = 'hal0.imageGen.gateRefusalDismissedAt'

// Storage that reads from the real one and fails where the test says. `vi.spyOn` on
// Storage.prototype did not stay put across tests here (a throwing getItem outlived its test),
// so the whole object is stubbed, and `vi.unstubAllGlobals()` puts the real one back.
function stubStorage(over: Partial<Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>>) {
  const real = window.localStorage
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => real.getItem(k),
    setItem: (k: string, v: string) => real.setItem(k, v),
    removeItem: (k: string) => real.removeItem(k),
    ...over,
  })
}
const blocked = () => {
  throw new Error('storage blocked')
}

beforeEach(() => {
  box.arbiter = envelope()
  box.status = statusBody()
  box.memory = memoryBody()
  box.models = { models: [], engine: { available: true, reason: null }, store: { free_gb: 100, path: '/m' } }
  box.slots = []
  box.urls = { comfyui: 'http://box:8188' }
  box.fail = undefined
  box.pinGate = undefined
  box.pinFail = undefined
  box.statusUpdatedAt = undefined
  calls.length = 0
  useImageRunStore.setState({ run: null })
  clearDismissedRefusal()
  try {
    window.localStorage.clear()
  } catch {
    /* no storage */
  }
})

afterEach(() => {
  for (const { root, host } of roots.splice(0)) {
    act(() => root.unmount())
    host.remove()
  }
  document.body.innerHTML = ''
  vi.useRealTimers()
  vi.unstubAllGlobals()
})


const dock = () => <Dock route="slots" param="image" expanded={false} onToggle={() => {}} />
const seg = () => byId('dock-gpu-segment')!

describe('the dock GPU segment', () => {
  it('segment renders headline, detail and the first two actions from statusLine', () => {
    box.arbiter = envelope({ mode: 'img', pinned: true })
    mount(dock())
    expect(seg().dataset.testid).toBe('dock-gpu-segment')
    expect(seg().getAttribute('role')).toBe('group')
    expect(seg().getAttribute('aria-label')).toBe('GPU status')
    expect(seg().classList.contains('gpu-seg-pin')).toBe(true)
    expect(byId('gpu-status-head')!.textContent).toBe('Image mode pinned')
    expect(byId('gpu-status-detail')!.textContent).toBeTruthy()
    const acts = Array.from(seg().querySelectorAll('.acts button'))
    expect(acts.map((b) => b.textContent)).toEqual(['Unpin', 'Stop image mode'])
    expect(acts.map((b) => b.className)).toEqual(['btn sm', 'btn sm ghost'])
    expect(acts.map((b) => b.getAttribute('data-testid'))).toEqual([
      'gpu-status-action-unpin',
      'gpu-status-action-stop',
    ])
  })

  it('announces the kind in the one live region', () => {
    box.arbiter = envelope({ mode: 'img', pinned: true })
    mount(dock())
    const live = byId('gpu-status-live')!
    expect(Array.from(byId('dock')!.querySelectorAll('[aria-live]'))).toEqual([live])
    expect(live.getAttribute('aria-live')).toBe('polite')
    expect(live.classList.contains('sr-only')).toBe(true)
    expect(live.textContent).toBe('Image mode pinned')
    // A button's children are presentational: the live region must not sit inside one.
    expect(live.closest('button')).toBeNull()
    expect(byId('gpu-status-head')!.getAttribute('aria-live')).toBeNull()
    expect(seg().getAttribute('aria-live')).toBeNull()
  })

  it('segment shows at most two actions after the pane filter', () => {
    // ComfyUI listens on the box's loopback: off the box there is no editor link to open.
    const happyDOM = (window as unknown as { happyDOM: { setURL: (u: string) => void } }).happyDOM
    happyDOM.setURL('http://box:9120/')
    try {
      box.urls = { comfyui: 'http://box:8188', comfyui_public: false }
      box.status = statusBody({ gate_refusal: { at: 1_700_000_000, reason: 'busy' } })
      mount(dock())
      const acts = Array.from(seg().querySelectorAll('.acts button')).map((b) => b.textContent)
      expect(acts).toEqual(['Dismiss'])
    } finally {
      happyDOM.setURL('about:blank')
    }
  })

  it('renders no segment when the engine is unavailable', () => {
    box.models = { models: [], engine: { available: false, reason: 'No ROCm lane' }, store: { free_gb: 1, path: '/m' } }
    mount(dock())
    expect(document.querySelectorAll('[data-testid="dock-gpu-segment"]')).toHaveLength(0)
    const row = document.querySelector('.foot-chips')!
    expect(row.classList.contains('dock-row')).toBe(true)
    expect((row.firstElementChild as HTMLElement).dataset.testid).toBe('foot-health-runtimes')
  })

  it('puts the segment first in the row, ahead of the slots group', () => {
    mount(dock())
    const row = document.querySelector('.foot-chips')!
    expect((row.firstElementChild as HTMLElement).dataset.testid).toBe('dock-gpu-segment')
    expect((row.children[1] as HTMLElement).dataset.testid).toBe('foot-health-runtimes')
  })

  it('draws the render progress bar only while rendering', () => {
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.status = statusBody({
      queue: { running: 1, pending: 0 },
      progress: { model: 'flux-dev', step: 5, total: 20, node: '9', elapsed_s: 72, prompt_id: 'p' },
    })
    mount(dock())
    expect(seg().querySelector('.mini')).not.toBeNull()
  })

  it('hold asks first, and a confirmed hold calls switchover with pin', async () => {
    mount(dock())
    await click(button('Hold image mode', seg()))
    // Nothing is sent before the answer.
    expect(calls).toEqual([])
    expect(dialogTitle()).toBe('Hold image mode?')
    expect(dialogBody()).toBe(PIN_BODY)
    expect(foot('Cancel')).toBeTruthy()
    await click(foot('Hold image mode'))
    expect(calls).toEqual([['switchover', { mode: 'generation', pin: true }]])
    expect(document.querySelector('.modal-shell')).toBeNull()
  })

  it('stop unpins a pinned box first, then switches back without force', async () => {
    box.arbiter = envelope({ mode: 'img', pinned: true })
    mount(dock())
    await click(byId('gpu-status-action-stop'))
    expect(calls).toEqual([
      ['pin', { pinned: false }],
      ['switchover', { mode: 'inference' }],
    ])
    expect(document.querySelector('.modal-shell')).toBeNull()
  })

  it("shows a failed action's message in the segment detail while collapsed", async () => {
    box.fail = new Error('GPU is in image mode already')
    mount(dock())
    await click(button('Hold image mode', seg()))
    await click(foot('Hold image mode'))
    const detail = byId('gpu-status-detail')!
    expect(detail.getAttribute('role')).toBe('alert')
    expect(detail.closest('button')).toBeNull()
    expect(detail.classList.contains('err')).toBe(true)
    expect(detail.textContent).toBe('GPU is in image mode already')
    // The buttons come back so it can be tried again.
    expect(button('Hold image mode', seg())!.disabled).toBe(false)
  })

  it('offers Cancel on a switch only to the run that started it', async () => {
    box.arbiter = envelope({ mode: 'img', phase: 'draining' })
    mount(dock())
    expect(button('Cancel', seg())).toBeUndefined()
    for (const { root, host } of roots.splice(0)) {
      act(() => root.unmount())
      host.remove()
    }
    const cancel = vi.fn()
    useImageRunStore.setState({ run: { running: true, cancel } })
    mount(dock())
    await click(button('Cancel', seg()))
    expect(cancel).toHaveBeenCalledTimes(1)
    expect(calls).toEqual([])
  })
})
