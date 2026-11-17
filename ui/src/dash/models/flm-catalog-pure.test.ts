import { describe, expect, it } from 'vitest'
import type { FlmCatalogRow } from '@/dash/npu/npu-models-pure'
import { filterFlmRows, flmRoleLines, flmState, flmUsedBy, flmUsers, sortFlmRows } from './flm-catalog-pure'

const row = (id: string, extra: Partial<FlmCatalogRow> = {}): FlmCatalogRow => ({
  id,
  installed: true,
  sizeBytes: 1_000_000_000,
  family: 'fam',
  audio: false,
  caps: ['chat'],
  inUseBy: [],
  ...extra,
})
const idle = { inFlight: false, modelId: null, pct: null }

describe('flmState', () => {
  it('names each lane once and marks the model being pulled', () => {
    const r = row('a', { inUseBy: [{ role: 'chat', slot: 'npu' }, { role: 'asr', slot: 'npu' }, { role: 'embed', slot: 'npu2' }] })
    expect(flmUsers(r)).toEqual(['npu', 'npu2'])
    expect(flmUsedBy(r)).toBe('chat on npu, asr on npu, embed on npu2')
    expect(flmState(r, idle)).toEqual({ installed: true, inUseBy: ['npu', 'npu2'], download: null })
    expect(flmState(row('b', { installed: false }), { inFlight: true, modelId: 'b', pct: 12 }).download).toEqual({ pct: 12 })
    expect(flmState(row('b', { installed: false }), { inFlight: true, modelId: 'other', pct: 12 }).download).toBeNull()
  })
})

describe('filterFlmRows', () => {
  const rows = [
    row('qwen3:4b', { family: 'qwen3', inUseBy: [{ role: 'chat', slot: 'npu' }] }),
    row('whisper-v3:turbo', { family: 'whisper', caps: ['stt'] }),
    row('embed-gemma:300m', { family: 'embed-gemma', caps: ['embed'], installed: false }),
  ]
  const ids = (f: Partial<Parameters<typeof filterFlmRows>[1]>) =>
    filterFlmRows(rows, { q: '', states: [], roles: [], ...f }, idle).map((r) => r.id)
  it('matches text against the name and the family', () => {
    expect(ids({ q: 'WHISPER' })).toEqual(['whisper-v3:turbo'])
    expect(ids({ q: 'gemma' })).toEqual(['embed-gemma:300m'])
  })
  it('ORs inside a group and ANDs across groups', () => {
    expect(ids({ states: ['inuse', 'available'] })).toEqual(['qwen3:4b', 'embed-gemma:300m'])
    expect(ids({ roles: ['stt', 'embed'] })).toEqual(['whisper-v3:turbo', 'embed-gemma:300m'])
    expect(ids({ states: ['installed'], roles: ['stt', 'embed'] })).toEqual(['whisper-v3:turbo'])
  })
})

describe('sortFlmRows', () => {
  const rows = [
    row('b', { sizeBytes: 3, family: 'zeta' }),
    row('a', { sizeBytes: null, family: null }),
    row('c', { sizeBytes: 1, family: 'alpha' }),
  ]
  const order = (field: string, dir: 'asc' | 'desc') => sortFlmRows(rows, field, dir).map((r) => r.id)
  it('sorts by name both ways without touching the input', () => {
    expect(order('name', 'asc')).toEqual(['a', 'b', 'c'])
    expect(order('name', 'desc')).toEqual(['c', 'b', 'a'])
    expect(rows.map((r) => r.id)).toEqual(['b', 'a', 'c'])
  })
  it('keeps a row with no size or family last in either direction', () => {
    expect(order('size', 'asc')).toEqual(['c', 'b', 'a'])
    expect(order('size', 'desc')).toEqual(['b', 'c', 'a'])
    expect(order('family', 'asc')).toEqual(['c', 'b', 'a'])
    expect(order('family', 'desc')).toEqual(['b', 'c', 'a'])
  })
})

describe('flmRoleLines', () => {
  it('says what the model can serve and which roles use it', () => {
    const lines = flmRoleLines(
      row('g', { caps: ['chat', 'stt'], inUseBy: [{ role: 'decision', slot: 'npu' }, { role: 'asr', slot: 'npu' }] }),
    )
    expect(lines.map((l) => [l.label, l.value])).toEqual([
      ['Chat', 'yes · decision on npu'],
      ['Speech to text', 'yes · asr on npu'],
      ['Embeddings', 'no'],
    ])
  })
})
