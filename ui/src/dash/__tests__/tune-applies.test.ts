// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { tuneApplies } from '../slot-tune-block.jsx'

describe('tuneApplies', () => {
  it('takes llm slots off the NPU', () => {
    expect(tuneApplies({ type: 'llm', device: 'gpu-rocm', runner: 'rocm' })).toBe(true)
  })
  it('takes an xdna slot on the NPU (llama-server launch)', () => {
    expect(tuneApplies({ type: 'llm', device: 'npu', binary: 'xdna' })).toBe(true)
  })
  it('refuses FastFlowLM on the NPU and non-llm slots', () => {
    expect(tuneApplies({ type: 'llm', device: 'npu', binary: 'flm' })).toBe(false)
    expect(tuneApplies({ type: 'llm', device: 'npu' })).toBe(false)
    expect(tuneApplies({ type: 'embedding', device: 'npu', binary: 'xdna' })).toBe(false)
    expect(tuneApplies(null)).toBe(false)
  })
})
