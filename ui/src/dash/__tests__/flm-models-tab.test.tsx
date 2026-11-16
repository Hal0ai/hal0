// @vitest-environment happy-dom
// Models → FLM tab: the catalogue as rows (name, size, audio, installed), Download on a model that
// is not installed, and the link back to the NPU lanes.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { box } = vi.hoisted(() => ({
  box: { start: vi.fn(), inFlight: false, catalog: undefined as unknown },
}))
vi.mock('@/api/hooks/useFlmCatalog', () => ({
  useFlmCatalog: () => ({ data: box.catalog, isLoading: false, isError: false }),
}))
vi.mock('@/api/hooks/useModels', () => ({
  usePullJob: () => ({ start: box.start, inFlight: box.inFlight, modelId: null, pct: null, error: null }),
}))

const { FlmModelsTab } = await import('../FlmModelsTab')

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  box.start.mockReset()
  box.inFlight = false
  box.catalog = {
    probe_ok: true,
    models: [
      { model: 'gemma4-it:e4b', installed: true, capabilities: ['chat', 'stt'], family: 'gemma4', size_bytes: 4_000_000_000 },
      { model: 'qwen3.5:9b', installed: false, capabilities: ['chat'], family: 'qwen3.5', size_bytes: 6_400_000_000 },
    ],
  }
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})
const q = (id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null

describe('FlmModelsTab', () => {
  it('lists installed and downloadable models with size, audio flag and state', () => {
    act(() => root.render(<FlmModelsTab />))
    expect(q('flm-state-gemma4-it:e4b')?.textContent).toBe('installed')
    expect(q('flm-size-gemma4-it:e4b')?.textContent).toBe('4.0 GB')
    expect(q('flm-model-gemma4-it:e4b')?.textContent).toContain('audio')
    expect(q('flm-state-qwen3.5:9b')?.textContent).toBe('not installed')
    expect(q('flm-model-qwen3.5:9b')?.textContent).not.toContain('audio')
    expect(q('flm-download-gemma4-it:e4b')).toBeNull()
    expect(q('flm-models-lane-link')?.getAttribute('href')).toBe('#slots/npu/lanes')
  })
  it('Download starts a pull for the model', () => {
    act(() => root.render(<FlmModelsTab />))
    act(() => q('flm-download-qwen3.5:9b')!.click())
    expect(box.start).toHaveBeenCalledWith('qwen3.5:9b')
  })
  it('says so when FLM is unreachable and the catalogue is empty', () => {
    box.catalog = { probe_ok: false, models: [] }
    act(() => root.render(<FlmModelsTab />))
    expect(q('flm-models-empty')).not.toBeNull()
  })
})
