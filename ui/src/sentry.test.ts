// Pins the browser-side secret scrubber in src/sentry.ts.
//
// `redactSecrets` is a hand-port of `LOG_SECRET_RE` in
// `src/hal0/api/_redact.py`. A port with no test is a leak waiting for the
// next pattern change, and this one is not theoretical: the first live test of
// the Sentry wiring shipped a bearer token from the browser that the Python
// side had already masked, because the browser hook only scrubbed URLs and
// bodies. These cases mirror `tests/observability/test_sentry.py`.

import { describe, expect, it } from 'vitest'

import { redactSecrets } from './sentry'

const MASK = '***REDACTED***'

describe('redactSecrets', () => {
  it('masks an Authorization: Bearer header, keeping the prefix', () => {
    const out = redactSecrets(
      '401 from upstream (Authorization: Bearer sk-live-abcdef0123456789)',
    ) as string
    expect(out).not.toContain('sk-live-abcdef0123456789')
    expect(out).toContain(MASK)
    // The prefix survives so an operator can tell what kind of secret it was.
    expect(out).toContain('Authorization: Bearer')
  })

  it('masks a bare Bearer token', () => {
    const out = redactSecrets('sent Bearer sk-live-abcdef0123456789 upstream') as string
    expect(out).toBe(`sent Bearer ${MASK} upstream`)
  })

  it('masks HAL0_BEARER_TOKEN= and *_KEY= assignments', () => {
    expect(redactSecrets('HAL0_BEARER_TOKEN=abc123')).toBe(`HAL0_BEARER_TOKEN=${MASK}`)
    expect(redactSecrets('HAL0_ADMIN_KEY=hal0-admin-abc123')).toBe(`HAL0_ADMIN_KEY=${MASK}`)
    expect(redactSecrets('KEY=abc123')).toBe(`KEY=${MASK}`)
  })

  it('masks a long client_id but leaves short non-secret labels alone', () => {
    expect(redactSecrets('client_id=abcdefghijklmnopqrst')).toBe(`client_id=${MASK}`)
    // The Python original length-gates this at 16+ chars so the legitimate
    // short labels (`anonymous`, the 12-hex-char hash) are not masked.
    expect(redactSecrets('client_id=anonymous')).toBe('client_id=anonymous')
  })

  it('masks every occurrence in one string, not just the first', () => {
    const out = redactSecrets('Bearer aaaaaaaaaaaa and HAL0_ADMIN_KEY=bbbbbbbbbbbb') as string
    expect(out).toBe(`Bearer ${MASK} and HAL0_ADMIN_KEY=${MASK}`)
  })

  it('leaves ordinary text and non-strings untouched', () => {
    expect(redactSecrets('slot flm failed to start on port 8081')).toBe(
      'slot flm failed to start on port 8081',
    )
    expect(redactSecrets(undefined)).toBeUndefined()
    expect(redactSecrets(42)).toBe(42)
  })
})
