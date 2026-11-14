import { describe, expect, it } from 'vitest'
import { arrayOwners, arrayShared } from './array-pure'

const slots = [
  { name: 'flm', type: 'llm', device_class: 'npu' },
  { name: 'flm-stt', type: 'transcription', device_class: 'npu' },
  { name: 'g1', type: 'llm', device_class: 'npu', binary: 'xdna' },
]
describe('arrayOwners', () => {
  it('gives each holder one shared object; shadows own nothing', () => {
    const o = arrayOwners(
      [
        { name: 'flm', cols: [0, 1, 2, 3], engine: 'flm', state: 'ready' },
        { name: 'flm-stt', cols: [0, 1, 2, 3], engine: 'flm', state: 'ready' },
        { name: 'g1', cols: [4, 5, 6, 7], engine: 'xdna', state: 'ready' },
      ],
      slots,
    )
    expect(o).toHaveLength(8)
    expect(o[0]).toBe(o[3])
    expect(o[4]).toBe(o[7])
    expect(o[0]).not.toBe(o[4])
    expect(o[0]?.name).toBe('flm')
    expect(o[4]?.engine).toBe('xdna')
    expect(arrayShared(o)).toBe(true)
  })
  it('empty when nothing holds columns', () => {
    const o = arrayOwners([], slots)
    expect(o.every((x) => x === null)).toBe(true)
    expect(arrayShared(o)).toBe(false)
  })
})
