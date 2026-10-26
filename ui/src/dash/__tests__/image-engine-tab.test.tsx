// @vitest-environment happy-dom
//
// The Image Gen pane's Engine tab: the engine card (state, container, gate, restart, logs, stop),
// the editor card (workflow chips, the editor link and its off-box state), the queue card and
// the GPU device card. The hooks are mocked at the module boundary, as image-status-line.test.tsx
// does; the page's host comes from the real location, so the on-box rule runs for real.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { React: typeof React }).React = React
// primitives.jsx (Modal, ConfirmDialog) reads `Icons` off the window at render time.
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type Json = Record<string, unknown>

const { box, calls } = vi.hoisted(() => ({
  box: {
    status: undefined as Json | undefined,
    arbiter: undefined as Json | undefined,
    models: undefined as Json | undefined,
    workflows: [] as Json[],
    urls: undefined as Json | undefined,
    // The 60-line log (the modal) and the 3-line one (the unreachable notice).
    logs: undefined as { lines: string[] } | undefined,
    logsTail: undefined as { lines: string[] } | undefined,
    logsError: undefined as Error | undefined,
    // The 3-line read failing.
    logsTailError: undefined as Error | undefined,
    fail: undefined as Error | undefined,
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
  useComfyui: (opts?: unknown) => {
    calls.push(['status-hook', opts])
    return { data: box.status }
  },
  useArbiter: () => ({ data: box.arbiter }),
  useComfyuiModels: () => ({ data: box.models }),
  useComfyuiWorkflows: () => ({ data: box.workflows }),
  useComfyuiLogs: (tail?: number) => {
    const short = tail === 3
    return {
      data: short ? box.logsTail : box.logs,
      isFetching: false,
      isError: short ? !!box.logsTailError : !!box.logsError,
      error: (short ? box.logsTailError : box.logsError) ?? null,
      refetch: async () => {
        calls.push(['logs-refetch', tail])
        return { data: short ? box.logsTail : box.logs }
      },
    }
  },
  useComfyuiSwitchover: () => ({ mutateAsync: record('switchover') }),
  useComfyuiPin: () => ({ mutateAsync: record('pin') }),
  useComfyuiRenderCancel: () => ({ mutateAsync: record('render-cancel') }),
  useComfyuiRestart: () => ({ mutateAsync: record('restart') }),
}))
vi.mock('@/api/hooks/useConfigUrls', () => ({ useConfigUrls: () => ({ data: box.urls }) }))

const { ImageEngineTab } = await import('../image/ImageEngineTab.jsx')

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
  unload_set: ['brain'],
  llm_waiters: { count: 0, oldest_s: null },
  ...over,
})
const envelope = (over: Json = {}) => ({
  mode: null,
  arbiter: arbiterBlock(over),
  switchover: { active: false, target: null, error: null },
  gate_refusal: null,
})
const statusBody = (over: Json = {}) => ({
  mode: 'inference',
  reachable: true,
  engine: 'running',
  container: { name: 'hal0-slot-img', state: 'running' },
  endpoint: ':8188',
  queue: { running: 0, pending: 0 },
  util: 0,
  temp: 61.5,
  clock: 2.7,
  progress: null,
  inventory: { checkpoints: 2 },
  gate: { state: 'current', version_seen: '3' },
  gate_refusal: null,
  ...over,
})
const row = (id: string, over: Json = {}) => ({
  id,
  label: `Label ${id}`,
  task: 'txt2img',
  state: 'complete',
  api_renderable: true,
  ...over,
})
const catalog = (models: Json[]) => ({
  models,
  engine: { available: true, reason: null },
  store: { free_gb: 100, path: '/m' },
  recommendation: null,
})

let host: HTMLDivElement
let root: Root

// The page's host: the on-box rule reads it, so the tests set it.
function visit(hostname: string) {
  ;(window as unknown as { happyDOM: { setURL: (u: string) => void } }).happyDOM.setURL(
    `http://${hostname}:9120/#slots/image/engine`,
  )
}

function mount(props: Json = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  act(() => {
    root.render(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement('div', { className: 'comfy-page' }, React.createElement(ImageEngineTab, { visible: true, ...props })),
      ),
    )
  })
}
const byId = (id: string) => host.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const allById = (id: string) => Array.from(host.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`))
const text = (id: string) => byId(id)?.textContent ?? null
const click = async (el: HTMLElement) => {
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}
const flush = () => act(async () => {})

beforeEach(() => {
  visit('localhost')
  box.status = statusBody()
  box.arbiter = envelope()
  box.models = catalog([row('sdxl-lightning'), row('qwen-image', { state: 'none' })])
  box.workflows = []
  box.urls = { comfyui: 'http://localhost:8188', comfyui_public: false }
  box.logs = { lines: ['2026-10-25T10:00:00+0000 comfy start', '2026-10-25T10:00:01+0000 comfy ready'] }
  box.logsTail = { lines: [] }
  box.logsError = undefined
  box.logsTailError = undefined
  box.fail = undefined
  calls.length = 0
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.restoreAllMocks()
  // What the copy tests put on the page's globals.
  const w = window as unknown as Record<string, unknown>
  delete w.__hal0Toast
  Reflect.deleteProperty(navigator, 'clipboard')
  Reflect.deleteProperty(document, 'execCommand')
})

// ── the engine card ──────────────────────────────────────────────────────────

describe('engine card', () => {
  it('states the engine, the Quadlet container, that it listens on loopback when fenced, and the editor route', () => {
    box.urls = { comfyui: 'http://localhost:8188', comfyui_public: false, comfyui_lan: false }
    mount()
    expect(text('engine-row-state')).toContain('running')
    expect(text('engine-row-container')).toContain('Quadlet')
    expect(text('engine-row-container')).toContain('hal0-slot-img')
    expect(text('engine-row-listen')).toContain('listens on loopback')
    expect(text('engine-row-editor')).toContain('127.0.0.1:8188')
    expect(text('engine-row-editor')).toContain('SSH tunnel')
    expect(byId('engine-pill')!.getAttribute('data-state')).toBe('running')
  })

  it('says the engine listens on the LAN, and where, while ComfyUI binds it (ADR-0041)', () => {
    visit('hal0.local')
    box.urls = { comfyui: 'http://hal0.local:8188', comfyui_public: false, comfyui_lan: true }
    mount()
    expect(text('engine-row-listen')).toContain('listens on the LAN')
    expect(text('engine-row-editor')).toContain('hal0.local:8188')
    expect(text('engine-row-editor')).toContain('from any machine on your network')
    expect(text('engine-row-editor')).not.toContain('SSH tunnel')
  })


  it.each([
    ['current', 'current'],
    ['stale', 'stale'],
    ['unknown', 'unknown'],
    ['no_credential', 'no credential'],
  ])('shows the GPU gate row for %s', (state, label) => {
    box.status = statusBody({ gate: { state, version_seen: state === 'current' ? '3' : null } })
    mount()
    expect(byId('engine-row-gate')!.getAttribute('data-gate-state')).toBe(state)
    expect(text('engine-row-gate')).toContain('GPU gate')
    expect(text('engine-row-gate')).toContain(label)
  })

  it('has no gate row without a gate, and never a plugins row', () => {
    box.status = statusBody({ gate: null })
    mount()
    expect(byId('engine-row-gate')).toBeNull()
    expect(host.textContent).not.toMatch(/plugin/i)
  })

  it('engine tab has no memory gauges', () => {
    // /status no longer carries a memory block, but a hal0 that has not updated yet still sends one:
    // the tab never draws it. Memory is the top bar ruler's and the fit meters'.
    box.status = statusBody({
      memory: { gtt_used_gb: 54, gtt_ceil_gb: 80, ram_used_gb: 61, ram_ceil_gb: 96, pressure: false },
    })
    mount()
    expect(host.textContent).not.toMatch(/gtt|system ram|\bram\b/i)
    expect(host.querySelector('.gauge, [role="meter"], [data-testid*="gtt"], [data-testid*="ram"]')).toBeNull()
    expect(host.textContent).not.toMatch(/54|\b80\b|\b96\b/)
  })

  it('shows a loading line, not a fake engine, until /status answers', () => {
    box.status = undefined
    mount()
    expect(byId('engine-loading')).not.toBeNull()
    expect(byId('engine-row-state')).toBeNull()
    expect(byId('engine-pill')).toBeNull()
  })

  it('survives a /status body without the usual fields', () => {
    // The dev mock and an older backend answer `{}`: the tab draws what is there and nothing
    // more, and never fills in a queue, an engine or a gate it was not given.
    box.status = {}
    mount()
    expect(byId('engine-card')).not.toBeNull()
    expect(byId('engine-pill')).toBeNull()
    expect(byId('engine-row-gate')).toBeNull()
    expect(byId('queue-card')).toBeNull()
    expect(byId('editor-card')).not.toBeNull()
  })

  it('polls /status at the active cadence only while the tab shows', () => {
    mount({ visible: true })
    mount({ visible: false })
    const opts = calls.filter((c) => c[0] === 'status-hook').map((c) => c[1])
    expect(opts[0]).toMatchObject({ active: true })
    expect(opts[opts.length - 1]).toMatchObject({ active: false })
  })
})

describe('engine states', () => {
  it('stopped engine says generate still works; unreachable shows last log lines', async () => {
    box.status = statusBody({ engine: 'stopped', reachable: false, container: { name: 'hal0-slot-img', state: 'exited' } })
    mount()
    expect(text('engine-stopped')).toContain('ComfyUI is stopped')
    expect(text('engine-stopped')).toContain(
      'Generate still works: the next render starts it (a cold start can take up to 2 min).',
    )
    expect(byId('engine-unreachable')).toBeNull()

    box.status = statusBody({ engine: 'starting', reachable: false })
    box.logsTail = { lines: ['line one', 'line two', 'line three'] }
    mount()
    await flush()
    expect(byId('engine-stopped')).toBeNull()
    expect(text('engine-unreachable')).toContain("ComfyUI isn't answering")
    expect(text('engine-unreachable-log')).toBe('line one\nline two\nline three')
    // The three lines are asked for, not the modal's longer tail.
    expect(calls.some((c) => c[0] === 'logs-refetch' && c[1] === 3)).toBe(true)
  })

  it('shows the last three of whatever the log holds', async () => {
    box.status = statusBody({ engine: 'starting', reachable: false })
    box.logsTail = { lines: ['a', 'b', 'c', 'd', 'e'] }
    mount()
    await flush()
    expect(text('engine-unreachable-log')).toBe('c\nd\ne')
  })

  it('says the log could not be read when the tail fetch fails, instead of staying on Reading', async () => {
    box.status = statusBody({ engine: 'starting', reachable: false })
    box.logsTail = undefined
    box.logsTailError = new Error('journal unreadable')
    mount()
    await flush()
    expect(text('engine-unreachable')).toContain("ComfyUI isn't answering")
    expect(text('engine-unreachable-error')).toContain('The log could not be read')
    expect(text('engine-unreachable-error')).toContain('journal unreadable')
    expect(text('engine-unreachable')).not.toContain('Reading the log')
    expect(byId('engine-unreachable-log')).toBeNull()
  })

  // A refetch that fails after lines were fetched must not blank them: the notice exists to show
  // why the engine is stuck, and the last lines read are still that.
  it('keeps the lines already fetched when a refresh fails, with a note that it could not refresh', async () => {
    box.status = statusBody({ engine: 'starting', reachable: false })
    box.logsTail = { lines: ['line one', 'line two', 'line three'] }
    box.logsTailError = new Error('journal unreadable')
    mount()
    await flush()
    expect(text('engine-unreachable-log')).toBe('line one\nline two\nline three')
    expect(text('engine-unreachable-stale')).toContain('Could not refresh the log')
    expect(text('engine-unreachable-stale')).toContain('journal unreadable')
    // Not the message for a log that was never read.
    expect(byId('engine-unreachable-error')).toBeNull()
    expect(text('engine-unreachable')).not.toContain('The log could not be read')
  })

  it('has no refresh note while the refreshes work', async () => {
    box.status = statusBody({ engine: 'starting', reachable: false })
    box.logsTail = { lines: ['line one'] }
    mount()
    await flush()
    expect(byId('engine-unreachable-stale')).toBeNull()
  })

  it('says so when the journal has nothing to show', async () => {
    box.status = statusBody({ engine: 'starting', reachable: false })
    box.logsTail = { lines: [] }
    mount()
    await flush()
    expect(text('engine-unreachable')).toContain("ComfyUI isn't answering")
    expect(text('engine-unreachable-log')).toBeNull()
    expect(text('engine-unreachable')).toContain('No log lines')
  })

  it('does not fetch the log while the engine answers', async () => {
    mount()
    await flush()
    expect(calls.some((c) => c[0] === 'logs-refetch')).toBe(false)
  })

  it('does not fetch the log while the tab is hidden', async () => {
    box.status = statusBody({ engine: 'starting', reachable: false })
    mount({ visible: false })
    await flush()
    expect(calls.some((c) => c[0] === 'logs-refetch')).toBe(false)
  })
})

describe('engine buttons', () => {
  it('Restart engine calls /restart', async () => {
    mount()
    await click(byId('engine-restart')!)
    expect(calls.filter((c) => c[0] === 'restart')).toHaveLength(1)
  })

  it('a failed restart says so', async () => {
    box.fail = new Error('podman said no')
    mount()
    await click(byId('engine-restart')!)
    expect(byId('engine-error')!.textContent).toContain('podman said no')
  })

  it('Stop image mode shows only while image mode is on, and is the status line stop', async () => {
    mount()
    expect(byId('engine-stop')).toBeNull()

    box.arbiter = envelope({ mode: 'img', pinned: true })
    mount()
    await click(byId('engine-stop')!)
    // Pinned: unpin first, then hand the GPU back. No render runs, so no force and no confirm.
    expect(calls.filter((c) => c[0] === 'pin' || c[0] === 'switchover')).toEqual([
      ['pin', { pinned: false }],
      ['switchover', { mode: 'inference' }],
    ])
  })

  it('hides Stop image mode while a switch is running', async () => {
    box.arbiter = envelope({ mode: 'img', switching: true, phase: 'unloading' })
    mount()
    expect(byId('engine-stop')).toBeNull()
    // Restart and Logs are still there.
    expect(byId('engine-restart')).not.toBeNull()
    expect(byId('engine-logs')).not.toBeNull()

    box.arbiter = envelope({ mode: 'img', switching: false })
    mount()
    expect(byId('engine-stop')).not.toBeNull()
  })

  it('Stop image mode asks before dropping a running render, then forces', async () => {
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.status = statusBody({ queue: { running: 1, pending: 0 } })
    mount()
    await click(byId('engine-stop')!)
    expect(calls.some((c) => c[0] === 'switchover')).toBe(false)
    const confirm = Array.from(document.querySelectorAll('button')).find((b) => b.textContent === 'Stop and cancel render')!
    expect(confirm).toBeTruthy()
    await click(confirm)
    expect(calls.filter((c) => c[0] === 'switchover')).toEqual([['switchover', { mode: 'inference', force: true }]])
  })

  // The ~3 s hand-off after a switch reads above `rendering` in the arbiter's phase, so the first
  // seconds of a hal0 render have phase `handoff` and an empty ComfyUI queue: a /v1 job is open.
  it('Stop image mode asks during the hand-off of a hal0 render, which has a /v1 job open', async () => {
    box.arbiter = envelope({ mode: 'img', phase: 'handoff', handoff_until: Date.now() / 1000 + 3, image_jobs: 1 })
    mount()
    await click(byId('engine-stop')!)
    expect(calls.some((c) => c[0] === 'switchover')).toBe(false)
    const confirm = Array.from(document.querySelectorAll('button')).find((b) => b.textContent === 'Stop and cancel render')!
    expect(confirm).toBeTruthy()
    await click(confirm)
    expect(calls.filter((c) => c[0] === 'switchover')).toEqual([['switchover', { mode: 'inference', force: true }]])
  })

  it('Stop image mode does not ask during a plain hand-off', async () => {
    box.arbiter = envelope({ mode: 'img', phase: 'handoff', handoff_until: Date.now() / 1000 + 3, image_jobs: 0 })
    mount()
    await click(byId('engine-stop')!)
    expect(document.querySelector('.modal-shell')).toBeNull()
    expect(calls.filter((c) => c[0] === 'switchover')).toEqual([['switchover', { mode: 'inference' }]])
  })
})

describe('logs', () => {
  it('logs open in a modal and close on Esc', async () => {
    mount()
    expect(byId('engine-logs-pre')).toBeNull()
    await click(byId('engine-logs')!)
    await flush()
    expect(calls.some((c) => c[0] === 'logs-refetch' && c[1] !== 3)).toBe(true)
    const pre = byId('engine-logs-pre')!
    expect(pre.tagName).toBe('PRE')
    expect(pre.textContent).toBe(
      '2026-10-25T10:00:00+0000 comfy start\n2026-10-25T10:00:01+0000 comfy ready',
    )
    expect(document.querySelector('[role="dialog"]')).not.toBeNull()

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(byId('engine-logs-pre')).toBeNull()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })

  it('says when there are no lines, and when the read failed', async () => {
    box.logs = { lines: [] }
    mount()
    await click(byId('engine-logs')!)
    expect(text('engine-logs-empty')).toContain('No log lines')

    box.logs = undefined
    box.logsError = new Error('journal unreadable')
    mount()
    expect(text('engine-logs-error')).toContain('journal unreadable')
  })
})

// ── the editor card ──────────────────────────────────────────────────────────

describe('editor card', () => {
  it('workflow strip lists only installed models plus custom files', () => {
    box.models = catalog([
      row('sdxl-lightning'),
      row('qwen-image', { state: 'partial' }),
      row('wan22-t2v', { state: 'complete', task: 'txt2video', api_renderable: false }),
      row('sdxl-turbo', { state: 'none' }),
    ])
    box.workflows = [
      { name: 'sdxl-lightning', source: 'primary' },
      { name: 'qwen-image', source: 'primary' },
      { name: 'wan22-t2v', source: 'primary' },
      { name: 'sdxl-turbo', source: 'primary' },
      { name: 'my-upscale', source: 'user' },
    ]
    mount()
    const chips = allById('editor-chip')
    expect(chips.map((c) => c.getAttribute('data-workflow'))).toEqual(['sdxl-lightning', 'wan22-t2v', 'my-upscale'])
    expect(chips.map((c) => c.getAttribute('data-custom'))).toEqual(['false', 'false', 'true'])
    expect(chips[0].textContent).toContain('Label sdxl-lightning')
    expect(chips[2].textContent).toContain('my-upscale')
  })

  it('on the box the editor button opens the editor link in a new tab', () => {
    mount()
    const open = byId('editor-open') as HTMLAnchorElement
    expect(open.tagName).toBe('A')
    expect(open.getAttribute('href')).toBe('http://localhost:8188')
    expect(open.getAttribute('target')).toBe('_blank')
    expect(open.getAttribute('rel')).toBe('noopener noreferrer')
    expect(open.textContent).toBe('Open ComfyUI editor ↗')
    expect(byId('editor-offbox')).toBeNull()
    expect(byId('editor-tunnel')).toBeNull()
    // The chips open the same editor.
    for (const chip of allById('editor-chip')) expect(chip.getAttribute('href')).toBe('http://localhost:8188')
  })

  it('editor button disabled off-box without proxy shows tunnel command', async () => {
    visit('hal0.local')
    box.urls = { comfyui: 'http://hal0.local:8188', comfyui_public: false }
    box.workflows = [{ name: 'my-upscale', source: 'user' }]
    mount()
    const open = byId('editor-open')!
    expect(open.tagName).toBe('BUTTON')
    expect((open as HTMLButtonElement).disabled).toBe(true)
    expect(open.textContent).toBe('Open ComfyUI editor ↗')
    expect(text('editor-offbox')).toBe('Only reachable on the box itself')
    expect(text('editor-tunnel')).toBe('ssh -L 8188:127.0.0.1:8188 <user>@hal0.local')
    // No chip links anywhere either.
    for (const chip of allById('editor-chip')) {
      expect(chip.tagName).not.toBe('A')
      expect(chip.getAttribute('href')).toBeNull()
      expect(chip.getAttribute('aria-disabled')).toBe('true')
    }
    expect(host.querySelector('a[href*="8188"]')).toBeNull()

    // The command can be copied.
    const write = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: write }, configurable: true })
    await click(byId('editor-tunnel-copy')!)
    expect(write).toHaveBeenCalledWith('ssh -L 8188:127.0.0.1:8188 <user>@hal0.local')
  })

  it('a LAN page whose /api/config/urls says nothing about a public URL is off the box', () => {
    visit('hal0.local')
    box.urls = { comfyui: 'http://hal0.local:8188' }
    mount()
    expect((byId('editor-open') as HTMLButtonElement).disabled).toBe(true)
    expect(byId('editor-offbox')).not.toBeNull()
  })

  it('copies the tunnel command on a plain-http page, where navigator.clipboard does not exist', async () => {
    // A dashboard served over http on a LAN is not a secure context: no async clipboard API. The
    // command copies through the legacy path, and the toast says what happened.
    visit('hal0.local')
    box.urls = { comfyui: 'http://hal0.local:8188', comfyui_public: false }
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    const exec = vi.fn((_command: string) => true)
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true })
    const toast = vi.fn()
    ;(window as unknown as { __hal0Toast: unknown }).__hal0Toast = toast
    let copied = ''
    const original = document.body.appendChild.bind(document.body)
    vi.spyOn(document.body, 'appendChild').mockImplementation(((node: Node) => {
      if (node instanceof HTMLTextAreaElement) copied = node.value
      return original(node)
    }) as typeof document.body.appendChild)

    mount()
    await click(byId('editor-tunnel-copy')!)
    expect(exec).toHaveBeenCalledWith('copy')
    expect(copied).toBe('ssh -L 8188:127.0.0.1:8188 <user>@hal0.local')
    expect(toast).toHaveBeenCalledWith('Command copied', 'ok')
  })

  it('says so when the command could not be copied at all', async () => {
    visit('hal0.local')
    box.urls = { comfyui: 'http://hal0.local:8188', comfyui_public: false }
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    Object.defineProperty(document, 'execCommand', { value: () => false, configurable: true })
    const toast = vi.fn()
    ;(window as unknown as { __hal0Toast: unknown }).__hal0Toast = toast
    mount()
    await click(byId('editor-tunnel-copy')!)
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast.mock.calls[0][1]).toBe('err')
    expect(String(toast.mock.calls[0][0])).toMatch(/copy it by hand/)
  })

  it('the server flag decides, not the shape of the link', () => {
    // A link on another host is not a declared public URL unless the server says it is.
    visit('hal0.example.com')
    box.urls = { comfyui: 'https://comfyui.example.com', comfyui_public: false }
    mount()
    expect((byId('editor-open') as HTMLButtonElement).disabled).toBe(true)
    expect(byId('editor-offbox')).not.toBeNull()
  })

  it('a public URL the operator declared still opens, from any host', () => {
    visit('hal0.example.com')
    box.urls = { comfyui: 'https://comfyui.example.com', comfyui_public: true }
    mount()
    const open = byId('editor-open') as HTMLAnchorElement
    expect(open.tagName).toBe('A')
    expect(open.getAttribute('href')).toBe('https://comfyui.example.com')
    expect(byId('editor-offbox')).toBeNull()
  })

  it('carries the editor note and says ComfyUI cannot open a workflow from a link', () => {
    mount()
    expect(text('editor-note')).toContain(
      "Prompts from the editor switch the GPU the same way. The GPU gate refuses one that hal0 can't hand the GPU to.",
    )
    expect(text('editor-workflow-hint')).toContain("ComfyUI can't open a workflow from a link")
    expect(text('editor-workflow-hint')).toContain("Workflows sidebar")
  })
})

// ── the queue card ───────────────────────────────────────────────────────────

describe('queue card', () => {
  it('counts what runs and what waits, and shows hal0 render progress when there is one', () => {
    box.status = statusBody({
      queue: { running: 1, pending: 2 },
      progress: { model: 'qwen-image', step: 4, total: 20, node: 'KSampler', elapsed_s: 31, prompt_id: 'p1' },
    })
    mount()
    expect(text('queue-summary')).toBe('1 running · 2 waiting')
    expect(byId('queue-empty')).toBeNull()
    expect(text('queue-progress')).toContain('qwen-image')
    expect(text('queue-progress')).toContain('step 4/20')
  })

  it('shows the counts alone for an editor prompt, which has no hal0 progress', () => {
    box.status = statusBody({ queue: { running: 1, pending: 0 } })
    mount()
    expect(text('queue-summary')).toBe('1 running · 0 waiting')
    expect(byId('queue-progress')).toBeNull()
    expect(byId('queue-empty')).toBeNull()
  })

  it('copes with partial counts', () => {
    box.status = statusBody({ queue: {} })
    mount()
    expect(text('queue-summary')).toBe('0 running · 0 waiting')
    expect(host.textContent).not.toContain('undefined')
  })

  it('says what shows here when nothing is queued', () => {
    mount()
    expect(text('queue-empty')).toBe(
      'Nothing queued. Renders from Generate, Open WebUI, the API and the editor all show here.',
    )
  })
})

// ── the GPU device card ──────────────────────────────────────────────────────

describe('GPU device card', () => {
  it('reads temperature and clock, and utilisation only while rendering', () => {
    mount()
    expect(text('device-temp')).toContain('61.5')
    expect(text('device-clock')).toContain('2.7')
    expect(byId('device-util')).toBeNull()

    box.status = statusBody({ util: 97, queue: { running: 1, pending: 0 } })
    mount()
    expect(text('device-util')).toContain('97')
  })

  it('shows a dash for a reading the box could not take, never a zero', () => {
    box.status = statusBody({ temp: null, clock: null })
    mount()
    expect(text('device-temp')).toContain('—')
    expect(text('device-clock')).toContain('—')
  })
})
