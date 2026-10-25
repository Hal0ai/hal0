// `#slots/<tab>[/<sub>]`: the Slots page's route param holds the tab and, for Image Gen, the
// sub-tab after it. Pure.
import { describe, expect, it } from 'vitest'
import { slotsSub, slotsTab } from '../slots-route'

describe('slotsTab', () => {
  it('is the first segment', () => {
    expect(slotsTab('image')).toBe('image')
    expect(slotsTab('image/generate')).toBe('image')
    expect(slotsTab('image/')).toBe('image')
    expect(slotsTab('endpoints')).toBe('endpoints')
  })

  it('is a slot name for the edit drawer, and null for a bare #slots', () => {
    expect(slotsTab('brain')).toBe('brain')
    expect(slotsTab(null)).toBeNull()
    expect(slotsTab(undefined)).toBeNull()
    expect(slotsTab('')).toBeNull()
  })
})

describe('slotsSub', () => {
  it('is the second segment', () => {
    expect(slotsSub('image/generate')).toBe('generate')
    expect(slotsSub('image/models/')).toBe('models')
  })

  it('is null when there is none', () => {
    expect(slotsSub('image')).toBeNull()
    expect(slotsSub('image/')).toBeNull()
    expect(slotsSub(null)).toBeNull()
    expect(slotsSub(undefined)).toBeNull()
  })
})
