// slot-shared — pure-logic unit coverage for the slot→model resolution helpers.
//
// Named `.test.ts` deliberately: vitest.config.ts's `include` is
// ['src/**/*.test.ts', 'tests/e2e/*.test.ts'], so the older
// src/dash/__tests__/*.test.mjs files are NOT collected by `vitest run`. A
// `.test.mjs` file added here would silently never execute.
import { describe, expect, it } from 'vitest'

// slot-shared.js is untyped JS; tsconfig has allowJs with checkJs:false, so the
// import resolves and the exports come through as `any`.
import { slotModelId, slotModelRow } from './slot-shared.js'

const ROWS = [
  { id: 'qwen3.6-27b', name: 'Qwen3.6 27B', type: 'llm' },
  { id: 'nomic-v1.5', name: 'nomic-embed-text-v1.5', type: 'embedding' },
]

describe('slotModelId', () => {
  it('prefers model_id over the display-label fallback', () => {
    expect(slotModelId({ model_id: 'a', model: 'b' })).toBe('a')
  })

  it('falls back to `model` when model_id is absent (bare /api/status entry)', () => {
    expect(slotModelId({ model: 'b' })).toBe('b')
  })

  it('returns "" for an unbound slot', () => {
    expect(slotModelId({})).toBe('')
    expect(slotModelId({ model_id: null, model: '' })).toBe('')
  })

  it('returns "" rather than throwing for null/undefined', () => {
    expect(slotModelId(null)).toBe('')
    expect(slotModelId(undefined)).toBe('')
  })
})

describe('slotModelRow', () => {
  it('resolves the full row for a bound slot', () => {
    expect(slotModelRow({ model_id: 'qwen3.6-27b' }, ROWS)).toBe(ROWS[0])
  })

  it('resolves through the `model` fallback too', () => {
    expect(slotModelRow({ model: 'nomic-v1.5' }, ROWS)).toBe(ROWS[1])
  })

  it('returns null for an unbound slot without scanning the list', () => {
    expect(slotModelRow({}, ROWS)).toBeNull()
    expect(slotModelRow(null, ROWS)).toBeNull()
  })

  it('returns null when the bound id is not in the list (mid-refetch / deleted)', () => {
    expect(slotModelRow({ model_id: 'gone' }, ROWS)).toBeNull()
  })

  it('tolerates a missing/undefined model list', () => {
    expect(slotModelRow({ model_id: 'qwen3.6-27b' }, undefined)).toBeNull()
    expect(slotModelRow({ model_id: 'qwen3.6-27b' }, null)).toBeNull()
  })

  it('skips null entries in the list instead of throwing', () => {
    expect(slotModelRow({ model_id: 'qwen3.6-27b' }, [null, ...ROWS])).toBe(ROWS[0])
  })

  it('falls back to a row carrying the id in `aliases` (#1656 legacy FLM id)', () => {
    const rows = [{ id: 'gemma4-it:e4b', aliases: ['gemma4-it-e4b-FLM'] }, ...ROWS]
    expect(slotModelRow({ model_id: 'gemma4-it-e4b-FLM' }, rows)).toBe(rows[0])
  })

  it('prefers an exact id match over an alias match', () => {
    const exact = { id: 'gemma4-it-e4b-FLM' }
    const aliased = { id: 'gemma4-it:e4b', aliases: ['gemma4-it-e4b-FLM'] }
    expect(slotModelRow({ model_id: 'gemma4-it-e4b-FLM' }, [aliased, exact])).toBe(exact)
  })

  it('tolerates rows with a non-array `aliases`', () => {
    const rows = [{ id: 'x', aliases: 'not-an-array' }]
    expect(slotModelRow({ model_id: 'gone' }, rows)).toBeNull()
  })
})
