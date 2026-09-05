import { describe, expect, it } from 'vitest'
import { resolveProvider, rowId, rowNeedsPull } from '../settings/pages/capabilities/selection-pure.js'

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

describe('rowNeedsPull (#2026 semantics)', () => {
  it('marks a row whose every backend is undownloaded', () => {
    expect(rowNeedsPull({ id: 'm', backends: [
      { id: 'gpu-vulkan', downloaded: false, pullable: true },
      { id: 'cpu', downloaded: false, pullable: true },
    ] })).toBe(true)
  })
  it('does not mark a row with at least one downloaded backend', () => {
    expect(rowNeedsPull({ id: 'm', backends: [
      { id: 'gpu-vulkan', downloaded: false },
      { id: 'cpu', downloaded: true },
    ] })).toBe(false)
  })
  it('never marks rows without a backends list (legacy fixtures, bare ids)', () => {
    expect(rowNeedsPull({ id: 'm' })).toBe(false)
    expect(rowNeedsPull({ id: 'm', backends: [] })).toBe(false)
    expect(rowNeedsPull('bare')).toBe(false)
  })
})
