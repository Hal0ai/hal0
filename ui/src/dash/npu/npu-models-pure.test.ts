import { describe, expect, it } from 'vitest'
import { flmCatalogRows, ggufNpuRows } from './npu-models-pure'

const models = [
  { model: 'qwen3:8b', installed: false, capabilities: ['chat'], family: 'qwen3' },
  { model: 'gemma4-it:e4b', installed: true, capabilities: ['chat', 'stt'], family: 'gemma4' },
  { model: 'whisper-v3:turbo', installed: true, capabilities: ['stt'], family: 'whisper' },
  { model: 'embed-gemma:300m', installed: true, capabilities: ['embed'], family: 'embed-gemma' },
  { model: 'llama3.2:1b', installed: false, capabilities: ['chat'], family: 'llama3' },
]
const usage = { 'gemma4-it:e4b': 'flm chat', 'whisper-v3:turbo': 'flm stt', 'embed-gemma:300m': 'flm embed' }
const ids = (r: { model: string }[]) => r.map((x) => x.model)

describe('flmCatalogRows', () => {
  it('lists installed first, then by family', () => {
    expect(ids(flmCatalogRows(models, { installed: 'all', cap: 'all' }, usage))).toEqual([
      'embed-gemma:300m',
      'gemma4-it:e4b',
      'whisper-v3:turbo',
      'llama3.2:1b',
      'qwen3:8b',
    ])
  })
  it('filters installed only and by capability', () => {
    expect(ids(flmCatalogRows(models, { installed: 'installed', cap: 'all' }, usage))).toHaveLength(3)
    expect(ids(flmCatalogRows(models, { installed: 'all', cap: 'stt' }, usage))).toEqual(['gemma4-it:e4b', 'whisper-v3:turbo'])
    expect(ids(flmCatalogRows(models, { installed: 'installed', cap: 'chat' }, usage))).toEqual(['gemma4-it:e4b'])
  })
  it('carries the usage tag, null when unused', () => {
    const rows = flmCatalogRows(models, { installed: 'all', cap: 'all' }, usage)
    expect(rows.find((r) => r.model === 'gemma4-it:e4b')!.usedBy).toBe('flm chat')
    expect(rows.find((r) => r.model === 'qwen3:8b')!.usedBy).toBeNull()
  })
  it('survives a missing family / capabilities', () => {
    const rows = flmCatalogRows([{ model: 'x', installed: true }] as never, { installed: 'all', cap: 'all' }, {})
    expect(ids(rows)).toEqual(['x'])
    expect(flmCatalogRows([{ model: 'x', installed: true }] as never, { installed: 'all', cap: 'chat' }, {})).toEqual([])
  })
})

describe('ggufNpuRows', () => {
  const reg = [
    { id: 'qwen3-4b', type: 'llm', size: '2.5 GB', architecture: 'qwen3' },
    { id: 'qwen35-0.8b', type: 'llm', size: '0.6 GB' },
    { id: 'kokoro', type: 'tts', size: '0.3 GB' },
    { id: 'not-in-fit', type: 'llm', size: '1 GB' },
  ]
  const fit = {
    'qwen3-4b': { xdna_path: 'fused', reason: 'all ops fit' },
    'qwen35-0.8b': { xdna_path: 'per-op', reason: 'ssm_out is Q4_K' },
    kokoro: { xdna_path: null, reason: 'not an llm' },
  }
  const slots = [
    { name: 'a', binary: 'xdna', model: 'qwen3-4b' },
    { name: 'b', binary: 'xdna', model: 'qwen3-4b' },
    { name: 'gpu', binary: 'llama-server', model: 'qwen35-0.8b' },
  ]
  it('keeps GGUF llm rows in the fit map, joined to the registry', () => {
    const rows = ggufNpuRows(reg as never, fit as never, slots as never)
    expect(rows.map((r) => r.id)).toEqual(['qwen3-4b', 'qwen35-0.8b'])
    expect(rows[0]).toMatchObject({ arch: 'qwen3', size: '2.5 GB', fit: 'fused', reason: 'all ops fit', usedBy: ['a', 'b'] })
    expect(rows[1]).toMatchObject({ arch: null, fit: 'per-op', usedBy: [] })
  })
  it('a fit row without a registry entry still shows, with no size', () => {
    const rows = ggufNpuRows([] as never, { z: { xdna_path: null, reason: 'file missing' } } as never, [])
    expect(rows).toEqual([{ id: 'z', arch: null, size: null, fit: null, reason: 'file missing', usedBy: [] }])
  })
})
