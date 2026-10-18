import { describe, expect, it } from 'vitest'
import { draftKey, draftSourceLine, draftVerdict, draftKindNeeded, missingRunnerNote, mergeDrafts } from '../draft-pick.js'

const d = (id: string, repo: string, source = 'repo', variant_hint: string | null = null) =>
  ({ id, repo, source, variant_hint, kind_hint: null, size_bytes: 1, size: '1 B' })

describe('draftSourceLine', () => {
  it('says this repo for a drafter in the inspected repo', () => {
    expect(draftSourceLine(d('a.gguf', 'o/r'), 'o/r')).toBe('found in this repo')
  })
  it('names the companion repo', () => {
    expect(draftSourceLine(d('a.gguf', 'o/other', 'companion'), 'o/r')).toBe('found in o/other')
  })
  it('prompts for a repo when nothing was found', () => {
    expect(draftSourceLine(undefined, 'o/r')).toBe('none found — paste a repo below')
  })
})

describe('draftVerdict', () => {
  it('lists the matched facts that are present', () => {
    const r = { fit: { ok: true, reasons: [] }, draft: { embedding_length: 5120, block_size: 8, target_layers: [1, 2, 3, 4, 5, 6] }, target: {} }
    expect(draftVerdict(r)).toBe('matches: hidden 5120, 6 target layers, block 8')
  })
  it('omits missing facts', () => {
    expect(draftVerdict({ fit: { ok: true, reasons: [] }, draft: { embedding_length: 2048 }, target: {} })).toBe('matches: hidden 2048')
  })
  it('gives the first reason on mismatch', () => {
    expect(draftVerdict({ fit: { ok: false, reasons: ['hidden size differs', 'x'] }, draft: {}, target: {} })).toBe('hidden size differs')
  })
  it('is empty without a response', () => {
    expect(draftVerdict(undefined)).toBe('')
  })
})

describe('draftKindNeeded + missingRunnerNote', () => {
  const backends = {
    a: { state: 'installed', supports: { draft_kinds: ['dflash', 'mtp'] } },
    b: { state: 'installable', supports: { draft_kinds: ['lilicorr'] } },
  }
  it('needs lilicorr for a lilicorr variant', () => {
    expect(draftKindNeeded({ kind: 'dflash', variant: 'lilicorr' })).toBe('lilicorr')
    expect(draftKindNeeded({ kind: 'eagle3', variant: 'eagle3' })).toBe('eagle3')
  })
  it('notes when no installed runner lists the kind', () => {
    expect(missingRunnerNote(backends, 'lilicorr')).toBe('no installed runner can use this drafter yet (needs lilicorr)')
    expect(missingRunnerNote(backends, 'eagle3')).toBe('no installed runner can use this drafter yet (needs eagle3)')
  })
  it('is empty when an installed runner lists it, or data is absent', () => {
    expect(missingRunnerNote(backends, 'dflash')).toBe('')
    expect(missingRunnerNote(undefined, 'dflash')).toBe('')
    expect(missingRunnerNote(backends, '')).toBe('')
  })
})

describe('mergeDrafts', () => {
  it('puts manual candidates first and dedupes by key', () => {
    const a = d('a.gguf', 'o/r'); const m = d('m.gguf', 'x/y')
    expect(mergeDrafts([m], [a, m]).map(draftKey)).toEqual(['x/y::m.gguf', 'o/r::a.gguf'])
  })
})
