import { describe, expect, it } from 'vitest'

import { drafterNote, isDrafterRow } from '../scan-rows.js'

describe('scan drafter rows', () => {
  it('flags only role=draft rows', () => {
    expect(isDrafterRow({ path: '/m/a.gguf', kind: 'chat' })).toBe(false)
    expect(isDrafterRow({ path: '/m/d.gguf', role: 'draft' })).toBe(true)
  })

  it('names the model a paired drafter goes with', () => {
    expect(drafterNote({ role: 'draft', pairs_with: '/m/a.gguf' })).toBe('drafter → /m/a.gguf')
  })

  it('renders known and unknown skip reasons', () => {
    expect(drafterNote({ role: 'draft', reason: 'drafter_mismatch' })).toBe(
      'drafter · does not fit the model beside it',
    )
    expect(drafterNote({ role: 'draft', reason: 'drafter_new_thing' })).toBe(
      'drafter · drafter_new_thing',
    )
  })

  it('is empty for an ordinary row', () => {
    expect(drafterNote({ path: '/m/a.gguf' })).toBe('')
  })
})
