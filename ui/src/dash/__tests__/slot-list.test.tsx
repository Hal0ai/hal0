// @vitest-environment happy-dom
// SlotList row: the speculation bolt after the model name (ADR-0031 §5).
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React
;(globalThis as any).DCard = ({ children }: any) => <div>{children}</div>
;(globalThis as any).StatusDot = () => <span />

const spec = (over: Record<string, unknown>) => ({
  active: false, acceptance: null, mean_accepted_len: null, mode: 'none',
  kind: null, variant: null, unavailable_reason: null, ...over,
})
const slot = (name: string, model_id: string, s: unknown) => ({
  name, type: 'llm', device: 'gpu-rocm', model: model_id, model_id, state: 'serving',
  metrics: { toks: 10, spec: s },
})

const SLOTS = [
  slot('lit', 'with-draft', spec({ active: true, mode: 'draft', variant: 'lilicorr', acceptance: 0.78 })),
  slot('off', 'with-draft', spec({})),
  slot('warn', 'with-draft', spec({ kind: 'dflash', variant: 'lilicorr', unavailable_reason: 'needs llama.cpp b11669' })),
  slot('plain', 'no-draft', spec({})),
  slot('mtp', 'mtp-model', spec({})),
]
const MODELS = [
  { id: 'with-draft', draft: '/m/d.gguf', draftVariant: 'lilicorr' },
  { id: 'no-draft', draft: null },
  { id: 'mtp-model', draft: null, mtp: true },
]

vi.mock('@/api/hooks/useSlots', () => ({ useSlots: () => ({ data: SLOTS, isLoading: false }) }))
vi.mock('@/api/hooks/useModels', () => ({ useModels: () => ({ data: MODELS }) }))
// The footer's free RAM reads the memory budget (ADR-0039), not the raw counters.
const MEMORY: { data: unknown } = { data: undefined }
vi.mock('@/api/hooks/useStatsHardware', () => ({ useMemoryBudget: () => MEMORY }))
vi.mock('@/lib/deviceMeta', () => ({ devKind: () => 'gpu' }))

// slot-list.jsx reads React/DCard/StatusDot off the window globals at import.
const { SlotList } = await import('../slot-list.jsx')

function bolt(host: HTMLElement, name: string) {
  return host.querySelector(`[data-testid="sl-row-${name}"] [data-testid="sl-spec-bolt"]`)
}

describe('SlotList speculation bolt', () => {
  const host = document.createElement('div')
  act(() => {
    createRoot(host).render(<SlotList />)
  })

  it('is lit with the drafter and acceptance while drafting', () => {
    const b = bolt(host, 'lit')!
    expect(b.className).toBe('sl-bolt')
    expect(b.getAttribute('title')).toBe('drafting · LiLiCorr · 78% accepted')
  })

  it('is grey when a drafter is paired but speculation is off', () => {
    const b = bolt(host, 'off')!
    expect(b.classList.contains('off')).toBe(true)
    expect(b.getAttribute('title')).toBe('drafter paired · speculation off')
  })

  it('is amber when the runner cannot use the drafter', () => {
    const b = bolt(host, 'warn')!
    expect(b.classList.contains('warn')).toBe(true)
    expect(b.getAttribute('title')).toBe(
      'drafter unavailable on this runner: needs llama.cpp b11669',
    )
  })

  it('shows grey for embedded MTP heads and nothing without a drafter', () => {
    expect(bolt(host, 'mtp')!.classList.contains('off')).toBe(true)
    expect(bolt(host, 'plain')).toBeNull()
  })
})

describe('SlotList footer', () => {
  const footer = (memory: unknown) => {
    MEMORY.data = memory
    const el = document.createElement('div')
    act(() => {
      createRoot(el).render(<SlotList />)
    })
    return el.querySelector('.slist-foot')!.textContent
  }

  it('free ram comes from the memory budget', () => {
    const ram = { limit_gb: 105.5, used_gb: 16.1, free_gb: 89.4, source: 'container' }
    expect(footer({ gpu: null, ram, render_free_gb: null })).toBe(
      '5 serving · scheduler steady · 89 GB free',
    )
  })

  it('omits the free clause when the budget is absent or RAM was unread', () => {
    expect(footer(undefined)).toBe('5 serving · scheduler steady')
    const unread = { limit_gb: 0, used_gb: 0, free_gb: 0, source: 'system' }
    expect(footer({ gpu: null, ram: unread, render_free_gb: null })).toBe(
      '5 serving · scheduler steady',
    )
  })
})
