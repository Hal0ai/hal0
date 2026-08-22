// Memory v2 (Bank workspace UI, task C1) — pure-helper unit tests for
// memory-v2-shared.jsx's fmtN/dayKey (ported verbatim from the design
// handoff prototype). The module's primary contract is the window-globals
// publish (`window.MemV2`), per the no-ES-imports-across-dash/*.jsx
// constraint — these two pure helpers are also named-exported purely for
// this vitest coverage.
import { describe, expect, it } from 'vitest'

import { dayKey, fmtN, isUuidLike, mvErrorHeadline } from '../memory-v2-shared.jsx'

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

// Post-smoke fix: MvError used to say "Memory engine unreachable" for every
// failure — live-verified wrong for the 404 the units route 404'd with
// against CT105's stale-deploy backend (2026-08-21). mvErrorHeadline
// branches by the error's HTTP status instead of one blanket message.
describe('mvErrorHeadline', () => {
  it('404: deploy-skew copy, ignores the detail message', () => {
    expect(mvErrorHeadline({ status: 404, message: 'API error 404' }, 'units')).toBe(
      "This install's API doesn't serve this view yet",
    )
  })

  it('other 4xx (e.g. 422): "Request rejected" with the detail', () => {
    expect(mvErrorHeadline({ status: 422, message: 'memory engine returned an error' }, 'reflect')).toBe(
      'Request rejected — memory engine returned an error',
    )
    expect(mvErrorHeadline({ status: 400, message: 'invalid bank' }, 'units')).toBe(
      'Request rejected — invalid bank',
    )
  })

  it('503/network/no-status: original "unreachable" copy, unchanged', () => {
    expect(mvErrorHeadline({ status: 503, message: 'hindsight-api is not responding' }, 'units')).toBe(
      'Memory engine unreachable — hindsight-api is not responding',
    )
    expect(mvErrorHeadline(undefined, 'units')).toBe('Memory engine unreachable — could not load units')
  })

  it('501 (engine_unsupported) falls into the unreachable bucket, not "rejected"', () => {
    // Not explicitly redefined by the post-smoke fix scope — 501 is a 5xx,
    // so it lands in the same bucket as 503/network rather than the 4xx
    // "rejected" bucket.
    expect(mvErrorHeadline({ status: 501, message: 'engine unsupported' }, 'units')).toBe(
      'Memory engine unreachable — engine unsupported',
    )
  })
})

// Live-observed (2026-08-21): a real production bank's tag set was heavily
// populated with auto-generated session/document-id UUIDs alongside real
// human tags — a bare UUID as a chip/bubble label carries zero information.
describe('isUuidLike', () => {
  it('matches a bare UUIDv4-shaped string, case-insensitively', () => {
    expect(isUuidLike('0c386b1b-1fe1-4bc8-9a24-7853eeaf0819')).toBe(true)
    expect(isUuidLike('0C386B1B-1FE1-4BC8-9A24-7853EEAF0819')).toBe(true)
  })

  it('does not match real human tags, including ones that merely contain a dash', () => {
    expect(isUuidLike('agent:claude')).toBe(false)
    expect(isUuidLike('project:hal0-web')).toBe(false)
    expect(isUuidLike('session:019f8563-e830-75d8-b77c-453428ff38e2')).toBe(false)
    expect(isUuidLike('gotcha')).toBe(false)
  })
})
