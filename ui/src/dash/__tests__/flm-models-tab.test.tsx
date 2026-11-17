// @vitest-environment happy-dom
// Models → FLM tab on the shared catalog shell: the catalogue as sectioned rows (name, size, audio,
// status), the toolbar's filters, and the detail card of the selected model with Download on one
// that is not installed and Remove (inline confirm, disabled while in use, inline errors) on an
// installed one, plus the link back to the NPU lanes.
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
    pullModel: null as string | null,
    pct: null as number | null,
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
  usePullJob: () => ({ start: box.start, inFlight: box.inFlight, modelId: box.pullModel, pct: box.pct, error: null }),
}))

// The Downloads pane has its own hooks and its own tests; here it is only a neighbour.
vi.mock('../models/DownloadsPane.jsx', () => ({ DownloadsPane: () => null }))

const { FlmModelsTab } = await import('../FlmModelsTab')

let host: HTMLDivElement
let root: Root
beforeEach(() => {
  box.start.mockReset()
  box.mutate.mockReset()
  box.remove = {}
  box.inFlight = false
  box.pullModel = null
  box.pct = null
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
const select = (id: string) => act(() => q(`flm-model-${id}`)!.click())
const rowIds = () =>
  [...host.querySelectorAll('[data-testid^="flm-model-"]')].map((el) => el.getAttribute('data-testid')!.slice('flm-model-'.length))

describe('FlmModelsTab', () => {
  it('lists every catalogue model with size, audio flag and status, in sections', () => {
    act(() => root.render(<FlmModelsTab />))
    expect(q('flm-state-gemma4-it:e4b')?.textContent).toBe('installed')
    expect(q('flm-size-gemma4-it:e4b')?.textContent).toBe('4.0 GB')
    expect(q('flm-model-gemma4-it:e4b')?.textContent).toContain('audio')
    expect(q('flm-state-qwen3.5:9b')?.textContent).toBe('not installed')
    expect(q('flm-model-qwen3.5:9b')?.textContent).not.toContain('audio')
    expect(q('flm-state-llama3.2:1b')?.textContent).toBe('in use · npu')
    expect(q('flm-models-lane-link')?.getAttribute('href')).toBe('#slots/npu/lanes')
    // In use first, then installed, then what can be downloaded.
    expect(rowIds()).toEqual(['llama3.2:1b', 'gemma4-it:e4b', 'qwen3.5:9b'])
    expect(q('mdl-section-inuse')?.textContent).toContain('In use · 1')
    expect(q('mdl-section-available')?.textContent).toContain('Not installed · 1')
  })
  it('opens on the first installed model and shows what it can serve', () => {
    act(() => root.render(<FlmModelsTab />))
    const detail = q('flm-detail')!
    expect(detail.querySelector('.nm')?.textContent).toBe('gemma4-it:e4b')
    expect(q('flm-detail-roles')?.textContent).toContain('Speech to textyes')
    expect(q('flm-detail-roles')?.textContent).toContain('Embeddingsno')
    expect(q('flm-download-gemma4-it:e4b')).toBeNull()
  })
  it('the State and Role chips narrow the list, and Clear restores it', () => {
    act(() => root.render(<FlmModelsTab />))
    act(() => q('mdl-filter-available')!.click())
    expect(rowIds()).toEqual(['qwen3.5:9b'])
    act(() => q('mdl-filter-inuse')!.click())
    expect(rowIds()).toEqual(['llama3.2:1b', 'qwen3.5:9b'])
    act(() => q('mdl-filter-stt')!.click())
    expect(rowIds()).toEqual([])
    expect(host.textContent).toContain('No models match')
    act(() => (host.querySelector('.mdl-clear') as HTMLElement).click())
    expect(rowIds()).toHaveLength(3)
  })
  it('the header\'s Download FLM model shows what is not installed', () => {
    act(() => root.render(<FlmModelsTab addSeq={0} />))
    expect(rowIds()).toHaveLength(3)
    act(() => root.render(<FlmModelsTab addSeq={1} />))
    expect(rowIds()).toEqual(['qwen3.5:9b'])
    expect(q('mdl-filter-available')?.getAttribute('aria-pressed')).toBe('true')
  })
  it('Download starts a pull for the selected model', () => {
    act(() => root.render(<FlmModelsTab />))
    select('qwen3.5:9b')
    expect(q('flm-download-qwen3.5:9b')?.textContent).toBe('Download · 6.4 GB')
    act(() => q('flm-download-qwen3.5:9b')!.click())
    expect(box.start).toHaveBeenCalledWith('qwen3.5:9b')
    expect(q('flm-remove-qwen3.5:9b')).toBeNull() // not installed: nothing to remove
  })
  it('a running pull shows on its row and its button', () => {
    box.inFlight = true
    box.pullModel = 'qwen3.5:9b'
    box.pct = 41.6
    act(() => root.render(<FlmModelsTab />))
    expect(q('flm-state-qwen3.5:9b')?.textContent).toBe('downloading 42%')
    select('qwen3.5:9b')
    const btn = q('flm-download-qwen3.5:9b') as HTMLButtonElement
    expect(btn.textContent).toBe('Downloading 42%')
    expect(btn.disabled).toBe(true)
  })
  it('Remove asks inline, naming the space freed, then removes', () => {
    act(() => root.render(<FlmModelsTab />))
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
  it('selecting another model drops a pending confirmation', () => {
    act(() => root.render(<FlmModelsTab />))
    act(() => q('flm-remove-gemma4-it:e4b')!.click())
    select('qwen3.5:9b')
    select('gemma4-it:e4b')
    expect(q('flm-remove-confirm-gemma4-it:e4b')).toBeNull()
    expect(q('flm-remove-gemma4-it:e4b')).not.toBeNull()
  })
  it('a model in use cannot be removed and says why', () => {
    act(() => root.render(<FlmModelsTab />))
    select('llama3.2:1b')
    const btn = q('flm-remove-llama3.2:1b') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(btn.title).toContain('chat on npu')
    expect(q('flm-remove-why-llama3.2:1b')?.textContent).toContain('chat on npu')
    expect(q('mdl-used-by')?.textContent).toContain('npu')
  })
  it('a refused remove shows the error on that model only', () => {
    box.remove = { isError: true, variables: 'gemma4-it:e4b', error: { message: 'a pull of gemma4-it:e4b is running' } }
    act(() => root.render(<FlmModelsTab />))
    expect(q('flm-remove-error-gemma4-it:e4b')?.textContent).toBe('a pull of gemma4-it:e4b is running')
    select('llama3.2:1b')
    expect(host.querySelector('[data-testid^="flm-remove-error-"]')).toBeNull()
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
    expect(host.textContent).toContain('Select a model')
  })
})
