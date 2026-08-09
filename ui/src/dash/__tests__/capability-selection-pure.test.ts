import { describe, expect, it } from 'vitest'
import { resolveProvider, rowId } from '../settings/pages/capabilities/selection-pure.js'

describe('rowId', () => {
  it('prefers id, then model_id, then the bare value', () => {
    expect(rowId({ id: 'a', model_id: 'b' })).toBe('a')
    expect(rowId({ model_id: 'b' })).toBe('b')
    expect(rowId('bare')).toBe('bare')
  })
})

describe('resolveProvider (#1470 semantics)', () => {
  const catalog = [{ id: 'kokoro-v1', provider: 'kokoro' }]
  const selection = { model: 'qwen3-tts', provider: 'qwen3tts' }

  it('prefers the catalog row for the model being edited', () => {
    expect(resolveProvider(catalog, 'kokoro-v1', selection)).toBe('kokoro')
  })
  it('falls back to the saved selection provider only while ids match', () => {
    expect(resolveProvider(catalog, 'qwen3-tts', selection)).toBe('qwen3tts')
    expect(resolveProvider(catalog, 'something-else', selection)).toBe('')
  })
  it('empty model resolves to empty provider', () => {
    expect(resolveProvider(catalog, '', selection)).toBe('')
  })
})
