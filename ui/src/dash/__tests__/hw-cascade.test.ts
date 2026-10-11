import { describe, expect, it } from 'vitest'
import { applyRunnerChoice, laneValues, runnerForBackend, runnerOptions, selectedRunnerKey } from '../hw-cascade.js'

// Backends fixture mirroring system-info's `backends[key]` shape (Task 2):
// title/blurb/tier alongside the existing supported_backends/
// device_class/runtime_family/state.
const backends = {
  standard:    { title: 'Standard',    blurb: 'llama.cpp for AMD GPUs.', tier: 'default',
                 supported_backends: ['rocm', 'vulkan'], device_class: 'gpu',
                 runtime_family: 'llama-server', state: 'installed' },
  qwen3tts:    { title: 'Qwen3 TTS',   blurb: 'Higher-quality TTS.', tier: 'optional',
                 supported_backends: ['rocm'], device_class: 'gpu',
                 runtime_family: 'qwen3tts', state: 'installed' },
  cuda:        { title: 'NVIDIA CUDA', blurb: 'llama.cpp for NVIDIA.', tier: 'optional',
                 supported_backends: ['cuda'], device_class: 'gpu',
                 runtime_family: 'llama-server', state: 'installable' },
  kokoro:      { title: 'Kokoro', supported_backends: ['cpu'], device_class: 'cpu',
                 runtime_family: 'kokoro', state: 'installed' },
}

describe('runnerOptions', () => {
  it('lists gpu llama runtimes for an llm slot, default first', () => {
    const { options } = runnerOptions({ backends, device: 'gpu-rocm', slotType: 'llm', hw: { rocm: true, vulkan: true, cuda: true } })
    expect(options.map(o => o.key)).toEqual(['standard', 'cuda'])
    expect(options[0].tier).toBe('default')
  })
  it('sorts by registry tier (default → optional → unknown), then title', () => {
    const tiered = {
      b: { title: 'B optional', tier: 'optional', device_class: 'gpu', runtime_family: 'llama-server' },
      c: { title: 'C untiered', device_class: 'gpu', runtime_family: 'llama-server' },
      d: { title: 'D default', tier: 'default', device_class: 'gpu', runtime_family: 'llama-server' },
      e: { title: 'A optional', tier: 'optional', device_class: 'gpu', runtime_family: 'llama-server' },
    }
    const { options } = runnerOptions({ backends: tiered, device: 'gpu-rocm', slotType: 'llm', hw: {} })
    expect(options.map(o => o.key)).toEqual(['d', 'e', 'b', 'c'])
  })
  it('ignores a legacy is_default flag: the tier is the only sort signal', () => {
    const legacy = {
      x: { title: 'X', is_default: true, device_class: 'gpu', runtime_family: 'llama-server' },
      y: { title: 'Y', tier: 'default', device_class: 'gpu', runtime_family: 'llama-server' },
    }
    const { options } = runnerOptions({ backends: legacy, device: 'gpu-rocm', slotType: 'llm', hw: {} })
    expect(options.map(o => o.key)).toEqual(['y', 'x'])
  })
  it('hides hardware-infeasible lanes (no cuda → the cuda-only runtime is gone)', () => {
    const { options } = runnerOptions({ backends, device: 'gpu-vulkan', slotType: 'llm', hw: { rocm: false, vulkan: true, cuda: false } })
    expect(options.map(o => o.key)).toEqual(['standard'])
  })
  it('gates by slot type via runtime family', () => {
    const { options } = runnerOptions({ backends, device: 'cpu', slotType: 'tts', hw: {} })
    expect(options.map(o => o.key)).toEqual(['kokoro'])
  })
  it('never crosses device_class (rocm↔vulkan gpu devices stay clear of the cpu-class runtime)', () => {
    const rocm = runnerOptions({ backends, device: 'gpu-rocm', slotType: undefined, hw: {} })
    const vulkan = runnerOptions({ backends, device: 'gpu-vulkan', slotType: undefined, hw: {} })
    expect(rocm.options.some(o => o.key === 'kokoro')).toBe(false)
    expect(vulkan.options.some(o => o.key === 'kokoro')).toBe(false)
  })
})

describe('lanes + choice', () => {
  it('only the multi-lane default offers lane values', () => {
    const { options } = runnerOptions({ backends, device: 'gpu-rocm', slotType: 'llm', hw: { rocm: true, vulkan: true, cuda: true } })
    expect(laneValues(options[0])).toEqual(['', 'rocm', 'vulkan'])
    expect(laneValues(options[1])).toEqual([])
  })
  it('single-lane pick derives the device; multi-lane keeps it', () => {
    const { options } = runnerOptions({ backends, device: 'gpu-vulkan', slotType: 'llm', hw: { rocm: true, vulkan: true, cuda: true } })
    expect(applyRunnerChoice({ options, key: 'cuda', currentDevice: 'gpu-vulkan' }))
      .toEqual({ binary: 'cuda', device: 'gpu-cuda' })
    expect(applyRunnerChoice({ options, key: 'standard', currentDevice: 'gpu-vulkan' }))
      .toEqual({ binary: 'standard', device: 'gpu-vulkan' })
  })
  it('out-of-vocab persisted binary → null (caller renders self-option)', () => {
    const { options } = runnerOptions({ backends, device: 'gpu-rocm', slotType: 'llm', hw: { rocm: true, vulkan: true, cuda: true } })
    expect(selectedRunnerKey({ binary: 'ghostbin', options })).toBeNull()
    expect(selectedRunnerKey({ binary: '', options })).toBe('')
  })
})

describe('runnerForBackend — mirror of hal0.runners.runner_for_backend', () => {
  it('cuda → cuda, cpu → cpu, every other GPU lane → standard', () => {
    expect(runnerForBackend('cuda', 'gpu')).toBe('cuda')
    expect(runnerForBackend('cpu', 'cpu')).toBe('cpu')
    expect(runnerForBackend(undefined, 'cpu')).toBe('cpu')
    expect(runnerForBackend('rocm', 'gpu')).toBe('standard')
    expect(runnerForBackend('vulkan', 'gpu')).toBe('standard')
    expect(runnerForBackend(undefined, 'gpu')).toBe('standard')
    expect(runnerForBackend(null, null)).toBe('standard')
  })
})
