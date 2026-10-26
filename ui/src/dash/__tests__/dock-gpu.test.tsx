// @vitest-environment happy-dom
//
// The GPU segment of the status dock (useGpuStatus + GpuSegment inside Dock). The hooks are mocked
// at the module boundary, the way image-files-tab.test.tsx mocks its own: what is pinned here is
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
const { GpuGateBlockedBanner } = await import('../dock/GpuGateBlockedBanner.jsx')
const { clearDismissedRefusal, useStatusLineActions } = await import('../image/useStatusLineActions')
const { ForceStopConfirm } = await import('../dock/gpu-parts.jsx')
const { useImageRunStore } = await import('@/stores/useImageRunStore')
const { resetGenerateStore } = await import('../image/generate-store')

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
  resetGenerateStore()
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
    // One alert in the dock, and it is the segment's detail.
    const alerts = document.querySelectorAll('[data-testid="dock"] [role="alert"]')
    expect(alerts).toHaveLength(1)
    expect(alerts[0]).toBe(detail)
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

// ── the GPU tab ──────────────────────────────────────────────────────────────

const open = () => <Dock route="slots" param="image" expanded={true} onToggle={() => {}} />
const facts = () => byId('gpu-facts')!
const factRow = (label: string) => {
  const dt = Array.from(facts().querySelectorAll('dt')).find((el) => el.textContent === label)
  expect(dt, `fact row ${label}`).toBeTruthy()
  return dt!.nextElementSibling as HTMLElement
}

describe('the dock GPU tab', () => {
  it('gpu tab shows all actions plus restart, logs and editor', async () => {
    box.urls = { comfyui: 'https://comfyui.box', comfyui_public: true }
    mount(open())
    // The tab strip: events, gpu (with a tone dot), downloads.
    expect(byId('dock-tab-events')!.textContent).toBe('events')
    expect(byId('dock-tab-downloads')!.textContent).toBe('downloads')
    expect(byId('dock-tab-gpu')!.querySelector('.dotc.tone-ok')).not.toBeNull()
    await click(byId('dock-tab-gpu'))
    {
      const pane = byId('dock-gpu')!
      expect(pane.classList.contains('gp')).toBe(true)
      const ids = Array.from(pane.querySelectorAll('.gp-acts button')).map((b) => b.getAttribute('data-testid'))
      expect(ids).toEqual([
        'gpu-status-action-hold',
        'gpu-status-action-restart_engine',
        'gpu-status-logs',
        'gpu-status-action-open_editor',
      ])
      expect(byId('gpu-status-action-hold')!.className).toBe('btn sm primary')
      expect(pane.querySelector('.gp-sep')).not.toBeNull()
      // The pane adds no live region: the segment's is the only one.
      expect(Array.from(byId('dock')!.querySelectorAll('[aria-live]'))).toHaveLength(1)
    }
  })

  it('gpu facts read mode, phase, gate and waiting chat', async () => {
    box.arbiter = envelope({ mode: 'img', pinned: true, phase: 'rendering', llm_waiters: { count: 2, oldest_s: 4 } })
    box.status = statusBody({ engine: 'running', gate: { state: 'stale', version_seen: '0' } })
    mount(open())
    await click(byId('dock-tab-gpu'))
    const text = facts().textContent!
    expect(text).toContain('image · pinned')
    expect(text).toContain('2 requests')
    expect(factRow('mode').textContent).toBe('image · pinned')
    expect(factRow('phase').textContent).toBe('rendering')
    expect(factRow('engine').textContent).toBe('running · gate 0')
    expect(factRow('engine').classList.contains('warn')).toBe(true)
    expect(factRow('chat waiting').textContent).toBe('2 requests · cap 2:00')
    expect(factRow('chat waiting').classList.contains('warn')).toBe(true)
    expect(factRow('queue').textContent).toBe('0 running · 0 waiting')
    // brain 20.2 + coder 9.1 freed from the 96 GB pool (40 used).
    expect(factRow('gpu pool').textContent).toBe('56 GB free of 96 · 29.3 GB freed')
    expect(byId('dock-gpu')!.querySelector('.gp-cost')!.textContent).toBe(
      'Image mode frees 29.3 GB of the GPU pool. Paused slots reload about 10 s after the last image unless pinned.',
    )
  })

  it('gpu facts list the slots, paused ones hollow while image mode is on', async () => {
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.slots = [
      { name: 'brain', model: 'm1', state: 'ready' },
      { name: 'coder', model: 'm2', state: 'ready' },
      { name: 'spare', model: 'm3', state: 'ready' },
      { name: 'empty' },
    ]
    mount(open())
    await click(byId('dock-tab-gpu'))
    const slots = Array.from(factRow('slots').querySelectorAll('.gp-slot'))
    expect(slots.map((el) => el.textContent)).toEqual(['brain', 'coder', 'spare'])
    expect(slots.map((el) => el.querySelector('.pip')!.className)).toEqual(['pip paused', 'pip paused', 'pip stale'])
  })

  it('hides the gpu tab when there is nothing to show', () => {
    box.memory = memoryBody(null)
    box.models = { models: [], engine: { available: false, reason: 'No ROCm lane' }, store: { free_gb: 1, path: '/m' } }
    mount(open())
    expect(document.querySelectorAll('[data-testid="dock-tab-gpu"]')).toHaveLength(0)
    expect(byId('dock-tab-events')).not.toBeNull()
  })

  it('restart engine calls restart', async () => {
    box.status = statusBody({ gate: { state: 'stale', version_seen: '0' } })
    mount(open())
    await click(byId('dock-tab-gpu'))
    await click(byId('gpu-status-action-restart_engine'))
    expect(calls).toEqual([['restart', undefined]])
    // The stale gate is also the headline's own first action: the extras do not repeat it.
    expect(byId('dock-gpu')!.querySelectorAll('[data-testid="gpu-status-action-restart_engine"]')).toHaveLength(1)
  })

  it('draws the render progress bar and the elapsed time', async () => {
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.status = statusBody({
      queue: { running: 1, pending: 0 },
      progress: { model: 'flux-dev', step: 5, total: 20, node: '9', elapsed_s: 72, prompt_id: 'p' },
    })
    mount(open())
    await click(byId('dock-tab-gpu'))
    const pane = byId('dock-gpu')!
    const bar = pane.querySelector('[role="progressbar"]') as HTMLElement
    expect(bar.getAttribute('aria-valuenow')).toBe('25')
    expect(byId('image-status-elapsed')!.textContent).toMatch(/^1:1[2-4]$/)
  })

  it('shows a failed action in the pane as an alert', async () => {
    box.fail = new Error('Restart refused')
    box.status = statusBody({ gate: { state: 'stale', version_seen: '0' } })
    mount(open())
    await click(byId('dock-tab-gpu'))
    await click(byId('gpu-status-action-restart_engine'))
    const err = byId('dock-gpu')!.querySelector('.gp-error')!
    expect(err.getAttribute('role')).toBe('alert')
    expect(err.textContent).toBe('Restart refused')
    // Expanded, the segment keeps the text but leaves the announcing to the pane.
    const alerts = document.querySelectorAll('[data-testid="dock"] [role="alert"]')
    expect(alerts).toHaveLength(1)
    expect(alerts[0]).toBe(err)
    expect(byId('gpu-status-detail')!.textContent).toBe('Restart refused')
  })

  it('holds the tabs and the active pane in one in-flow container', async () => {
    mount(open())
    const box_ = byId('dock-expanded')!
    expect(box_.classList.contains('dock-x')).toBe(true)
    expect(box_.querySelector('.foot-tabs')).not.toBeNull()
    expect(box_.querySelector('.foot-pane')).not.toBeNull()
    await click(byId('dock-tab-gpu'))
    expect(document.querySelector('[data-testid="dock-expanded"] [data-testid="dock-gpu"]')).not.toBeNull()
    expect(box_.querySelector('.foot-pane')).toBeNull()
    await click(byId('dock-tab-downloads'))
    expect(box_.querySelector('.foot-downloads')).not.toBeNull()
  })

  it('clicking the segment opens the dock on the gpu tab', async () => {
    const onToggle = vi.fn()
    const host = mount(<Dock route="slots" param="image" expanded={false} onToggle={onToggle} />)
    await click(byId('gpu-status-open'))
    expect(onToggle).toHaveBeenCalledTimes(1)
    rerender(host, <Dock route="slots" param="image" expanded={true} onToggle={onToggle} />)
    expect(byId('dock-gpu')).not.toBeNull()
    expect(byId('dock-tab-gpu')!.classList.contains('active')).toBe(true)
  })

  it('falls back to the events tab when the gpu view goes away', async () => {
    const host = mount(open())
    await click(byId('dock-tab-gpu'))
    expect(byId('dock-gpu')).not.toBeNull()
    box.models = { models: [], engine: { available: false, reason: 'gone' }, store: { free_gb: 1, path: '/m' } }
    rerender(host, open())
    expect(byId('dock-gpu')).toBeNull()
    expect(byId('dock-tab-events')!.classList.contains('active')).toBe(true)
    expect(document.querySelector('.foot-pane')).not.toBeNull()
  })

  it('last image row reads — without a render in this browser', async () => {
    mount(open())
    await click(byId('dock-tab-gpu'))
    expect(factRow('last image · this browser').textContent).toBe('—')
  })

  it("last image row reads this browser's newest result", async () => {
    const generate = await import('@/api/hooks/useComfyui')
    vi.spyOn(generate, 'generateImage').mockResolvedValue({
      data: [{ url: '/api/images/cache/a.png' }],
      _hal0: { model: 'flux-dev', meta: { seed: 7, width: 1024, height: 768 } },
    } as never)
    const { startRun } = await import('../image/generate-store')
    await startRun({ prompt: 'a fox', model: 'flux-dev', size: '1024x768' } as never)
    mount(open())
    await click(byId('dock-tab-gpu'))
    expect(factRow('last image · this browser').textContent).toMatch(/^\d\d:\d\d:\d\d · flux-dev · 1024x768$/)
  })
})

// ── the clocks ───────────────────────────────────────────────────────────────

describe('the dock GPU clocks', () => {
  const head = () => byId('gpu-status-head')!.textContent

  it('counts the grace down between polls', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0, 0))
    const t = Date.now() / 1000
    box.arbiter = envelope({ mode: 'img', phase: 'grace', restore_at: t + 8 })
    mount(dock())
    expect(head()).toBe('Image mode ends in 8 s')
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(head()).toBe('Image mode ends in 5 s')
  })

  // `restore_at` is on the server's clock. The hook keeps the browser's skew against it
  // (`skewMs`, from the response's Date header); the countdown is taken on the server's clock.
  it('counts the grace down on the server clock when the browser clock is ahead', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0, 30))
    // The server says 8 s left; the browser is 30 s ahead, so restore_at is 22 s in its past.
    const serverNow = Date.now() / 1000 - 30
    box.arbiter = { ...envelope({ mode: 'img', phase: 'grace', restore_at: serverNow + 8 }), skewMs: 30_000 }
    mount(dock())
    expect(head()).toBe('Image mode ends in 8 s')
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(head()).toBe('Image mode ends in 5 s')
  })

  it('counts the grace down on the server clock when the browser clock is behind', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0, 0))
    const serverNow = Date.now() / 1000 + 45
    box.arbiter = { ...envelope({ mode: 'img', phase: 'grace', restore_at: serverNow + 8 }), skewMs: -45_000 }
    mount(dock())
    expect(head()).toBe('Image mode ends in 8 s')
  })

  it('takes the skew as 0 when the answer carries none', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0, 0))
    box.arbiter = envelope({ mode: 'img', phase: 'grace', restore_at: Date.now() / 1000 + 8 })
    mount(dock())
    expect(head()).toBe('Image mode ends in 8 s')
  })

  it('carries the render time on from the last poll', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0, 0))
    // /status answered now, with the render at 72 s.
    box.statusUpdatedAt = Date.now()
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.status = statusBody({
      queue: { running: 1, pending: 0 },
      progress: { model: 'flux-dev', step: 5, total: 20, node: '9', elapsed_s: 72, prompt_id: 'p' },
    })
    mount(open())
    act(() => {
      byId('dock-tab-gpu')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(byId('image-status-elapsed')!.textContent).toBe('1:12')
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    // No new poll: the figure moved on by the three seconds that passed.
    expect(byId('image-status-elapsed')!.textContent).toBe('1:15')
  })
})

describe('the dock GPU segment, shown and hidden', () => {
  it('is hidden without a gpu', () => {
    box.memory = memoryBody(null)
    mount(dock())
    expect(document.querySelectorAll('[data-testid="dock-gpu-segment"]')).toHaveLength(0)
  })

  it('is shown while the model list has not loaded yet', () => {
    box.models = undefined
    mount(dock())
    expect(byId('dock-gpu-segment')).not.toBeNull()
  })
})

describe('a failed action in the dock', () => {
  it("drops the message once the box is in another state", async () => {
    box.fail = new Error('The GPU is busy')
    const host = mount(dock())
    await click(button('Hold image mode', seg()))
    await click(foot('Hold image mode'))
    expect(document.querySelector('[role="alert"]')!.textContent).toBe('The GPU is busy')

    // The poll lands: a render has started. The old alert is about a state that is gone.
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.status = statusBody({ queue: { running: 1, pending: 0 } })
    rerender(host, dock())
    expect(byId('gpu-status-head')!.textContent).toBe('Rendering · editor prompt · 1 queued')
    expect(document.querySelector('[role="alert"]')).toBeNull()
  })

  it("keeps the message while the same state counts on", async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0, 0))
    box.arbiter = envelope({ mode: 'img', phase: 'grace', restore_at: Date.now() / 1000 + 8 })
    box.fail = new Error('The pin was refused')
    mount(dock())
    await click(byId('gpu-status-action-keep'))
    await click(foot('Keep image mode on'))
    expect(document.querySelector('[role="alert"]')!.textContent).toBe('The pin was refused')
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    // The headline moved (8 s to 5 s); the state did not.
    expect(byId('gpu-status-head')!.textContent).toBe('Image mode ends in 5 s')
    expect(document.querySelector('[role="alert"]')!.textContent).toBe('The pin was refused')
  })
})

describe('stop while a render runs', () => {
  function Stop({ ctx }: { ctx: Json }) {
    const actions = useStatusLineActions()
    return (
      <>
        <button data-testid="stop" onClick={() => void actions.run('stop', ctx)}>
          go
        </button>
        <ForceStopConfirm confirm={actions.confirm} />
      </>
    )
  }

  it('stop asks before forcing while a render runs', async () => {
    mount(<Stop ctx={{ pinned: true, renderRunning: true }} />)
    await click(byId('stop'))
    // Nothing is sent before the answer, not even the unpin.
    expect(calls).toEqual([])
    expect(document.querySelector('.modal-shell')!.textContent).toMatch(/render/i)
    await click(foot('Stop and cancel render'))
    expect(calls).toEqual([
      ['pin', { pinned: false }],
      ['switchover', { mode: 'inference', force: true }],
    ])
    expect(document.querySelector('.modal-shell')).toBeNull()
  })

  it('sends the switchover only after the unpin has resolved', async () => {
    let release!: () => void
    box.pinGate = new Promise<void>((resolve) => {
      release = resolve
    })
    mount(<Stop ctx={{ pinned: true, renderRunning: true }} />)
    await click(byId('stop'))
    await click(foot('Stop and cancel render'))
    expect(calls).toEqual([['pin', { pinned: false }]])
    // Time passes and the unpin is still out: the switchover waits for it.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10))
    })
    expect(calls).toEqual([['pin', { pinned: false }]])
    await act(async () => {
      release()
    })
    expect(calls).toEqual([
      ['pin', { pinned: false }],
      ['switchover', { mode: 'inference', force: true }],
    ])
  })

  it('leaves everything as it was when the confirm is declined', async () => {
    mount(<Stop ctx={{ pinned: true, renderRunning: true }} />)
    await click(byId('stop'))
    await click(foot('Cancel'))
    expect(calls).toEqual([])
    expect(document.querySelector('.modal-shell')).toBeNull()
  })

  it('does not ask, or force, when no render runs', async () => {
    mount(<Stop ctx={{ pinned: false, renderRunning: false }} />)
    await click(byId('stop'))
    expect(document.querySelector('.modal-shell')).toBeNull()
    expect(calls).toEqual([['switchover', { mode: 'inference' }]])
  })
})

describe('gate refusal in the dock', () => {
  const AT = new Date(2026, 9, 25, 14, 5).getTime() / 1000
  const heads = () => Array.from(document.querySelectorAll('[data-testid="gpu-status-head"]')).map((el) => el.textContent)

  it('dismiss hides a gate refusal and remembers it by timestamp', async () => {
    box.status = statusBody({ gate_refusal: { at: AT, reason: 'ComfyUI queue is not hal0 <b>owned</b>' } })
    mount(dock())
    expect(byId('gpu-status-head')!.textContent).toBe('GPU gate refused a prompt · 14:05')
    // The reason is text from another container: it stays text.
    expect(byId('gpu-status-detail')!.textContent).toBe('ComfyUI queue is not hal0 <b>owned</b>')
    expect(byId('gpu-status-detail')!.querySelector('b')).toBeNull()

    await click(byId('gpu-status-action-dismiss'))
    expect(byId('gpu-status-head')!.textContent).toBe('GPU · chat mode')
    expect(window.localStorage.getItem(DISMISS_KEY)).toBe(String(AT))
  })

  it('stays dismissed after a remount, and shows a newer refusal again', async () => {
    window.localStorage.setItem(DISMISS_KEY, String(AT))
    box.status = statusBody({ gate_refusal: { at: AT, reason: 'old' } })
    mount(dock())
    expect(byId('gpu-status-head')!.textContent).toBe('GPU · chat mode')

    box.status = statusBody({ gate_refusal: { at: AT + 60, reason: 'new' } })
    mount(dock())
    expect(heads()).toContain('GPU gate refused a prompt · 14:06')
  })

  it('renders, and dismisses for the session, without usable storage', async () => {
    stubStorage({ getItem: blocked, setItem: blocked, removeItem: blocked })
    box.status = statusBody({ gate_refusal: { at: AT + 600, reason: 'busy' } })
    mount(dock())
    expect(byId('gpu-status-head')!.textContent).toBe('GPU gate refused a prompt · 14:15')
    await click(byId('gpu-status-action-dismiss'))
    expect(byId('gpu-status-head')!.textContent).toBe('GPU · chat mode')
  })

  it('dismisses for the session when storage reads fine but cannot write', async () => {
    stubStorage({ setItem: blocked })
    box.status = statusBody({ gate_refusal: { at: AT + 1800, reason: 'busy' } })
    mount(dock())
    expect(byId('gpu-status-head')!.textContent).toBe('GPU gate refused a prompt · 14:35')
    await click(byId('gpu-status-action-dismiss'))
    // Nothing was stored, and it must not come straight back.
    expect(window.localStorage.getItem(DISMISS_KEY)).toBeNull()
    expect(byId('gpu-status-head')!.textContent).toBe('GPU · chat mode')

    // A newer refusal still shows.
    box.status = statusBody({ gate_refusal: { at: AT + 1860, reason: 'again' } })
    mount(dock())
    expect(heads()).toContain('GPU gate refused a prompt · 14:36')
  })

  it('has no Dismiss for the missing credential', () => {
    box.status = statusBody({ gate: { state: 'no_credential', version_seen: null }, gate_refusal: { at: AT + 1200, reason: 'x' } })
    mount(dock())
    expect(seg().classList.contains('gpu-seg-err')).toBe(true)
    expect(byId('gpu-status-action-dismiss')).toBeNull()
  })
})

// ── the blocked-gate banner ──────────────────────────────────────────────────

describe('the blocked-gate banner', () => {
  it('blocked gate shows the top banner on every page and no dismiss', () => {
    box.status = statusBody({ gate: { state: 'no_credential', version_seen: null } })
    mount(<GpuGateBlockedBanner />)
    const banner = byId('gpu-gate-blocked-banner')!
    expect(banner).not.toBeNull()
    expect(banner.querySelector('[role="alert"]')).not.toBeNull()
    expect(banner.querySelectorAll('button')).toHaveLength(0)
  })

  it('no banner in chat or image mode', () => {
    box.arbiter = envelope({ mode: 'img', pinned: true })
    const host = mount(<GpuGateBlockedBanner />)
    expect(document.querySelectorAll('[data-testid="gpu-gate-blocked-banner"]')).toHaveLength(0)
    expect(host.textContent).toBe('')

    box.arbiter = envelope()
    rerender(host, <GpuGateBlockedBanner />)
    expect(document.querySelectorAll('[data-testid="gpu-gate-blocked-banner"]')).toHaveLength(0)
  })
})
