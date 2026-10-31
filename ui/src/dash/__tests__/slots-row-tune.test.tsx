// @vitest-environment happy-dom
//
// Tune glyphs on the Slots list row, the slot card's profile chip and the
// model pickers: the thinking bubble and the vision-off eye follow the slot's
// `tune_effective` and ride the slot's CURRENT model only.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const idle = { mutate: () => {}, mutateAsync: async () => ({}), isPending: false, reset: () => {} }
vi.mock('@/api/hooks/useSlots', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useSlotRestart: () => idle,
}))

await import('../primitives.jsx')
await import('../slots.jsx')
const { modelPickerOptions } = await import('../inference-pane.jsx')
const { SlotListRow, SlotCard } = window as unknown as Record<string, React.ComponentType<Record<string, unknown>>>

const EFF = {
  ctx_size: 32768, enable_thinking: true, reasoning_effort: 'high', reasoning_budget: 2048,
  vision: 'off', speculation: 'auto', sources: {}, reasoning_effort_unavailable: false,
}
const VM = { id: 'qwen', longName: 'Qwen', capabilities: ['chat', 'vision'], mmproj: '/m.gguf' }
const OTHER = { id: 'gemma', longName: 'Gemma', capabilities: ['chat', 'vision'] }
const SLOT = {
  name: 'chat', type: 'llm', device: 'gpu-rocm', model: 'qwen', model_id: 'qwen', state: 'ready',
  profile: 'agent-chat', metrics: {}, tune: { vision: 'off', enable_thinking: true }, tune_effective: EFF,
}

function render(node: React.ReactElement) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const qc = new QueryClient()
  act(() => {
    createRoot(host).render(React.createElement(QueryClientProvider, { client: qc }, node))
  })
  return host
}
afterEach(() => {
  document.body.innerHTML = ''
})

describe('Slots list row tune glyphs', () => {
  it('shows the thinking mark and a vision-off eye on the current model', () => {
    const host = render(<SlotListRow slot={SLOT} modelRow={VM} />)
    expect(host.querySelector('[data-testid="thinking-mark"]')!.getAttribute('title')).toBe(
      'thinking on · effort high · budget 2048',
    )
    const eye = host.querySelector('.modality-icon.off')!
    expect(eye.getAttribute('title')).toBe('vision off for this slot')
  })
  it('shows nothing when thinking is off and vision is on', () => {
    const slot = { ...SLOT, tune: {}, tune_effective: { ...EFF, enable_thinking: false, vision: 'auto' } }
    const host = render(<SlotListRow slot={slot} modelRow={VM} />)
    expect(host.querySelector('[data-testid="thinking-mark"]')).toBeNull()
    expect(host.querySelector('.modality-icon.off')).toBeNull()
  })
})

describe('Slot card profile chip', () => {
  it('shows +N for the override count and nothing without overrides', () => {
    const withTune = render(<SlotCard slot={SLOT} modelRow={VM} />)
    expect(withTune.querySelector('[data-testid="sl-tune-count"]')!.textContent).toBe('+2')
    document.body.innerHTML = ''
    const without = render(<SlotCard slot={{ ...SLOT, tune: {} }} modelRow={VM} />)
    expect(without.querySelector('[data-testid="sl-tune-count"]')).toBeNull()
  })
})

describe('model picker options', () => {
  const options = (s: Record<string, unknown>) =>
    modelPickerOptions({ s, opts: [VM, OTHER], cur: 'qwen', has: true, allSlots: [], verdictFor: null })

  it('current row and trigger carry the glyphs; other rows never do', () => {
    const [cur, other] = options(SLOT) as any[]
    for (const node of [cur.row, cur.trigger]) {
      const host = render(node)
      expect(host.querySelector('[data-testid="thinking-mark"]')).not.toBeNull()
      expect(host.querySelector('.modality-icon.off')).not.toBeNull()
      document.body.innerHTML = ''
    }
    for (const node of [other.row, other.trigger]) {
      const host = render(node)
      expect(host.querySelector('[data-testid="thinking-mark"]')).toBeNull()
      expect(host.querySelector('.modality-icon.off')).toBeNull()
      expect(host.querySelector('.modality-icon')!.getAttribute('title')).toBe('vision')
      document.body.innerHTML = ''
    }
  })
  it('a slot with no tune_effective shows no glyphs', () => {
    const [cur] = options({ ...SLOT, tune_effective: null, tune: {} }) as any[]
    const host = render(cur.trigger)
    expect(host.querySelector('[data-testid="thinking-mark"]')).toBeNull()
    expect(host.querySelector('.modality-icon.off')).toBeNull()
  })
})
