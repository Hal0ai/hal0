import { describe, expect, it } from 'vitest'
import { applyRunnerChoice, laneValues, runnerOptions, selectedRunnerKey } from '../hw-cascade.js'

// Backends fixture mirroring system-info's `backends[key]` shape (Task 2):
// title/blurb/is_default alongside the existing supported_backends/
// device_class/runtime_family/state.
const backends = {
  rocmfpx:     { title: 'Standard',    blurb: 'Runs everything.', is_default: true,
                 supported_backends: ['rocm', 'vulkan'], device_class: 'gpu',
                 runtime_family: 'llama-server', state: 'installed' },
  promptforge: { title: 'PromptForge', blurb: 'Accelerated PF.', is_default: false,
                 supported_backends: ['rocm'], device_class: 'gpu',
                 runtime_family: 'llama-server', state: 'installed' },
  strix:       { title: 'Strix',       blurb: 'Qwen4 + MTP.', is_default: false,
                 supported_backends: ['vulkan'], device_class: 'gpu',
                 runtime_family: 'llama-server', state: 'installable' },
  kokoro:      { title: 'Kokoro', supported_backends: ['cpu'], device_class: 'cpu',
                 runtime_family: 'kokoro', state: 'installed' },
}

describe('runnerOptions', () => {
  it('lists gpu llama runtimes for an llm slot, default first', () => {
    const { options } = runnerOptions({ backends, device: 'gpu-rocm', slotType: 'llm', hw: { rocm: true, vulkan: true } })
    expect(options.map(o => o.key)).toEqual(['rocmfpx', 'promptforge', 'strix'])
    expect(options[0].isDefault).toBe(true)
  })
  it('hides hardware-infeasible lanes (no kfd → rocm-only rows gone)', () => {
    const { options } = runnerOptions({ backends, device: 'gpu-vulkan', slotType: 'llm', hw: { rocm: false, vulkan: true } })
    expect(options.map(o => o.key)).toEqual(['rocmfpx', 'strix'])
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
    const { options } = runnerOptions({ backends, device: 'gpu-rocm', slotType: 'llm', hw: { rocm: true, vulkan: true } })
    expect(laneValues(options[0])).toEqual(['', 'rocm', 'vulkan'])
    expect(laneValues(options[1])).toEqual([])
  })
  it('single-lane pick derives the device; multi-lane keeps it', () => {
    const { options } = runnerOptions({ backends, device: 'gpu-vulkan', slotType: 'llm', hw: { rocm: true, vulkan: true } })
    expect(applyRunnerChoice({ options, key: 'promptforge', currentDevice: 'gpu-vulkan' }))
      .toEqual({ binary: 'promptforge', device: 'gpu-rocm' })
    expect(applyRunnerChoice({ options, key: 'rocmfpx', currentDevice: 'gpu-vulkan' }))
      .toEqual({ binary: 'rocmfpx', device: 'gpu-vulkan' })
  })
  it('out-of-vocab persisted binary → null (caller renders self-option)', () => {
    const { options } = runnerOptions({ backends, device: 'gpu-rocm', slotType: 'llm', hw: { rocm: true, vulkan: true } })
    expect(selectedRunnerKey({ binary: 'ghostbin', options })).toBeNull()
    expect(selectedRunnerKey({ binary: '', options })).toBe('')
  })
})
