// slotModelFit — the one engine-fit predicate behind every slot model dropdown.
import { describe, expect, it } from 'vitest'

import { compatibleModels } from '@/dash/slots/slot-shared.js'
import {
  filterModelsForSlot,
  isFlmModel,
  isFlmSlot,
  modelFitsSlot,
} from './slotModelFit'

// The shapes an FLM model arrives in from /api/models.
const FLM_REGISTRY_ROW = {
  id: 'qwen3:4b',
  capabilities: ['chat'],
  backends: ['npu'],
  provider_effective: 'flm',
  installed: true,
}
const FLM_PROBE_ROW = {
  // host-flm probe row: no model_to_dict, so no provider_effective
  id: 'gemma4-it-e4b-FLM',
  capabilities: ['chat'],
  backend: 'flm',
  upstream: 'npu',
  owned_by: 'flm',
  device: 'npu',
  installed: true,
  origin: 'local',
}
const GGUF = {
  id: 'qwen3.6-27b',
  capabilities: ['chat'],
  backends: ['vulkan', 'rocm', 'cpu'],
  provider_effective: 'llama-server',
  installed: true,
}
const GPU_SLOT = { name: 'primary', type: 'llm', device: 'gpu-rocm', device_class: 'gpu' }
const CPU_SLOT = { name: 'cpu', type: 'llm', device: 'cpu' }
const NPU_SLOT = { name: 'flm', type: 'llm', device: 'npu', device_class: 'npu' }

describe('isFlmModel', () => {
  it('trusts the server provider_effective first', () => {
    expect(isFlmModel(FLM_REGISTRY_ROW)).toBe(true)
    expect(isFlmModel(GGUF)).toBe(false)
    // authoritative even against a contradicting tag
    expect(isFlmModel({ id: 'x-FLM', provider_effective: 'llama-server' })).toBe(false)
  })

  it('recognises probe rows, old tags and -FLM ids without provider_effective', () => {
    expect(isFlmModel(FLM_PROBE_ROW)).toBe(true)
    expect(isFlmModel({ id: 'a', backends: ['npu'] })).toBe(true)
    expect(isFlmModel({ id: 'a', backends: ['FLM'] })).toBe(true)
    expect(isFlmModel({ id: 'tag-FLM' })).toBe(true)
    expect(isFlmModel({ id: 'a', device: 'npu' })).toBe(true)
  })

  it('is false for GGUF rows, lane-only tags and nullish input', () => {
    expect(isFlmModel({ id: 'a', backends: ['vulkan', 'rocm', 'cpu'] })).toBe(false)
    expect(isFlmModel({ id: 'a' })).toBe(false)
    expect(isFlmModel(null)).toBe(false)
    expect(isFlmModel(undefined)).toBe(false)
  })
})

describe('isFlmSlot', () => {
  it('is true for an NPU device, device_class or flm provider', () => {
    expect(isFlmSlot(NPU_SLOT)).toBe(true)
    expect(isFlmSlot({ device_class: 'npu' })).toBe(true)
    expect(isFlmSlot({ provider: 'flm' })).toBe(true)
    // an old backend token folds through backend_to_device -> npu
    expect(isFlmSlot({ device: 'flm' })).toBe(true)
  })

  it('is false for GPU/CPU slots and nullish input', () => {
    expect(isFlmSlot(GPU_SLOT)).toBe(false)
    expect(isFlmSlot(CPU_SLOT)).toBe(false)
    expect(isFlmSlot({ device: 'gpu-vulkan' })).toBe(false)
    expect(isFlmSlot({})).toBe(false)
    expect(isFlmSlot(null)).toBe(false)
  })
})

describe('modelFitsSlot / filterModelsForSlot', () => {
  it('never offers FLM models to a GPU or CPU slot', () => {
    for (const slot of [GPU_SLOT, CPU_SLOT]) {
      expect(modelFitsSlot(FLM_REGISTRY_ROW, slot)).toBe(false)
      expect(modelFitsSlot(FLM_PROBE_ROW, slot)).toBe(false)
      expect(modelFitsSlot(GGUF, slot)).toBe(true)
    }
  })

  it('offers an FLM slot only FLM models', () => {
    expect(modelFitsSlot(FLM_REGISTRY_ROW, NPU_SLOT)).toBe(true)
    expect(modelFitsSlot(FLM_PROBE_ROW, NPU_SLOT)).toBe(true)
    expect(modelFitsSlot(GGUF, NPU_SLOT)).toBe(false)
  })

  it('filters a list for the slot and tolerates a missing list', () => {
    const all = [FLM_REGISTRY_ROW, FLM_PROBE_ROW, GGUF]
    expect(filterModelsForSlot(all, GPU_SLOT)).toEqual([GGUF])
    expect(filterModelsForSlot(all, NPU_SLOT)).toEqual([FLM_REGISTRY_ROW, FLM_PROBE_ROW])
    expect(filterModelsForSlot(undefined, GPU_SLOT)).toEqual([])
  })
})

describe('compatibleModels (shared by drawer, swap popover, card and NPU pickers)', () => {
  const EMBED = {
    id: 'embed-gemma:300m',
    capabilities: ['embed'],
    backends: ['npu'],
    provider_effective: 'flm',
    installed: true,
  }
  const ASR = {
    id: 'whisper-v3:turbo',
    capabilities: ['transcription'],
    backends: ['npu'],
    provider_effective: 'flm',
    installed: true,
  }
  const UPSTREAM_FLM = { ...FLM_REGISTRY_ROW, id: 'up', origin: 'upstream', installed: false }
  const rows = [FLM_REGISTRY_ROW, FLM_PROBE_ROW, GGUF, EMBED, ASR, UPSTREAM_FLM]
  const ids = (list: { id: string }[]) => list.map((m) => m.id)

  it('GPU llm slot: GGUF only', () => {
    expect(ids(compatibleModels(rows, { type: 'llm', slot: GPU_SLOT }))).toEqual([GGUF.id])
  })

  it('NPU chat slot: FLM chat models only (no ASR, embed, GGUF or upstream rows)', () => {
    expect(ids(compatibleModels(rows, { type: 'llm', slot: NPU_SLOT }))).toEqual([
      FLM_REGISTRY_ROW.id,
      FLM_PROBE_ROW.id,
    ])
  })

  it('NPU stt / embed slots: only their own role', () => {
    expect(
      ids(compatibleModels(rows, { type: 'transcription', slot: { ...NPU_SLOT, type: 'transcription' } })),
    ).toEqual([ASR.id])
    expect(
      ids(compatibleModels(rows, { type: 'embedding', slot: { ...NPU_SLOT, type: 'embedding' } })),
    ).toEqual([EMBED.id])
  })
})
