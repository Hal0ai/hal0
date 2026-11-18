import { describe, expect, it } from 'vitest'
import { LANE_CODES, laneForBackend, laneForSlot } from '../lane-code-pure'

describe('LANE_CODES', () => {
  it('gives every lane a distinct three-letter code', () => {
    const codes = Object.values(LANE_CODES).map((l) => l.code)
    expect(codes.every((c) => /^[A-Z]{3}$/.test(c))).toBe(true)
    expect(new Set(codes).size).toBe(codes.length)
  })
})

describe('laneForSlot', () => {
  it('reads GPU and CPU lanes off the device', () => {
    expect(laneForSlot({ device: 'gpu-vulkan' })).toBe('vulkan')
    expect(laneForSlot({ device: 'gpu-rocm' })).toBe('rocm')
    expect(laneForSlot({ device: 'cpu' })).toBe('cpu')
    expect(laneForSlot(null)).toBe('cpu')
  })
  it('reads a CUDA device as the CUDA lane, not ROCm', () => {
    expect(laneForSlot({ device: 'gpu-cuda' })).toBe('cuda')
  })
  it('names an NPU slot FLM unless it runs the xdna runner', () => {
    expect(laneForSlot({ device: 'npu' })).toBe('flm')
    expect(laneForSlot({ device: 'npu', binary: 'flm' })).toBe('flm')
    expect(laneForSlot({ device: 'npu', binary: 'xdna' })).toBe('npu')
  })
})

describe('laneForBackend', () => {
  it('maps the backend tokens a model row lists', () => {
    expect(['vulkan', 'rocm', 'cuda', 'cpu'].map((b) => laneForBackend(b))).toEqual(['vulkan', 'rocm', 'cuda', 'cpu'])
    expect(laneForBackend('gpu-rocm')).toBe('rocm')
  })
  it('names the npu backend FLM only on an FLM model', () => {
    expect(laneForBackend('npu')).toBe('npu')
    expect(laneForBackend('npu', { provider_effective: 'flm' })).toBe('flm')
    expect(laneForBackend('npu', { provider_effective: 'llama-server' })).toBe('npu')
  })
  it('returns null for a token outside the vocabulary', () => {
    expect(laneForBackend('metal')).toBeNull()
    expect(laneForBackend('')).toBeNull()
    expect(laneForBackend('constructor')).toBeNull()
  })
})
