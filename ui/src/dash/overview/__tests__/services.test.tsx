// @vitest-environment happy-dom
//
// Overview v2 Services rows: the four companions, OpenWebUI's wired chips, row navigation, and the
// ComfyUI editor arrow under the on-box / off-box rule (ported from services-comfyui-link.test.tsx's
// "Overview Services widget" block).
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { OFF_BOX_NOTE } from '../../image/editor-reach'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

type Json = Record<string, unknown>
const { box } = vi.hoisted(() => ({
  box: { services: [] as Json[], pending: false, urls: undefined as Json | undefined },
}))

vi.mock('@/api/hooks/useServices', () => ({
  useServices: () => ({ services: box.services, mdns: null, pending: box.pending }),
}))
vi.mock('@/api/hooks/useConfigUrls', () => ({ useConfigUrls: () => ({ data: box.urls }) }))

await import('../shell.jsx')
await import('../services.jsx')
const OvServices = (window as any).OvServices as React.ComponentType<{ onGo?: (id: string) => void }>

const svc = (id: string, over: Json = {}) => ({
  id,
  name: id,
  description: '',
  managed: true,
  unit: `hal0-${id}.service`,
  unit_state: { active_state: 'active', sub_state: 'running', unit_file_state: 'enabled', since: null },
  up: true,
  detail: 'reachable',
  stat: null,
  url: `http://hal0.lan:${id.length}000`,
  mdns_url: null,
  loopback_port: null,
  actions: ['restart'],
  mdns_capable: false,
  hints: [],
  ...over,
})

let host: HTMLDivElement
let root: Root
let opened: unknown[][]
let went: string[]

function visit(hostname: string) {
  ;(window as any).happyDOM.setURL(`http://${hostname}:9120/#overview`)
}
function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  act(() => {
    root.render(
      React.createElement(QueryClientProvider, { client: qc }, React.createElement(OvServices, { onGo: (id: string) => went.push(id) })),
    )
  })
}
const click = (el: Element) =>
  act(() => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
const comfyOpen = () => host.querySelector<HTMLElement>('[data-testid="ov-svc-open-comfyui"]')!

beforeEach(() => {
  visit('localhost')
  box.pending = false
  box.services = [
    svc('openwebui', { name: 'OpenWebUI', url: 'http://hal0.lan:3001', wired: { chat: true, voice: true, documents: false, images: false, web_search: false } }),
    svc('comfyui', { name: 'ComfyUI', url: 'http://hal0.lan:8188' }),
    svc('hermes', { name: 'Hermes' }),
    svc('n8n', { name: 'n8n', up: false, managed: false, detail: 'not installed' }),
    svc('extra', { name: 'extra' }),
  ]
  box.urls = { comfyui: 'http://localhost:8188', comfyui_public: false }
  opened = []
  went = []
  vi.spyOn(window, 'open').mockImplementation((...args: unknown[]) => {
    opened.push(args)
    return null
  })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.restoreAllMocks()
})

describe('Overview v2 services rows', () => {
  it('renders the four companions in order, nothing more', () => {
    mount()
    const names = [...host.querySelectorAll('.ov-svc .ov-nm')].map((n) => n.textContent)
    expect(names).toEqual(['openwebui', 'comfyui', 'hermes', 'n8n'])
  })

  it('shows OpenWebUI wired chips for true keys only', () => {
    mount()
    const row = host.querySelector('.ov-svc')!
    expect([...row.querySelectorAll('.ov-chip')].map((c) => c.textContent)).toEqual(['chat', 'voice'])
  })

  it('shows no chips for a service without wired', () => {
    mount()
    const hermes = host.querySelectorAll('.ov-svc')[2]
    expect(hermes.querySelectorAll('.ov-chip')).toHaveLength(0)
  })

  it('clicking a row goes to services', () => {
    mount()
    click(host.querySelectorAll('.ov-svc')[2])
    expect(went).toEqual(['services'])
  })

  it('Enter on a focused open arrow does not navigate; Enter on the row does', () => {
    mount()
    const arrow = host.querySelector('[data-testid="ov-svc-open-openwebui"]')!
    act(() => {
      arrow.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect(went).toEqual([])
    act(() => {
      host.querySelectorAll('.ov-svc')[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect(went).toEqual(['services'])
  })

  it('says the source is pending while services have not loaded', () => {
    box.pending = true
    box.services = []
    mount()
    expect(host.textContent).toContain('source pending')
  })

  it('opens the ComfyUI editor from a page on the box', () => {
    mount()
    expect(comfyOpen().getAttribute('aria-disabled')).toBeNull()
    click(comfyOpen())
    expect(opened).toEqual([['http://localhost:8188', '_blank', 'noopener']])
    expect(went).toEqual([])
  })

  it('shows the ComfyUI arrow disabled off the box with OFF_BOX_NOTE, and it opens nothing', () => {
    visit('hal0.lan')
    box.urls = { comfyui: 'http://hal0.lan:8188', comfyui_public: false }
    mount()
    expect(comfyOpen().getAttribute('aria-disabled')).toBe('true')
    expect(comfyOpen().getAttribute('title')).toBe(OFF_BOX_NOTE)
    click(comfyOpen())
    expect(opened).toEqual([])
    expect(went).toEqual([])
  })

  it('still opens the other services from anywhere', () => {
    visit('hal0.lan')
    box.urls = { comfyui: 'http://hal0.lan:8188', comfyui_public: false }
    mount()
    click(host.querySelector('[data-testid="ov-svc-open-openwebui"]')!)
    expect(opened).toEqual([['http://hal0.lan:3001', '_blank', 'noopener']])
    expect(went).toEqual([])
  })
})
