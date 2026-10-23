import { describe, expect, it } from 'vitest'

import { _pct } from './OverviewPage.jsx'

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
