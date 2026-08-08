// isMtpEligibleModel — pins the #1642 migration fix: the helper must mirror
// the backend's model_meta.model_is_mtp_eligible (defaults.mtp wins, else
// the registry `mtp` tag) instead of the retired tag+filename-sniff rule.
import { describe, expect, it } from 'vitest'

import { isMtpEligibleModel } from './normalizeApiModel'

describe('isMtpEligibleModel', () => {
  it('is eligible when defaults.mtp is explicitly true, even with no mtp tag and no name marker', () => {
    // The #1632 boot sweep (tag_retirement) folds the legacy `mtp` tag into
    // defaults.mtp=true and STRIPS the tag — this is the migrated shape.
    expect(isMtpEligibleModel({ id: 'gemma-4-12b-it-ud-q4-k-xl', tags: ['chat'], defaults: { mtp: true } })).toBe(true)
  })

  it('is eligible when the legacy mtp tag is present and defaults.mtp is unset', () => {
    expect(isMtpEligibleModel({ id: 'some-model', tags: ['chat', 'mtp'] })).toBe(true)
  })

  it('is NOT eligible for a name-only "mtp" marker with no tag and no defaults.mtp (removed sniff)', () => {
    // The old MTP_NAME_RE fallback used to false-positive on this; the
    // backend's sniff was removed, so the UI must not diverge.
    expect(isMtpEligibleModel({ id: 'foo-mtp-q4_k_m.gguf', tags: [] })).toBe(false)
    expect(isMtpEligibleModel({ id: 'foo', path: '/models/foo-mtp-q4_k_m.gguf', tags: [] })).toBe(false)
  })

  it('is not eligible with neither signal', () => {
    expect(isMtpEligibleModel({ id: 'plain-model', tags: ['chat'] })).toBe(false)
    expect(isMtpEligibleModel({ id: 'plain-model' })).toBe(false)
  })

  it('handles null/undefined models', () => {
    expect(isMtpEligibleModel(null)).toBe(false)
    expect(isMtpEligibleModel(undefined)).toBe(false)
  })
})
