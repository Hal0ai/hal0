// @vitest-environment happy-dom
//
// Slots → Endpoints shows the local endpoints and one quiet line that sends remote
// providers to Models → Providers. The provider list itself no longer lives here.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const g = globalThis as unknown as Record<string, unknown>
g.Icons = new Proxy({}, { get: () => null })
for (const name of ['CreateSlotModal', 'EditSlotDrawer', 'SlotLogsDrawer', 'ModelDrawer']) g[name] = () => null

type Json = Record<string, unknown>
vi.mock('@/api/hooks/useSlots', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
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
vi.mock('../inference-pane.jsx', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  InferencePane: () => null,
}))

await import('../slots.jsx')
const { SlotsView } = window as unknown as { SlotsView: React.ComponentType<Json> }

let host: HTMLDivElement
let root: Root
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

beforeEach(() => {
  g.LocalEndpointsPanel = () => React.createElement('div', { 'data-testid': 'stub-local-endpoints' })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => {
    root.render(
      React.createElement(
        QueryClientProvider,
        { client },
        React.createElement(SlotsView, { slotParam: 'endpoints', slotSub: null, onGo: () => {} }),
      ),
    )
  })
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  delete g.LocalEndpointsPanel
})

describe('Slots → Endpoints', () => {
  it('shows the local endpoints and a quiet line that opens Providers', () => {
    expect(host.querySelector('[data-testid="stub-local-endpoints"]')).not.toBeNull()
    const note = host.querySelector('[data-testid="endpoints-providers-note"]')!
    expect(note.textContent).toContain('Remote providers moved to Models → Providers.')
    const link = note.querySelector('a')!
    expect(link.textContent).toBe('Open Providers')
    expect(link.getAttribute('href')).toBe('#models/providers')
  })

  it('no longer mounts a provider list', () => {
    expect(host.textContent).not.toContain('Upstream providers')
    expect(host.textContent).not.toContain('Add upstream')
    expect(host.querySelector('.uplist')).toBeNull()
  })
})
