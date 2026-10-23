import { describe, expect, it } from 'vitest'

import { _gpuUtil, _pct } from './OverviewPage.jsx'

describe('_gpuUtil', () => {
  it('shows the plain percentage when the counter is trustworthy', () => {
    expect(_gpuUtil({ gpu_util: 0.32, util_is_forced_high: false })).toBe('32%')
    expect(_gpuUtil({ gpu_util: 0.32 })).toBe('32%')
  })

  it('captions the value as pinned on a forced-high host (matches telemetry header)', () => {
    expect(_gpuUtil({ gpu_util: 1, util_is_forced_high: true })).toBe(
      '100% · pinned (forced performance level)',
    )
  })

  it('keeps the placeholder when there is no reading', () => {
    expect(_gpuUtil({ gpu_util: null, util_is_forced_high: true })).toBe('—')
  })
})

describe('_pct', () => {
  it('renders the 0..1 API fraction as a percentage', () => {
    expect(_pct(0.32)).toBe('32%')
    expect(_pct(1)).toBe('100%')
    expect(_pct(0)).toBe('0%')
  })

  it('keeps the missing-value placeholder', () => {
    expect(_pct(null)).toBe('—')
    expect(_pct(undefined)).toBe('—')
  })
})
