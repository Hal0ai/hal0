import { describe, expect, it } from 'vitest'
import {
  applyBackendChoice,
  backendOptions,
  optionValue,
  selectedBackendValue,
} from '../hw-cascade.js'

// Catalog fixture mirroring system-info's RUNNER_IMAGES shape. The combined
// image ships one binary that serves BOTH llama backends (rocmfpx · rocm and
// rocmfpx · vulkan) plus a vulkan-only sibling — the exact shape the cascade
// exists for.
const COMBINED = 'ghcr.io/hal0ai/hal0-combined-upstream:0829'
const backends = {
  rocmfpx: {
    image: COMBINED,
    backend: 'rocm',
    supported_backends: ['rocm', 'vulkan'],
    device_class: 'gpu',
    runtime_family: 'llama-server',
    supports: { specialties: ['promptforge'] },
  },
  otherbin: {
    image: COMBINED,
    backend: 'vulkan',
    device_class: 'gpu',
    runtime_family: 'llama-server',
  },
  cpubin: {
    image: 'ghcr.io/hal0ai/hal0-cpu:1',
    backend: 'cpu',
    device_class: 'cpu',
    runtime_family: 'llama-server',
  },
  kokorobin: {
    image: 'ghcr.io/hal0ai/hal0-tts:1',
    backend: 'rocm',
    device_class: 'gpu',
    runtime_family: 'kokoro',
  },
}

const gpuLlm = { device: 'gpu-rocm', slotType: 'llm' }

describe('backendOptions — release-catalog default (no image pin)', () => {
  const res = backendOptions({ backends, pinnedImage: '', ...gpuLlm })
  const values = res.options.map((o) => optionValue(o.binary, o.backend))

  it('expands multi-backend binaries into one option per (binary, backend) pair', () => {
    expect(values).toContain(optionValue('rocmfpx', 'rocm'))
    expect(values).toContain(optionValue('rocmfpx', 'vulkan'))
    expect(values).toContain(optionValue('otherbin', 'vulkan'))
  })
  it('derives the target device from the backend token', () => {
    const rv = res.options.find((o) => o.binary === 'rocmfpx' && o.backend === 'vulkan')
    expect(rv?.device).toBe('gpu-vulkan')
    const rr = res.options.find((o) => o.binary === 'rocmfpx' && o.backend === 'rocm')
    expect(rr?.device).toBe('gpu-rocm')
  })
  it('filters by device class and slot type', () => {
    expect(values.some((v) => v.startsWith('cpubin'))).toBe(false) // cpu class
    expect(values.some((v) => v.startsWith('kokorobin'))).toBe(false) // tts family
  })
  it('carries specialties through for the option label', () => {
    const rr = res.options.find((o) => o.binary === 'rocmfpx' && o.backend === 'rocm')
    expect(rr?.specialties).toEqual(['promptforge'])
  })
  it('is neither a fallback nor an empty pin', () => {
    expect(res.fallback).toBe(false)
    expect(res.emptyPin).toBe(false)
  })
})

describe('backendOptions — catalog image pinned', () => {
  it('lists only pairs shipping in the pinned image', () => {
    const res = backendOptions({ backends, pinnedImage: COMBINED, ...gpuLlm })
    const values = res.options.map((o) => optionValue(o.binary, o.backend))
    expect(values).toEqual(
      expect.arrayContaining([
        optionValue('rocmfpx', 'rocm'),
        optionValue('rocmfpx', 'vulkan'),
        optionValue('otherbin', 'vulkan'),
      ]),
    )
    expect(values.some((v) => v.startsWith('cpubin'))).toBe(false)
  })
  it('a catalog image whose binaries do not fit the slot → emptyPin, no options', () => {
    const res = backendOptions({
      backends,
      pinnedImage: 'ghcr.io/hal0ai/hal0-cpu:1',
      ...gpuLlm,
    })
    expect(res.options).toEqual([])
    expect(res.emptyPin).toBe(true)
    expect(res.fallback).toBe(false)
  })
})

describe('backendOptions — custom (non-catalog) image ref', () => {
  it('cannot enumerate, so falls back to the device-fit union and flags it', () => {
    const res = backendOptions({
      backends,
      pinnedImage: 'ghcr.io/other/debug-build:abc',
      ...gpuLlm,
    })
    const values = res.options.map((o) => optionValue(o.binary, o.backend))
    expect(values).toContain(optionValue('rocmfpx', 'rocm'))
    expect(res.fallback).toBe(true)
    expect(res.emptyPin).toBe(false)
  })
})

describe('backendOptions — non-GPU slot never flips device', () => {
  it('cpu slot lists only its own backend and keeps the device', () => {
    const res = backendOptions({
      backends,
      pinnedImage: '',
      device: 'cpu',
      slotType: 'llm',
    })
    const values = res.options.map((o) => optionValue(o.binary, o.backend))
    expect(values).toEqual([optionValue('cpubin', 'cpu')])
    expect(res.options[0].device).toBe('cpu')
  })
})

describe('applyBackendChoice', () => {
  const res = backendOptions({ backends, pinnedImage: '', ...gpuLlm })
  it('picking a cross-backend pair flips the device with the binary', () => {
    expect(
      applyBackendChoice(res.options, optionValue('rocmfpx', 'vulkan'), 'gpu-rocm'),
    ).toEqual({ binary: 'rocmfpx', device: 'gpu-vulkan' })
  })
  it('picking a same-backend pair keeps the device', () => {
    expect(
      applyBackendChoice(res.options, optionValue('rocmfpx', 'rocm'), 'gpu-rocm'),
    ).toEqual({ binary: 'rocmfpx', device: 'gpu-rocm' })
  })
  it('an unknown value (out-of-vocab persisted pair) keeps the current device', () => {
    expect(applyBackendChoice(res.options, optionValue('ghost', 'rocm'), 'gpu-rocm')).toEqual({
      binary: 'ghost',
      device: 'gpu-rocm',
    })
  })
})

describe('selectedBackendValue', () => {
  const res = backendOptions({ backends, pinnedImage: '', ...gpuLlm })
  it('resolves the (binary, device) pair to its option value', () => {
    expect(
      selectedBackendValue({ binary: 'rocmfpx', device: 'gpu-vulkan', options: res.options }),
    ).toBe(optionValue('rocmfpx', 'vulkan'))
  })
  it('returns null for a persisted pair outside the option list (screenshot mismatch)', () => {
    const pinned = backendOptions({
      backends,
      pinnedImage: 'ghcr.io/hal0ai/hal0-cpu:1',
      ...gpuLlm,
    })
    expect(
      selectedBackendValue({ binary: 'otherbin', device: 'gpu-rocm', options: pinned.options }),
    ).toBe(null)
  })
  it('returns empty string when no binary is pinned (auto)', () => {
    expect(selectedBackendValue({ binary: '', device: 'gpu-rocm', options: res.options })).toBe('')
  })
})
