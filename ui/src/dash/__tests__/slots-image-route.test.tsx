// @vitest-environment happy-dom
//
// `#slots/image/models` on its way to the Image Gen pane. main.jsx's parseRoute keeps the param
// `image/models` and hands the second segment down as `slotSub`; SlotsView takes the first as its
// tab and passes `slotSub` on to ImageGenTabs. main.jsx cannot be imported (it mounts the app at
// import), so its half is pinned from its source; SlotsView's half is rendered.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import mainSrc from '../main.jsx?raw'

;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
// What slots.jsx reads off the window: the icons, and the modals other modules publish.
const g = globalThis as unknown as Record<string, unknown>
g.Icons = new Proxy({}, { get: () => null })
for (const name of ['CreateSlotModal', 'EditSlotDrawer', 'SlotLogsDrawer', 'ModelDrawer']) g[name] = () => null

type Json = Record<string, unknown>

// What `/api/slots` answers: one slot by default, or none (a box with no slots yet), or not at
// all yet (loading). With none, SlotsView shows its empty state on the slot-centric tabs.
const box = vi.hoisted(() => ({
  slots: [{ name: 'brain', kind: 'local', type: 'chat', state: 'running' }] as unknown[] | undefined,
  isLoading: false,
}))
vi.mock('@/api/hooks/useSlots', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useSlots: () => ({ data: box.slots, isLoading: box.isLoading }),
}))
vi.mock('@/api/hooks/useModels', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useModels: () => ({ data: [] }),
}))
vi.mock('@/api/hooks/useComfyui', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useComfyui: () => ({ data: undefined }),
}))
vi.mock('../telemetry-header.jsx', () => ({ TelemetryHeader: () => null }))
vi.mock('../activity-log.jsx', () => ({ ActivityLog: () => null }))
vi.mock('../inference-pane.jsx', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  InferencePane: () => null,
}))
vi.mock('../image/ImageGenTabs', async () => {
  const R = await import('react')
  return {
    ImageGenTabs: (p: { sub?: string | null }) =>
      R.createElement('div', { 'data-testid': 'stub-image-pane', 'data-sub': p.sub ?? '' }),
  }
})

await import('../slots.jsx')
const { SlotsView } = window as unknown as { SlotsView: React.ComponentType<Json> }
const { slotsSub } = await import('../slots-route')

let host: HTMLDivElement
let root: Root
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

beforeEach(() => {
  box.slots = [{ name: 'brain', kind: 'local', type: 'chat', state: 'running' }]
  box.isLoading = false
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('slotSub reaches the Image Gen pane', () => {
  // What main.jsx does for the route: `slotParam={param}` and `slotSub={slotsSub(param)}`.
  const renderRoute = (param: string | null) =>
    act(() => {
      root.render(
        React.createElement(
          QueryClientProvider,
          { client },
          React.createElement(SlotsView, { slotParam: param, slotSub: slotsSub(param), onGo: () => {} }),
        ),
      )
    })
  const imageTab = () => host.querySelector<HTMLElement>('.slot-tab.comfy')!
  const pane = () => host.querySelector<HTMLElement>('[data-testid="stub-image-pane"]')

  it('#slots/image/models selects the Image Gen tab and tells the pane to open Models', () => {
    renderRoute('image/models')
    expect(imageTab().getAttribute('aria-selected')).toBe('true')
    expect(pane()!.getAttribute('data-sub')).toBe('models')
  })

  it('a bare #slots/image gives the pane no sub-tab', () => {
    renderRoute('image')
    expect(imageTab().getAttribute('aria-selected')).toBe('true')
    expect(pane()!.getAttribute('data-sub')).toBe('')
  })

  it('a later sub-tab in the route reaches the mounted pane', () => {
    renderRoute('image/generate')
    expect(pane()!.getAttribute('data-sub')).toBe('generate')
    renderRoute('image/engine')
    expect(imageTab().getAttribute('aria-selected')).toBe('true')
    expect(pane()!.getAttribute('data-sub')).toBe('engine')
  })

  it('another tab shows no Image Gen pane', () => {
    renderRoute('endpoints')
    expect(imageTab().getAttribute('aria-selected')).toBe('false')
    expect(pane()).toBeNull()
  })

  describe('on a box with no slots', () => {
    // The first-run state: the image catalog is where a box with no slots starts, so the pane must
    // be reachable, as Endpoints, Runner Images and Stacks are.
    it('still shows the Image Gen pane, not "No slots configured"', () => {
      box.slots = []
      renderRoute('image')
      expect(pane()).not.toBeNull()
      expect(imageTab().getAttribute('aria-selected')).toBe('true')
      expect(host.textContent).not.toContain('No slots configured')
    })

    it('hands a deep link through to the pane too', () => {
      box.slots = []
      renderRoute('image/models')
      expect(pane()!.getAttribute('data-sub')).toBe('models')
    })

    it('shows the pane, not the loading skeleton, while /api/slots loads', () => {
      box.slots = undefined
      box.isLoading = true
      renderRoute('image')
      expect(pane()).not.toBeNull()
      expect(host.textContent).not.toContain('Loading slots')
    })

    it('keeps "No slots configured" on the Inference tab', () => {
      box.slots = []
      renderRoute('inference')
      expect(host.textContent).toContain('No slots configured')
      expect(pane()).toBeNull()
    })
  })

  it("main.jsx hands the route's second segment to SlotsView", () => {
    // parseRoute keeps everything after `slots/` as the param, and the Slots route passes it on.
    expect(mainSrc).toMatch(/param: rest\.join\("\/"\) \|\| null/)
    expect(mainSrc).toMatch(/<SlotsView[^>]*slotParam=\{param\}[^>]*slotSub=\{slotsSub\(param\)\}/s)
  })
})
