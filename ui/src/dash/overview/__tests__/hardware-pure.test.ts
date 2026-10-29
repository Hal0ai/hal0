// pushSample ring + hardwareRows model for the Overview Hardware card.
import { describe, expect, it } from 'vitest'
import { GPU_PINNED_LONG } from '@/lib/gpuUtil'
import { hardwareRows, pushSample, type HardwareInput } from '../hardware-pure'

describe('pushSample', () => {
  it('appends and returns a new array', () => {
    const ring = [1, 2]
    const next = pushSample(ring, 3)
    expect(next).toEqual([1, 2, 3])
    expect(next).not.toBe(ring)
    expect(ring).toEqual([1, 2])
  })

  it('caps at 30 by dropping the oldest', () => {
    let ring: number[] = []
    for (let i = 0; i < 40; i++) ring = pushSample(ring, i)
    expect(ring).toHaveLength(30)
    expect(ring[0]).toBe(10)
    expect(ring[29]).toBe(39)
  })

  it('honours a custom cap', () => {
    expect(pushSample([1, 2, 3], 4, 3)).toEqual([2, 3, 4])
  })

  it('skips null and non-finite values', () => {
    expect(pushSample([1], null)).toEqual([1])
    expect(pushSample([1], Number.NaN)).toEqual([1])
  })
})

const CT105: HardwareInput = {
  hasGpu: true,
  hasNpu: true,
  gpuName: 'Radeon 8060S',
  cores: '16c · 32t',
  stats: {
    gpu_util: 0.33,
    gpu_clock_mhz: 600,
    gpu_temp_c: 47,
    cpu_util: 0.12,
    npu_util: null,
    npu_status: { ok: false },
  },
  power: { gpu_power_w: 33, cpu_temp_c: 52 },
  ram: { usedGb: 13.14, limitGb: 106.2, source: 'container' },
  disk: { free_mb: 124 * 1024, floor_mb: 512, total_mb: 248 * 1024, path: '/var/lib/hal0' },
  npuSlots: [],
}

const byKey = (rows: ReturnType<typeof hardwareRows>, k: string) => rows.find((r) => r.key === k)!

describe('hardwareRows', () => {
  it('full CT105 input yields five rows with the documented captions', () => {
    const rows = hardwareRows(CT105)
    expect(rows.map((r) => r.key)).toEqual(['gpu', 'cpu', 'npu', 'ram', 'disk'])
    expect(byKey(rows, 'gpu').caption).toBe('Radeon 8060S · 0.6 GHz · 47°C · 33 W')
    expect(byKey(rows, 'gpu').pct).toBe(33)
    expect(byKey(rows, 'gpu').value).toBe('33%')
    expect(byKey(rows, 'cpu').caption).toBe('16c · 32t · 52°C')
    expect(byKey(rows, 'cpu').pct).toBe(12)
    expect(byKey(rows, 'npu').caption).toBe('no FLM slot loaded')
    expect(byKey(rows, 'npu').pill).toBe('idle')
    expect(byKey(rows, 'ram').value).toBe('13.1 / 106 GB')
    expect(byKey(rows, 'ram').caption).toBe('this box · container limit')
    expect(byKey(rows, 'disk').value).toBe('124 GB free')
    expect(byKey(rows, 'disk').caption).toBe('/var/lib/hal0 · floor 0.5 GB')
    expect(byKey(rows, 'disk').pct).toBe(50)
  })

  it('CPU-only input: GPU caption "no GPU access", pct null, no NPU row', () => {
    const rows = hardwareRows({ ...CT105, hasGpu: false, hasNpu: false })
    const gpu = byKey(rows, 'gpu')
    expect(gpu.caption).toBe('no GPU access')
    expect(gpu.pct).toBeNull()
    expect(gpu.value).toBe('—')
    expect(rows.map((r) => r.key)).toEqual(['gpu', 'cpu', 'ram', 'disk'])
  })

  it('omits missing GPU caption parts', () => {
    const rows = hardwareRows({
      ...CT105,
      stats: { ...CT105.stats, gpu_clock_mhz: null, gpu_temp_c: null },
      power: { gpu_power_w: null, cpu_temp_c: null },
    })
    expect(byKey(rows, 'gpu').caption).toBe('Radeon 8060S')
    expect(byKey(rows, 'cpu').caption).toBe('16c · 32t')
  })

  it('prefixes the pinned caption when the counter is forced high', () => {
    const rows = hardwareRows({ ...CT105, stats: { ...CT105.stats, gpu_util: 1, util_is_forced_high: true } })
    expect(byKey(rows, 'gpu').caption).toBe(`${GPU_PINNED_LONG} · Radeon 8060S · 0.6 GHz · 47°C · 33 W`)
  })

  it('names coresident NPU slots and marks active', () => {
    const rows = hardwareRows({
      ...CT105,
      stats: { ...CT105.stats, npu_util: 0.4, npu_status: { ok: true, model_mb: 4096 } },
      npuSlots: ['flm-a', 'flm-b'],
    })
    const npu = byKey(rows, 'npu')
    expect(npu.caption).toBe('flm-a + flm-b coresident · 4.0 GB')
    expect(npu.pill).toBe('active')
    expect(npu.pct).toBe(40)
  })

  it('names a single NPU slot', () => {
    const rows = hardwareRows({ ...CT105, npuSlots: ['flm'] })
    expect(byKey(rows, 'npu').caption).toBe('flm')
  })

  it('shows free disk as the same floored figure the Needs-you line uses', () => {
    const rows = hardwareRows({ ...CT105, disk: { free_mb: 124429, floor_mb: 512, total_mb: 248 * 1024, path: '/var/lib/hal0' } })
    expect(byKey(rows, 'disk').value).toBe('121 GB free')
  })

  it('renders dashes, never zero, for unsourced RAM and disk', () => {
    const rows = hardwareRows({ ...CT105, ram: null, disk: null })
    expect(byKey(rows, 'ram').value).toBe('—')
    expect(byKey(rows, 'ram').pct).toBeNull()
    expect(byKey(rows, 'disk').value).toBe('—')
    expect(byKey(rows, 'disk').pct).toBeNull()
  })

  it('disk pct is null when the check carries no total', () => {
    const rows = hardwareRows({ ...CT105, disk: { free_mb: 124 * 1024, floor_mb: 512, path: '/var/lib/hal0' } })
    expect(byKey(rows, 'disk').pct).toBeNull()
    expect(byKey(rows, 'disk').value).toBe('124 GB free')
  })

  it('system-source RAM reads "system"', () => {
    const rows = hardwareRows({ ...CT105, ram: { usedGb: 10, limitGb: 64, source: 'system' } })
    expect(byKey(rows, 'ram').caption).toBe('this box · system')
  })
})
