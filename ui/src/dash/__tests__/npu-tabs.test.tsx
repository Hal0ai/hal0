// @vitest-environment happy-dom
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { box } = vi.hoisted(() => ({
  box: { occ: undefined as unknown, hw: undefined as unknown },
}))
vi.mock('@/api/hooks/useNpuOccupancy', () => ({ useNpuOccupancy: () => ({ data: box.occ }) }))
vi.mock('@/api/hooks/useStatsHardware', () => ({ useStatsHardware: () => ({ data: box.hw }) }))

vi.mock('@/api/hooks/useSlots', () => {
  const m = () => ({ isPending: false, mutate: () => {}, mutateAsync: async () => {} })
  return { useSlotConfig: () => ({ data: undefined }), useSlotEdit: m, useSlotRestart: m, useSlotCreate: m, useSlotLoad: m, useSlotUnload: m }
})
vi.mock('@/api/hooks/useRunnerImages', () => ({ useRunnerImagePullJob: () => ({ start: async () => {}, state: 'idle', pct: null, error: null, inFlight: false }) }))
vi.mock('../npu/GgmlSlotDrawer.jsx', () => ({ GgmlSlotDrawer: () => null }))
vi.mock('@/api/hooks/useFlmCatalog', () => ({ useFlmCatalog: () => ({ data: { models: [], probe_ok: true } }) }))
vi.mock('@/api/hooks/useXdnaFit', () => ({ useXdnaFit: () => ({ data: {} }) }))
vi.mock('@/api/hooks/useModels', () => ({ useModels: () => ({ data: [] }), usePullJob: () => ({ start: async () => {}, state: 'idle', modelId: null, error: null, pct: null }) }))

const { NpuTabs } = await import('../npu/NpuTabs')

let host: HTMLDivElement
let root: Root
const render = (props: Record<string, unknown>) =>
  act(() => {
    root.render(<NpuTabs slots={[]} {...props} />)
  })
const present = { present: true, slots: [], npu_engines: null, npu_host: null }

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  box.occ = present
  box.hw = {}
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('NpuTabs', () => {
  it('renders three sub-tabs, Lanes by default', () => {
    render({})
    for (const id of ['lanes', 'models', 'engines']) expect(host.querySelector(`[data-testid="npu-tab-${id}"]`)).toBeTruthy()
    expect(host.querySelector('[data-testid="npu-tab-lanes"]')?.getAttribute('aria-selected')).toBe('true')
  })
  it('sub="models" opens Models', () => {
    render({ sub: 'models' })
    expect(host.querySelector('[data-testid="npu-tab-models"]')?.getAttribute('aria-selected')).toBe('true')
    expect((host.querySelector('#npu-panel-models') as HTMLElement).hidden).toBe(false)
  })
  it('clicking Engines rewrites the hash with replaceState', () => {
    const spy = vi.spyOn(window.history, 'replaceState')
    render({})
    act(() => {
      ;(host.querySelector('[data-testid="npu-tab-engines"]') as HTMLElement).click()
    })
    expect(spy).toHaveBeenCalledWith(window.history.state, '', '#slots/npu/engines')
    spy.mockRestore()
  })
  it('no NPU and no npu slot renders the B7 panel', () => {
    box.occ = { present: false, slots: [], npu_host: { device_node: null, driver_loaded: false, xdna_gen: null } }
    render({})
    expect(host.querySelector('[data-testid="npu-absent"]')).toBeTruthy()
    expect(host.querySelector('a[href="/docs/getting-started/drivers/"]')).toBeTruthy()
    expect(host.querySelector('[data-testid="npu-tab-lanes"]')).toBeNull()
  })
  it('npu slots with present:false keep the tab shell (degraded)', () => {
    box.occ = { present: false, slots: [] }
    render({ slots: [{ name: 'n', device_class: 'npu' }] })
    expect(host.querySelector('[data-testid="npu-tab-lanes"]')).toBeTruthy()
  })
})
