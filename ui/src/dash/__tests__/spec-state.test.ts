// Speculation display state for the Slots row bolt, the slot drawer line and
// the catalog drafter chip/filter (ADR-0031 §5).
import { describe, expect, it } from 'vitest'

import {
  draftVariantLabel,
  isDrafterModel,
  slotBolt,
  slotSpecLine,
} from '../spec-state.js'

const spec = (over: Record<string, unknown> = {}) => ({
  active: false,
  acceptance: null,
  mean_accepted_len: null,
  mode: 'none',
  kind: null,
  variant: null,
  unavailable_reason: null,
  ...over,
})

describe('draftVariantLabel', () => {
  it.each([
    ['lilicorr', 'LiLiCorr'],
    ['dflash2', 'DFlash2'],
    ['dspark', 'DSpark'],
    ['dflash', 'DFlash'],
    ['eagle3', 'EAGLE3'],
    ['mtp', 'MTP sidecar'],
    ['simple', 'draft model'],
  ])('%s -> %s', (v, label) => {
    expect(draftVariantLabel(v)).toBe(label)
  })
  it('passes an unknown variant through and falls back to "drafter"', () => {
    expect(draftVariantLabel('newthing')).toBe('newthing')
    expect(draftVariantLabel(null)).toBe('drafter')
  })
})

describe('isDrafterModel', () => {
  it('keys on the paired draft path', () => {
    expect(isDrafterModel({ draft: '/m/d.gguf' })).toBe(true)
    expect(isDrafterModel({ draft: null })).toBe(false)
    expect(isDrafterModel({})).toBe(false)
  })
})

describe('slotBolt', () => {
  const withDraft = { draft: '/m/d.gguf' }

  it('lit with the acceptance rate while drafting', () => {
    const b = slotBolt(spec({ active: true, mode: 'draft', variant: 'lilicorr', acceptance: 0.78 }), withDraft)
    expect(b).toEqual({ state: 'lit', title: 'drafting · LiLiCorr · 78% accepted' })
  })

  it('lit without a rate when acceptance is not a number', () => {
    const b = slotBolt(spec({ active: true, mode: 'draft', variant: 'lilicorr' }), withDraft)
    expect(b?.title).toBe('drafting · LiLiCorr')
  })

  it('labels embedded MTP heads', () => {
    const b = slotBolt(spec({ active: true, mode: 'mtp', kind: 'mtp', acceptance: 0.5 }), { mtp: true })
    expect(b?.title).toBe('drafting · MTP heads · 50% accepted')
  })

  it('n-gram is a lit bolt while active', () => {
    expect(slotBolt(spec({ active: true, mode: 'ngram' }), {})).toEqual({
      state: 'lit',
      title: 'drafting · n-gram',
    })
  })

  it.each([
    ['draft', 'drafter paired · not running'],
    ['mtp', 'MTP heads · not running'],
    ['ngram', 'n-gram · not running'],
  ])('mode %s with no live scrape reads not running, not off', (mode, title) => {
    expect(slotBolt(spec({ mode, active: false }), withDraft)).toEqual({ state: 'off', title })
  })

  it('off when a drafter is paired but speculation is off', () => {
    expect(slotBolt(spec({ mode: 'none' }), withDraft)).toEqual({
      state: 'off',
      title: 'drafter paired · speculation off',
    })
    expect(slotBolt(spec({ mode: 'none' }), { mtp: true })?.state).toBe('off')
  })

  it('warn names the reason', () => {
    const b = slotBolt(
      spec({ mode: 'none', variant: 'lilicorr', unavailable_reason: 'needs llama.cpp b11669' }),
      withDraft,
    )
    expect(b).toEqual({
      state: 'warn',
      title: 'drafter unavailable on this runner: needs llama.cpp b11669',
    })
  })

  it('no bolt when the model has neither drafter nor MTP heads', () => {
    expect(slotBolt(spec({ mode: 'none' }), {})).toBeNull()
    expect(slotBolt(spec({ mode: 'none' }), undefined)).toBeNull()
    expect(slotBolt(undefined, withDraft)).toBeNull()
  })
})

describe('slotSpecLine', () => {
  it('names the drafter with the live numbers', () => {
    const line = slotSpecLine(
      { mode: 'draft', kind: 'dflash', variant: 'lilicorr', unavailable_reason: null },
      spec({ active: true, acceptance: 0.78, mean_accepted_len: 3.44 }),
    )
    expect(line).toBe('Speculation: LiLiCorr · 78% accepted · 3.4 tok/step')
  })
  it('omits numbers that do not exist', () => {
    expect(
      slotSpecLine({ mode: 'mtp', kind: 'mtp', variant: null, unavailable_reason: null }, undefined),
    ).toBe('Speculation: MTP heads')
  })
  it('names embedded MTP heads (kind mtp) apart from a paired MTP sidecar', () => {
    expect(
      slotSpecLine({ mode: 'mtp', kind: 'mtp', variant: null, unavailable_reason: null }, undefined),
    ).toBe('Speculation: MTP heads')
    expect(
      slotSpecLine({ mode: 'draft', kind: 'mtp', variant: 'mtp', unavailable_reason: null }, undefined),
    ).toBe('Speculation: MTP sidecar')
  })
  it('off', () => {
    expect(
      slotSpecLine({ mode: 'none', kind: null, variant: null, unavailable_reason: null }, undefined),
    ).toBe('Speculation: off')
  })
  it('unavailable', () => {
    expect(
      slotSpecLine(
        { mode: 'none', kind: 'dflash', variant: 'lilicorr', unavailable_reason: 'needs b11669' },
        undefined,
      ),
    ).toBe('Speculation: drafter unavailable — needs b11669')
  })
  it('null while /resolved has not answered', () => {
    expect(slotSpecLine(undefined, undefined)).toBeNull()
  })
})
