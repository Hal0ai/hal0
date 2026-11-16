// @vitest-environment happy-dom
// Models → FLM tab: the catalogue as rows (name, size, audio, installed), Download on a model that
// is not installed, Remove (inline confirm, disabled while in use, inline errors) on an installed
// one, and the link back to the NPU lanes.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { box } = vi.hoisted(() => ({
  box: {
    start: vi.fn(),
    inFlight: false,
    catalog: undefined as unknown,
    mutate: vi.fn(),
    reset: vi.fn(),
    remove: {} as Record<string, unknown>,
  },
}))
vi.mock('@/api/hooks/useFlmCatalog', () => ({
  useFlmCatalog: () => ({ data: box.catalog, isLoading: false, isError: false }),
  useRemoveFlmModel: () => ({ mutate: box.mutate, reset: box.reset, isPending: false, isError: false, isSuccess: false, ...box.remove }),
}))
vi.mock('@/api/hooks/useModels', () => ({
  usePullJob: () => ({ start: box.start, inFlight: box.inFlight, modelId: null, pct: null, error: null }),
}))

const { FlmModelsTab } = await import('../FlmModelsTab')

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  box.start.mockReset()
  box.mutate.mockReset()
  box.remove = {}
  box.inFlight = false
  box.catalog = {
    probe_ok: true,
    models: [
      { model: 'gemma4-it:e4b', installed: true, capabilities: ['chat', 'stt'], family: 'gemma4', size_bytes: 4_000_000_000 },
      { model: 'qwen3.5:9b', installed: false, capabilities: ['chat'], family: 'qwen3.5', size_bytes: 6_400_000_000 },
      {
        model: 'llama3.2:1b',
        installed: true,
        capabilities: ['chat'],
        family: 'llama3.2',
        size_bytes: 1_300_000_000,
        in_use_by: [{ role: 'chat', slot: 'npu' }],
      },
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
  it('Remove asks inline, naming the space freed, then removes', () => {
    act(() => root.render(<FlmModelsTab />))
    expect(q('flm-remove-qwen3.5:9b')).toBeNull() // not installed: nothing to remove
    act(() => q('flm-remove-gemma4-it:e4b')!.click())
    expect(box.mutate).not.toHaveBeenCalled()
    expect(q('flm-remove-confirm-gemma4-it:e4b')?.textContent).toContain('free 4.0 GB')
    act(() => q('flm-remove-yes-gemma4-it:e4b')!.click())
    expect(box.mutate).toHaveBeenCalledWith('gemma4-it:e4b')
    expect(q('flm-remove-confirm-gemma4-it:e4b')).toBeNull()
  })
  it('Cancel leaves the model alone', () => {
    act(() => root.render(<FlmModelsTab />))
    act(() => q('flm-remove-gemma4-it:e4b')!.click())
    act(() => q('flm-remove-no-gemma4-it:e4b')!.click())
    expect(box.mutate).not.toHaveBeenCalled()
    expect(q('flm-remove-gemma4-it:e4b')).not.toBeNull()
  })
  it('a model in use cannot be removed and says why', () => {
    act(() => root.render(<FlmModelsTab />))
    const btn = q('flm-remove-llama3.2:1b') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(btn.title).toContain('chat on npu')
    expect(q('flm-state-llama3.2:1b')?.textContent).toBe('installed · in use')
  })
  it('a refused remove shows the error inline on its row', () => {
    box.remove = { isError: true, variables: 'gemma4-it:e4b', error: { message: 'a pull of gemma4-it:e4b is running' } }
    act(() => root.render(<FlmModelsTab />))
    expect(q('flm-remove-error-gemma4-it:e4b')?.textContent).toBe('a pull of gemma4-it:e4b is running')
    expect(q('flm-remove-error-llama3.2:1b')).toBeNull()
  })
  it('passes on flm warnings after a remove', () => {
    box.remove = { isSuccess: true, variables: 'gemma4-it:e4b', data: { warnings: ['Installed model x:y requires gemma4-it:e4b'] } }
    act(() => root.render(<FlmModelsTab />))
    expect(q('flm-remove-warning-gemma4-it:e4b')?.textContent).toContain('x:y')
  })
  it('says so when FLM is unreachable and the catalogue is empty', () => {
    box.catalog = { probe_ok: false, models: [] }
    act(() => root.render(<FlmModelsTab />))
    expect(q('flm-models-empty')).not.toBeNull()
  })
})
