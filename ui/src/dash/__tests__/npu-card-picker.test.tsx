// @vitest-environment happy-dom
//
// NPU occupancy card: each FLM slot card carries the inference cards' RichSelect
// model dropdown, limited to FLM models of the card's role. A chat pick applies
// through the slot drawer's contract (npuApplyBody -> PUT the ANCHOR's config,
// then POST restart); stt/embed cards are read-only (FLM has no per-role model
// flag, providers/flm.py).
//
// npu-pane.jsx is a window-globals dash module — same harness as
// inference-pane-pickers.test.tsx.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { editCalls, restartCalls, order, modelsBox, anchorCfgBox } = vi.hoisted(() => ({
  editCalls: [] as { name: string; body: Record<string, unknown> }[],
  restartCalls: [] as string[],
  order: [] as string[],
  modelsBox: { current: [] as unknown[] },
  anchorCfgBox: { current: { npu: { chat: true, asr: true, embed: false } } as unknown },
}))

vi.mock('@/api/hooks/useNpuOccupancy', () => ({ useNpuOccupancy: () => ({ data: { slots: [] } }) }))
vi.mock('@/api/hooks/useStatsHardware', () => ({ useStatsHardware: () => ({ data: {} }) }))
vi.mock('@/api/hooks/useModels', () => ({ useModels: () => ({ data: modelsBox.current }) }))
vi.mock('@/api/hooks/useModelsFeasibility', () => ({
  useModelsFeasibility: () => ({ data: undefined, mutate: () => {} }),
}))
vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: undefined, isLoading: false }),
  useSlotLoad: () => ({ mutate: () => {} }),
  useSlotUnload: () => ({ mutate: () => {} }),
  useSlotSwap: () => ({ mutate: () => {} }),
  useSlotConfig: () => ({ data: anchorCfgBox.current }),
  useSlotRestart: () => ({
    isPending: false,
    mutate: (name: string) => {
      order.push('restart')
      restartCalls.push(name)
    },
  }),
  useSlotEdit: () => ({
    isPending: false,
    mutate: (
      args: { name: string; body: Record<string, unknown> },
      opts?: { onSuccess?: () => void },
    ) => {
      order.push('edit')
      editCalls.push(args)
      opts?.onSuccess?.()
    },
  }),
}))

const { NpuOccupancyCard } = await import('../npu-pane.jsx')

const FLM = (id: string, caps: string[], extra: Record<string, unknown> = {}) => ({
  id,
  longName: id,
  capabilities: caps,
  backends: ['npu'],
  provider_effective: 'flm',
  installed: true,
  origin: 'local',
  ...extra,
})
const CHAT_A = FLM('qwen3:4b', ['chat'])
const CHAT_B = FLM('gemma3:1b', ['chat'])
const ASR = FLM('whisper-v3:turbo', ['transcription'])
const EMBED = FLM('embed-gemma:300m', ['embed'])
const GGUF = {
  id: 'qwen3.6-27b',
  longName: 'qwen3.6-27b',
  capabilities: ['chat'],
  backends: ['vulkan', 'rocm', 'cpu'],
  provider_effective: 'llama-server',
  installed: true,
}

const SLOTS = [
  { name: 'flm', type: 'llm', device: 'npu', device_class: 'npu', modelDefault: 'qwen3:4b', state: 'ready' },
  { name: 'flm-stt', type: 'transcription', device: 'npu', device_class: 'npu', model: 'whisper-v3:turbo', state: 'ready' },
  { name: 'flm-embed', type: 'embedding', device: 'npu', device_class: 'npu', model: 'embed-gemma:300m', state: 'ready' },
]

function mount() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(<NpuOccupancyCard slots={SLOTS} />)
  })
  return host
}

const trigger = (host: HTMLElement, name: string) =>
  host.querySelector(`[data-testid="npu-model-${name}"]`) as HTMLButtonElement | null

const click = (el: Element) =>
  act(() => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })

const optionIds = (host: HTMLElement) =>
  Array.from(host.querySelectorAll('.rsel-listbox [role="option"]')).map((o) =>
    o.getAttribute('data-option-id'),
  )

beforeEach(() => {
  document.body.innerHTML = ''
  editCalls.length = 0
  restartCalls.length = 0
  order.length = 0
  modelsBox.current = [CHAT_A, CHAT_B, ASR, EMBED, GGUF]
  anchorCfgBox.current = { npu: { chat: true, asr: true, embed: false } }
})

describe('NPU card model picker', () => {
  it('renders a RichSelect per slot card showing the slot model', () => {
    const host = mount()
    for (const n of ['flm', 'flm-stt', 'flm-embed']) expect(trigger(host, n)).not.toBeNull()
    expect(trigger(host, 'flm')!.textContent).toContain('qwen3:4b')
    expect(trigger(host, 'flm-stt')!.textContent).toContain('whisper-v3:turbo')
  })

  it('chat card lists only FLM chat models, with the runtime chip in the NPU tone', () => {
    const host = mount()
    click(trigger(host, 'flm')!)
    expect(optionIds(host)).toEqual(['qwen3:4b', 'gemma3:1b'])
    const chip = host.querySelector('[data-option-id="gemma3:1b"] .rsel-option-desc .chip.dev-npu')!
    expect(chip.textContent).toBe('flm')
  })

  it('stt and embed cards are read-only (no per-role model flag in FLM)', () => {
    const host = mount()
    expect(trigger(host, 'flm-stt')!.disabled).toBe(true)
    expect(trigger(host, 'flm-embed')!.disabled).toBe(true)
    expect(trigger(host, 'flm')!.disabled).toBe(false)
  })

  it('a chat pick PUTs the anchor config with all [npu] booleans + [model].default, then restarts', () => {
    const host = mount()
    click(trigger(host, 'flm')!)
    click(host.querySelector('[data-option-id="gemma3:1b"]')!)
    expect(editCalls).toEqual([
      {
        name: 'flm',
        body: {
          npu: { chat: true, asr: true, embed: false },
          model: { default: 'gemma3:1b' },
        },
      },
    ])
    expect(restartCalls).toEqual(['flm'])
    expect(order).toEqual(['edit', 'restart'])
  })

  it('re-picking the current model writes nothing', () => {
    const host = mount()
    click(trigger(host, 'flm')!)
    click(host.querySelector('[data-option-id="qwen3:4b"]')!)
    expect(editCalls).toEqual([])
  })

  it('disables the chat picker while chat is off, like the drawer', () => {
    anchorCfgBox.current = { npu: { chat: false, asr: true, embed: false } }
    const host = mount()
    expect(trigger(host, 'flm')!.disabled).toBe(true)
  })

  it('a spare model-less NPU LLM slot cannot edit the anchor through its picker', () => {
    const spare = { name: 'flm-spare', type: 'llm', device: 'npu', device_class: 'npu', state: 'offline' }
    const host = document.createElement('div')
    document.body.appendChild(host)
    act(() => {
      createRoot(host).render(<NpuOccupancyCard slots={[SLOTS[0], spare]} />)
    })
    expect(trigger(host, 'flm')!.disabled).toBe(false)
    expect(trigger(host, 'flm-spare')!.disabled).toBe(true)
  })

  it('keeps the slot bound to the older <tag>-FLM id selected', () => {
    modelsBox.current = [FLM('qwen3-4b-FLM', ['chat']), CHAT_B]
    const slots = [{ ...SLOTS[0], modelDefault: 'qwen3:4b' }]
    const host = document.createElement('div')
    document.body.appendChild(host)
    act(() => {
      createRoot(host).render(<NpuOccupancyCard slots={slots} />)
    })
    click(trigger(host, 'flm')!)
    expect(host.querySelector('[data-option-id="qwen3-4b-FLM"]')!.getAttribute('aria-selected')).toBe('true')
  })
})
