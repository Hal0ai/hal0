import { describe, expect, it } from 'vitest'

import { slotRunnerKey } from '../useRuntimes'

const BACKENDS = {
  standard: { runtime_family: 'llama-server' },
  cpu: { runtime_family: 'llama-server' },
  cuda: { runtime_family: 'llama-server' },
  kokoro: { runtime_family: 'kokoro' },
  moonshine: { runtime_family: 'moonshine' },
  flm: { runtime_family: 'flm' },
}

describe('slotRunnerKey', () => {
  it('uses an explicit binary first', () => {
    expect(slotRunnerKey({ binary: 'cuda', device: 'cpu' }, BACKENDS)).toBe('cuda')
  })

  it('maps a fixed-family slot to its family runner, not the device default', () => {
    expect(slotRunnerKey({ device: 'cpu', provider: 'kokoro' }, BACKENDS)).toBe('kokoro')
    expect(slotRunnerKey({ device: 'cpu', provider: 'moonshine' }, BACKENDS)).toBe('moonshine')
    expect(slotRunnerKey({ device: 'npu', provider: 'flm' }, BACKENDS)).toBe('flm')
  })

  it('falls back to the llama runner for the device', () => {
    expect(slotRunnerKey({ device: 'cpu', provider: 'llama-server' }, BACKENDS)).toBe('cpu')
    expect(slotRunnerKey({ device: 'gpu-rocm' }, BACKENDS)).toBe('standard')
    expect(slotRunnerKey({ device: 'gpu-cuda' }, BACKENDS)).toBe('cuda')
    expect(slotRunnerKey({ device: 'npu' }, BACKENDS)).toBeUndefined()
  })

  it('finds no runner for a family this install does not list', () => {
    expect(slotRunnerKey({ device: 'cpu', provider: 'qwen3tts' }, BACKENDS)).toBeUndefined()
  })
})
