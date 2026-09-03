// @vitest-environment happy-dom
//
// Inference pane slot-card pickers (card-dropdowns Task 1):
//   the card model picker (ModelPicker) migrated from a native <select> to
//   RichSelect — multi-line rows (quant tag, modality tag, size + used-by +
//   engine desc line) and a lazy GTT fit-chip batch on first open.
//
// inference-pane.jsx is a window-globals dash module (bare `React.Fragment`/
// `React.cloneElement` identifiers resolve off `window` under vitest's
// classic-JSX esbuild config) — same pattern as model-drawer.test.tsx.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React

// GTT feasibility probe — a real useState-backed mutation stand-in so calling
// `mutate()` from ModelPicker's onOpenChange handler genuinely re-renders the
// component, the same way react-query's own mutation state does (idiom lifted
// from model-drawer.test.tsx's feasibility mock).
const { feasibilityCalls, feasibilityHandler } = vi.hoisted(() => ({
  feasibilityCalls: [] as unknown[],
  feasibilityHandler: {
    current: undefined as
      | ((body: { models: { model_id: string; ctx?: number }[] }) => { results: unknown[] } | undefined)
      | undefined,
  },
}))

vi.mock('@/api/hooks/useModelsFeasibility', () => ({
  useModelsFeasibility: () => {
    const [data, setData] = React.useState<{ results: unknown[] } | undefined>(undefined)
    return {
      data,
      mutate: (body: { models: { model_id: string; ctx?: number }[] }) => {
        feasibilityCalls.push(body)
        const resp = feasibilityHandler.current?.(body)
        if (resp) setData(resp)
      },
    }
  },
}))

const { ModelPicker } = await import('../inference-pane.jsx')

function mount(el: React.ReactElement) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(el)
  })
  return { host, root }
}

const q = (host: HTMLElement, testid: string) =>
  host.querySelector(`[data-testid="${testid}"]`) as HTMLElement | null

function trigger(host: HTMLElement, name: string) {
  return q(host, `infer-model-${name}`)!
}

function openMenu(host: HTMLElement, name: string) {
  act(() => {
    trigger(host, name).dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}

function optionEls(host: HTMLElement) {
  return Array.from(host.querySelectorAll('.rsel-listbox [role="option"]')) as HTMLElement[]
}

function optionById(host: HTMLElement, id: string) {
  return host.querySelector(`[data-option-id="${id}"]`) as HTMLElement | null
}

// m1: current model of NO slot in the base fixture set (used-by = "other")
// so its used-by text reads as a plain count, matching the brief's literal
// "used by 1 slot" example rather than the "used by this slot" branch.
const M1 = {
  id: 'm1',
  longName: 'Qwen3.6-8B',
  quant: 'Q8_0',
  size_bytes: 2_791_728_742, // (2_791_728_742 / 1024**3).toFixed(1) === "2.6"
  type: 'llm',
  capabilities: ['chat'],
  installed: true,
  provider_effective: 'llama-server',
}
const M2 = {
  id: 'm2',
  longName: 'Ornith-Vision',
  quant: 'Q4_K',
  size_bytes: 9_223_372_036, // ~8.6 GB
  type: 'llm',
  capabilities: ['chat', 'vision'],
  installed: true,
  provider_effective: 'vllm',
}
// upstream-advertised, not locally installed — excluded from `opts` by the
// same rule slot-modals' compatibleModels() applies.
const M3_UPSTREAM = {
  id: 'm3',
  longName: 'Upstream-Only',
  type: 'llm',
  origin: 'upstream',
  installed: false,
}
const MODELS = [M1, M2, M3_UPSTREAM]

const SLOT = { name: 'chat', type: 'llm', model_id: '', ctx_max: 32768 }
// slotsUsingModel (model-usage.js) matches on `model`/`model_default`, not
// `model_id` — the slot roster shape it was written against.
const ALL_SLOTS = [
  { name: 'chat', model: '' },
  { name: 'other', model: 'm1' },
]

beforeEach(() => {
  feasibilityCalls.length = 0
  feasibilityHandler.current = undefined
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('ModelPicker (card-dropdowns Task 1)', () => {
  it('renders rich rows: quant tag, size desc, used-by', () => {
    const { host } = mount(
      <ModelPicker s={SLOT} models={MODELS} allSlots={ALL_SLOTS} disabled={false} onSwap={() => {}} />,
    )
    openMenu(host, 'chat')
    const row = optionById(host, 'm1')!
    expect(row.textContent).toContain('Q8_0')
    expect(row.querySelector('.rsel-option-desc')!.textContent).toContain('2.6 GB')
    expect(row.querySelector('.rsel-option-desc')!.textContent).toContain('used by 1 slot')
    expect(row.querySelector('.rsel-option-desc')!.textContent).toContain('other')
    // upstream-only row never appears at all.
    expect(optionById(host, 'm3')).toBeNull()
  })

  it('fires one lazy feasibility batch on first open with slot ctx, not on render', () => {
    const { host } = mount(
      <ModelPicker s={SLOT} models={MODELS} allSlots={ALL_SLOTS} disabled={false} onSwap={() => {}} />,
    )
    expect(feasibilityCalls.length).toBe(0)
    openMenu(host, 'chat')
    expect(feasibilityCalls.length).toBe(1)
    expect(feasibilityCalls[0]).toEqual({
      models: [
        { model_id: 'm1', ctx: 32768 },
        { model_id: 'm2', ctx: 32768 },
      ],
    })
    // Closing and reopening does not re-fire — first open only.
    act(() => {
      document.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    })
    openMenu(host, 'chat')
    expect(feasibilityCalls.length).toBe(1)
  })

  it('verdict chips render per feasibilityHint tone; unknown renders no chip', () => {
    feasibilityHandler.current = () => ({
      results: [
        { model_id: 'm1', verdict: 'fits', needed_mb: 2662, gtt_free_mb: 8192, gtt_total_mb: 16384 },
        // m2 has no result row at all → unknown → no chip.
      ],
    })
    const { host } = mount(
      <ModelPicker s={SLOT} models={MODELS} allSlots={ALL_SLOTS} disabled={false} onSwap={() => {}} />,
    )
    openMenu(host, 'chat')
    const row1 = optionById(host, 'm1')!
    expect(row1.querySelector('.rsel-option-right')!.textContent).toContain('fits')
    const row2 = optionById(host, 'm2')!
    expect(row2.querySelector('.rsel-option-right')).toBeNull()
  })

  it('pick calls onSwap(id); picking current is a no-op', () => {
    const onSwap = vi.fn()
    const cur = { ...SLOT, model_id: 'm1' }
    const { host } = mount(
      <ModelPicker s={cur} models={MODELS} allSlots={ALL_SLOTS} disabled={false} onSwap={onSwap} />,
    )
    openMenu(host, 'chat')
    act(() => {
      optionById(host, 'm1')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(onSwap).not.toHaveBeenCalled()
    openMenu(host, 'chat')
    act(() => {
      optionById(host, 'm2')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(onSwap).toHaveBeenCalledWith('m2')
  })

  it('current out-of-vocab model renders as its own row', () => {
    const cur = { ...SLOT, model_id: 'ghost-7b', model: 'ghost-7b' }
    const { host } = mount(
      <ModelPicker s={cur} models={MODELS} allSlots={ALL_SLOTS} disabled={false} onSwap={() => {}} />,
    )
    openMenu(host, 'chat')
    const row = optionById(host, 'ghost-7b')
    expect(row).not.toBeNull()
    expect(row!.textContent).toContain('ghost-7b')
    expect(optionEls(host).length).toBe(3) // ghost-7b + m1 + m2
  })

  it('non-llm slot keeps the static line', () => {
    const util = { name: 'embed', type: 'embedding', model: 'bge-m3' }
    const { host } = mount(
      <ModelPicker s={util} models={MODELS} allSlots={ALL_SLOTS} disabled={false} onSwap={() => {}} />,
    )
    expect(host.querySelector('.smodel')!.textContent).toBe('bge-m3')
    expect(q(host, 'infer-model-embed')).toBeNull()
    expect(feasibilityCalls.length).toBe(0)
  })
})
