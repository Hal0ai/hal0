// @vitest-environment happy-dom
//
// Where the dashboard links to the ComfyUI editor outside the Image Gen pane: the Services page,
// the Services board card and the Overview Services widget. ComfyUI listens on the box's loopback
// only, so the link opens from a page on the box and from nowhere else (editor-reach.ts has the
// rule, useEditorAccess applies it). Off the box the Services page says so and gives the SSH
// command; the compact surfaces disable the link.
//
// The same module set once opened ComfyUI's own /queue from the browser (ComfyJobQueue), which an
// off-box page could not reach either: nothing on these surfaces fetches :8188 any more, and the
// card still renders when /api/comfyui/status has not answered (no placeholder status stands in).
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const g = globalThis as unknown as Record<string, unknown>
g.React = React
g.IS_REACT_ACT_ENVIRONMENT = true
// What these modules read off the window: the card shell and the info icon.
g.DCard = ({ title, right, children }: { title?: string; right?: unknown; children?: unknown }) =>
  React.createElement('section', { 'data-card': title }, right as React.ReactNode, children as React.ReactNode)
g.FieldInfoIcon = () => null

type Json = Record<string, unknown>

const { box } = vi.hoisted(() => ({
  box: {
    services: [] as Json[],
    health: [] as Json[],
    status: undefined as Json | undefined,
    urls: undefined as Json | undefined,
  },
}))

const mutation = () => ({ mutate: () => {}, mutateAsync: async () => ({}), isPending: false, variables: undefined, data: undefined })
vi.mock('@/api/hooks/useServices', () => ({
  useServices: () => ({ services: box.services, mdns: null, pending: false }),
  useServiceAction: mutation,
  useMdnsAdvertise: mutation,
  useUnitLogs: () => ({ data: undefined, isPending: false }),
}))
vi.mock('@/api/hooks/useServicesHealth', () => ({
  useServicesHealth: () => ({ services: box.health, pending: false }),
}))
vi.mock('@/api/hooks/useComfyui', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useComfyui: () => ({ data: box.status }),
}))
vi.mock('@/api/hooks/useConfigUrls', () => ({ useConfigUrls: () => ({ data: box.urls }) }))
vi.mock('@/api/hooks/useComponents', () => ({
  useComponents: () => ({ data: undefined }),
  useComponentConverge: mutation,
}))
vi.mock('@/api/hooks/useUpdates', () => ({ useUpdateJob: () => ({ job: null, terminal: false }) }))

// dashboard-redesign.jsx imports every hook the whole dashboard uses; the Services widget needs
// only the ones mocked for real above, and the rest answer nothing.
const inert = vi.hoisted(
  () => () => new Proxy({}, { get: (_t, k) => (k === 'then' ? undefined : () => ({ data: undefined })) }),
)
vi.mock('@/api/hooks/useSlots', inert)
vi.mock('@/api/hooks/useHardware', inert)
vi.mock('@/api/hooks/useStatsHardware', inert)
vi.mock('@/api/hooks/useStatsPower', inert)
vi.mock('@/api/hooks/useThroughputHistory', inert)
vi.mock('@/api/hooks/useRequestsRollup', inert)
vi.mock('@/api/hooks/useActivity', inert)
vi.mock('@/api/hooks/useAgents', inert)
vi.mock('@/api/hooks/useDashLayout', inert)
vi.mock('../notifications.jsx', inert)

await import('../services.jsx')
await import('../services-card.jsx')
const { RDServicesCard } = await import('../dashboard-redesign.jsx')
const { ServicesView, ServicesCard } = window as unknown as {
  ServicesView: React.ComponentType
  ServicesCard: React.ComponentType
}

// ── fixtures ─────────────────────────────────────────────────────────────────

const OFF_BOX_NOTE = 'Only reachable on the box itself'

const comfyService = (over: Json = {}) => ({
  id: 'comfyui',
  name: 'ComfyUI',
  description: 'Image generation engine (img slot container, loopback :8188).',
  managed: true,
  unit: 'hal0-slot@img.service',
  unit_state: { active_state: 'active', sub_state: 'running', unit_file_state: 'enabled', since: null },
  up: true,
  detail: 'running — 0 job(s) active',
  stat: null,
  // What the API derives from the request host: it answers on the box only.
  url: 'http://hal0.lan:8188',
  mdns_url: null,
  loopback_port: null,
  actions: ['restart'],
  mdns_capable: false,
  hints: [],
  ...over,
})
const openwebuiService = () =>
  comfyService({
    id: 'openwebui',
    name: 'OpenWebUI',
    description: 'Chat UI companion.',
    unit: 'hal0-openwebui.service',
    url: 'http://hal0.lan:3001',
    actions: ['restart'],
  })
const healthRow = (id: string, over: Json = {}) => ({
  id,
  name: id === 'comfyui' ? 'ComfyUI' : id,
  up: true,
  state: 'up',
  detail: 'running',
  // The health probe's url is the API's own view of ComfyUI (its loopback), not a browser link.
  url: id === 'comfyui' ? 'http://127.0.0.1:8188' : null,
  stat: null,
  ...over,
})
const statusBody = (over: Json = {}) => ({
  mode: 'inference',
  reachable: true,
  engine: 'running',
  container: { name: 'hal0-slot-img', state: 'running' },
  endpoint: ':8188',
  queue: { running: 0, pending: 0 },
  ...over,
})

let host: HTMLDivElement
let root: Root
let opened: unknown[][]
let fetched: string[]

// The page's host: the on-box rule reads it, so the tests set it.
function visit(hostname: string) {
  ;(window as unknown as { happyDOM: { setURL: (u: string) => void } }).happyDOM.setURL(
    `http://${hostname}:9120/#services`,
  )
}
function mount(node: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  act(() => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, node))
  })
}
const byId = (id: string) => host.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const click = (el: HTMLElement) =>
  act(() => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })

beforeEach(() => {
  visit('localhost')
  box.services = [comfyService(), openwebuiService()]
  box.health = [healthRow('comfyui')]
  box.status = statusBody()
  box.urls = { comfyui: 'http://localhost:8188', comfyui_public: false }
  opened = []
  fetched = []
  vi.spyOn(window, 'open').mockImplementation((...args: unknown[]) => {
    opened.push(args)
    return null
  })
  vi.stubGlobal('fetch', (url: unknown) => {
    fetched.push(String(url))
    return Promise.reject(new Error('no network in this test'))
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

// ── the Services page ────────────────────────────────────────────────────────

describe('Services page ComfyUI link', () => {
  it('opens the editor from a page on the box', () => {
    mount(React.createElement(ServicesView))
    const open = byId('svcp-open-comfyui') as HTMLButtonElement
    expect(open.disabled).toBe(false)
    click(open)
    expect(opened).toEqual([['http://localhost:8188', '_blank', 'noopener']])
    expect(byId('svcp-comfyui-tunnel')).toBeNull()
    expect(host.textContent).not.toContain(OFF_BOX_NOTE)
  })

  it('is disabled off the box, says why, and gives the tunnel command', () => {
    visit('hal0.lan')
    box.urls = { comfyui: 'http://hal0.lan:8188', comfyui_public: false }
    mount(React.createElement(ServicesView))
    const open = byId('svcp-open-comfyui') as HTMLButtonElement
    expect(open.disabled).toBe(true)
    click(open)
    expect(opened).toEqual([])
    expect(byId('svcp-comfyui-offbox')!.textContent).toBe(OFF_BOX_NOTE)
    expect(byId('svcp-comfyui-tunnel')!.textContent).toBe('ssh -L 8188:127.0.0.1:8188 <user>@hal0.lan')
    // The derived link is dead from here, so the card does not print it as if it worked.
    expect(host.textContent).not.toContain('http://hal0.lan:8188')
  })

  it('opens a public URL the operator declared, from any page', () => {
    visit('hal0.lan')
    box.urls = { comfyui: 'https://comfy.example.test', comfyui_public: true }
    mount(React.createElement(ServicesView))
    const open = byId('svcp-open-comfyui') as HTMLButtonElement
    expect(open.disabled).toBe(false)
    click(open)
    expect(opened).toEqual([['https://comfy.example.test', '_blank', 'noopener']])
    expect(byId('svcp-comfyui-tunnel')).toBeNull()
  })

  it('leaves every other service link alone', () => {
    visit('hal0.lan')
    box.urls = { comfyui: 'http://hal0.lan:8188', comfyui_public: false }
    mount(React.createElement(ServicesView))
    const open = byId('svcp-open-openwebui') as HTMLButtonElement
    expect(open.disabled).toBe(false)
    click(open)
    expect(opened).toEqual([['http://hal0.lan:3001', '_blank', 'noopener']])
  })

  it('has no Queue drawer, and never fetches ComfyUI from the browser', () => {
    mount(React.createElement(ServicesView))
    expect(byId('svcp-queue-comfyui')).toBeNull()
    expect(host.textContent).not.toMatch(/\bQueue\b/)
    expect(fetched.filter((u) => /8188|\/queue|\/history/.test(u))).toEqual([])
    expect((window as unknown as Json).ComfyJobQueue).toBeUndefined()
  })
})

// ── the Services board card ──────────────────────────────────────────────────

describe('Services board card', () => {
  it('renders before /api/comfyui/status has answered, with no placeholder ComfyUI row', () => {
    box.status = undefined
    box.health = [healthRow('hermes', { url: null })]
    mount(React.createElement(ServicesCard))
    expect(host.textContent).toContain('hermes')
    expect(host.textContent).not.toMatch(/comfy/i)
    expect(host.textContent).not.toMatch(/mode unknown|source pending/)
  })

  it('opens the browser-reachable editor link on the box, not the API probe address', () => {
    mount(React.createElement(ServicesCard))
    const open = host.querySelector<HTMLButtonElement>('[data-testid="svc-open-comfyui"]')!
    expect(open.disabled).toBe(false)
    click(open)
    expect(opened).toEqual([['http://localhost:8188', '_blank', 'noopener']])
  })

  it('disables the link off the box and says why', () => {
    visit('hal0.lan')
    box.urls = { comfyui: 'http://hal0.lan:8188', comfyui_public: false }
    mount(React.createElement(ServicesCard))
    const open = host.querySelector<HTMLButtonElement>('[data-testid="svc-open-comfyui"]')!
    expect(open.disabled).toBe(true)
    click(open)
    expect(opened).toEqual([])
    expect(host.querySelector('[data-testid="svc-open-comfyui-wrap"]')!.getAttribute('title')).toContain(
      OFF_BOX_NOTE,
    )
  })

  it('synthesizes the ComfyUI row from /status without a link it cannot open', () => {
    visit('hal0.lan')
    box.urls = { comfyui: 'http://hal0.lan:8188', comfyui_public: false }
    box.health = []
    box.status = statusBody({ queue: { running: 1, pending: 2 } })
    mount(React.createElement(ServicesCard))
    expect(host.textContent).toContain('ComfyUI')
    expect(host.textContent).toContain('3')
    expect((host.querySelector('[data-testid="svc-open-comfyui"]') as HTMLButtonElement).disabled).toBe(true)
  })

  it('has no queue expander, and never fetches ComfyUI from the browser', () => {
    mount(React.createElement(ServicesCard))
    expect(host.querySelector('.svc-expand-btn')).toBeNull()
    expect(host.querySelector('.svc-comfy-drawer')).toBeNull()
    expect(fetched.filter((u) => /8188|\/queue|\/history/.test(u))).toEqual([])
  })
})

// ── the Overview Services widget ─────────────────────────────────────────────

describe('Overview Services widget', () => {
  const widget = () => mount(React.createElement(RDServicesCard, { swap: null, onGo: () => {} }))
  const comfyOpen = () => host.querySelector<HTMLElement>('[data-testid="rd-svc-open-comfyui"]')!

  it('opens the editor from a page on the box', () => {
    widget()
    expect(comfyOpen().getAttribute('aria-disabled')).toBeNull()
    click(comfyOpen())
    expect(opened).toEqual([['http://localhost:8188', '_blank', 'noopener']])
  })

  it('shows the arrow disabled off the box, and it opens nothing', () => {
    visit('hal0.lan')
    box.urls = { comfyui: 'http://hal0.lan:8188', comfyui_public: false }
    widget()
    expect(comfyOpen().getAttribute('aria-disabled')).toBe('true')
    expect(comfyOpen().getAttribute('title')).toBe(OFF_BOX_NOTE)
    click(comfyOpen())
    expect(opened).toEqual([])
  })

  it('still opens the other services from anywhere', () => {
    visit('hal0.lan')
    box.urls = { comfyui: 'http://hal0.lan:8188', comfyui_public: false }
    widget()
    click(host.querySelector<HTMLElement>('[data-testid="rd-svc-open-openwebui"]')!)
    expect(opened).toEqual([['http://hal0.lan:3001', '_blank', 'noopener']])
  })
})
