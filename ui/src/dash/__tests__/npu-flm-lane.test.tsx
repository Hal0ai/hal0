// @vitest-environment happy-dom
// FLM lane: one card per role; a toggle or model pick applies at once (confirm while FLM runs),
// one write + one restart/start/stop, pull-before-write, abandon a download on external change.
import React, { act } from 'react'
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
    loads: [] as string[],
    unloads: [] as string[],
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
  useSlotRestart: () => ({ isPending: false, mutate: (n: string) => box.restarts.push(n), mutateAsync: async (n: string) => { box.restarts.push(n); return {} } }),
  useSlotCreate: () => ({ isPending: false, mutate: (b: Record<string, unknown>) => box.creates.push(b) }),
  useSlotLoad: () => ({ mutate: (n: string) => box.loads.push(n), mutateAsync: async (n: string) => { box.loads.push(n); return {} } }),
  useSlotUnload: () => ({ mutate: (n: string) => box.unloads.push(n) }),
}))
vi.mock('@/api/hooks/useFlmCatalog', () => ({
  useFlmCatalog: () => ({ data: { models: box.catalog, probe_ok: true } }),
}))
vi.mock('@/api/hooks/useModels', () => ({
  usePullJob: () => ({ start: box.pullStart, ...box.pull, pct: null, reset: () => {} }),
}))

const { FlmLane } = await import('../npu/FlmLane')

const running = [
  { name: 'flm', type: 'llm', device_class: 'npu', model_id: 'gemma4-it:e4b', container_status: 'running', container_health: true },
  { name: 'flm-stt', type: 'transcription', device_class: 'npu' },
]
const stopped = [{ name: 'flm', type: 'llm', device_class: 'npu', model_id: 'gemma4-it:e4b', container_status: 'stopped' }]
let host: HTMLDivElement
let root: Root
const q = (id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
const click = (id: string) => act(() => { q(id)!.click() })
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() })
// The confirm is a portalled Modal: look in the whole document.
const confirmText = () => document.querySelector('[data-testid="flm-confirm"]')?.textContent ?? null
const pressButton = (label: string) => {
  const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent?.trim() === label)
  if (!b) throw new Error(`no button "${label}"`)
  act(() => { b.click() })
}

beforeEach(() => {
  for (const k of ['edits', 'restarts', 'loads', 'unloads', 'creates', 'toasts'] as const) box[k].length = 0
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
const render = (slotList: unknown[] = running) => act(() => { root.render(<FlmLane slots={slotList as never} />) })

// The model dropdown is the shared RichSelect: open it, then click an option.
const pick = (id: string) => {
  act(() => { q('flm-chat-model')!.click() })
  act(() => { (host.querySelector(`[data-option-id="${id}"]`) as HTMLElement).click() })
}

describe('FlmLane role toggles', () => {
  it('running FLM: a toggle asks first, naming the roles that pause, then one PUT and one restart', async () => {
    render()
    click('flm-cap-asr')
    expect(box.edits).toHaveLength(0)
    expect(confirmText()).toContain('chat stops answering')
    pressButton('Start speech-to-text and restart FLM')
    await flush()
    expect(box.edits).toEqual([
      { name: 'flm', body: { npu: { chat: true, asr: true, embed: false }, model: { default: 'gemma4-it:e4b' } } },
    ])
    expect(box.restarts).toEqual(['flm'])
    expect(confirmText()).toBeNull()
  })

  it('Cancel sends nothing', () => {
    render()
    click('flm-cap-embed')
    pressButton('Cancel')
    expect(box.edits).toHaveLength(0)
    expect(box.restarts).toHaveLength(0)
    expect(confirmText()).toBeNull()
  })

  it('turning off the last running role writes it and stops FLM', async () => {
    render()
    click('flm-cap-chat')
    expect(confirmText()).toContain('FLM stops')
    pressButton('Stop FLM')
    await flush()
    expect(box.edits[0].body).toMatchObject({ npu: { chat: false, asr: false, embed: false } })
    expect(box.unloads).toEqual(['flm'])
    expect(box.restarts).toHaveLength(0)
  })

  it('stopped FLM: turning a role on writes and starts FLM without asking', async () => {
    render(stopped)
    click('flm-cap-asr')
    await flush()
    expect(confirmText()).toBeNull()
    expect(box.edits).toHaveLength(1)
    expect(box.loads).toEqual(['flm'])
    expect(box.restarts).toHaveLength(0)
  })

  it('stopped FLM: turning a role off only writes', async () => {
    render(stopped)
    click('flm-cap-chat')
    await flush()
    expect(box.edits).toHaveLength(1)
    expect(box.loads).toHaveLength(0)
    expect(box.restarts).toHaveLength(0)
  })

  it('after a write the toggles stay locked until the refetched config differs', async () => {
    render()
    click('flm-cap-asr')
    pressButton('Start speech-to-text and restart FLM')
    await flush()
    expect(box.edits).toHaveLength(1)
    // Same config as before the write: still the stale baseline, so locked.
    render()
    expect((q('flm-cap-embed') as HTMLButtonElement).disabled).toBe(true)
    box.cfg = { npu: { chat: true, asr: true, embed: false }, model: { default: 'gemma4-it:e4b' } }
    render()
    await flush()
    expect((q('flm-cap-embed') as HTMLButtonElement).disabled).toBe(false)
  })

  it('the chat model dropdown is disabled while chat is off', () => {
    box.cfg = { npu: { chat: false, asr: false, embed: false }, model: { default: 'gemma4-it:e4b' } }
    render()
    const trigger = q('flm-chat-model') as HTMLButtonElement
    expect(trigger.disabled || trigger.getAttribute('aria-disabled') === 'true').toBe(true)
  })

  it('config still loading → toggles disabled', () => {
    box.cfg = undefined
    render()
    expect((q('flm-cap-asr') as HTMLButtonElement).disabled).toBe(true)
    expect((q('flm-cap-chat') as HTMLButtonElement).disabled).toBe(true)
  })

  it('a role that is off shows a grey dot and "off" on its card', () => {
    render()
    expect(q('flm-row-embed')?.textContent).toContain('off')
    expect(q('flm-row-embed')?.querySelector('.sdot')?.className).toContain('offline')
    expect(q('flm-row-chat')?.querySelector('.sdot')?.className).not.toContain('offline')
  })

  it('STT and Embed cards carry their shadow names and the bundled model', () => {
    render()
    expect(q('flm-row-asr')?.textContent).toContain('flm-stt')
    expect(q('flm-row-asr')?.textContent).toContain('whisper-v3:turbo')
    expect(q('flm-row-asr')?.textContent).not.toContain('gemma4-it:e4b')
    expect(q('flm-row-embed')?.textContent).toContain('flm-embed')
    expect(q('flm-row-embed')?.textContent).toContain('embed-gemma:300m')
  })
})

describe('FlmLane chat model', () => {
  it('the dropdown offers only chat models, installed ones under their own header', () => {
    render()
    act(() => { q('flm-chat-model')!.click() })
    const ids = Array.from(host.querySelectorAll('[data-option-id]')).map((e) => e.getAttribute('data-option-id'))
    expect(ids).toEqual(['__hdr-installed', 'gemma4-it:e4b', '__hdr-download', 'qwen3:8b'])
  })

  it('running FLM, installed model: asks, then one PUT and one restart, no download', async () => {
    box.catalog.push({ model: 'gemma4-it:e2b', installed: true, capabilities: ['chat', 'stt'] })
    render()
    pick('gemma4-it:e2b')
    expect(confirmText()).not.toContain('downloads first')
    pressButton('Switch and restart FLM')
    await flush()
    expect(box.pullStart).not.toHaveBeenCalled()
    expect(box.edits[0].body).toMatchObject({ model: { default: 'gemma4-it:e2b' } })
    expect(box.restarts).toEqual(['flm'])
    box.catalog.pop()
  })

  it('running FLM, not installed: confirm, download with progress, then one PUT and one restart', async () => {
    box.pullStart.mockResolvedValue({})
    render()
    pick('qwen3:8b')
    expect(confirmText()).toContain('downloads first')
    pressButton('Download and switch')
    await flush()
    expect(box.pullStart).toHaveBeenCalledWith('qwen3:8b')
    expect(q('flm-pull-progress')?.textContent).toContain('Downloading qwen3:8b')
    expect(box.edits).toHaveLength(0)
    box.pull = { state: 'completed', modelId: 'qwen3:8b', error: null }
    render()
    await flush()
    expect(box.edits).toEqual([
      { name: 'flm', body: { npu: { chat: true, asr: false, embed: false }, model: { default: 'qwen3:8b' } } },
    ])
    expect(box.restarts).toEqual(['flm'])
    expect(q('flm-pull-progress')).toBeNull()
  })

  it('a download that fails to start → no PUT, no restart, error shown', async () => {
    box.pullStart.mockRejectedValue(new Error('download exploded'))
    render()
    pick('qwen3:8b')
    pressButton('Download and switch')
    await flush()
    expect(box.edits).toHaveLength(0)
    expect(box.restarts).toHaveLength(0)
    expect(q('flm-error')?.textContent).toContain('download exploded')
  })

  it('pull state failed → no PUT, error shown', async () => {
    box.pullStart.mockResolvedValue({})
    render()
    pick('qwen3:8b')
    pressButton('Download and switch')
    await flush()
    box.pull = { state: 'failed', modelId: 'qwen3:8b', error: { message: 'disk full' } }
    render()
    await flush()
    expect(box.edits).toHaveLength(0)
    expect(q('flm-error')?.textContent).toContain('disk full')
  })

  it('config changing during a download → nothing written, toast', async () => {
    box.pullStart.mockResolvedValue({})
    render()
    pick('qwen3:8b')
    pressButton('Download and switch')
    await flush()
    box.pull = { state: 'running', modelId: 'qwen3:8b', error: null }
    render()
    box.cfg = { npu: { chat: true, asr: true, embed: false }, model: { default: 'gemma4-it:e4b' } }
    render()
    await flush()
    box.pull = { state: 'completed', modelId: 'qwen3:8b', error: null }
    render()
    await flush()
    expect(box.edits).toHaveLength(0)
    expect(box.restarts).toHaveLength(0)
    expect(box.toasts.some((t) => t.includes('changed elsewhere'))).toBe(true)
    expect(q('flm-error')).toBeNull()
  })

  it('stopped FLM: a pick writes without asking and does not start FLM', async () => {
    box.catalog.push({ model: 'gemma4-it:e2b', installed: true, capabilities: ['chat', 'stt'] })
    render(stopped)
    pick('gemma4-it:e2b')
    await flush()
    expect(confirmText()).toBeNull()
    expect(box.edits).toHaveLength(1)
    expect(box.restarts).toHaveLength(0)
    expect(box.loads).toHaveLength(0)
    box.catalog.pop()
  })
})

describe('FlmLane anchor', () => {
  it('no anchor → Create FLM slot button posts the flm profile', () => {
    render([])
    click('flm-create')
    expect(box.creates).toEqual([{ name: 'flm', type: 'llm', runtime: 'container', device: 'npu', profile: 'flm' }])
  })

  it('create falls back to flm-npu (npu is a reserved Slots tab), then flm-npu-2', () => {
    render([{ name: 'flm', type: 'llm', device_class: 'gpu' }])
    click('flm-create')
    expect(box.creates[0]).toMatchObject({ name: 'flm-npu' })
    box.creates.length = 0
    render([{ name: 'flm', type: 'llm', device_class: 'gpu' }, { name: 'flm-npu', type: 'llm', device_class: 'gpu' }])
    click('flm-create')
    expect(box.creates[0]).toMatchObject({ name: 'flm-npu-2' })
  })

  it('Edit opens the drawer with a return to the tab', async () => {
    window.location.hash = '#slots/npu/lanes'
    render()
    click('slot-ctrl-edit')
    expect(window.location.hash).toBe('#slots/flm')
    const { takeSlotDrawerReturn } = await import('../slots-route')
    expect(takeSlotDrawerReturn('flm')).toBe('#slots/npu/lanes')
  })
})
