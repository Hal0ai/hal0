// Overview v2 Image engine card: the model behind it, as plain data.
import { describe, expect, it } from 'vitest'
import type { ArbiterReading, ComfyuiStatus } from '@/api/hooks/useComfyui'
import { imageModel } from '../image-engine-pure'

function status(over: Partial<ComfyuiStatus> = {}): ComfyuiStatus {
  return {
    mode: 'inference',
    reachable: true,
    engine: 'running',
    container: { name: 'hal0-img', state: 'running' },
    endpoint: null,
    queue: { running: 0, pending: 0 },
    util: null,
    temp: null,
    clock: null,
    progress: null,
    inference: { hermes: false },
    inventory: { checkpoints: 3, diffusion: 2, loras: 5, vae: 1, controlnet: 9 },
    switchover: { active: false, target: null, error: null },
    arbiter: null,
    gate: null,
    gate_refusal: null,
    ...over,
  }
}

function arbiter(mode: 'llm' | 'img', over: Record<string, unknown> = {}): ArbiterReading {
  return {
    mode: mode === 'llm' ? 'inference' : 'generation',
    arbiter: {
      mode,
      pinned: false,
      saved_llm_slots: [],
      switching: false,
      image_jobs: 0,
      phase: null,
      restore_at: null,
      handoff_until: null,
      drain_in_flight: 0,
      unload_set: ['brain', 'utility', 'embed', 'stt'],
      llm_waiters: { count: 0, oldest_s: null },
      ...over,
    },
    switchover: { active: false, target: null, error: null },
    gate_refusal: null,
    skewMs: 0,
  }
}

describe('imageModel', () => {
  it('is null when the status is undefined (404, no img slot)', () => {
    expect(imageModel(undefined, arbiter('llm'))).toBeNull()
  })

  it('idle in chat mode: ok pill, empty queue line, no note, four inventory tiles in order', () => {
    const m = imageModel(status(), arbiter('llm'))!
    expect(m.modeTone).toBe('ok')
    expect(m.modeText).toBe('chat mode')
    expect(m.engine).toBe('running')
    expect(m.queueLine).toBe('queue 0 running · 0 pending')
    expect(m.pendingNote).toBeNull()
    expect(m.progress).toBeNull()
    expect(m.inventory).toEqual([
      { k: 'ckpt', v: 3 },
      { k: 'diffusion', v: 2 },
      { k: 'loras', v: 5 },
      { k: 'vae', v: 1 },
    ])
  })

  it('image mode: img tone', () => {
    const m = imageModel(status(), arbiter('img'))!
    expect(m.modeTone).toBe('img')
    expect(m.modeText).toBe('image mode')
  })

  it('a switch in flight reads warn', () => {
    const m = imageModel(status(), arbiter('llm', { switching: true }))!
    expect(m.modeTone).toBe('warn')
    expect(m.modeText).toBe('switching')
  })

  it('a pending render in chat mode explains the wait, counting the unload set', () => {
    const m = imageModel(status({ queue: { running: 0, pending: 1 } }), arbiter('llm'))!
    expect(m.pendingNote).toBe('1 render queued · waits for image mode (switch pauses 4 slots)')
    expect(m.queueLine).toBe('queue 0 running · 1 pending')
  })

  it('no note when pending in image mode, or for an unknown unload set it omits the count', () => {
    expect(imageModel(status({ queue: { running: 0, pending: 2 } }), arbiter('img'))!.pendingNote).toBeNull()
    const m = imageModel(status({ queue: { running: 0, pending: 2 } }), arbiter('llm', { unload_set: null }))!
    expect(m.pendingNote).toBe('2 renders queued · waits for image mode')
  })

  it('falls back to the status mode when the arbiter has not answered', () => {
    expect(imageModel(status({ mode: 'generation' }), undefined)!.modeText).toBe('image mode')
    expect(imageModel(status({ mode: 'inference' }), undefined)!.modeText).toBe('chat mode')
    expect(imageModel(status({ mode: null }), undefined)!.modeText).toBe('—')
  })

  it('progress at step 14/30 is 47%, with the clock and the node', () => {
    const m = imageModel(
      status({
        queue: { running: 1, pending: 0 },
        progress: { model: 'flux-dev', step: 14, total: 30, node: 'KSampler', elapsed_s: 83, prompt_id: 'p' },
      }),
      arbiter('img'),
    )!
    expect(m.progress).toEqual({ model: 'flux-dev', step: 'step 14/30', node: 'KSampler', elapsed: '1:23', pct: 47 })
  })

  it('progress before the first frame has no step, node or percentage', () => {
    const m = imageModel(
      status({ progress: { model: 'flux-dev', step: null, total: null, node: null, elapsed_s: 4, prompt_id: 'p' } }),
      arbiter('img'),
    )!
    expect(m.progress).toEqual({ model: 'flux-dev', step: null, node: null, elapsed: '0:04', pct: null })
  })

  it('no inventory block, or a key missing from it, yields no tile for it (never a made-up 0)', () => {
    expect(imageModel(status({ inventory: null }), arbiter('llm'))!.inventory).toEqual([])
    const m = imageModel(status({ inventory: { checkpoints: 3, vae: 0 } }), arbiter('llm'))!
    expect(m.inventory).toEqual([
      { k: 'ckpt', v: 3 },
      { k: 'vae', v: 0 },
    ])
  })

  it('a body without a queue reads queue —, not a made-up empty queue', () => {
    const m = imageModel(status({ queue: undefined as any }), arbiter('llm'))!
    expect(m.queueLine).toBe('queue —')
  })

  it('an unreachable engine reads queue — (the backend sends {0,0} when /queue failed)', () => {
    const m = imageModel(status({ reachable: false }), arbiter('llm'))!
    expect(m.queueLine).toBe('queue —')
  })

  it('a step count with no total shows no step label (agrees with the percentage)', () => {
    const m = imageModel(
      status({ progress: { model: 'm', step: 3, total: 0, node: null, elapsed_s: 1, prompt_id: 'p' } }),
      arbiter('img'),
    )!
    expect(m.progress!.step).toBeNull()
    expect(m.progress!.pct).toBeNull()
  })
})
