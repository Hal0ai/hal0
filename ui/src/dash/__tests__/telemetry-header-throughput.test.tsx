// @vitest-environment happy-dom
// THROUGHPUT hero (#3859): the history endpoint now emits the idle bins after
// the last burst as a measured 0, so the hero reads the newest bin, and an
// idle (0) bin falls back to the slots' lifecycle serving count rather than
// reading "0 slots serving" for a loaded idle slot.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React
;(globalThis as any).FieldInfoIcon = () => null

const SLOTS = [{ name: 'brain', type: 'llm', device: 'gpu-rocm', model: 'm-brain', state: 'serving' }]

const state: { history: any } = { history: null }

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: SLOTS, isLoading: false }),
}))
vi.mock('@/api/hooks/useHardware', () => ({
  useHardware: () => ({ data: { cpu: 'test cpu', name: 'box', platform_label: 'test' }, isLoading: false }),
}))
vi.mock('@/api/hooks/useStatsHardware', () => ({
  useStatsHardware: () => ({ data: { cpu_util: 0.1, memory: null, per_slot: {} }, isLoading: false }),
  useMemoryBudget: () => ({ data: null }),
}))
vi.mock('@/api/hooks/useStatsPower', () => ({ useStatsPower: () => ({ data: null }) }))
vi.mock('@/api/hooks/useThroughputHistory', () => ({
  useThroughputHistory: () => ({ data: state.history }),
}))
vi.mock('@/api/hooks/useNpuOccupancy', () => ({ useNpuOccupancy: () => ({ data: null }) }))
vi.mock('@/api/hooks/useProxmoxSettings', () => ({
  useProxmoxSettings: () => ({ data: null, isLoading: false }),
}))

const { TelemetryHeader } = await import('../telemetry-header.jsx')

function tpsCell(history: any): HTMLElement {
  state.history = history
  const host = document.createElement('div')
  document.body.appendChild(host)
  act(() => {
    createRoot(host).render(<TelemetryHeader slots={SLOTS} />)
  })
  const cell = host.querySelector<HTMLElement>('.th-cell-tps')
  if (!cell) throw new Error('throughput cell not rendered')
  return cell
}

afterEach(() => {
  document.body.innerHTML = ''
})

const sample = (ts: number, total_tps: number, serving_slots: number) => ({ ts, total_tps, serving_slots })

describe('THROUGHPUT hero', () => {
  it('reads 0.0 once the newest bin is idle, not the last busy bin', () => {
    const cell = tpsCell({
      window_s: 100,
      bucket_s: 5,
      samples: [sample(1000, 27.8, 1), sample(1005, 27.6, 1), sample(1010, 0, 0), sample(1015, 0, 0)],
      per_slot: {},
    })
    expect(cell.querySelector('.th-hero')?.textContent).toBe('0.0')
    // the idle bin counts no generating slot; the loaded slot is still serving
    expect(cell.querySelector('.th-sub .s')?.textContent).toBe('1 slot serving')
  })

  it('reads the newest busy bin and its measured serving count', () => {
    const cell = tpsCell({
      window_s: 100,
      bucket_s: 5,
      samples: [sample(2000, 10, 1), sample(2005, 27.8, 2)],
      per_slot: {},
    })
    expect(cell.querySelector('.th-hero')?.textContent).toBe('27.8')
    expect(cell.querySelector('.th-sub .s')?.textContent).toBe('2 slots serving')
  })
})
