import { describe, expect, it } from 'vitest'

import { formatGpuUtil, gpuUtilReading } from './gpuUtil'

describe('gpuUtilReading', () => {
  it('reads a trustworthy counter as a plain percentage', () => {
    expect(gpuUtilReading({ gpu_util: 0.32, util_is_forced_high: false })).toEqual({
      pct: 32,
      pinned: false,
      text: '32%',
    })
    expect(gpuUtilReading({ gpu_util: 0.32 }).pinned).toBe(false)
  })

  it('flags a forced-high counter as pinned', () => {
    expect(gpuUtilReading({ gpu_util: 1, util_is_forced_high: true })).toEqual({
      pct: 100,
      pinned: true,
      text: '100%',
    })
  })

  it('has no reading, and is never pinned, without a number', () => {
    for (const s of [null, undefined, {}, { gpu_util: null, util_is_forced_high: true }]) {
      expect(gpuUtilReading(s)).toEqual({ pct: null, pinned: false, text: '—' })
    }
  })
})

describe('formatGpuUtil', () => {
  it('captions a pinned value like the telemetry header', () => {
    expect(formatGpuUtil({ gpu_util: 1, util_is_forced_high: true })).toBe(
      '100% · pinned (forced performance level)',
    )
  })

  it('leaves a normal value and the placeholder bare', () => {
    expect(formatGpuUtil({ gpu_util: 0.32 })).toBe('32%')
    expect(formatGpuUtil({ gpu_util: null, util_is_forced_high: true })).toBe('—')
  })
})
