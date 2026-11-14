import { describe, expect, it } from 'vitest'
import { npuProfiles, readinessRows } from './npu-engines-pure'

const occ = (host: unknown) => ({ npu_host: host }) as never
const hw = (over: Record<string, unknown> = {}) => over as never
const row = (rows: ReturnType<typeof readinessRows>, id: string) => rows.find((r) => r.id === id)!

describe('readinessRows', () => {
  it('lists the eight rows in order', () => {
    expect(readinessRows(occ(null), hw()).map((r) => r.id)).toEqual(['device', 'driver', 'firmware', 'xrt', 'iommu', 'pmode', 'fclk', 'memlock'])
  })
  it('null facts are unknown, never ok', () => {
    const rows = readinessRows(undefined, undefined)
    for (const id of ['device', 'driver', 'pmode', 'fclk']) expect(row(rows, id).state).toBe('unknown')
  })
  it('device and driver follow npu_host', () => {
    const rows = readinessRows(occ({ device_node: '/dev/accel/accel0', driver_loaded: true, xdna_gen: 2 }), hw())
    expect(row(rows, 'device')).toMatchObject({ state: 'ok', detail: '/dev/accel/accel0' })
    expect(row(rows, 'driver').state).toBe('ok')
  })
  it('driver false reads as not detected (warn), not a failure', () => {
    const d = row(readinessRows(occ({ device_node: null, driver_loaded: false, xdna_gen: null }), hw()), 'driver')
    expect(d.state).toBe('warn')
    expect(d.detail).toMatch(/not detected/)
  })
  it('pmode: null unknown, default warns with the command, turbo ok', () => {
    expect(row(readinessRows(occ(null), hw({ npu_pmode: null })), 'pmode').state).toBe('unknown')
    const w = row(readinessRows(occ(null), hw({ npu_pmode: 'default' })), 'pmode')
    expect(w.state).toBe('warn')
    expect(w.command).toBe('sudo xrt-smi configure --pmode turbo')
    expect(row(readinessRows(occ(null), hw({ npu_pmode: 'turbo' })), 'pmode').state).toBe('ok')
  })
  it('firmware, XRT, IOMMU, memlock are always unknown with the requirement', () => {
    const rows = readinessRows(occ(null), hw())
    expect(row(rows, 'firmware')).toMatchObject({ state: 'unknown' })
    expect(row(rows, 'firmware').detail).toContain('1.1.2.65')
    expect(row(rows, 'xrt').detail).toContain('2.25.0')
    expect(row(rows, 'iommu').detail).toContain('iommu=pt amd_iommu=on')
    expect(row(rows, 'memlock').state).toBe('unknown')
  })
  it('fclk shows the MHz when read', () => {
    const f = row(readinessRows(occ(null), hw({ npu_fclk: 1600 })), 'fclk')
    expect(f.state).toBe('ok')
    expect(f.detail).toContain('1600')
  })
})

describe('npuProfiles', () => {
  const profiles = [
    { name: 'flm', runner: 'flm', seed: true, ctx_size: 16384, intent: 'FLM NPU (chat/embed/STT)', flags: '', used_by: ['npu-flm'] },
    { name: 'xdna', runner: 'xdna', seed: true, ctx_size: 8192, vision: 'off', intent: 'GGUF on the NPU (ggml-xdna)', flags: '' },
    { name: 'xdna-long', runner: 'xdna', seed: false, cloned_from: 'xdna', ctx_size: 32768, vision: 'off', flags: '' },
    { name: 'flm-mine', seed: false, cloned_from: 'flm', ctx_size: 8192, flags: '' },
    { name: 'chat', runner: 'llama', seed: true, flags: '-fa on' },
    { name: 'other-clone', cloned_from: 'chat', flags: '' },
  ] as never
  const slots = [
    { name: 'gg1', profile: 'xdna' },
    { name: 'gg2', profile: 'xdna-long' },
    { name: 'gpu', profile: 'chat' },
  ] as never
  it('keeps NPU profiles only, with engine and seed flag', () => {
    const r = npuProfiles(profiles, slots)
    expect(r.map((p) => p.name)).toEqual(['flm', 'xdna', 'flm-mine', 'xdna-long'])
    expect(r.find((p) => p.name === 'flm')).toMatchObject({ engine: 'flm', seed: true })
    expect(r.find((p) => p.name === 'xdna-long')).toMatchObject({ engine: 'xdna', seed: false })
  })
  it('used-by comes from slots on that profile, merged with the profile used_by', () => {
    const r = npuProfiles(profiles, slots)
    expect(r.find((p) => p.name === 'xdna')!.usedBy).toEqual(['gg1'])
    expect(r.find((p) => p.name === 'flm')!.usedBy).toEqual(['npu-flm'])
    expect(r.find((p) => p.name === 'flm-mine')!.usedBy).toEqual([])
  })
  it('body summarises ctx, vision and flags (no flags key → none)', () => {
    const r = npuProfiles(profiles, slots)
    expect(r.find((p) => p.name === 'xdna')!.body).toBe('ctx 8192 · vision off · no flags')
    expect(r.find((p) => p.name === 'flm')!.body).toBe('ctx 16384 · no flags')
  })
})
