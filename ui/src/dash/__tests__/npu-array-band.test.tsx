// @vitest-environment happy-dom
// The array band: who holds the columns, and the shared (time-sliced) treatment.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { ArrayBand } = await import('../npu/ArrayBand')

let host: HTMLDivElement
let root: Root
const render = (occSlots: unknown[], slots: unknown[], extra: Record<string, unknown> = {}, hw: Record<string, unknown> = {}) =>
  act(() => {
    root.render(<ArrayBand occ={{ present: true, columns_available: true, cols_total: 8, cols_used: 8, slots: occSlots, ...extra } as never} hw={hw as never} slots={slots as never} />)
  })
const q = (id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null

const flm = { name: 'flm', type: 'llm', device_class: 'npu', state: 'ready' }
const ggml = { name: 'npu-gguf', type: 'llm', device_class: 'npu', binary: 'xdna', state: 'ready' }
const occ = (name: string, engine: string, cols: number[]) => ({ name, engine, state: 'ready', cols, model: null, gb: null })

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('ArrayBand shared treatment', () => {
  it('two holders (flm + xdna): grid is shared and the foot says time-sliced', () => {
    render([occ('flm', 'flm', [0, 1, 2, 3]), occ('npu-gguf', 'xdna', [4, 5, 6, 7])], [flm, ggml])
    expect(q('aie-grid')!.getAttribute('data-shared')).toBe('true')
    expect(q('aie-grid')!.className).toContain('shared')
    expect(q('npu-array-foot')!.textContent).toContain('shared · time-sliced')
    expect(q('npu-holders')!.textContent).toContain('flm')
    expect(q('npu-holders')!.textContent).toContain('npu-gguf')
  })
  it('one holder: not shared, plain foot', () => {
    render([occ('flm', 'flm', [0, 1, 2, 3, 4, 5, 6, 7])], [flm])
    expect(q('aie-grid')!.getAttribute('data-shared')).toBe('false')
    expect(q('npu-array-foot')!.textContent).not.toContain('time-sliced')
  })
})

describe('ArrayBand banners', () => {
  const both = () => [occ('flm', 'flm', [0, 1, 2, 3]), occ('npu-gguf', 'xdna', [4, 5, 6, 7])]
  it('renders shared_warning text, and nothing when null', () => {
    render(both(), [flm, ggml], { shared_warning: 'flm and npu-gguf share the array' })
    expect(q('npu-shared-warning')!.textContent).toBe('flm and npu-gguf share the array')
    render(both(), [flm, ggml], { shared_warning: null })
    expect(q('npu-shared-warning')).toBeNull()
  })
  it('power-mode banner for a non-turbo pmode with an xdna slot up, linking to Engines', () => {
    render(both(), [flm, ggml], {}, { npu_pmode: 'default' })
    expect(q('npu-pmode-banner')!.textContent).toContain('Host power mode is default.')
    expect(q('npu-pmode-banner')!.textContent).toContain('+15% prefill, +20% decode')
    expect(q('npu-pmode-fix')!.getAttribute('href')).toBe('#slots/npu/engines')
  })
  it('no power-mode banner for turbo or an unknown pmode', () => {
    render(both(), [flm, ggml], {}, { npu_pmode: 'turbo' })
    expect(q('npu-pmode-banner')).toBeNull()
    render(both(), [flm, ggml], {}, { npu_pmode: null })
    expect(q('npu-pmode-banner')).toBeNull()
  })
  it('no power-mode banner when no xdna slot is up', () => {
    render([occ('flm', 'flm', [0, 1, 2, 3, 4, 5, 6, 7]), { ...occ('npu-gguf', 'xdna', []), state: 'offline' }], [flm, ggml], {}, { npu_pmode: 'default' })
    expect(q('npu-pmode-banner')).toBeNull()
  })
})
