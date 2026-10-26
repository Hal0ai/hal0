// @vitest-environment happy-dom
//
// The GPU status line (ImageStatusLine), the one-line banner that links to it and the paused
// slot card. The hooks are mocked at the module boundary, the way image-files-tab.test.tsx and
// inference-pane-pickers.test.tsx mock theirs: what is pinned here is what each button calls,
// what the line hides and what the card says, not the polling (imageHooks.test.tsx has that).
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
vi.mock('@/api/hooks/useProfiles', () => ({ useProfiles: () => ({ data: [] }) }))
vi.mock('@/api/hooks/useRuntimes', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useSystemInfo: () => ({ data: { backends: {} } }),
}))

const { ImageStatusLine, ForceStopConfirm } = await import('../image/ImageStatusLine.jsx')
const { useStatusLineActions, clearDismissedRefusal } = await import('../image/useStatusLineActions')
const { GpuImageModeBanner } = await import('../primitives.jsx')
const { SlotCards, MiniCards } = await import('../inference-pane.jsx')

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

// ── the line ─────────────────────────────────────────────────────────────────

describe('ImageStatusLine', () => {
  it('status line renders headline, detail and actions from statusLine', () => {
    mount(<ImageStatusLine />)
    const line = byId('image-status-line')!
    expect(line.dataset.tone).toBe('ok')
    expect(byId('image-status-head')!.textContent).toBe('GPU · chat mode')
    expect(byId('image-status-detail')!.textContent).toBe(
      'Pauses brain and coder (29.3 GB). They reload about 10 s after the last image.',
    )
    expect(Array.from(line.querySelectorAll('button')).map((b) => b.textContent)).toEqual(['Hold image mode'])
  })

  // The headline counts seconds and steps, and a screen reader would read out every change. The
  // one live region is a visually hidden phrase that changes only with the line's kind.
  it('announces the kind of state politely, in the one live region of the line', () => {
    mount(<ImageStatusLine />)
    const line = byId('image-status-line')!
    const live = byId('image-status-live')!
    expect(Array.from(line.querySelectorAll('[aria-live]'))).toEqual([live])
    expect(live.getAttribute('aria-live')).toBe('polite')
    expect(live.classList.contains('sr-only')).toBe(true)
    expect(live.textContent).toBe('Chat mode')
    // Not the visible headline, the detail, or the line itself.
    expect(byId('image-status-head')!.getAttribute('aria-live')).toBeNull()
    expect(byId('image-status-detail')!.getAttribute('aria-live')).toBeNull()
    expect(line.getAttribute('aria-live')).toBeNull()
  })

  it('does not re-announce a countdown tick or a step, only a change of kind', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0, 0))
    box.arbiter = envelope({ mode: 'img', phase: 'grace', restore_at: Date.now() / 1000 + 8 })
    const host = mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('Image mode ends in 8 s')
    expect(byId('image-status-live')!.textContent).toBe('Image mode, grace period')
    const node = byId('image-status-live')!.firstChild
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    // The headline moved; the live region did not (same text node, untouched).
    expect(byId('image-status-head')!.textContent).toBe('Image mode ends in 5 s')
    expect(byId('image-status-live')!.textContent).toBe('Image mode, grace period')
    expect(byId('image-status-live')!.firstChild).toBe(node)

    // A render with steps: the step counter moves, the announcement stays "Rendering".
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.status = statusBody({
      queue: { running: 1, pending: 0 },
      progress: { model: 'flux-dev', step: 1, total: 20, node: '9', elapsed_s: 3, prompt_id: 'p' },
    })
    rerender(host, <ImageStatusLine />)
    expect(byId('image-status-live')!.textContent).toBe('Rendering')
    const rendering = byId('image-status-live')!.firstChild
    box.status = statusBody({
      queue: { running: 1, pending: 0 },
      progress: { model: 'flux-dev', step: 2, total: 20, node: '9', elapsed_s: 4, prompt_id: 'p' },
    })
    rerender(host, <ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('Rendering · flux-dev · step 2/20')
    expect(byId('image-status-live')!.firstChild).toBe(rendering)
  })

  it('hold asks first, and a confirmed hold calls switchover with pin', async () => {
    mount(<ImageStatusLine />)
    await click(button('Hold image mode'))
    // Nothing is sent before the answer.
    expect(calls).toEqual([])
    expect(dialogTitle()).toBe('Hold image mode?')
    expect(dialogBody()).toBe(PIN_BODY)
    expect(foot('Cancel')).toBeTruthy()
    await click(foot('Hold image mode'))
    expect(calls).toEqual([['switchover', { mode: 'generation', pin: true }]])
    expect(document.querySelector('.modal-shell')).toBeNull()
  })

  it('a declined hold sends nothing', async () => {
    mount(<ImageStatusLine />)
    await click(button('Hold image mode'))
    await click(foot('Cancel'))
    expect(calls).toEqual([])
    expect(document.querySelector('.modal-shell')).toBeNull()
    // Nothing is left pending: the button is there to try again.
    expect(button('Hold image mode')!.disabled).toBe(false)
  })

  it('keep asks first, and a confirmed keep pins the image mode that is on', async () => {
    box.arbiter = envelope({ mode: 'img', phase: 'grace', restore_at: Date.now() / 1000 + 8 })
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toMatch(/^Image mode ends in \d+ s$/)
    await click(button('Keep image mode'))
    expect(calls).toEqual([])
    expect(dialogTitle()).toBe('Keep image mode on?')
    expect(dialogBody()).toBe(PIN_BODY)
    expect(foot('Cancel')).toBeTruthy()
    await click(foot('Keep image mode on'))
    expect(calls).toEqual([['pin', { pinned: true }]])
    expect(document.querySelector('.modal-shell')).toBeNull()
  })

  it('a declined keep sends nothing', async () => {
    box.arbiter = envelope({ mode: 'img', phase: 'grace', restore_at: Date.now() / 1000 + 8 })
    mount(<ImageStatusLine />)
    await click(button('Keep image mode'))
    await click(foot('Cancel'))
    expect(calls).toEqual([])
    expect(document.querySelector('.modal-shell')).toBeNull()
  })

  it('counts the grace down between polls', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0, 0))
    const t = Date.now() / 1000
    box.arbiter = envelope({ mode: 'img', phase: 'grace', restore_at: t + 8 })
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('Image mode ends in 8 s')
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(byId('image-status-head')!.textContent).toBe('Image mode ends in 5 s')
  })

  // `restore_at` is on the server's clock. The hook keeps the browser's skew against it
  // (`skewMs`, from the response's Date header); the countdown is taken on the server's clock.
  it('counts the grace down on the server clock when the browser clock is ahead', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0, 30))
    // The server says 8 s left; the browser is 30 s ahead, so restore_at is 22 s in its past.
    const serverNow = Date.now() / 1000 - 30
    box.arbiter = { ...envelope({ mode: 'img', phase: 'grace', restore_at: serverNow + 8 }), skewMs: 30_000 }
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('Image mode ends in 8 s')
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(byId('image-status-head')!.textContent).toBe('Image mode ends in 5 s')
  })

  it('counts the grace down on the server clock when the browser clock is behind', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0, 0))
    const serverNow = Date.now() / 1000 + 45
    box.arbiter = { ...envelope({ mode: 'img', phase: 'grace', restore_at: serverNow + 8 }), skewMs: -45_000 }
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('Image mode ends in 8 s')
  })

  it('takes the skew as 0 when the answer carries none', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0, 0))
    box.arbiter = envelope({ mode: 'img', phase: 'grace', restore_at: Date.now() / 1000 + 8 })
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('Image mode ends in 8 s')
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
    mount(<ImageStatusLine />)
    expect(byId('image-status-elapsed')!.textContent).toBe('1:12')
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    // No new poll: the figure moved on by the three seconds that passed.
    expect(byId('image-status-elapsed')!.textContent).toBe('1:15')
  })

  it('unpin calls pin false', async () => {
    box.arbiter = envelope({ mode: 'img', pinned: true })
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('Image mode pinned')
    await click(button('Unpin'))
    expect(calls).toEqual([['pin', { pinned: false }]])
  })

  it('stop unpins a pinned box first, then switches back without force', async () => {
    box.arbiter = envelope({ mode: 'img', pinned: true })
    mount(<ImageStatusLine />)
    await click(button('Stop image mode'))
    expect(calls).toEqual([
      ['pin', { pinned: false }],
      ['switchover', { mode: 'inference' }],
    ])
    expect(document.querySelector('.modal-shell')).toBeNull()
  })

  it('does not switch back when the unpin fails, and shows the server message', async () => {
    box.arbiter = envelope({ mode: 'img', pinned: true })
    box.pinFail = new Error('The pin could not be removed')
    mount(<ImageStatusLine />)
    await click(button('Stop image mode'))
    expect(calls).toEqual([['pin', { pinned: false }]])
    expect(document.querySelector('[role="alert"]')!.textContent).toBe('The pin could not be removed')
    // Nothing is left pending: the buttons are there to try again.
    expect(button('Stop image mode')!.disabled).toBe(false)
  })

  it('cancel render calls render/cancel', async () => {
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.status = statusBody({
      queue: { running: 1, pending: 0 },
      progress: { model: 'flux-dev', step: 5, total: 20, node: '9', elapsed_s: 12.4, prompt_id: 'p' },
    })
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('Rendering · flux-dev · step 5/20')
    await click(button('Cancel render'))
    expect(calls).toEqual([['render-cancel', undefined]])
  })

  it('draws the render progress bar and the elapsed time', () => {
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.status = statusBody({
      queue: { running: 1, pending: 0 },
      progress: { model: 'flux-dev', step: 5, total: 20, node: '9', elapsed_s: 72, prompt_id: 'p' },
    })
    mount(<ImageStatusLine />)
    const bar = document.querySelector('[role="progressbar"]') as HTMLElement
    expect(bar.getAttribute('aria-valuenow')).toBe('25')
    expect(byId('image-status-elapsed')!.textContent).toMatch(/^1:1[2-4]$/)
  })

  it('draws no progress bar for an editor render', () => {
    box.arbiter = envelope({ mode: 'img', phase: 'grace', restore_at: Date.now() / 1000 + 5 })
    box.status = statusBody({ queue: { running: 1, pending: 1 } })
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('Rendering · editor prompt · 2 queued')
    expect(document.querySelector('[role="progressbar"]')).toBeNull()
  })

  it('shows the switch steps while the GPU is being handed over', () => {
    box.arbiter = envelope({ mode: 'img', phase: 'unloading' })
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('Switching to image mode')
    const steps = Array.from(document.querySelectorAll('[data-testid="image-status-step"]')).map(
      (el) => `${el.textContent}:${(el as HTMLElement).dataset.state}`,
    )
    expect(steps).toEqual(['Chat replies:done', 'Unload slots:active', 'Start engine:todo'])
  })

  it('offers Cancel on a switch only to the run that started it', async () => {
    box.arbiter = envelope({ mode: 'img', phase: 'draining' })
    const onCancelOwnRun = vi.fn()
    mount(<ImageStatusLine />)
    expect(button('Cancel')).toBeUndefined()
    for (const { root, host } of roots.splice(0)) {
      act(() => root.unmount())
      host.remove()
    }
    mount(<ImageStatusLine ownRun onCancelOwnRun={onCancelOwnRun} />)
    await click(button('Cancel'))
    expect(onCancelOwnRun).toHaveBeenCalledTimes(1)
    expect(calls).toEqual([])
  })

  it('shows how many of the paused slots are back while they reload', () => {
    box.arbiter = envelope({ mode: 'img', phase: 'reloading', unload_set: ['brain', 'coder'] })
    box.slots = [
      { name: 'brain', state: 'ready' },
      { name: 'coder', state: 'warming' },
    ]
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('Reloading GPU slots · 1 of 2 ready')
    const steps = Array.from(document.querySelectorAll('[data-testid="image-status-step"]')).map(
      (el) => `${el.textContent}:${(el as HTMLElement).dataset.state}`,
    )
    expect(steps).toEqual(['brain:done', 'coder:active'])
  })

  it('restart engine calls restart', async () => {
    box.status = statusBody({ gate: { state: 'stale', version_seen: '0' } })
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('The image engine runs an older GPU gate')
    await click(button('Restart engine'))
    expect(calls).toEqual([['restart', undefined]])
  })

  it('open editor opens the declared editor URL from the config urls in a new tab', async () => {
    box.urls = { comfyui: 'https://comfyui.box', comfyui_public: true }
    box.status = statusBody({ gate_refusal: { at: 1_700_000_000, reason: 'busy' } })
    const open = vi.fn(() => null)
    vi.stubGlobal('open', open)
    mount(<ImageStatusLine />)
    await click(button('Open editor'))
    expect(open).toHaveBeenCalledWith('https://comfyui.box', '_blank', 'noopener,noreferrer')
  })

  it('open editor opens the editor on a page that is on the box', async () => {
    const happyDOM = (window as unknown as { happyDOM: { setURL: (u: string) => void } }).happyDOM
    happyDOM.setURL('http://localhost:9120/')
    try {
      box.urls = { comfyui: 'http://localhost:8188', comfyui_public: false }
      box.status = statusBody({ gate_refusal: { at: 1_700_000_000, reason: 'busy' } })
      const open = vi.fn(() => null)
      vi.stubGlobal('open', open)
      mount(<ImageStatusLine />)
      await click(button('Open editor'))
      expect(open).toHaveBeenCalledWith('http://localhost:8188', '_blank', 'noopener,noreferrer')
    } finally {
      happyDOM.setURL('about:blank')
    }
  })

  it('shows no Open editor button on a page that is not on the box', () => {
    // ComfyUI listens on the box's loopback: the link hal0 derives from a LAN host does not answer,
    // and the server says it is not a declared URL (`comfyui_public` false).
    const happyDOM = (window as unknown as { happyDOM: { setURL: (u: string) => void } }).happyDOM
    happyDOM.setURL('http://box:9120/')
    try {
      box.urls = { comfyui: 'http://box:8188', comfyui_public: false }
      box.status = statusBody({ gate_refusal: { at: 1_700_000_000, reason: 'busy' } })
      mount(<ImageStatusLine />)
      expect(button('Open editor')).toBeUndefined()
    } finally {
      happyDOM.setURL('about:blank')
    }
  })

  it('shows the server message under the line when an action fails', async () => {
    box.fail = new Error('GPU is in image mode already')
    mount(<ImageStatusLine />)
    await click(button('Hold image mode'))
    await click(foot('Hold image mode'))
    expect(document.querySelector('[role="alert"]')!.textContent).toBe('GPU is in image mode already')
    // The buttons come back so it can be tried again.
    expect(button('Hold image mode')!.disabled).toBe(false)
  })

  it('drops a failed action\'s message once the box is in another state', async () => {
    box.fail = new Error('The GPU is busy')
    const host = mount(<ImageStatusLine />)
    await click(button('Hold image mode'))
    await click(foot('Hold image mode'))
    expect(document.querySelector('[role="alert"]')!.textContent).toBe('The GPU is busy')

    // The poll lands: a render has started. The old alert is about a state that is gone.
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.status = statusBody({ queue: { running: 1, pending: 0 } })
    rerender(host, <ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('Rendering · editor prompt · 1 queued')
    expect(document.querySelector('[role="alert"]')).toBeNull()
  })

  it('keeps a failed action\'s message while the same state counts on', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0, 0))
    box.arbiter = envelope({ mode: 'img', phase: 'grace', restore_at: Date.now() / 1000 + 8 })
    box.fail = new Error('The pin was refused')
    mount(<ImageStatusLine />)
    await click(button('Keep image mode'))
    await click(foot('Keep image mode on'))
    expect(document.querySelector('[role="alert"]')!.textContent).toBe('The pin was refused')
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    // The headline moved (8 s to 5 s); the state did not.
    expect(byId('image-status-head')!.textContent).toBe('Image mode ends in 5 s')
    expect(document.querySelector('[role="alert"]')!.textContent).toBe('The pin was refused')
  })

  it('status line hidden without gpu', () => {
    box.memory = memoryBody(null)
    const host = mount(<ImageStatusLine />)
    expect(byId('image-status-line')).toBeNull()
    expect(host.textContent).toBe('')
  })

  it('status line hidden when the image engine is unavailable on this host', () => {
    box.models = { models: [], engine: { available: false, reason: 'No ROCm lane' }, store: { free_gb: 1, path: '/m' } }
    mount(<ImageStatusLine />)
    expect(byId('image-status-line')).toBeNull()
  })

  it('is shown while the model list has not loaded yet', () => {
    box.models = undefined
    mount(<ImageStatusLine />)
    expect(byId('image-status-line')).not.toBeNull()
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

describe('gate refusal', () => {
  const AT = new Date(2026, 9, 25, 14, 5).getTime() / 1000

  it('dismiss hides a gate refusal and remembers it by timestamp', async () => {
    box.status = statusBody({ gate_refusal: { at: AT, reason: 'ComfyUI queue is not hal0 <b>owned</b>' } })
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('GPU gate refused a prompt · 14:05')
    // The reason is text from another container: it stays text.
    expect(byId('image-status-detail')!.textContent).toBe('ComfyUI queue is not hal0 <b>owned</b>')
    expect(byId('image-status-detail')!.querySelector('b')).toBeNull()

    await click(button('Dismiss'))
    expect(byId('image-status-head')!.textContent).toBe('GPU · chat mode')
    expect(window.localStorage.getItem(DISMISS_KEY)).toBe(String(AT))
  })

  it('stays dismissed after a remount, and shows a newer refusal again', async () => {
    window.localStorage.setItem(DISMISS_KEY, String(AT))
    box.status = statusBody({ gate_refusal: { at: AT, reason: 'old' } })
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('GPU · chat mode')

    box.status = statusBody({ gate_refusal: { at: AT + 60, reason: 'new' } })
    mount(<ImageStatusLine />)
    const heads = Array.from(document.querySelectorAll('[data-testid="image-status-head"]')).map((el) => el.textContent)
    expect(heads).toContain('GPU gate refused a prompt · 14:06')
  })

  it('renders, and dismisses for the session, without usable storage', async () => {
    stubStorage({ getItem: blocked, setItem: blocked, removeItem: blocked })
    box.status = statusBody({ gate_refusal: { at: AT + 600, reason: 'busy' } })
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('GPU gate refused a prompt · 14:15')
    await click(button('Dismiss'))
    expect(byId('image-status-head')!.textContent).toBe('GPU · chat mode')
  })

  it('dismisses for the session when storage reads fine but cannot write', async () => {
    stubStorage({ setItem: blocked })
    box.status = statusBody({ gate_refusal: { at: AT + 1800, reason: 'busy' } })
    mount(<ImageStatusLine />)
    expect(byId('image-status-head')!.textContent).toBe('GPU gate refused a prompt · 14:35')
    await click(button('Dismiss'))
    // Nothing was stored, and it must not come straight back.
    expect(window.localStorage.getItem(DISMISS_KEY)).toBeNull()
    expect(byId('image-status-head')!.textContent).toBe('GPU · chat mode')

    // A newer refusal still shows.
    box.status = statusBody({ gate_refusal: { at: AT + 1860, reason: 'again' } })
    mount(<ImageStatusLine />)
    const heads = Array.from(document.querySelectorAll('[data-testid="image-status-head"]')).map((el) => el.textContent)
    expect(heads).toContain('GPU gate refused a prompt · 14:36')
  })

  it('has no Dismiss for the missing credential', () => {
    box.status = statusBody({ gate: { state: 'no_credential', version_seen: null }, gate_refusal: { at: AT + 1200, reason: 'x' } })
    mount(<ImageStatusLine />)
    expect(byId('image-status-line')!.dataset.tone).toBe('err')
    expect(button('Dismiss')).toBeUndefined()
  })
})

// ── the banner ───────────────────────────────────────────────────────────────

describe('GpuImageModeBanner', () => {
  it('shows nothing in chat mode', () => {
    const host = mount(<GpuImageModeBanner />)
    expect(host.textContent).toBe('')
  })

  it('reads the arbiter envelope and links to the Engine tab', () => {
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    const host = mount(<GpuImageModeBanner />)
    expect(host.querySelector('.banner-heading')!.textContent).toBe('Rendering')
    const link = host.querySelector('a[href="#slots/image/engine"]')
    expect(link).not.toBeNull()
  })

  it('dismisses for the episode', async () => {
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    const host = mount(<GpuImageModeBanner />)
    await click(host.querySelector('.banner-dismiss'))
    expect(host.querySelector('.banner')).toBeNull()
  })
})

// ── the slot card ────────────────────────────────────────────────────────────

describe('paused slot cards', () => {
  const handlers = {
    onSwap: () => {},
    onStart: () => {},
    onStop: () => {},
    onRestart: () => {},
    onLogs: () => {},
    onEdit: () => {},
    onSetDefault: () => {},
  }
  const stopped = (name: string) => ({
    name,
    type: 'llm',
    device: 'gpu-vulkan',
    profile: 'rocm',
    binary: '',
    container_status: 'exited',
    container_health: false,
  })
  const running = (name: string) => ({ ...stopped(name), container_status: 'running', container_health: true })

  const cards = (slots: Json[], arbiter: unknown) =>
    mount(
      <SlotCards
        rows={slots.map((s) => ({ s, ind: { cls: 'offline', tooltip: '' } }))}
        full={false}
        models={[]}
        allSlots={slots}
        busyName={null}
        handlers={handlers}
        loading={false}
        modelRows={() => null}
        arbiter={arbiter}
      />,
    )

  it('slot card shows paused chip and wait line in image mode', () => {
    const host = cards(
      [stopped('brain'), stopped('other')],
      arbiterBlock({ mode: 'img', phase: 'grace', unload_set: ['brain'], llm_waiters: { count: 3, oldest_s: 65 } }),
    )
    const chip = host.querySelector('[data-testid="slot-paused-brain"]')!
    expect(chip.textContent).toBe('paused · image mode')
    const card = host.querySelector('[data-testid="infer-slot-brain"]')!
    expect(card.textContent).toContain('Reloads about 10 s after the last image.')
    expect(card.textContent).toContain('3 chat requests waiting, longest 1:05 of 2:00.')
    // A slot outside the set is not paused.
    expect(host.querySelector('[data-testid="slot-paused-other"]')).toBeNull()
    expect(host.querySelector('[data-testid="infer-slot-other"]')!.textContent).not.toContain('Reloads about')
  })

  it('shows no chip in chat mode, or on a slot that is still running', () => {
    const chat = cards([stopped('brain')], arbiterBlock({ mode: 'llm', unload_set: ['brain'] }))
    expect(chat.querySelector('[data-testid="slot-paused-brain"]')).toBeNull()

    const still = cards([running('brain')], arbiterBlock({ mode: 'img', unload_set: ['brain'] }))
    expect(still.querySelector('[data-testid="slot-paused-brain"]')).toBeNull()
  })

  it('puts the chip, without the note, on a paused utility slot', () => {
    const slots = [stopped('embed'), stopped('rerank')]
    const host = mount(
      <MiniCards
        rows={slots.map((s) => ({ s, ind: { cls: 'offline', tooltip: '' } }))}
        busyName={null}
        handlers={handlers}
        arbiter={arbiterBlock({ mode: 'img', unload_set: ['embed'], llm_waiters: { count: 1, oldest_s: 3 } })}
      />,
    )
    expect(host.querySelector('[data-testid="slot-paused-embed"]')!.textContent).toBe('paused · image mode')
    expect(host.querySelector('[data-testid="slot-paused-rerank"]')).toBeNull()
    expect(host.querySelector('[data-testid="slot-paused-note-embed"]')).toBeNull()
  })

  it('shows no chip without an arbiter answer', () => {
    const host = cards([stopped('brain')], undefined)
    expect(host.querySelector('[data-testid="slot-paused-brain"]')).toBeNull()
  })
})
