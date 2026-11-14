import { describe, expect, it } from 'vitest'
import { npuAnchorSlot } from '../slots/slot-shared'

describe('npuAnchorSlot', () => {
  const xdna = { name: 'g', binary: 'xdna', device_class: 'npu', type: 'llm' }
  const flm = { name: 'flm', binary: 'flm', device_class: 'npu', type: 'llm' }
  it('skips an xdna slot and picks the FLM anchor', () => {
    expect(npuAnchorSlot([xdna, flm])?.name).toBe('flm')
  })
  it('is null when only xdna slots exist', () => {
    expect(npuAnchorSlot([xdna])).toBeNull()
  })
})
