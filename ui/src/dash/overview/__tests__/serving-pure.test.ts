import { describe, expect, it } from 'vitest'
import { laneRows } from '../serving-pure'
import { SERVING_UTILITY, SLOTS, SYS } from './serving-fixtures'

describe('laneRows', () => {
  it('groups by rail group in RAIL_GROUPS order and omits empty lanes', () => {
    const lanes = laneRows(SLOTS, SYS, undefined)
    expect(lanes.map((l) => l.key)).toEqual(['vulkan', 'rocm', 'npu', 'cpu'])
    expect(lanes[0].loaded.map((t) => t.name)).toEqual(['agent', 'utility', 'brain'])
    expect(lanes[0].stopped.map((s) => s.name)).toEqual(['ops'])
  })

  it('omits a lane with no slots', () => {
    const lanes = laneRows([{ name: 'a', device: 'gpu-vulkan', state: 'ready', type: 'llm' }] as any, undefined, undefined)
    expect(lanes.map((l) => l.key)).toEqual(['vulkan'])
  })

  it('keeps slots with odd device tokens in a lane', () => {
    const lanes = laneRows(
      [
        { name: 'x', device: null, state: 'ready', type: 'llm' },
        { name: 'y', device: 'npu0', state: 'offline' },
      ] as any,
      undefined,
      undefined,
    )
    expect(
      lanes
        .flatMap((l) => [...l.loaded, ...l.stopped])
        .map((t) => t.name)
        .sort(),
    ).toEqual(['x', 'y'])
  })

  const ctxFor = (metrics: any, extra: any = {}) =>
    laneRows(
      [{ name: 'utility', device: 'gpu-vulkan', state: 'ready', type: 'llm', ctx_effective: 131072, metrics, ...extra }] as any,
      // kv_used is ppm of the KV cache (not tokens) and must not feed the context bar
      { slots: [{ slot_id: 'utility', kv_used: 999999 }] } as any,
      undefined,
    )[0].loaded[0].ctx

  it('reads the busiest llama slot prompt tokens (metrics.ctx) against ctx_effective', () =>
    expect(ctxFor({ ctx: 10486 })).toEqual({ used: 10486, max: 131072, pct: 8 }))

  it('a full window is 100% of the context', () =>
    expect(ctxFor({ ctx: 131072 })).toEqual({ used: 131072, max: 131072, pct: 100 }))

  it('falls back to ctx_max when ctx_effective is absent', () =>
    expect(ctxFor({ ctx: 2048 }, { ctx_effective: undefined, ctx_max: 4096 })).toEqual({ used: 2048, max: 4096, pct: 50 }))

  it('treats metrics.ctx of 0 as absent, like the slot pane', () => expect(ctxFor({ ctx: 0 })).toBeNull())

  it('leaves ctx null when either side is missing', () => {
    const noUsed = laneRows([{ name: 'u', device: 'gpu-vulkan', state: 'ready', type: 'llm', ctx_effective: 4096 }] as any, undefined, undefined)
    const noMax = laneRows([{ name: 'u', device: 'gpu-vulkan', state: 'ready', type: 'llm', metrics: { ctx: 5 } }] as any, undefined, undefined)
    expect(noUsed[0].loaded[0].ctx).toBeNull()
    expect(noMax[0].loaded[0].ctx).toBeNull()
  })

  it('attaches a sparkline only when serving with two or more points', () => {
    const per = { utility: [40, 52, 61] }
    expect(laneRows([SERVING_UTILITY], undefined, per)[0].loaded[0].spark).toEqual([40, 52, 61])
    expect(laneRows([SERVING_UTILITY], undefined, { utility: [40] })[0].loaded[0].spark).toBeNull()
    expect(
      laneRows([{ name: 'utility', device: 'gpu-vulkan', state: 'ready', type: 'llm' }] as any, undefined, per)[0].loaded[0].spark,
    ).toBeNull()
  })

  it('state text carries tok/s and in-flight when serving', () =>
    expect(laneRows([SERVING_UTILITY], { slots: [{ slot_id: 'utility', inflight: 2 }] } as any, undefined)[0].loaded[0].stateText).toBe(
      '58 tok/s · 2 in flight',
    ))

  it('state text drops in-flight when unknown and falls back to the label without a reading', () => {
    expect(laneRows([SERVING_UTILITY], undefined, undefined)[0].loaded[0].stateText).toBe('58 tok/s')
    const noToks = { ...SERVING_UTILITY, metrics: {} }
    expect(laneRows([noToks], undefined, undefined)[0].loaded[0].stateText).toBe('serving')
    expect(laneRows([{ ...SERVING_UTILITY, state: 'ready' }], undefined, undefined)[0].loaded[0].stateText).toBe('ready')
  })

  it('a measured 0 tok/s still renders with its reading', () =>
    expect(laneRows([{ ...SERVING_UTILITY, metrics: { toks: 0 } }], { slots: [{ slot_id: 'utility', inflight: 1 }] } as any, undefined)[0].loaded[0].stateText).toBe(
      '0.0 tok/s · 1 in flight',
    ))

  it('captions the image engine and support slots', () => {
    const lanes = laneRows(SLOTS, SYS, undefined)
    const rocm = lanes[1].loaded
    expect(rocm.find((t) => t.name === 'img')).toMatchObject({ caption: 'engine 5.7 GB resident', isImageEngine: true })
    expect(rocm.find((t) => t.name === 'tts')).toMatchObject({ caption: 'support slot', isImageEngine: false })
    expect(lanes[0].loaded.find((t) => t.name === 'brain')?.caption).toBeNull()
  })

  it('carries model, port, default flag, class and memory label', () => {
    const agent = laneRows(SLOTS, SYS, undefined)[0].loaded[0]
    expect(agent).toMatchObject({ name: 'agent', model: 'Qwen3.8-27B-MTP', port: 8101, isDefault: true, cls: 'stale', memLabel: '22.0 GB' })
  })
})
