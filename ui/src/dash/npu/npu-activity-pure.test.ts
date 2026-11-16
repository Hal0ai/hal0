import { describe, expect, it } from 'vitest'
import { npuLaneRows } from './npu-activity-pure'

const occ = [
  { name: 'flm', engine: 'flm', state: 'ready', cols: [0, 1, 2, 3, 4, 5, 6, 7], model: 'gemma4-it:e4b', gb: 4, last_used_age_s: 3 },
  { name: 'flm-stt', engine: 'flm', state: 'ready', cols: [0, 1, 2, 3, 4, 5, 6, 7], model: 'whisper-v3:turbo', gb: null, served_by: 'flm', last_used_age_s: 250 },
  { name: 'flm-embed', engine: 'flm', state: 'off', cols: [], model: 'embedding-gemma', gb: null, served_by: 'flm' },
  { name: 'npu-fast', engine: 'xdna', state: 'idle', cols: [0, 1, 2, 3, 4, 5, 6, 7], model: 'qwen3.5-0.8b', gb: null, last_used_age_s: null },
  { name: 'npu-lfm', engine: 'xdna', state: 'offline', cols: [], model: 'lfm2', gb: null },
] as never

describe('npuLaneRows', () => {
  it('lists the lanes that are up, the anchor STT row included, offline/off ones dropped', () => {
    const rows = npuLaneRows(occ, [], undefined, true)
    expect(rows.map((r) => r.name)).toEqual(['flm', 'flm-stt', 'npu-fast'])
  })
  it('roles, columns and the shared marker', () => {
    const [flm, stt, ggml] = npuLaneRows(occ, [], undefined, true)
    expect([flm.role, flm.cols, flm.engine]).toEqual(['chat', '8 · shared', 'flm'])
    expect([stt.role, stt.cols]).toEqual(['stt', 'shares flm'])
    expect([ggml.role, ggml.engine]).toEqual(['chat', 'xdna'])
    expect(npuLaneRows(occ, [], undefined, false)[0].cols).toBe('8')
  })
  it('live tok/s from the slot metrics while serving, null when idle; activity by name', () => {
    const lane = { tps: [1], peak_tps: 1, tokens: 1, requests: 2, errors: 0, ttft_p50_ms: 300 }
    const serving = (occ as unknown as Array<Record<string, unknown>>).map((o) => (o.name === 'flm' ? { ...o, state: 'serving' } : o)) as never
    const rows = npuLaneRows(
      serving,
      [{ name: 'flm', metrics: { toks: 18.2 } }, { name: 'npu-fast', metrics: { toks: 0 } }],
      { flm: lane },
      false,
    )
    expect(rows[0].nowToks).toBe(18.2)
    expect(rows[0].activity).toBe(lane)
    expect(rows[2].nowToks).toBeNull()
    expect(rows[2].activity).toBeNull()
    expect(rows[1].lastUsedAge).toBe(250)
  })
  it('a lane back at ready drops its last sample instead of showing it as live', () => {
    // The backend keeps a slot's last tok/s after the request ends.
    const rows = npuLaneRows(occ, [{ name: 'flm', metrics: { toks: 18.2 } }], undefined, false)
    expect(rows[0].nowToks).toBeNull()
  })
})
