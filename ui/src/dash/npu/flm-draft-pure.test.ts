import { describe, expect, it } from 'vitest'
import { capEnabled, cfgFingerprint, draftBody, draftCount, pickChatModel, savedFlm, toggleCap, type SavedFlm } from './flm-draft-pure'

const saved: SavedFlm = { chat: true, asr: false, embed: false, chatModel: 'gemma4-it:e4b' }
const noModel: SavedFlm = { chat: true, asr: false, embed: false, chatModel: null }

describe('flm draft', () => {
  it('toggling twice returns an empty draft', () => {
    expect(toggleCap(toggleCap({}, saved, 'asr'), saved, 'asr')).toEqual({})
    expect(toggleCap({}, saved, 'asr')).toEqual({ asr: true })
  })
  it('picking the saved model returns an empty draft', () => {
    expect(pickChatModel({}, saved, 'gemma4-it:e4b')).toEqual({})
    expect(pickChatModel({ chatModel: 'x' }, saved, 'gemma4-it:e4b')).toEqual({})
    expect(pickChatModel({}, saved, 'qwen3:8b')).toEqual({ chatModel: 'qwen3:8b' })
  })
  it('asr/embed need a saved or staged chat model', () => {
    expect(capEnabled(noModel, {}, 'asr')).toBe(false)
    expect(capEnabled(noModel, {}, 'embed')).toBe(false)
    expect(capEnabled(noModel, { chatModel: 'qwen3:8b' }, 'asr')).toBe(true)
    expect(capEnabled(noModel, {}, 'chat')).toBe(true)
    expect(capEnabled(saved, {}, 'embed')).toBe(true)
  })
  it('draftBody merges the draft over the saved values', () => {
    expect(draftBody({ asr: true }, saved)).toEqual({
      npu: { chat: true, asr: true, embed: false },
      model: { default: 'gemma4-it:e4b' },
    })
    expect(draftCount({ asr: true, chatModel: 'x' })).toBe(2)
  })
  it('savedFlm reads [model].default first and defaults absent keys', () => {
    expect(savedFlm({ npu: {}, model: { default: 'a:1' } }, { modelDefault: 'b:2' })).toEqual({
      chat: true, asr: false, embed: false, chatModel: 'a:1',
    })
    expect(savedFlm(null, { model_id: 'c:3-FLM', npu: { chat: false, asr: true } })).toEqual({
      chat: false, asr: true, embed: false, chatModel: 'c:3',
    })
  })
  it('cfgFingerprint tracks npu and model only', () => {
    const a = cfgFingerprint({ npu: { chat: true }, model: { default: 'a' } })
    expect(cfgFingerprint({ npu: { chat: true }, model: { default: 'a' } })).toBe(a)
    expect(cfgFingerprint({ npu: { chat: false }, model: { default: 'a' } })).not.toBe(a)
    expect(cfgFingerprint({ npu: { chat: true }, model: { default: 'b' } })).not.toBe(a)
  })
})
