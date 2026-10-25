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

vi.mock('@/api/hooks/useSlots', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  // One slot: with none, SlotsView shows its empty state instead of the tabs.
  useSlots: () => ({ data: [{ name: 'brain', kind: 'local', type: 'chat', state: 'running' }], isLoading: false }),
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

  it("main.jsx hands the route's second segment to SlotsView", () => {
    // parseRoute keeps everything after `slots/` as the param, and the Slots route passes it on.
    expect(mainSrc).toMatch(/param: rest\.join\("\/"\) \|\| null/)
    expect(mainSrc).toMatch(/<SlotsView[^>]*slotParam=\{param\}[^>]*slotSub=\{slotsSub\(param\)\}/s)
  })
})
