import { describe, expect, it } from 'vitest'
import { NPU_TABS, npuStatusParts, npuTabVisible, validNpuTab } from './npu-pure'

const occ = (over: Record<string, unknown> = {}) =>
  ({ present: true, slots: [], npu_engines: undefined, npu_host: undefined, ...over }) as never

describe('npuTabVisible', () => {
  it('shows when the NPU is present', () => {
    expect(npuTabVisible(occ({ present: true }), [])).toBe(true)
  })
  it('shows when absent but an npu slot exists', () => {
    expect(npuTabVisible(occ({ present: false }), [{ device_class: 'npu' }] as never)).toBe(true)
  })
  it('hides with neither', () => {
    expect(npuTabVisible(occ({ present: false }), [{ device_class: 'gpu' }] as never)).toBe(false)
    expect(npuTabVisible(undefined, [])).toBe(false)
  })
})

describe('validNpuTab', () => {
  it('accepts the three ids only', () => {
    expect(NPU_TABS.map((t) => t.id)).toEqual(['lanes', 'models', 'engines'])
    expect(validNpuTab('engines')).toBe('engines')
    expect(validNpuTab('x')).toBeNull()
    expect(validNpuTab(null)).toBeNull()
  })
})

describe('npuStatusParts', () => {
  const find = (parts: { k: string; v: string; tone?: string }[], k: string) => parts.find((p) => p.k === k)!
  it('warns on a non-turbo pmode with an xdna slot up', () => {
    const o = occ({ slots: [{ name: 'g', state: 'ready', engine: 'xdna' }] })
    const parts = npuStatusParts(o, { npu_pmode: 'default', npu_fclk: 1000 } as never, [])
    expect(find(parts, 'pmode').tone).toBe('warn')
    expect(find(parts, 'fclk').v).toBe('1000 MHz')
  })
  it('does not warn on turbo', () => {
    const o = occ({ slots: [{ name: 'g', state: 'ready', engine: 'xdna' }] })
    expect(find(npuStatusParts(o, { npu_pmode: 'turbo' } as never, []), 'pmode').tone).toBeUndefined()
  })
  it('suffixes time-sliced for two holders', () => {
    const o = occ({
      slots: [
        { name: 'a', state: 'ready', engine: 'flm' },
        { name: 'b', state: 'loaded', engine: 'xdna' },
        { name: 'c', state: 'offline', engine: 'xdna' },
      ],
    })
    expect(find(npuStatusParts(o, undefined, []), 'holders').v.endsWith(' · time-sliced')).toBe(true)
  })
  it('renders dashes for missing values', () => {
    const parts = npuStatusParts(undefined, undefined, [])
    for (const k of ['device', 'pmode', 'fclk', 'flm', 'ggml-xdna']) expect(find(parts, k).v).toBe('—')
  })
  it('fills engine facts', () => {
    const o = occ({
      npu_engines: { flm: { image: 'i', version: '0.9.4' }, xdna: { image: 'x', tag: 'b123', pulled: true } },
      npu_host: { device_node: '/dev/accel/accel0', driver_loaded: true, xdna_gen: 2 },
    })
    const parts = npuStatusParts(o, undefined, [])
    expect(find(parts, 'flm').v).toBe('0.9.4')
    expect(find(parts, 'ggml-xdna').v).toBe('b123')
    expect(find(parts, 'device').v).toContain('/dev/accel/accel0')
  })
})
