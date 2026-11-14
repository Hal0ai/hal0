// @vitest-environment happy-dom
// FLM lane: staged changes, one write + one restart, drop-on-external-change, pull-before-write.
import React, { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { box } = vi.hoisted(() => ({
  box: {
    edits: [] as { name: string; body: Record<string, unknown> }[],
    restarts: [] as string[],
    creates: [] as Record<string, unknown>[],
    cfg: { npu: { chat: true, asr: false, embed: false }, model: { default: 'gemma4-it:e4b' } } as Record<string, unknown> | undefined,
    catalog: [
      { model: 'gemma4-it:e4b', installed: true, capabilities: ['chat', 'stt'] },
      { model: 'qwen3:8b', installed: false, capabilities: ['chat'] },
      { model: 'whisper-v3:turbo', installed: true, capabilities: ['stt'] },
      { model: 'embed-gemma:300m', installed: true, capabilities: ['embed'] },
    ],
    pull: { state: 'idle', modelId: null as string | null, error: null as { message: string } | null },
    pullStart: vi.fn(),
    toasts: [] as string[],
  },
}))

vi.mock('@/api/hooks/useSlots', () => ({
  useSlotConfig: () => ({ data: box.cfg as Record<string, unknown> | undefined }),
  useSlotEdit: () => ({
    isPending: false,
    mutateAsync: async (a: { name: string; body: Record<string, unknown> }) => {
      box.edits.push(a)
    },
  }),
  useSlotRestart: () => ({ isPending: false, mutate: (n: string) => box.restarts.push(n) }),
  useSlotCreate: () => ({ isPending: false, mutate: (b: Record<string, unknown>) => box.creates.push(b) }),
  useSlotLoad: () => ({ mutate: () => {} }),
  useSlotUnload: () => ({ mutate: () => {} }),
}))
vi.mock('@/api/hooks/useFlmCatalog', () => ({
  useFlmCatalog: () => ({ data: { models: box.catalog, probe_ok: true } }),
}))
vi.mock('@/api/hooks/useModels', () => ({
  usePullJob: () => ({ start: box.pullStart, ...box.pull, pct: null, reset: () => {} }),
}))

const { FlmLane } = await import('../npu/FlmLane')

const slots = [
  { name: 'flm', type: 'llm', device_class: 'npu', model_id: 'gemma4-it:e4b' },
  { name: 'flm-stt', type: 'transcription', device_class: 'npu' },
]
let host: HTMLDivElement
let root: Root
function Harness({ slotList = slots }: { slotList?: unknown[] }) {
  const [draft, setDraft] = useState({})
  return <FlmLane slots={slotList as never} draft={draft} setDraft={setDraft} />
}
const q = (id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
const click = (id: string) => act(() => { q(id)!.click() })
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve() })

beforeEach(() => {
  box.edits.length = 0
  box.restarts.length = 0
  box.creates.length = 0
  box.toasts.length = 0
  box.cfg = { npu: { chat: true, asr: false, embed: false }, model: { default: 'gemma4-it:e4b' } }
  box.pullStart.mockReset()
  box.pull = { state: 'idle', modelId: null, error: null }
  ;(window as unknown as { __hal0Toast: (m: string) => void }).__hal0Toast = (m) => { box.toasts.push(m) }
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})
const render = (el = <Harness />) => act(() => { root.render(el) })

describe('FlmLane', () => {
  it('two staged toggles then Apply → one PUT, one restart', async () => {
    render()
    click('flm-cap-asr')
    click('flm-cap-embed')
    expect(q('flm-applybar')?.textContent).toContain('2 changes staged')
    expect(box.edits).toHaveLength(0)
    click('flm-apply')
    await flush()
    expect(box.edits).toEqual([
      { name: 'flm', body: { npu: { chat: true, asr: true, embed: true }, model: { default: 'gemma4-it:e4b' } } },
    ])
    expect(box.restarts).toEqual(['flm'])
    expect(q('flm-applybar')).toBeNull()
  })

  it('Discard sends nothing and clears the bar', () => {
    render()
    click('flm-cap-asr')
    click('flm-discard')
    expect(box.edits).toHaveLength(0)
    expect(box.restarts).toHaveLength(0)
    expect(q('flm-applybar')).toBeNull()
  })

  it('anchor config changing while staged drops the draft with a toast', () => {
    render()
    click('flm-cap-asr')
    expect(q('flm-applybar')).toBeTruthy()
    box.cfg = { npu: { chat: true, asr: false, embed: true }, model: { default: 'gemma4-it:e4b' } }
    render()
    expect(q('flm-applybar')).toBeNull()
    expect(box.toasts).toContain('FLM settings changed elsewhere, staged changes dropped')
    expect(box.edits).toHaveLength(0)
  })

  it('uninstalled model whose pull fails → no PUT, no restart, error shown, bar kept', async () => {
    box.pullStart.mockRejectedValue(new Error('download exploded'))
    render()
    act(() => {
      const sel = q('flm-chat-model') as unknown as HTMLSelectElement
      const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
      set.call(sel, 'qwen3:8b')
      sel.dispatchEvent(new Event('change', { bubbles: true }))
    })
    click('flm-apply')
    await flush()
    expect(box.pullStart).toHaveBeenCalledWith('qwen3:8b')
    expect(box.edits).toHaveLength(0)
    expect(box.restarts).toHaveLength(0)
    expect(q('flm-error')?.textContent).toContain('download exploded')
    expect(q('flm-applybar')).toBeTruthy()
  })

  const pickQwen = () =>
    act(() => {
      const sel = q('flm-chat-model') as unknown as HTMLSelectElement
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(sel, 'qwen3:8b')
      sel.dispatchEvent(new Event('change', { bubbles: true }))
    })

  it('pull completes → one PUT, one restart, draft cleared', async () => {
    box.pullStart.mockResolvedValue({})
    render()
    pickQwen()
    click('flm-apply')
    await flush()
    expect(box.edits).toHaveLength(0)
    box.pull = { state: 'running', modelId: 'qwen3:8b', error: null }
    render()
    expect(box.edits).toHaveLength(0)
    box.pull = { state: 'completed', modelId: 'qwen3:8b', error: null }
    render()
    await flush()
    expect(box.edits).toEqual([
      { name: 'flm', body: { npu: { chat: true, asr: false, embed: false }, model: { default: 'qwen3:8b' } } },
    ])
    expect(box.restarts).toEqual(['flm'])
    expect(q('flm-applybar')).toBeNull()
  })

  it('pull state failed → no PUT, error shown, draft kept', async () => {
    box.pullStart.mockResolvedValue({})
    render()
    pickQwen()
    click('flm-apply')
    await flush()
    box.pull = { state: 'failed', modelId: 'qwen3:8b', error: { message: 'disk full' } }
    render()
    await flush()
    expect(box.edits).toHaveLength(0)
    expect(q('flm-error')?.textContent).toContain('disk full')
    expect(q('flm-applybar')).toBeTruthy()
  })

  it('config changing during a download → no PUT, toast, draft dropped', async () => {
    box.pullStart.mockResolvedValue({})
    render()
    pickQwen()
    click('flm-apply')
    await flush()
    box.pull = { state: 'running', modelId: 'qwen3:8b', error: null }
    render()
    box.cfg = { npu: { chat: true, asr: true, embed: false }, model: { default: 'gemma4-it:e4b' } }
    render()
    await flush()
    expect(box.toasts).toContain('FLM settings changed elsewhere, staged changes dropped')
    box.pull = { state: 'completed', modelId: 'qwen3:8b', error: null }
    render()
    await flush()
    expect(box.edits).toHaveLength(0)
    expect(box.restarts).toHaveLength(0)
    expect(q('flm-applybar')).toBeNull()
    expect(q('flm-error')).toBeNull()
  })

  it('config still loading → controls disabled, no false drop when it arrives', () => {
    box.cfg = undefined
    render()
    expect((q('flm-cap-asr') as HTMLButtonElement).disabled).toBe(true)
    box.cfg = { npu: { chat: true, asr: false, embed: false }, model: { default: 'gemma4-it:e4b' } }
    render()
    click('flm-cap-asr')
    expect(q('flm-applybar')).toBeTruthy()
    expect(box.toasts).toHaveLength(0)
  })

  it('no anchor → Create FLM slot button posts the flm profile', () => {
    render(<Harness slotList={[]} />)
    click('flm-create')
    expect(box.creates).toEqual([{ name: 'flm', type: 'llm', runtime: 'container', device: 'npu', profile: 'flm' }])
  })

  it('create uses name npu when flm is taken (by a non-NPU slot)', () => {
    render(<Harness slotList={[{ name: 'flm', type: 'llm', device_class: 'gpu' }]} />)
    click('flm-create')
    expect(box.creates[0]).toMatchObject({ name: 'npu' })
  })

  it('STT and Embed show the bundled model text', () => {
    render()
    expect(q('flm-row-asr')?.textContent).toContain('whisper-v3:turbo')
    expect(q('flm-row-asr')?.textContent).not.toContain('gemma4-it:e4b')
    expect(q('flm-row-embed')?.textContent).toContain('embed-gemma:300m')
  })
})
