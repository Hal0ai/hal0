// `#slots/<tab>[/<sub>]`: the Slots page's route param holds the tab and, for Image Gen, the
// sub-tab after it. Pure.
import { describe, expect, it } from 'vitest'
import { isImageGenRoute, slotsSub, slotsTab } from '../slots-route'

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

// The GPU banner and the Image Gen status line say the same thing; on the Image Gen pane the
// banner is hidden so it is not said twice.
describe('isImageGenRoute', () => {
  it('is true for the Image Gen pane and its sub-tabs', () => {
    expect(isImageGenRoute('slots', 'image')).toBe(true)
    expect(isImageGenRoute('slots', 'image/generate')).toBe(true)
    expect(isImageGenRoute('slots', 'image/models')).toBe(true)
    expect(isImageGenRoute('slots', 'image/engine')).toBe(true)
  })

  it('is false for every other tab and page', () => {
    expect(isImageGenRoute('slots', null)).toBe(false)
    expect(isImageGenRoute('slots', 'endpoints')).toBe(false)
    expect(isImageGenRoute('slots', 'brain')).toBe(false)
    // A slot that happens to start with "image" is not the pane.
    expect(isImageGenRoute('slots', 'image-gen-slot')).toBe(false)
    expect(isImageGenRoute('overview', 'image')).toBe(false)
    expect(isImageGenRoute('models', null)).toBe(false)
    expect(isImageGenRoute(undefined, undefined)).toBe(false)
  })
})

describe('slot edit routes', () => {
  it('slotEditHash routes a reserved name through edit/', async () => {
    const { slotEditHash } = await import('../slots-route')
    expect(slotEditHash('npu')).toBe('#slots/edit/npu')
    expect(slotEditHash('agent')).toBe('#slots/agent')
  })
  it('slotEditTarget resolves the drawer target', async () => {
    const { slotEditTarget } = await import('../slots-route')
    expect(slotEditTarget('edit/npu')).toBe('npu')
    expect(slotEditTarget('agent')).toBe('agent')
    expect(slotEditTarget('npu')).toBeNull()
    expect(slotEditTarget('npu/lanes')).toBeNull()
    expect(slotEditTarget(null)).toBeNull()
  })
})
