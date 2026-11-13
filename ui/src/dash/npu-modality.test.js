import { describe, expect, it } from 'vitest'
import { isNpuShadowSlot, npuPillOn, npuRoleForSlot } from './npu-modality'

describe('npu-modality xdna awareness', () => {
  it('npuRoleForSlot still types xdna slots as chat (no modality roles apply)', () => {
    expect(npuRoleForSlot({ binary: 'xdna', type: 'transcription' })).toBe('asr')
    // xdna never emits non-chat types today; chat fallback is the safe default.
    expect(npuRoleForSlot({ binary: 'xdna' })).toBe('chat')
  })

  it('isNpuShadowSlot is false for xdna slots regardless of type', () => {
    expect(isNpuShadowSlot({ binary: 'xdna', type: 'transcription' })).toBe(false)
    expect(isNpuShadowSlot({ binary: 'xdna', type: 'embedding' })).toBe(false)
    expect(isNpuShadowSlot({ type: 'transcription' })).toBe(true)
    expect(isNpuShadowSlot({ type: 'embedding' })).toBe(true)
  })

  it('npuPillOn for an xdna anchor-less context falls back to raw chat modality', () => {
    expect(npuPillOn({ binary: 'xdna' }, { chat: false }, 'chat')).toBe(false)
    expect(npuPillOn({ binary: 'xdna', npu_modality_active: true }, {}, 'asr')).toBe(true)
  })
})
