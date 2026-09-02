import { describe, expect, it } from 'vitest'
import { slotsUsingModel, footSummary } from '../model-usage.js'

describe('slotsUsingModel', () => {
  it('matches by model_default or live model, dedupes, preserves order', () => {
    const slots = [
      { name: 'agent', model_default: 'm1', model: 'm1' },
      { name: 'brain', model_default: 'm2', model: 'm1' },
      { name: 'tts', model_default: 'm3', model: null },
    ]
    expect(slotsUsingModel(slots, 'm1')).toEqual([{ name: 'agent' }, { name: 'brain' }])
    expect(slotsUsingModel(slots, 'm4')).toEqual([])
    expect(slotsUsingModel(undefined, 'm1')).toEqual([])
  })
})

describe('footSummary', () => {
  it("clean drawer → dim 'no changes'", () => {
    const changes = {
      name: false,
      provider: false,
      mmproj: false,
      hfRepo: false,
      hfFilename: false,
      extra: false,
      profile: false,
      ctx: false,
      chatTemplate: false,
      mtp: false,
      thinking: false,
      jinja: false,
      vision: false,
      any: false,
    }
    const usingSlots = [{ name: 'agent' }]
    expect(footSummary(changes, usingSlots)).toBe('no changes')
  })

  it('staged changes → counted summary with field names and restart set', () => {
    const changes = {
      name: false,
      provider: false,
      mmproj: false,
      hfRepo: false,
      hfFilename: false,
      extra: true,
      profile: false,
      ctx: true,
      chatTemplate: false,
      mtp: false,
      thinking: false,
      jinja: false,
      vision: false,
      any: true,
    }
    const usingSlots = [{ name: 'agent' }, { name: 'brain' }]
    expect(footSummary(changes, usingSlots)).toBe('2 changes — flags · context ⟳ restarts 2 slots: agent · brain')
  })

  it('zero using slots → restart clause omitted', () => {
    const changes = {
      name: false,
      provider: false,
      mmproj: false,
      hfRepo: false,
      hfFilename: false,
      extra: true,
      profile: false,
      ctx: true,
      chatTemplate: false,
      mtp: false,
      thinking: false,
      jinja: false,
      vision: false,
      any: true,
    }
    const usingSlots: { name: string }[] = []
    expect(footSummary(changes, usingSlots)).toBe('2 changes — flags · context')
  })

  it('source fields (hfRepo/hfFilename) dedupe to one display name', () => {
    const changes = {
      name: false,
      provider: false,
      mmproj: false,
      hfRepo: true,
      hfFilename: true,
      extra: false,
      profile: false,
      ctx: false,
      chatTemplate: false,
      mtp: false,
      thinking: false,
      jinja: false,
      vision: false,
      any: true,
    }
    const usingSlots = [{ name: 'agent' }]
    expect(footSummary(changes, usingSlots)).toBe('1 changes — source ⟳ restarts 1 slots: agent')
  })

  it('override fields (mtp/thinking/jinja/vision) dedupe to one display name', () => {
    const changes = {
      name: false,
      provider: false,
      mmproj: false,
      hfRepo: false,
      hfFilename: false,
      extra: false,
      profile: false,
      ctx: false,
      chatTemplate: false,
      mtp: true,
      thinking: true,
      jinja: false,
      vision: true,
      any: true,
    }
    const usingSlots = [{ name: 'agent' }]
    expect(footSummary(changes, usingSlots)).toBe('1 changes — overrides ⟳ restarts 1 slots: agent')
  })
})
