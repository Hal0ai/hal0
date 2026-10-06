// #1974: useCapabilities re-polls only while NPU presence is unsettled, at a
// cadence the backend sets via `backends_retry_in_s`: every 2 s while a probe
// is pending, once per retry window while the probe cannot answer, and not at
// all once settled or against an API that does not send the field.
import { describe, expect, it } from 'vitest'
import { capabilitiesRefetchInterval, type CapabilitiesBag } from '../useCapabilities'

/** A capabilities response with the given extra fields. */
function bag(extra: Partial<CapabilitiesBag>): CapabilitiesBag {
  return { backends: [], catalogs: {}, selections: {}, ...extra }
}

describe('capabilitiesRefetchInterval (#1974)', () => {
  it('does not poll once settled', () => {
    expect(capabilitiesRefetchInterval(bag({ backends_settled: true, backends_retry_in_s: 0 }))).toBe(false)
  })

  it('does not poll without data or against an API without the field', () => {
    expect(capabilitiesRefetchInterval(undefined)).toBe(false)
    expect(capabilitiesRefetchInterval(bag({}))).toBe(false)
  })

  it('polls every 2 s while a probe is pending', () => {
    expect(capabilitiesRefetchInterval(bag({ backends_settled: false, backends_retry_in_s: 0 }))).toBe(2000)
    expect(capabilitiesRefetchInterval(bag({ backends_settled: false }))).toBe(2000)
  })

  it('waits out the retry window while the probe cannot answer', () => {
    expect(capabilitiesRefetchInterval(bag({ backends_settled: false, backends_retry_in_s: 30 }))).toBe(30_000)
    expect(capabilitiesRefetchInterval(bag({ backends_settled: false, backends_retry_in_s: 1 }))).toBe(2000)
  })
})
