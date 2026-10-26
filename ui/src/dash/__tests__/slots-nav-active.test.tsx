// @vitest-environment happy-dom
//
// The sidebar's active row for the Slots routes. `#slots/image/generate` has the param
// `image/generate`: the tab is the first segment, so Slots and its Image Gen child are active,
// and Inference, the default tab and the owner of every param that is not a tab (a slot name),
// is not.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type Json = Record<string, unknown>

vi.mock('@/api/hooks/useSlots', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useSlots: () => ({ data: [] }),
}))
vi.mock('@/api/hooks/useModels', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useModels: () => ({ data: [] }),
}))
vi.mock('@/api/hooks/useMemory', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useMemoryEnabled: () => true,
}))
vi.mock('@/api/hooks/useConfigUrls', () => ({ useConfigUrls: () => ({ data: undefined }) }))

await import('../chrome.jsx')
const { Sidebar } = window as unknown as { Sidebar: React.ComponentType<Json> }

let host: HTMLDivElement
let root: Root

function render(route: string, param: string | null) {
  act(() => {
    root.render(React.createElement(Sidebar, { route, param, onGo: () => {} }))
  })
}
const row = (id: string) => host.querySelector<HTMLElement>(`[data-testid="nav-${id}"]`)!
const active = (id: string) => row(id).getAttribute('aria-current') === 'page'

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('sidebar', () => {
  it('sidebar marks Slots active on #slots/image/generate', () => {
    render('slots', 'image/generate')
    expect(active('slots')).toBe(true)
    expect(active('slots-image')).toBe(true)
    expect(active('slots-inference')).toBe(false)
    expect(active('slots-endpoints')).toBe(false)
  })

  it.each(['image', 'image/models', 'image/engine'])('marks Image Gen on #slots/%s', (param) => {
    render('slots', param)
    expect(active('slots-image')).toBe(true)
    expect(active('slots-inference')).toBe(false)
  })

  it('still marks the other tabs, Inference for a bare #slots and for a slot name', () => {
    render('slots', 'endpoints')
    expect(active('slots-endpoints')).toBe(true)
    expect(active('slots-inference')).toBe(false)
    render('slots', null)
    expect(active('slots-inference')).toBe(true)
    render('slots', 'brain')
    expect(active('slots-inference')).toBe(true)
    expect(active('slots-image')).toBe(false)
  })
})
