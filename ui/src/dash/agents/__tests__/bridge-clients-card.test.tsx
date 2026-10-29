// @vitest-environment happy-dom
//
// The Bridge clients card (Agents ▸ MCP tab). A REAL render + click test: the card is a
// window-globals file, so everything it reads off `window` is installed by the real
// agents-hook-bridge (its published names are what the card reads) and primitives.jsx (Modal,
// ConfirmDialog). Only the API client is mocked, at its boundary, so the hooks, the cache
// invalidation and the card's state machine run for real.
//
// Pinned here: a client row with its scope chips, last-seen tooltip, revoked badge and per-row
// active-run count; the Add modal's defaults and name rule; the result view showing the key
// exactly once and one `claude mcp add` line per scope; the key not leaking into toasts or the
// console, nor surviving Done; Revoke and Rotate behind a confirm; the scopes editor; and the
// `bridge.disabled` one-liner.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn(), apiPatch: vi.fn(), apiDelete: vi.fn() }
})

const client = await import('@/api/client')
await import('../../primitives.jsx')
await import('../agents-hook-bridge')
await import('../bridge-clients-card.jsx')

const KEY = 'hal0bk_Zx81nQ0cK3mV9-secret-value'
const NOW_S = Math.floor(Date.now() / 1000)

const CLIENTS = [
  {
    name: 'laptop',
    note: 'alex dev box',
    scopes: ['agents', 'memory', 'admin'],
    created_at: NOW_S - 86400 * 2,
    created_by: 'api:admin',
    revoked_at: null,
    last_seen_at: NOW_S - 300,
  },
  {
    name: 'ci-bot',
    note: '',
    scopes: ['agents'],
    created_at: NOW_S - 86400 * 9,
    created_by: 'cli:mint',
    revoked_at: NOW_S - 3600,
    last_seen_at: null,
  },
]

const LINES = [
  { server: 'hal0-agents', scope: 'agents', mcp_url: 'http://hal0.lan/mcp/agents/mcp' },
  { server: 'hal0-memory', scope: 'memory', mcp_url: 'http://hal0.lan/mcp/memory/mcp' },
].map((l) => ({
  ...l,
  claude_mcp_add: `claude mcp add --transport http ${l.server} ${l.mcp_url} --header "Authorization: Bearer ${KEY}"`,
}))

const minted = (name: string, scopes = ['agents', 'memory']) => ({
  client: { ...CLIENTS[0], name, scopes },
  key: KEY,
  onboarding: { lines: LINES.filter((l) => scopes.includes(l.scope)) },
})

const RUN = (status: string, id: string) => ({
  run_id: id,
  agent: 'hermes',
  profile: null,
  client: 'laptop',
  status,
  created_at: NOW_S - 100,
  updated_at: NOW_S - 90,
  input_preview: 'x',
  output: null,
  error: null,
  usage: null,
  pending: null,
  last_seq: 0,
})

type W = Record<string, unknown>
const w = window as unknown as W

let root: Root | null = null
let container: HTMLElement
const toasts: unknown[][] = []

function routeGets(runs: unknown[] = []) {
  vi.mocked(client.apiGet).mockImplementation(async (path: string) => {
    if (path === '/api/bridge/clients') return { items: CLIENTS }
    if (path.startsWith('/api/bridge/runs')) return { items: runs }
    throw new Error('unexpected GET ' + path)
  })
}

async function flush() {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
}

async function mount() {
  const Card = w.BridgeClientsCard as React.ComponentType
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root!.render(
      <QueryClientProvider client={qc}>
        <Card />
      </QueryClientProvider>,
    )
  })
  await flush()
}

const q = (sel: string) => container.querySelector(sel) as HTMLElement | null
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as HTMLElement[]
const text = () => container.textContent ?? ''
const byText = (sel: string, t: string) => qa(sel).find((el) => el.textContent?.trim() === t) ?? null

async function click(el: Element | null) {
  expect(el).not.toBeNull()
  await act(async () => {
    ;(el as HTMLElement).click()
  })
  await flush()
}

async function type(el: HTMLElement | null, value: string) {
  expect(el).not.toBeNull()
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(el, value)
    el!.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

const occurrences = (hay: string, needle: string) => hay.split(needle).length - 1

beforeEach(() => {
  for (const fn of [client.apiGet, client.apiPost, client.apiPatch, client.apiDelete]) vi.mocked(fn).mockReset()
  toasts.length = 0
  w.__hal0Toast = (...args: unknown[]) => toasts.push(args)
  w.__hal0CopyText = vi.fn(async () => true)
  w.Icon = () => null
  // The real EnginePane is internal to connections.jsx; this stands in with the same slots.
  w.EnginePane = ({ title, pill, headRight, strip, foot, children }: Record<string, React.ReactNode> & { pill: { text: string } }) => (
    <section data-testid="pane">
      <h3>{title}</h3>
      <span data-testid="pane-pill">{pill.text}</span>
      {headRight}
      {strip}
      {children}
      {foot}
    </section>
  )
  routeGets()
})

afterEach(() => {
  if (root) act(() => root!.unmount())
  root = null
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('client list', () => {
  it('renders a row per client with scope chips named like the server rows, a last-seen tooltip and a revoked badge', async () => {
    await mount()

    const laptop = q('[data-testid="bridge-client-laptop"]')!
    expect(laptop).not.toBeNull()
    expect(laptop.textContent).toContain('alex dev box')
    const chips = Array.from(laptop.querySelectorAll('[data-scope]')).map((c) => c.textContent)
    expect(chips).toEqual(['hal0-agents', 'hal0-memory', 'hal0-admin'])
    expect(laptop.querySelector('[data-testid="bridge-last-seen"]')!.textContent).toBe('seen 5m ago')
    expect(laptop.querySelector('[data-testid="bridge-last-seen"]')!.getAttribute('title')).toMatch(
      /updated at most every 60 s/i,
    )
    expect(laptop.querySelector('[data-testid="bridge-revoked"]')).toBeNull()

    const bot = q('[data-testid="bridge-client-ci-bot"]')!
    expect(bot.getAttribute('data-revoked')).toBe('true')
    expect(bot.querySelector('[data-testid="bridge-revoked"]')!.textContent).toBe('revoked')
    expect(bot.querySelector('[data-testid="bridge-last-seen"]')!.textContent).toBe('never seen')

    // one live client of two
    expect(q('[data-testid="pane-pill"]')!.textContent).toBe('1 active')
  })

  it('counts a client\'s live runs only once its row is open', async () => {
    routeGets([RUN('running', 'r1'), RUN('waiting_for_approval', 'r2'), RUN('completed', 'r3')])
    await mount()
    expect(vi.mocked(client.apiGet).mock.calls.some(([p]) => String(p).startsWith('/api/bridge/runs'))).toBe(false)

    await click(q('[data-testid="bridge-client-laptop"] .mcprow-main'))
    expect(client.apiGet).toHaveBeenCalledWith('/api/bridge/runs?client=laptop&limit=50')
    expect(q('[data-testid="bridge-active-runs"]')!.textContent).toBe('active runs 2')
  })

  it('shows an empty state with no clients', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({ items: [] })
    await mount()
    expect(text()).toContain('No bridge clients yet')
  })
})

describe('Add modal', () => {
  it('opens with agents locked on, memory on and admin off, the admin warning beside admin', async () => {
    await mount()
    await click(q('[data-testid="bridge-add"]'))

    const box = (s: string) => q(`input[type="checkbox"][data-scope="${s}"]`) as HTMLInputElement
    expect(box('agents').checked).toBe(true)
    expect(box('agents').disabled).toBe(true)
    expect(box('memory').checked).toBe(true)
    expect(box('memory').disabled).toBe(false)
    expect(box('admin').checked).toBe(false)
    expect(text()).toContain("Reaches hal0's admin MCP tools. Treat this key like an operator credential.")
    // nothing to submit until there is a name
    expect((q('[data-testid="bridge-add-submit"]') as HTMLButtonElement).disabled).toBe(true)
  })

  it('refuses a name the API would refuse, with the API\'s wording', async () => {
    await mount()
    await click(q('[data-testid="bridge-add"]'))
    await type(q('[data-testid="bridge-add-name"]'), 'Bad Name')

    expect(text()).toContain(
      "invalid bridge client name 'Bad Name': use 1-32 characters from a-z, 0-9, '_' and '-', starting with a letter or digit",
    )
    expect((q('[data-testid="bridge-add-submit"]') as HTMLButtonElement).disabled).toBe(true)

    await type(q('[data-testid="bridge-add-name"]'), 'good_name-1')
    expect(q('[role="alert"]')).toBeNull()
    expect((q('[data-testid="bridge-add-submit"]') as HTMLButtonElement).disabled).toBe(false)
  })

  it('posts {name, note, scopes}, then shows the key once and one claude mcp add line per scope', async () => {
    vi.mocked(client.apiPost).mockResolvedValue(minted('phone', ['agents', 'memory', 'admin']))
    const consoleSpies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) =>
      vi.spyOn(console, m).mockImplementation(() => {}),
    )
    await mount()
    await click(q('[data-testid="bridge-add"]'))
    await type(q('[data-testid="bridge-add-name"]'), 'phone')
    await type(q('[data-testid="bridge-add-note"]'), ' my phone ')
    await click(q('input[type="checkbox"][data-scope="admin"]'))
    await click(q('[data-testid="bridge-add-submit"]'))

    expect(client.apiPost).toHaveBeenCalledWith('/api/bridge/clients', {
      name: 'phone',
      note: 'my phone',
      scopes: ['agents', 'memory', 'admin'],
    })

    // the form gave way to the result view
    expect(q('[data-testid="bridge-add-name"]')).toBeNull()
    expect(q('[data-testid="bridge-result"]')).not.toBeNull()
    expect(qa('[data-testid="bridge-key"]')).toHaveLength(1)
    expect(q('[data-testid="bridge-key"]')!.textContent).toBe(KEY)
    expect(text()).toContain('Shown once — hal0 stores only a hash.')
    // the lines carry the key too (they are what gets pasted); there is one per server reached
    const lines = qa('[data-testid^="bridge-add-line-"]')
    expect(lines.map((l) => l.getAttribute('data-testid'))).toEqual([
      'bridge-add-line-hal0-agents',
      'bridge-add-line-hal0-memory',
    ])
    expect(lines[0].textContent).toMatch(/^claude mcp add --transport http hal0-agents http:\/\/hal0\.lan\/mcp\/agents\/mcp/)
    expect(occurrences(text(), KEY)).toBe(1 + lines.length)
    expect(byText('button', 'Copy key')).not.toBeNull()
    expect(byText('button', 'Copy hal0-agents')).not.toBeNull()
    expect(byText('button', 'Copy hal0-memory')).not.toBeNull()

    // Copy hands the clipboard the line, and says nothing of the key in the toast
    await click(byText('button', 'Copy hal0-memory'))
    expect(w.__hal0CopyText).toHaveBeenCalledWith(LINES[1].claude_mcp_add)

    // the result view closes only on Done: Escape and the backdrop leave it
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    })
    expect(q('[data-testid="bridge-key"]')).not.toBeNull()

    await click(q('[data-testid="bridge-result-done"]'))
    expect(q('[data-testid="bridge-result"]')).toBeNull()
    expect(text()).not.toContain(KEY)
    expect(text()).not.toContain('hal0bk_')

    // the key went nowhere else
    expect(JSON.stringify(toasts)).not.toContain(KEY)
    for (const spy of consoleSpies) expect(JSON.stringify(spy.mock.calls)).not.toContain(KEY)
  })

  it('shows the one-liner and turns Add off when the bridge is disabled', async () => {
    vi.mocked(client.apiPost).mockRejectedValue(
      new client.Hal0Error('The agent bridge is disabled in [bridge] config.', {
        code: 'bridge.disabled',
        status: 404,
      }),
    )
    await mount()
    expect(text()).not.toContain('Agent bridge disabled in [bridge] config')

    await click(q('[data-testid="bridge-add"]'))
    await type(q('[data-testid="bridge-add-name"]'), 'laptop2')
    await click(q('[data-testid="bridge-add-submit"]'))

    expect(text()).toContain('Agent bridge disabled in [bridge] config')
    expect(q('[data-testid="bridge-add-name"]')).toBeNull()
    expect((q('[data-testid="bridge-add"]') as HTMLButtonElement).disabled).toBe(true)
    // the list still renders: listing works while the bridge is off
    expect(q('[data-testid="bridge-client-laptop"]')).not.toBeNull()
  })

  it('keeps the modal and shows the server\'s message on a refusal that is not disabled', async () => {
    vi.mocked(client.apiPost).mockRejectedValue(
      new client.Hal0Error("a bridge client named 'laptop' already exists", {
        code: 'bridge.client_exists',
        status: 409,
      }),
    )
    await mount()
    await click(q('[data-testid="bridge-add"]'))
    await type(q('[data-testid="bridge-add-name"]'), 'laptop')
    await click(q('[data-testid="bridge-add-submit"]'))

    expect(q('[data-testid="bridge-add-name"]')).not.toBeNull()
    expect(q('[role="alert"]')!.textContent).toBe("a bridge client named 'laptop' already exists")
    expect(text()).not.toContain('Agent bridge disabled')
    expect((q('[data-testid="bridge-add"]') as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('row actions', () => {
  it('revokes only after a confirm, and a revoked row offers no actions', async () => {
    vi.mocked(client.apiDelete).mockResolvedValue({ client: { ...CLIENTS[0], revoked_at: NOW_S } })
    await mount()
    await click(q('[data-testid="bridge-client-laptop"] .mcprow-main'))
    await click(byText('[data-testid="bridge-client-laptop"] button', 'Revoke'))

    expect(client.apiDelete).not.toHaveBeenCalled()
    expect(text()).toContain('Revoke laptop?')
    await click(byText('.modal-foot button', 'Revoke'))

    expect(client.apiDelete).toHaveBeenCalledWith('/api/bridge/clients/laptop')
    expect(toasts.some((t) => String(t[0]).includes('Revoked laptop'))).toBe(true)

    // the already-revoked row has nothing to act on
    await click(q('[data-testid="bridge-client-ci-bot"] .mcprow-main'))
    expect(qa('[data-testid="bridge-client-ci-bot"] button')).toHaveLength(0)
  })

  it('rotates behind a confirm and ends in the same result view', async () => {
    vi.mocked(client.apiPost).mockResolvedValue(minted('laptop'))
    await mount()
    await click(q('[data-testid="bridge-client-laptop"] .mcprow-main'))
    await click(byText('[data-testid="bridge-client-laptop"] button', 'Rotate key'))

    expect(client.apiPost).not.toHaveBeenCalled()
    await click(byText('.modal-foot button', 'Rotate key'))

    expect(client.apiPost).toHaveBeenCalledWith('/api/bridge/clients/laptop/rotate')
    expect(qa('[data-testid="bridge-key"]')).toHaveLength(1)
    expect(text()).toContain('New key for laptop')
    expect(text()).toContain('Shown once — hal0 stores only a hash.')
    expect(qa('[data-testid^="bridge-add-line-"]')).toHaveLength(2)

    await click(q('[data-testid="bridge-result-done"]'))
    expect(text()).not.toContain(KEY)
  })

  it('edits scopes with PATCH, keeping agents on', async () => {
    vi.mocked(client.apiPatch).mockResolvedValue({ client: { ...CLIENTS[0], scopes: ['agents'] } })
    await mount()
    await click(q('[data-testid="bridge-client-laptop"] .mcprow-main'))
    await click(byText('[data-testid="bridge-client-laptop"] button', 'Scopes'))

    const box = (s: string) => q(`input[type="checkbox"][data-scope="${s}"]`) as HTMLInputElement
    expect(box('agents').checked && box('agents').disabled).toBe(true)
    expect(box('memory').checked).toBe(true)
    expect(box('admin').checked).toBe(true)
    // nothing changed yet
    expect((q('[data-testid="bridge-scopes-save"]') as HTMLButtonElement).disabled).toBe(true)

    await click(box('memory'))
    await click(box('admin'))
    await click(q('[data-testid="bridge-scopes-save"]'))

    expect(client.apiPatch).toHaveBeenCalledWith('/api/bridge/clients/laptop', { scopes: ['agents'] })
    expect(q('[data-testid="bridge-scopes-save"]')).toBeNull()
  })
})
