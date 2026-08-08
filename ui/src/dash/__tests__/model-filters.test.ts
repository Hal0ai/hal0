// isMtpModel / isMoeModel — pins the #1649 fix: the MOE filter chip must key
// on the same signal the backend uses (hardware.recommend._resolve_primary_ctx
// / profiles.generate._looks_moe), not on Model.architecture (never persisted
// on a registry row) or the retired "moe" curated tag.
//
// Named `.test.ts` deliberately: vitest.config.ts's `include` is
// ['src/**/*.test.ts', 'tests/e2e/*.test.ts'], so a `.test.mjs` file added
// alongside model-sort.test.mjs would silently never execute under
// `npm run test:unit`.
import { describe, expect, it } from 'vitest'

// model-sort.js is untyped JS; tsconfig has allowJs with checkJs:false, so
// the import resolves and the exports come through as `any`.
import { isMoeModel, isMtpModel } from '../model-sort.js'

describe('isMtpModel', () => {
  it('is true when defaults.mtp is explicitly true', () => {
    expect(isMtpModel({ id: 'm', defaults: { mtp: true } })).toBe(true)
  })

  it('is true when the legacy mtp tag is present', () => {
    expect(isMtpModel({ id: 'm', tags: ['chat', 'mtp'] })).toBe(true)
  })

  it('is false with neither signal', () => {
    expect(isMtpModel({ id: 'm', tags: ['chat'] })).toBe(false)
    expect(isMtpModel({ id: 'm' })).toBe(false)
  })
})

describe('isMoeModel', () => {
  it('is FALSE for a fresh install with no architecture, no tag, no id marker (the #1649 repro shape before the fix)', () => {
    expect(isMoeModel({ id: 'plain-chat-model', tags: ['chat'] })).toBe(false)
  })

  it('is true for a known MoE architecture id, once Model.architecture is ever backfilled', () => {
    expect(isMoeModel({ id: 'whatever', architecture: 'qwen3next' })).toBe(true)
    expect(isMoeModel({ id: 'whatever', architecture: 'Mixtral' })).toBe(true)
  })

  it('a known dense architecture id is NOT flagged moe even with an "a3b"-shaped id', () => {
    // architecture, when present, is authoritative and short-circuits the
    // id/tag fallback — mirrors the backend's precedence exactly.
    expect(isMoeModel({ id: 'some-a3b-named-thing', architecture: 'llama' })).toBe(false)
  })

  it('is true for the curated qwen3.6-35b-a3b MTP repro model (id substring + mtp tag, no architecture)', () => {
    expect(
      isMoeModel({ id: 'qwen3-6-35b-a3b-halostrix-dyn-mtp-v7', tags: ['chat', 'mtp', 'rocmfp4'] }),
    ).toBe(true)
  })

  it('is true for an A3B MoE coder with no mtp tag at all (id substring alone is enough)', () => {
    expect(isMoeModel({ id: 'Qwen3-Coder-30B-A3B-Instruct-GGUF', tags: ['chat', 'coder'] })).toBe(true)
  })

  it('is true when the legacy "moe" or "a3b" tag is present even with a plain id', () => {
    expect(isMoeModel({ id: 'plain-id', tags: ['moe'] })).toBe(true)
    expect(isMoeModel({ id: 'plain-id', tags: ['a3b'] })).toBe(true)
  })

  it('is false for a plain dense model with none of the signals', () => {
    expect(isMoeModel({ id: 'gemma-4-12b-it-ud-q4-k-xl', tags: ['chat'] })).toBe(false)
    expect(isMoeModel({})).toBe(false)
  })
})

describe('DENSE bucket (neither mtp nor moe)', () => {
  it('an A3B MoE model with no mtp tag is excluded from dense (was previously mis-bucketed as dense)', () => {
    const m = { id: 'Qwen3-Coder-30B-A3B-Instruct-GGUF', tags: ['chat', 'coder'] }
    expect(!isMtpModel(m) && !isMoeModel(m)).toBe(false)
  })

  it('a plain dense model with no signals stays in dense', () => {
    const m = { id: 'gemma-4-12b-it-ud-q4-k-xl', tags: ['chat'] }
    expect(!isMtpModel(m) && !isMoeModel(m)).toBe(true)
  })
})
