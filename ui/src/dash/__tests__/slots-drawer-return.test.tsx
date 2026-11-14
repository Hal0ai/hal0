// @vitest-environment happy-dom
//
// The drawer's return-to-NPU-tab record must not outlive the drawer: SlotsView drops it when the
// route leaves the drawer without onClose (flm -> npu) and when the view unmounts with the drawer
// open, and keeps it across re-renders (slots poll) while the drawer stays open.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
vi.mock('../inference-pane.jsx', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  InferencePane: () => null,
}))
vi.mock('../npu/NpuTabs', async () => {
  const R = await import('react')
  return { NpuTabs: () => R.createElement('div', { 'data-testid': 'stub-npu-pane' }) }
})
vi.mock('../image/ImageGenTabs', async () => {
  const R = await import('react')
  return {
    ImageGenTabs: (p: { sub?: string | null }) =>
      R.createElement('div', { 'data-testid': 'stub-image-pane', 'data-sub': p.sub ?? '' }),
  }
})

await import('../slots.jsx')
const { SlotsView } = window as unknown as { SlotsView: React.ComponentType<Json> }
const { setSlotDrawerReturn, takeSlotDrawerReturn } = await import('../slots-route')

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

const show = (param: string) =>
  React.createElement(
    QueryClientProvider,
    { client },
    React.createElement(SlotsView, { slotParam: param, slotSub: null, onGo: () => {} }),
  )

describe('slot drawer return target lifetime', () => {
  it('flm -> npu (leaving by hash) drops the record', () => {
    act(() => root.render(show('flm')))
    setSlotDrawerReturn('#slots/npu/lanes', 'flm')
    act(() => root.render(show('npu')))
    expect(takeSlotDrawerReturn('flm')).toBeNull()
  })
  it('a re-render with the drawer still open keeps the record', () => {
    act(() => root.render(show('flm')))
    setSlotDrawerReturn('#slots/npu/lanes', 'flm')
    act(() => root.render(show('flm')))
    expect(takeSlotDrawerReturn('flm')).toBe('#slots/npu/lanes')
  })
  it('unmounting with the drawer open drops the record', () => {
    act(() => root.render(show('flm')))
    setSlotDrawerReturn('#slots/npu/lanes', 'flm')
    act(() => root.unmount())
    expect(takeSlotDrawerReturn('flm')).toBeNull()
    root = createRoot(host) // afterEach unmounts again
  })
})
