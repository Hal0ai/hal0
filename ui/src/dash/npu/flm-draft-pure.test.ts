import { describe, expect, it } from 'vitest'
import {
  capEnabled,
  cfgFingerprint,
  draftBody,
  flmRoleIndicator,
  planModelPick,
  planRoleToggle,
  savedFlm,
  type SavedFlm,
} from './flm-draft-pure'

const saved: SavedFlm = { chat: true, asr: false, embed: false, chatModel: 'gemma4-it:e4b' }
const noModel: SavedFlm = { chat: true, asr: false, embed: false, chatModel: null }

describe('flm config helpers', () => {
  it('asr/embed need a saved chat model; chat is always switchable', () => {
    expect(capEnabled(noModel, 'asr')).toBe(false)
    expect(capEnabled(noModel, 'embed')).toBe(false)
    expect(capEnabled(noModel, 'chat')).toBe(true)
    expect(capEnabled(saved, 'embed')).toBe(true)
  })
  it('draftBody merges the draft over the saved values', () => {
    expect(draftBody({ asr: true }, saved)).toEqual({
      npu: { chat: true, asr: true, embed: false },
      model: { default: 'gemma4-it:e4b' },
    })
  })
  it('savedFlm reads [model].default first and defaults absent keys', () => {
    expect(savedFlm({ npu: {}, model: { default: 'a:1' } }, { modelDefault: 'b:2' })).toEqual({
      chat: true, asr: false, embed: false, chatModel: 'a:1',
    })
    expect(savedFlm(null, { model_id: 'c:3-FLM', npu: { chat: false, asr: true } })).toEqual({
      chat: false, asr: true, embed: false, chatModel: 'c:3',
    })
  })
  it('savedFlm falls through an empty modelDefault (useSlots normalises absent to "") to the live id', () => {
    expect(savedFlm({ npu: {} }, { modelDefault: '', model_id: 'live:1' }).chatModel).toBe('live:1')
  })
  it('cfgFingerprint tracks npu and model only', () => {
    const a = cfgFingerprint({ npu: { chat: true }, model: { default: 'a' } })
    expect(cfgFingerprint({ npu: { chat: true }, model: { default: 'a' } })).toBe(a)
    expect(cfgFingerprint({ npu: { chat: false }, model: { default: 'a' } })).not.toBe(a)
    expect(cfgFingerprint({ npu: { chat: true }, model: { default: 'b' } })).not.toBe(a)
  })
})

describe('planRoleToggle', () => {
  it('running FLM: turning a role on restarts it and names the roles that pause', () => {
    const p = planRoleToggle(saved, 'asr', true)
    expect(p.next).toEqual({ asr: true })
    expect(p.after).toBe('restart')
    expect(p.confirm?.title).toBe('Start speech-to-text?')
    expect(p.confirm?.body).toContain('While it restarts, chat stops answering')
    expect(p.confirm?.confirmLabel).toBe('Start speech-to-text and restart FLM')
    expect(p.confirm?.destructive).toBe(false)
  })
  it('running FLM: turning a role off with others on restarts, lists both others', () => {
    const p = planRoleToggle({ ...saved, asr: true, embed: true }, 'chat', true)
    expect(p.next).toEqual({ chat: false })
    expect(p.after).toBe('restart')
    expect(p.confirm?.body).toContain('speech-to-text and embeddings stop answering')
    expect(p.confirm?.destructive).toBe(true)
  })
  it('running FLM: turning off the last running role stops FLM', () => {
    const p = planRoleToggle(saved, 'chat', true)
    expect(p.after).toBe('stop')
    expect(p.confirm?.confirmLabel).toBe('Stop FLM')
    expect(p.confirm?.body).toContain('FLM stops')
  })
  it('stopped FLM: on writes and starts, off only writes, neither asks', () => {
    expect(planRoleToggle(saved, 'embed', false)).toEqual({ next: { embed: true }, after: 'start', confirm: null })
    expect(planRoleToggle(saved, 'chat', false)).toEqual({ next: { chat: false }, after: 'none', confirm: null })
  })
})

describe('planModelPick', () => {
  it('the current model is a no-op', () => {
    expect(planModelPick(saved, 'gemma4-it:e4b', true, true)).toBeNull()
  })
  it('running FLM: asks, restarts, and says a download comes first when not installed', () => {
    const inst = planModelPick(saved, 'gemma4-it:e2b', true, true)!
    expect(inst.after).toBe('restart')
    expect(inst.confirm?.confirmLabel).toBe('Switch and restart FLM')
    expect(inst.confirm?.body).not.toContain('downloads first')
    const dl = planModelPick(saved, 'qwen3:8b', false, true)!
    expect(dl.next).toEqual({ chatModel: 'qwen3:8b' })
    expect(dl.confirm?.confirmLabel).toBe('Download and switch')
    expect(dl.confirm?.body).toContain('downloads first')
  })
  it('stopped FLM: writes without asking and does not start it', () => {
    expect(planModelPick(saved, 'qwen3:8b', false, false)).toEqual({ next: { chatModel: 'qwen3:8b' }, after: 'none', confirm: null })
  })
})

describe('flmRoleIndicator', () => {
  const serving = { cls: 'serving', label: 'serving', tooltip: 'Serving' }
  it('a role that is off is grey whatever FLM does', () => {
    expect(flmRoleIndicator(serving, false, 'asr')).toMatchObject({ cls: 'offline', label: 'off', off: true })
  })
  it('a busy FLM shows chat serving and the other roles ready', () => {
    expect(flmRoleIndicator(serving, true, 'chat').cls).toBe('serving')
    expect(flmRoleIndicator(serving, true, 'embed')).toMatchObject({ cls: 'stale', label: 'ready' })
  })
  it('otherwise an on role follows the process', () => {
    expect(flmRoleIndicator({ cls: 'warming', label: 'starting' }, true, 'asr')).toMatchObject({ cls: 'warming', label: 'starting' })
    expect(flmRoleIndicator({ cls: 'offline', label: 'stopped' }, true, 'asr')).toMatchObject({ cls: 'offline', label: 'stopped', off: false })
  })
})
