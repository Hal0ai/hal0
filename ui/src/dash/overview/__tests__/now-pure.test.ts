import { describe, expect, it } from 'vitest'
import { laneRows } from '../serving-pure'
import { contextualActions, modeLabel, nowFacts } from '../now-pure'

const slot = (name: string, state: string, device = 'gpu-vulkan', toks?: number) => ({
  name,
  state,
  device,
  type: 'llm',
  ...(toks != null ? { metrics: { toks } } : {}),
})

// 13 slots: 5 loaded (2 serving at 40 + 39 tok/s, 3 ready), 8 stopped.
const SLOTS_2_SERVING: any[] = [
  slot('brain', 'serving', 'gpu-rocm', 40),
  slot('agent', 'serving', 'gpu-vulkan', 39),
  slot('embed', 'ready'),
  slot('utility', 'ready'),
  slot('vision', 'idle'),
  ...Array.from({ length: 8 }, (_, i) => slot(`off${i}`, 'offline')),
]
const SLOTS_IDLE: any[] = [
  slot('brain', 'offline', 'gpu-rocm'),
  slot('agent', 'offline', 'gpu-vulkan'),
  slot('utility', 'offline', 'gpu-vulkan'),
  slot('cpu-one', 'offline', 'cpu'),
]
const SYS: any = {
  ts: 'x',
  fleet: {},
  slots: [
    { slot_id: 'brain', state: 'serving', inflight: 2 },
    { slot_id: 'agent', state: 'serving', inflight: 1 },
    { slot_id: 'embed', state: 'ready', inflight: 9 },
  ],
}

describe('nowFacts', () => {
  it('counts a starting and an errored slot as loaded, agreeing with the Serving now tiles', () => {
    const slots = [
      { name: 'a', device: 'gpu-vulkan', state: 'ready', type: 'llm' },
      { name: 'w', device: 'gpu-vulkan', state: 'starting', type: 'llm' },
      { name: 'e', device: 'gpu-vulkan', state: 'error', type: 'llm' },
      { name: 'o', device: 'gpu-vulkan', state: 'offline', type: 'llm' },
    ] as any[]
    const facts = nowFacts(slots, undefined, null, null, { access: true, pct: null, tempC: null, watts: null })
    const tiles = laneRows(slots, undefined, undefined).flatMap((l) => l.loaded)
    expect(facts.loaded).toBe(3)
    expect(tiles).toHaveLength(facts.loaded)
  })

  it('counts loaded and serving and sums tok/s', () => {
    const r = nowFacts(SLOTS_2_SERVING, SYS, 48, 116, { access: true, pct: 0, tempC: 47, watts: 33 })
    expect(r).toMatchObject({
      loaded: 5,
      total: 13,
      serving: 2,
      tps: 79,
      inflight: 3,
      freeLabel: '48.0 GB free of 116',
      gpuLabel: 'gpu 0% · 47°C · 33 W',
    })
  })
  it('dashes tps with no reading and inflight with no system stats', () => {
    const r = nowFacts(SLOTS_IDLE, undefined, 48, 116, { access: true, pct: 0, tempC: null, watts: null })
    expect(r.tps).toBeNull()
    expect(r.inflight).toBeNull()
    expect(r.gpuLabel).toBe('gpu 0%')
  })
  it('keeps a measured 0 tok/s instead of dashing it', () => {
    const slots: any[] = [slot('brain', 'serving', 'gpu-rocm', 0)]
    expect(nowFacts(slots, undefined, null, null, { access: true, pct: null, tempC: null, watts: null }).tps).toBe(0)
  })
  it('says no GPU access on a CPU-only box', () =>
    expect(
      nowFacts(SLOTS_IDLE, undefined, null, null, { access: false, pct: 90, tempC: 70, watts: 100 }).gpuLabel,
    ).toBe('no GPU access'))
  it('dashes the free label when the pool is unknown', () =>
    expect(nowFacts(SLOTS_IDLE, undefined, null, null, { access: true, pct: 0, tempC: null, watts: null }).freeLabel).toBe('—'))
})

describe('modeLabel', () => {
  it('maps arbiter modes', () => {
    expect(modeLabel({ mode: 'llm', switching: false })).toEqual({ kind: 'chat', text: 'chat mode' })
    expect(modeLabel({ mode: 'img', switching: false })).toEqual({ kind: 'image', text: 'image mode' })
    expect(modeLabel({ mode: 'img', switching: true })).toEqual({ kind: 'switching', text: 'switching' })
    expect(modeLabel(null)).toEqual({ kind: 'unknown', text: '—' })
  })
})

describe('contextualActions', () => {
  it('in chat mode offers hold, load first vulkan offline slot, agent chat', () => {
    const acts = contextualActions('chat', SLOTS_IDLE)
    expect(acts.map((a) => a.id)).toEqual(['hold-image', 'load-slot', 'agent-chat'])
    expect(acts[1]).toMatchObject({ slot: 'agent', label: 'Load agent' })
  })
  it('falls back to the first offline slot anywhere, and omits load with none', () => {
    const rocmOnly: any[] = [slot('brain', 'ready', 'gpu-vulkan'), slot('r', 'offline', 'gpu-rocm')]
    expect(contextualActions('chat', rocmOnly).find((a) => a.id === 'load-slot')?.slot).toBe('r')
    const allUp: any[] = [slot('brain', 'ready')]
    expect(contextualActions('chat', allUp).map((a) => a.id)).toEqual(['hold-image', 'agent-chat'])
  })
  it('in image mode offers back to chat and agent chat', () =>
    expect(contextualActions('image', SLOTS_IDLE).map((a) => a.id)).toEqual(['back-to-chat', 'agent-chat']))
  it('while switching or unknown offers only agent chat', () => {
    expect(contextualActions('switching', SLOTS_IDLE).map((a) => a.id)).toEqual(['agent-chat'])
    expect(contextualActions('unknown', SLOTS_IDLE).map((a) => a.id)).toEqual(['agent-chat'])
  })
})
