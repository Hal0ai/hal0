// Pins the FLM [npu] modality defaults to the backend contract
// (config/schema.py NpuConfig + providers/flm.py build_env): chat defaults ON
// when the key is absent; asr/embed default OFF unless explicitly true.
//
// Regression: the NPU occupancy card rendered its STT/Embed pills with
// `npu.asr !== false` (absent ⇒ On) while the edit drawer seeded its toggles
// with `npu.asr === true` (absent ⇒ Off) — the two surfaces showed opposite
// states for the same slot. Both now read through npuModalityOn.

import { describe, expect, it } from 'vitest'

import { npuModalityOn, npuPillOn, npuRoleForSlot } from '../npu-modality.js'

describe('npuModalityOn', () => {
  it('defaults chat ON, asr/embed OFF when the [npu] table is absent', () => {
    for (const npu of [undefined, null, {}]) {
      expect(npuModalityOn(npu, 'chat')).toBe(true)
      expect(npuModalityOn(npu, 'asr')).toBe(false)
      expect(npuModalityOn(npu, 'embed')).toBe(false)
    }
  })

  it('honours explicit values', () => {
    const npu = { chat: false, asr: true, embed: true }
    expect(npuModalityOn(npu, 'chat')).toBe(false)
    expect(npuModalityOn(npu, 'asr')).toBe(true)
    expect(npuModalityOn(npu, 'embed')).toBe(true)
  })

  it('treats a bare [npu] section with partial keys per-key', () => {
    expect(npuModalityOn({ asr: true }, 'asr')).toBe(true)
    expect(npuModalityOn({ asr: true }, 'embed')).toBe(false)
    expect(npuModalityOn({ asr: true }, 'chat')).toBe(true)
  })
})

describe('npuPillOn (#1661)', () => {
  it('asr/embed resolve off npu_modality_active, not the raw [npu] table', () => {
    // Model-less anchor: the write already landed npu.asr=true on disk (or
    // is still in flight), but the backend's npu_modality_active gate is
    // False whenever the anchor has no model bound — the pill must agree.
    const shadow = { type: 'transcription', npu_modality_active: false }
    expect(npuPillOn(shadow, { asr: true }, 'asr')).toBe(false)
  })

  it('reflects npu_modality_active true once the anchor is actually activated', () => {
    const shadow = { type: 'embedding', npu_modality_active: true }
    expect(npuPillOn(shadow, { embed: true }, 'embed')).toBe(true)
  })

  it('treats a missing npu_modality_active field as off', () => {
    const shadow = { type: 'transcription' }
    expect(npuPillOn(shadow, { asr: true }, 'asr')).toBe(false)
  })

  it('chat still reads the raw [npu] table (the anchor carries no npu_modality_active field)', () => {
    const anchor = { type: 'llm' }
    expect(npuPillOn(anchor, { chat: true }, 'chat')).toBe(true)
    expect(npuPillOn(anchor, { chat: false }, 'chat')).toBe(false)
    expect(npuPillOn(anchor, undefined, 'chat')).toBe(true)
  })
})

describe('npuRoleForSlot', () => {
  it('derives the role from the slot TYPE, never the display name', () => {
    // Shadow semantics everywhere else key off type — a shadow with a
    // non-canonical name (legacy `stt-npu`, operator rename) must not fall
    // into the chat branch and flip the anchor's chat modality.
    expect(npuRoleForSlot({ name: 'weird-name', type: 'transcription' })).toBe('asr')
    expect(npuRoleForSlot({ name: 'weird-name', type: 'embedding' })).toBe('embed')
    expect(npuRoleForSlot({ name: 'flm-stt', type: 'transcription' })).toBe('asr')
    expect(npuRoleForSlot({ name: 'flm-embed', type: 'embedding' })).toBe('embed')
  })

  it('treats the anchor (type=llm) and unknowns as chat', () => {
    expect(npuRoleForSlot({ name: 'flm', type: 'llm' })).toBe('chat')
    expect(npuRoleForSlot({ name: 'npu', type: 'llm' })).toBe('chat')
    expect(npuRoleForSlot(undefined)).toBe('chat')
  })
})
