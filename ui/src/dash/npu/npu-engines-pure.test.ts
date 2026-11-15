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
    expect(w.detail).toContain('over the default power mode')
    expect(w.command).toBe('sudo xrt-smi configure --pmode turbo')
    expect(row(readinessRows(occ(null), hw({ npu_pmode: 'turbo' })), 'pmode').state).toBe('ok')
  })
  const FULL = {
    device_node: '/dev/accel/accel0',
    driver_loaded: true,
    xdna_gen: 2,
    firmware_version: '1.1.2.65',
    firmware_min: '1.1.2.65',
    firmware_ok: true,
    cmdline_missing: [],
    xrt_version: '2.25.0',
    xrt_min: '2.25.0',
    xrt_ok: true,
    memlock: 'unlimited',
    fclk_held: true,
    fclk_perf_level: 'high',
  }
  it('with no host block, host-derived rows are unknown (not reported)', () => {
    const rows = readinessRows(occ(null), hw())
    for (const id of ['firmware', 'xrt', 'iommu', 'memlock']) {
      expect(row(rows, id).state).toBe('unknown')
      expect(row(rows, id).detail).toBe('not reported')
    }
  })
  it('all facts good: firmware, XRT, kernel params, memlock, fabric clock are ok', () => {
    const rows = readinessRows(occ(FULL), hw({ npu_fclk: 2000 }))
    expect(row(rows, 'firmware')).toMatchObject({ state: 'ok', detail: '1.1.2.65 (needs ≥ 1.1.2.65)' })
    expect(row(rows, 'xrt').state).toBe('ok')
    expect(row(rows, 'iommu').state).toBe('ok')
    expect(row(rows, 'memlock')).toMatchObject({ state: 'ok' })
    expect(row(rows, 'memlock').detail).toContain('memlock=-1')
    expect(row(rows, 'fclk')).toMatchObject({ state: 'ok' })
    expect(row(rows, 'fclk').detail).toContain('2000 MHz')
  })
  it('old firmware warns and names the minimum', () => {
    const f = row(readinessRows(occ({ ...FULL, firmware_version: '1.0.0.63', firmware_ok: false }), hw()), 'firmware')
    expect(f.state).toBe('warn')
    expect(f.detail).toContain('1.0.0.63')
    expect(f.detail).toContain('1.1.2.65')
  })
  it('unreadable firmware is unknown with the sysfs path as the reason', () => {
    const f = row(readinessRows(occ({ ...FULL, firmware_version: null, firmware_ok: null }), hw()), 'firmware')
    expect(f.state).toBe('unknown')
    expect(f.detail).toContain('fw_version')
  })
  it('kernel params: missing tokens warn naming each, unreadable is unknown', () => {
    const w = row(readinessRows(occ({ ...FULL, cmdline_missing: ['iommu=pt', 'amd_iommu=on'] }), hw()), 'iommu')
    expect(w.state).toBe('warn')
    expect(w.detail).toContain('`iommu=pt`')
    expect(w.detail).toContain('`amd_iommu=on`')
    expect(row(readinessRows(occ({ ...FULL, cmdline_missing: null }), hw()), 'iommu').state).toBe('unknown')
  })
  it('XRT: below minimum warns; none on the host is info naming the bundled image XRT', () => {
    expect(row(readinessRows(occ({ ...FULL, xrt_version: '2.19.0', xrt_ok: false }), hw()), 'xrt').state).toBe('warn')
    const none = row(readinessRows(occ({ ...FULL, xrt_version: null, xrt_ok: null }), hw()), 'xrt')
    expect(none.state).toBe('info')
    expect(none.detail).toContain('runner image')
  })
  it('pmode unknown says how to get xrt-smi and offers the install command', () => {
    const p = row(readinessRows(occ(FULL), hw({ npu_pmode: null })), 'pmode')
    expect(p.state).toBe('unknown')
    expect(p.detail).toContain('libxrt-utils-npu')
    expect(p.command).toBe('sudo apt install libxrt-utils-npu')
  })
  it('fabric clock: unheld warns with the host command; held unknown shows MHz as info', () => {
    const w = row(readinessRows(occ({ ...FULL, fclk_held: false, fclk_perf_level: 'auto' }), hw({ npu_fclk: 400 })), 'fclk')
    expect(w.state).toBe('warn')
    expect(w.detail).toContain('hal0-fabric-clock hold')
    const i = row(readinessRows(occ({ ...FULL, fclk_held: null, fclk_perf_level: null }), hw({ npu_fclk: 1600 })), 'fclk')
    expect(i.state).toBe('info')
    expect(i.detail).toContain('1600')
    expect(row(readinessRows(occ({ ...FULL, fclk_held: null }), hw()), 'fclk').state).toBe('unknown')
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
