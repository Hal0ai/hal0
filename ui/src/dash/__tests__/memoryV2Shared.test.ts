// Memory v2 (Bank workspace UI, task C1) — pure-helper unit tests for
// memory-v2-shared.jsx's fmtN/dayKey (ported verbatim from the design
// handoff prototype). The module's primary contract is the window-globals
// publish (`window.MemV2`), per the no-ES-imports-across-dash/*.jsx
// constraint — these two pure helpers are also named-exported purely for
// this vitest coverage.
import { describe, expect, it } from 'vitest'

import { dayKey, fmtN } from '../memory-v2-shared.jsx'

describe('fmtN', () => {
  it('formats with en-US thousands separators', () => {
    expect(fmtN(1000)).toBe('1,000')
    expect(fmtN(10823)).toBe('10,823')
    expect(fmtN(0)).toBe('0')
  })
})

describe('dayKey', () => {
  it('extracts MM/DD from a local-ISO timestamp', () => {
    expect(dayKey('2026-08-21T00:41')).toBe('08/21')
    expect(dayKey('2026-05-25T09:00:00.000Z')).toBe('05/25')
  })
})
