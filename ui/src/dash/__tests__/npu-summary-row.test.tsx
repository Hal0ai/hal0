// @vitest-environment happy-dom
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { box } = vi.hoisted(() => ({ box: { occ: undefined as unknown } }))
vi.mock('@/api/hooks/useNpuOccupancy', () => ({ useNpuOccupancy: () => ({ data: box.occ }) }))

const { NpuSummaryRow } = await import('../npu/NpuSummaryRow')

let host: HTMLDivElement
let root: Root
const render = (slots: unknown[]) =>
  act(() => {
    root.render(<NpuSummaryRow slots={slots as never} />)
  })
const q = (id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null

const flm = { name: 'flm', device_class: 'npu', state: 'serving', metrics: { toks: 20.5 } }
const ggml = { name: 'npu-gguf', device_class: 'npu', binary: 'xdna', state: 'ready', metrics: { toks: 10 } }
const row = (name: string, state: string, engine: string) => ({ name, state, engine, model: null, cols: [], gb: null })

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  box.occ = { present: true, slots: [row('flm', 'serving', 'flm')] }
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('NpuSummaryRow', () => {
  it('links to the NPU tab', () => {
    render([flm])
    expect(q('npu-summary-link')!.getAttribute('href')).toBe('#slots/npu')
  })
  it('shows holders and summed tok/s', () => {
    box.occ = { present: true, slots: [row('flm', 'serving', 'flm'), row('npu-gguf', 'ready', 'xdna')] }
    render([flm, ggml])
    expect(q('npu-summary-holders')!.textContent).toContain('2')
    expect(q('npu-summary-toks')!.textContent).toContain('30.5')
  })
  it('flags time-sliced only with more than one holder', () => {
    render([flm])
    expect(q('npu-summary-sliced')).toBeNull()
    box.occ = { present: true, slots: [row('flm', 'serving', 'flm'), row('npu-gguf', 'ready', 'xdna')] }
    render([flm, ggml])
    expect(q('npu-summary-sliced')).not.toBeNull()
  })
  it('renders — for tok/s when nothing reports a rate', () => {
    render([{ ...flm, metrics: {} }])
    expect(q('npu-summary-toks')!.textContent).toContain('—')
  })
  it('renders nothing without an NPU or NPU slots', () => {
    box.occ = { present: false, slots: [] }
    render([{ name: 'gpu', device_class: 'gpu' }])
    expect(q('npu-summary-row')).toBeNull()
  })
  it('stays visible when the probe failed but an NPU slot exists', () => {
    box.occ = { present: false, slots: [] }
    render([flm])
    expect(q('npu-summary-row')).not.toBeNull()
  })
})
