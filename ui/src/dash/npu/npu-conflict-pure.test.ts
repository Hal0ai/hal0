import { describe, expect, it } from 'vitest'
import { Hal0Error } from '@/api/client'
import { npuConflictCopy, npuConflictFromError } from './npu-conflict-pure'

const err = (details: Record<string, unknown>, code = 'slot.npu_conflict') =>
  new Hal0Error('NPU is held by flm', { code, status: 409, details })

const FLM = {
  slot: 'npu-gguf',
  holders: ['flm'],
  holder_engine: 'flm',
  unloads: ['flm', 'flm-stt', 'flm-embed'],
  pinned: false,
  pending: false,
  reason: 'x',
}

describe('npuConflictFromError', () => {
  it('parses a 409 slot.npu_conflict', () => {
    expect(npuConflictFromError(err(FLM))).toEqual({
      slot: 'npu-gguf',
      holders: ['flm'],
      holderEngine: 'flm',
      unloads: ['flm', 'flm-stt', 'flm-embed'],
      pinned: false,
      pending: false,
    })
  })
  it('returns null for other codes and non-errors', () => {
    expect(npuConflictFromError(err(FLM, 'slot.not_found'))).toBeNull()
    expect(npuConflictFromError(new Error('boom'))).toBeNull()
    expect(npuConflictFromError(null)).toBeNull()
  })
  it('tolerates missing details', () => {
    const c = npuConflictFromError(new Hal0Error('m', { code: 'slot.npu_conflict', status: 409 }))
    expect(c).toMatchObject({ holders: [], unloads: [], pinned: false, pending: false })
  })
})

describe('npuConflictCopy', () => {
  it('FLM holder copy', () => {
    const c = npuConflictFromError(err(FLM))!
    expect(npuConflictCopy(c)).toEqual({
      title: 'Unload flm to start npu-gguf?',
      body: 'npu-gguf runs a per-op model, which needs the whole NPU. Starting it will unload flm, flm-stt, flm-embed.',
      confirmLabel: 'Unload and start',
    })
  })
  it('adds the pinned sentence only when pinned', () => {
    expect(npuConflictCopy(npuConflictFromError(err(FLM))!).body).not.toContain('pinned')
    const p = npuConflictFromError(err({ ...FLM, pinned: true }))!
    expect(npuConflictCopy(p).body).toContain(' flm is pinned.')
  })
  it('reverse direction names the xdna slots', () => {
    const c = npuConflictFromError(
      err({ ...FLM, slot: 'flm', holders: ['npu-gguf', 'npu-gguf-2'], holder_engine: 'xdna', unloads: ['npu-gguf', 'npu-gguf-2'] }),
    )!
    const copy = npuConflictCopy(c)
    expect(copy.title).toBe('Unload npu-gguf, npu-gguf-2 to start flm?')
    expect(copy.body).toContain('Starting it will unload npu-gguf, npu-gguf-2.')
    expect(copy.body).toContain('npu-gguf, npu-gguf-2 hold the NPU with per-op models.')
    expect(copy.confirmLabel).toBe('Unload and start')
  })
})
