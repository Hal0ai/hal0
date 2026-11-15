import { describe, expect, it } from 'vitest'
import { flmPickerOptions, formatFlmSize, ggufNpuRows } from './npu-models-pure'

const models = [
  { model: 'qwen3:8b', installed: false, capabilities: ['chat'], family: 'qwen3' },
  { model: 'gemma4-it:e4b', installed: true, capabilities: ['chat', 'stt'], family: 'gemma4' },
  { model: 'whisper-v3:turbo', installed: true, capabilities: ['stt'], family: 'whisper' },
  { model: 'embed-gemma:300m', installed: true, capabilities: ['embed'], family: 'embed-gemma' },
  { model: 'llama3.2:1b', installed: false, capabilities: ['chat'], family: 'llama3' },
]
const ids = (r: { id: string }[]) => r.map((x) => x.id)

describe('flmPickerOptions', () => {
  const cat = [
    ...models.map((m) => ({ ...m, size_bytes: 0 })),
    { model: 'qwen3.5:9b', installed: false, capabilities: ['chat'], family: 'qwen3.5', size_bytes: 6_400_000_000 },
    { model: 'gemma4-it:e2b', installed: false, capabilities: ['chat', 'stt'], family: 'gemma4', size_bytes: 5_100_000_000 },
  ]
  it('chat row: chat-capable models only, installed and download groups sorted by family then name', () => {
    const g = flmPickerOptions(cat, 'chat')
    expect(ids(g.installed)).toEqual(['gemma4-it:e4b'])
    expect(ids(g.download)).toEqual(['gemma4-it:e2b', 'llama3.2:1b', 'qwen3:8b', 'qwen3.5:9b'])
  })
  it('stt row: speech models only (a chat model that hears audio qualifies)', () => {
    const g = flmPickerOptions(cat, 'stt')
    expect(ids(g.installed)).toEqual(['gemma4-it:e4b', 'whisper-v3:turbo'])
    expect(ids(g.download)).toEqual(['gemma4-it:e2b'])
  })
  it('embed row: embedding models only', () => {
    const g = flmPickerOptions(cat, 'embed')
    expect(ids(g.installed)).toEqual(['embed-gemma:300m'])
    expect(g.download).toEqual([])
  })
  it('chips: audio on a chat model that also lists stt, size when reported, family', () => {
    const g = flmPickerOptions(cat, 'chat')
    expect(g.installed[0]).toMatchObject({ audio: true, installed: true, sizeBytes: null, family: 'gemma4' })
    const q9 = g.download.find((o) => o.id === 'qwen3.5:9b')!
    expect(q9).toMatchObject({ audio: false, installed: false, sizeBytes: 6_400_000_000 })
    // the audio chip belongs to the chat picker: a speech picker row is not "also hears audio"
    expect(flmPickerOptions(cat, 'stt').installed.every((o) => !o.audio)).toBe(true)
  })
  it('always offers the current model, as installed when the catalogue lacks it', () => {
    const g = flmPickerOptions(cat, 'chat', 'custom:1b')
    expect(g.installed.find((o) => o.id === 'custom:1b')).toMatchObject({ installed: true, sizeBytes: null })
    expect(flmPickerOptions(cat, 'chat', 'gemma4-it:e4b').installed).toHaveLength(1)
  })
  it('a row without a capabilities field is chat-only; an empty list serves nothing; no catalogue is empty', () => {
    const bare = [{ model: 'x', installed: true }] as never
    expect(ids(flmPickerOptions(bare, 'chat').installed)).toEqual(['x'])
    expect(flmPickerOptions(bare, 'stt').installed).toEqual([])
    expect(flmPickerOptions([{ model: 'y', installed: true, capabilities: [] }] as never, 'chat').installed).toEqual([])
    expect(flmPickerOptions(undefined, 'chat')).toEqual({ installed: [], download: [] })
  })
})

describe('formatFlmSize', () => {
  it('formats GB with one decimal, MB below a gigabyte, null when unknown', () => {
    expect(formatFlmSize(6_400_000_000)).toBe('6.4 GB')
    expect(formatFlmSize(21_000_000_000)).toBe('21 GB')
    expect(formatFlmSize(700_000_000)).toBe('700 MB')
    expect(formatFlmSize(0)).toBeNull()
    expect(formatFlmSize(undefined)).toBeNull()
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
