// @vitest-environment happy-dom
// GGML slot form: create posts the body then /load; fixed knobs are never editable; delete is
// a two-click in-page confirm; edit sends only what changed; a taken name disables Create.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { box } = vi.hoisted(() => ({
  box: {
    creates: [] as Record<string, unknown>[],
    loads: [] as string[],
    edits: [] as { name: string; body: Record<string, unknown> }[],
    restarts: [] as string[],
    deletes: [] as string[],
    cfg: { model: { default: 'qwen3-4b' }, profile: 'xdna', xdna: { pmode: 'turbo', env: {}, host_xrt: null } } as Record<string, unknown>,
    createErr: null as Error | null,
    cfgLoading: false,
    fit: {} as Record<string, { xdna_path: string | null; reason: string }>,
    fitState: { isLoading: false, isError: false } as { isLoading: boolean; isError: boolean },
    refetches: 0,
    models: [] as Record<string, unknown>[],
  },
}))

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: [{ name: 'agent' }, { name: 'npu-gguf' }] }),
  useSlotConfig: () => ({ data: box.cfgLoading ? undefined : box.cfg }),
  useSlotCreate: () => ({ isPending: false, mutateAsync: async (b: Record<string, unknown>) => { if (box.createErr) throw box.createErr; box.creates.push(b); return {} } }),
  useSlotLoad: () => ({ isPending: false, mutateAsync: async (n: string) => { box.loads.push(n); return {} } }),
  useSlotEdit: () => ({ isPending: false, mutateAsync: async (a: { name: string; body: Record<string, unknown> }) => { box.edits.push(a); return {} } }),
  useSlotRestart: () => ({ isPending: false, mutateAsync: async (n: string) => { box.restarts.push(n); return {} } }),
  useSlotDelete: () => ({ isPending: false, mutateAsync: async (n: string) => { box.deletes.push(n); return {} } }),
}))
vi.mock('@/api/hooks/useModels', () => ({
  useModels: () => ({ data: box.models }),
}))
vi.mock('@/api/hooks/useXdnaFit', () => ({
  useXdnaFit: () => ({
    data: box.fitState.isLoading || box.fitState.isError ? undefined : box.fit,
    isLoading: box.fitState.isLoading,
    isError: box.fitState.isError,
    refetch: () => { box.refetches += 1 },
  }),
}))
vi.mock('@/api/hooks/useXdnaKnobs', () => ({
  useXdnaKnobs: () => ({
    data: {
      pmodes: ['balanced', 'turbo'],
      env: [
        { name: 'GGML_XDNA_HEAD', default: '1', doc: 'head mode', fixed: false },
        { name: 'GGML_XDNA_PGEMM_GLU_A', default: '1', doc: 'pinned', fixed: true },
      ],
      host_xrt: { doc: '/opt/xilinx/xrt' },
    },
  }),
}))
vi.mock('@/api/hooks/useProfiles', () => ({
  useProfiles: () => ({
    data: [
      { name: 'xdna', runner: 'xdna', runtime_family: 'llama-server', ctx_size: 8192 },
      { name: 'flm', runner: 'flm', runtime_family: 'flm' },
    ],
  }),
}))

const { GgmlSlotForm } = await import('../npu/GgmlSlotForm.jsx')

let host: HTMLDivElement
let root: Root
const q = (id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
const click = (id: string) => act(() => { q(id)!.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
const flush = () => act(async () => { await Promise.resolve() })
function typeInto(id: string, value: string) {
  const el = q(id) as HTMLInputElement
  act(() => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    set.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
function mount(props: Record<string, unknown>) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root.render(React.createElement(GgmlSlotForm, props as never)))
}

beforeEach(() => {
  box.creates.length = 0; box.loads.length = 0; box.edits.length = 0; box.restarts.length = 0; box.deletes.length = 0
  box.createErr = null
  box.cfgLoading = false
  box.fitState = { isLoading: false, isError: false }
  box.refetches = 0
  box.models = [
    { id: 'qwen3-4b', type: 'llm', installed: true, runtime: 'llama-server', labels: [] },
    { id: 'big-moe', type: 'llm', installed: true, runtime: 'llama-server', labels: [] },
  ]
  box.fit = { 'qwen3-4b': { xdna_path: 'fused', reason: 'ok' }, 'big-moe': { xdna_path: null, reason: 'not gguf' } }
})
afterEach(() => { act(() => root.unmount()); document.body.innerHTML = '' })

describe('create', () => {
  it('Create and start posts the body, then /load', async () => {
    const done = vi.fn()
    mount({ mode: 'create', onDone: done })
    expect((q('ggml-name') as HTMLInputElement).value).toBe('npu-gguf-2')
    click('ggml-model-qwen3-4b')
    click('ggml-ctx-65536')
    click('ggml-pmode-turbo')
    await act(async () => { q('ggml-create-start')!.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await flush()
    expect(box.creates).toEqual([
      {
        name: 'npu-gguf-2', type: 'llm', runtime: 'container', binary: 'xdna', device: 'npu',
        model: 'qwen3-4b', profile: 'xdna', autoload: false,
        xdna: { pmode: 'turbo', env: {}, host_xrt: null }, tune: { ctx_size: 65536 },
      },
    ])
    expect(box.loads).toEqual(['npu-gguf-2'])
    expect(done).toHaveBeenCalled()
  })

  it('Create (no start) does not load', async () => {
    mount({ mode: 'create', initialModel: 'qwen3-4b', onDone: () => {} })
    await act(async () => { q('ggml-create')!.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    expect(box.creates).toHaveLength(1)
    expect(box.loads).toEqual([])
  })

  it('a name used by any slot disables Create with the reason (Review Focus 4)', () => {
    mount({ mode: 'create', initialModel: 'qwen3-4b', onDone: () => {} })
    typeInto('ggml-name', 'agent')
    expect(q('ggml-name-error')!.textContent).toBe('name already in use')
    expect((q('ggml-create') as HTMLButtonElement).disabled).toBe(true)
    expect((q('ggml-create-start') as HTMLButtonElement).disabled).toBe(true)
    typeInto('ggml-name', 'Bad_Name')
    expect(q('ggml-name-error')!.textContent).toBe('lowercase + dashes only')
    expect((q('ggml-create') as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows an API error inline and stays open', async () => {
    box.createErr = new Error('slot.name_taken')
    const done = vi.fn()
    mount({ mode: 'create', initialModel: 'qwen3-4b', onDone: done })
    await act(async () => { q('ggml-create')!.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await flush()
    expect(q('ggml-error')!.textContent).toBe('slot.name_taken')
    expect(done).not.toHaveBeenCalled()
  })

  it('lists fit chips per model and a context placeholder from the profile', () => {
    mount({ mode: 'create', onDone: () => {} })
    expect(q('ggml-model-qwen3-4b')!.querySelector('[data-fit]')!.getAttribute('data-fit')).toBe('fused')
    expect(q('ggml-model-big-moe')!.querySelector('[data-fit]')!.getAttribute('data-fit')).toBe('none')
    expect((q('ggml-ctx-custom') as HTMLInputElement).placeholder).toBe('8,192 from xdna')
    expect(q('ggml-ctx-row')!.textContent).toContain('Hermes needs at least 64K')
  })

  it('rejects an out-of-range custom context', () => {
    mount({ mode: 'create', initialModel: 'qwen3-4b', onDone: () => {} })
    typeInto('ggml-ctx-custom', '64')
    expect(q('ggml-ctx-error')).not.toBeNull()
    expect((q('ggml-create') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('knobs', () => {
  it('never offers GGML_XDNA_PGEMM_GLU_A as an editable input', () => {
    mount({ mode: 'create', initialModel: 'qwen3-4b', onDone: () => {} })
    expect(q('ggml-fixed-GGML_XDNA_PGEMM_GLU_A')).not.toBeNull()
    expect(host.querySelector('#ggml-env-in-GGML_XDNA_PGEMM_GLU_A')).toBeNull()
    expect(host.querySelector('[data-testid="ggml-env-GGML_XDNA_PGEMM_GLU_A"]')).toBeNull()
    expect(host.querySelector('#ggml-env-in-GGML_XDNA_HEAD')).not.toBeNull()
  })

  it('rejects a relative host_xrt path', () => {
    mount({ mode: 'create', initialModel: 'qwen3-4b', onDone: () => {} })
    typeInto('ggml-host-xrt', 'opt/xrt')
    expect(q('ggml-host-xrt-error')!.textContent).toBe('host_xrt must be an absolute path')
    expect((q('ggml-create') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('edit', () => {
  const slot = { name: 'npu-gguf', port: 8093, binary: 'xdna' }

  it('sends only the changed key, then restarts for Save and restart', async () => {
    mount({ mode: 'edit', slot, onDone: () => {} })
    expect((q('ggml-save') as HTMLButtonElement).disabled).toBe(true)
    click('ggml-ctx-32768')
    await act(async () => { q('ggml-save-restart')!.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await flush()
    expect(box.edits).toEqual([{ name: 'npu-gguf', body: { tune: { ctx_size: 32768 } } }])
    expect(box.restarts).toEqual(['npu-gguf'])
    expect(q('ggml-port')!.textContent).toBe('8093')
  })

  it('Delete needs two clicks', async () => {
    const done = vi.fn()
    mount({ mode: 'edit', slot, onDone: done })
    click('ggml-delete')
    expect(box.deletes).toEqual([])
    expect(q('ggml-delete-confirm')).not.toBeNull()
    await act(async () => { q('ggml-delete-confirm')!.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await flush()
    expect(box.deletes).toEqual(['npu-gguf'])
    expect(done).toHaveBeenCalled()
  })

  it('Keep backs out of the delete confirm', () => {
    mount({ mode: 'edit', slot, onDone: () => {} })
    click('ggml-delete')
    click('ggml-delete-cancel')
    expect(q('ggml-delete')).not.toBeNull()
    expect(box.deletes).toEqual([])
  })
})

describe('engine variant', () => {
  const slot = { name: 'npu-gguf', port: 8093, binary: 'xdna', state: 'ready' }

  it('renders only the engine section and saves just {xdna}, restarting a running slot', async () => {
    mount({ mode: 'edit', variant: 'engine', slot })
    for (const id of ['ggml-name', 'ggml-model-list', 'ggml-ctx-row', 'ggml-delete', 'ggml-save', 'ggml-profile'])
      expect(q(id)).toBeNull()
    expect(q('ggml-pmode-row')).not.toBeNull()
    expect(q('ggml-host-xrt')).not.toBeNull()
    click('ggml-pmode-balanced')
    await act(async () => { q('ggml-save-engine')!.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await flush()
    expect(box.edits).toEqual([{ name: 'npu-gguf', body: { xdna: { pmode: 'balanced', env: {}, host_xrt: null } } }])
    expect(box.restarts).toEqual(['npu-gguf'])
  })

  it('does not restart a stopped slot', async () => {
    mount({ mode: 'edit', variant: 'engine', slot: { ...slot, state: 'stopped' } })
    click('ggml-pmode-balanced')
    await act(async () => { q('ggml-save-engine')!.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await flush()
    expect(box.edits).toHaveLength(1)
    expect(box.restarts).toEqual([])
  })
})

describe('per-op requantize hint', () => {
  it('shows the command for a qwen35 model whose only gap is ssm_out', () => {
    box.fit = { 'qwen3-4b': { xdna_path: 'per-op', reason: 'blk.0.ssm_out.weight is Q8_0, not Q5_K' } }
    mount({ mode: 'create', initialModel: 'qwen3-4b', onDone: () => {} })
    expect(q('ggml-requant-hint')!.textContent).toContain('Q8_0')
    expect(q('ggml-requant-cmd')!.textContent).toBe('llama-quantize --tensor-type ssm_out=q5_K <in> <out> Q4_K_M')
    expect(q('ggml-requant-copy')).not.toBeNull()
  })

  it('shows only the reason for a per-op lfm2 model', () => {
    box.fit = { 'qwen3-4b': { xdna_path: 'per-op', reason: "architecture 'lfm2' is not 'qwen35'" } }
    mount({ mode: 'create', initialModel: 'qwen3-4b', onDone: () => {} })
    expect(q('ggml-requant-hint')).toBeNull()
    expect(q('ggml-perop-reason')!.textContent).toContain('lfm2')
  })

  it('shows it in the engine variant and survives a rejected clipboard write', async () => {
    box.fit = { 'qwen3-4b': { xdna_path: 'per-op', reason: 'blk.0.ssm_out.weight is Q6_K, not Q5_K' } }
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: () => Promise.reject(new Error('denied')) } })
    mount({ mode: 'edit', variant: 'engine', slot: { name: 'npu-gguf', state: 'ready' } })
    expect(q('ggml-requant-hint')).not.toBeNull()
    await act(async () => { q('ggml-requant-copy')!.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await flush()
    expect(q('ggml-requant-hint')).not.toBeNull()
  })
})

describe('fit fallback and fixed knobs in the form', () => {
  it('falls back to the slot payload fit when the fit map misses the model', () => {
    box.fit = {}
    mount({ mode: 'edit', variant: 'engine', slot: { name: 'npu-gguf', state: 'ready', xdna_path: 'per-op', xdna_reason: 'blk.0.ssm_out.weight is Q8_0, not Q5_K' } })
    expect(q('ggml-requant-hint')).not.toBeNull()
  })

  it('does not re-send a hand-edited GLU_A on a pmode change', async () => {
    box.cfg = { model: { default: 'qwen3-4b' }, profile: 'xdna', xdna: { pmode: 'turbo', env: { GGML_XDNA_PGEMM_GLU_A: '1', GGML_XDNA_HEAD: '1' }, host_xrt: null } }
    mount({ mode: 'edit', variant: 'engine', slot: { name: 'npu-gguf', state: 'stopped' } })
    click('ggml-pmode-balanced')
    await act(async () => { q('ggml-save-engine')!.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    await flush()
    expect(box.edits[0].body).toEqual({ xdna: { pmode: 'balanced', env: { GGML_XDNA_HEAD: '1' }, host_xrt: null } })
    box.cfg = { model: { default: 'qwen3-4b' }, profile: 'xdna', xdna: { pmode: 'turbo', env: {}, host_xrt: null } }
  })
})

describe('config still loading', () => {
  it('cannot save before the slot config resolves (engine and full)', async () => {
    box.cfgLoading = true
    mount({ mode: 'edit', variant: 'engine', slot: { name: 'npu-gguf', state: 'ready' } })
    expect(q('ggml-loading')).not.toBeNull()
    click('ggml-pmode-balanced')
    expect((q('ggml-save-engine') as HTMLButtonElement).disabled).toBe(true)
    await act(async () => { q('ggml-save-engine')!.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    expect(box.edits).toEqual([])
    act(() => root.unmount())
    mount({ mode: 'edit', slot: { name: 'npu-gguf', state: 'ready' }, onDone: () => {} })
    click('ggml-ctx-32768')
    expect((q('ggml-save') as HTMLButtonElement).disabled).toBe(true)
    expect((q('ggml-save-restart') as HTMLButtonElement).disabled).toBe(true)
    expect(box.edits).toEqual([])
  })
})

describe('model list follows the fit map (#3647)', () => {
  it('offers exactly the fit map keys: a registry llm row outside it has no button, a fit key the registry lacks has one', () => {
    box.models = [
      ...box.models,
      { id: 'orphan-llm', type: 'llm', installed: true, runtime: 'llama-server', labels: [] },
    ]
    box.fit = { ...box.fit, 'hub-blob-sha': { xdna_path: 'fused', reason: '' } }
    mount({ mode: 'create', onDone: () => {} })
    expect(q('ggml-model-orphan-llm')).toBeNull()
    expect(q('ggml-model-hub-blob-sha')).not.toBeNull()
    expect(q('ggml-model-qwen3-4b')).not.toBeNull()
  })
})

describe('fit query states', () => {
  it('shows a loading hint, not the empty message, while the fit map loads', () => {
    box.fitState = { isLoading: true, isError: false }
    mount({ mode: 'create', onDone: () => {} })
    expect(q('ggml-models-loading')).not.toBeNull()
    expect(q('ggml-models-error')).toBeNull()
    expect(q('ggml-model-list')!.textContent).not.toContain('No GGUF chat models installed')
  })

  it('shows an error hint with retry when the fit map fails', () => {
    box.fitState = { isLoading: false, isError: true }
    mount({ mode: 'create', onDone: () => {} })
    expect(q('ggml-models-error')).not.toBeNull()
    expect(q('ggml-model-list')!.textContent).not.toContain('No GGUF chat models installed')
    click('ggml-models-retry')
    expect(box.refetches).toBe(1)
  })

  it('still says none installed when the fit map is loaded and empty', () => {
    box.fit = {}
    mount({ mode: 'create', onDone: () => {} })
    expect(q('ggml-model-list')!.textContent).toContain('No GGUF chat models installed')
  })
})
