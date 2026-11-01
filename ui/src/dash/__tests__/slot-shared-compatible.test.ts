// compatibleModels — the one list behind every slot model dropdown. The runtime
// family of the slot's effective runner decides whether halogen (Flash-Next)
// rows are offered: a halogen slot binds only models whose backends carry
// 'halogen', and every other slot never sees them.
import { describe, expect, it } from 'vitest'

import { compatibleModels } from '../slots/slot-shared.js'

const LLAMA = { id: 'qwen-gguf', capabilities: ['chat'], backends: ['rocm', 'vulkan', 'cpu'], installed: true }
const HALOGEN = { id: 'qwen-halo', capabilities: ['chat'], backends: ['halogen'], installed: true }
const FLM = { id: 'qwen:4b', capabilities: ['chat'], backends: ['npu'], provider_effective: 'flm', installed: true }
const EMBED_HALOGEN = { id: 'embed-halo', capabilities: ['embed'], backends: ['halogen'], installed: true }
const UPSTREAM_HALOGEN = { id: 'up-halo', capabilities: ['chat'], backends: ['halogen'], origin: 'upstream' }

const GPU_SLOT = { name: 'chat', type: 'llm', device: 'gpu-rocm', device_class: 'gpu' }
const NPU_SLOT = { name: 'flm', type: 'llm', device: 'npu', device_class: 'npu' }
const ids = (rows: { id: string }[]) => rows.map((m) => m.id)

describe('compatibleModels runtime family', () => {
  const MODELS = [LLAMA, HALOGEN, FLM, EMBED_HALOGEN, UPSTREAM_HALOGEN]

  it('offers only halogen rows to a halogen slot', () => {
    const rows = compatibleModels(MODELS, { type: 'llm', slot: GPU_SLOT, runtimeFamily: 'halogen' })
    expect(ids(rows)).toEqual(['qwen-halo'])
  })

  it('keeps halogen rows out of a llama-server slot', () => {
    const rows = compatibleModels(MODELS, { type: 'llm', slot: GPU_SLOT, runtimeFamily: 'llama-server' })
    expect(ids(rows)).toEqual(['qwen-gguf'])
  })

  it('treats an unknown or absent family like any non-halogen one', () => {
    expect(ids(compatibleModels(MODELS, { type: 'llm', slot: GPU_SLOT }))).toEqual(['qwen-gguf'])
    expect(ids(compatibleModels(MODELS, { type: 'llm', slot: GPU_SLOT, runtimeFamily: '' }))).toEqual(['qwen-gguf'])
  })

  it('still applies the type, upstream and engine-fit filters', () => {
    // upstream-advertised halogen rows have no local path; an embedding row is the wrong type.
    expect(ids(compatibleModels(MODELS, { type: 'llm', slot: GPU_SLOT, runtimeFamily: 'halogen' }))).not.toContain('up-halo')
    expect(ids(compatibleModels(MODELS, { type: 'llm', slot: GPU_SLOT, runtimeFamily: 'halogen' }))).not.toContain('embed-halo')
    // an FLM slot is still offered FLM models only, halogen rows excluded.
    expect(ids(compatibleModels(MODELS, { type: 'llm', slot: NPU_SLOT, runtimeFamily: 'flm' }))).toEqual(['qwen:4b'])
  })
})
