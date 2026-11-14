import { describe, expect, it } from 'vitest'
import { arrayHolders, arrayOwners, arrayShared } from './array-pure'

const slots = [
  { name: 'flm', type: 'llm', device_class: 'npu' },
  { name: 'flm-stt', type: 'transcription', device_class: 'npu' },
  { name: 'g1', type: 'llm', device_class: 'npu', binary: 'xdna' },
]
describe('arrayOwners', () => {
  it('gives each holder one shared object; shadows own nothing', () => {
    const occ = [
      { name: 'flm', cols: [0, 1, 2, 3], engine: 'flm' as const, state: 'ready' },
      { name: 'flm-stt', cols: [0, 1, 2, 3], engine: 'flm' as const, state: 'ready' },
      { name: 'g1', cols: [4, 5, 6, 7], engine: 'xdna' as const, state: 'ready' },
    ]
    const o = arrayOwners(occ, slots)
    expect(o).toHaveLength(8)
    expect(o[0]).toBe(o[3])
    expect(o[4]).toBe(o[7])
    expect(o[0]).not.toBe(o[4])
    expect(o[0]?.name).toBe('flm')
    expect(o[4]?.engine).toBe('xdna')
    expect(arrayShared(arrayHolders(occ, slots))).toBe(true)
  })
  it('empty when nothing holds columns', () => {
    const o = arrayOwners([], slots)
    expect(o.every((x) => x === null)).toBe(true)
    expect(arrayShared(arrayHolders([], slots))).toBe(false)
  })
  it('an anchor named npu: its synthesized -stt/-embed rows (served_by, no slot record) own nothing', () => {
    const all = [0, 1, 2, 3, 4, 5, 6, 7]
    const occ = [
      { name: 'npu', cols: all, engine: 'flm' as const, state: 'ready' },
      { name: 'npu-stt', cols: all, engine: 'flm' as const, state: 'ready', served_by: 'npu' },
      { name: 'npu-embed', cols: all, engine: 'flm' as const, state: 'ready', served_by: 'npu' },
    ]
    const npuSlots = [{ name: 'npu', type: 'llm', device_class: 'npu' }]
    const o = arrayOwners(occ, npuSlots)
    expect(o.every((x) => x?.name === 'npu')).toBe(true)
    expect(arrayShared(arrayHolders(occ, npuSlots))).toBe(false)
  })
  it('FLM and a GGML slot claiming the same columns are two holders and shared (#3657)', () => {
    const all = [0, 1, 2, 3, 4, 5, 6, 7]
    const occ = [
      { name: 'flm', cols: all, engine: 'flm' as const, state: 'ready' },
      { name: 'g1', cols: all, engine: 'xdna' as const, state: 'ready' },
    ]
    const h = arrayHolders(occ, slots)
    expect(h.map((x) => x.name)).toEqual(['flm', 'g1'])
    expect(arrayShared(h)).toBe(true)
  })
})
