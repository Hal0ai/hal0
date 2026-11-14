// Pure builders for the NPU Engines & profiles sub-tab (Slots → NPU → Engines).
// Host readiness: only what hal0 can read from inside its container; everything else is
// `unknown` with the requirement spelled out — a null fact is never `ok`.
import type { NpuOccupancy } from '@/api/hooks/useNpuOccupancy'
import type { Profile } from '@/api/hooks/useProfiles'

export type ReadinessState = 'ok' | 'warn' | 'info' | 'unknown'
export interface ReadinessRow {
  id: string
  label: string
  state: ReadinessState
  detail: string
  command?: string
}

export const PMODE_TURBO_CMD = 'sudo xrt-smi configure --pmode turbo'

type Hw = { npu_pmode?: string | null; npu_fclk?: number | null } | null | undefined

export function readinessRows(occ: Pick<NpuOccupancy, 'npu_host'> | null | undefined, hw: Hw): ReadinessRow[] {
  const host = occ?.npu_host
  const pmode = hw?.npu_pmode ?? null
  const fclk = hw?.npu_fclk ?? null
  return [
    host?.device_node
      ? { id: 'device', label: 'Device node', state: 'ok', detail: host.device_node }
      : { id: 'device', label: 'Device node', state: 'unknown', detail: host ? 'no device node found' : 'not reported' },
    host?.driver_loaded == null
      ? { id: 'driver', label: 'amdxdna driver', state: 'unknown', detail: 'not reported' }
      : host.driver_loaded
        ? { id: 'driver', label: 'amdxdna driver', state: 'ok', detail: 'loaded' }
        : { id: 'driver', label: 'amdxdna driver', state: 'warn', detail: 'not detected (hal0 may be unable to read the module list from its container)' },
    { id: 'firmware', label: 'Firmware', state: 'unknown', detail: 'needs ≥ 1.1.2.65; hal0 cannot read host firmware from the container' },
    { id: 'xrt', label: 'XRT', state: 'unknown', detail: 'needs 2.25.0' },
    { id: 'iommu', label: 'Kernel params', state: 'unknown', detail: 'needs `iommu=pt amd_iommu=on`' },
    pmode == null
      ? { id: 'pmode', label: 'Power mode', state: 'unknown', detail: 'not reported', command: PMODE_TURBO_CMD }
      : pmode === 'turbo'
        ? { id: 'pmode', label: 'Power mode', state: 'ok', detail: 'turbo' }
        : { id: 'pmode', label: 'Power mode', state: 'warn', detail: `${pmode}; turbo measured about +15% prefill and +20% decode over the default power mode`, command: PMODE_TURBO_CMD },
    fclk == null
      ? { id: 'fclk', label: 'Fabric clock', state: 'unknown', detail: 'not reported' }
      : { id: 'fclk', label: 'Fabric clock', state: 'info', detail: `${fclk} MHz (no threshold known; not judged)` },
    { id: 'memlock', label: 'Memlock', state: 'unknown', detail: 'set on GGML slot containers' },
  ]
}

export interface NpuProfileRow {
  name: string
  engine: 'flm' | 'xdna'
  seed: boolean
  body: string
  usedBy: string[]
}

type SlotLike = { name: string; profile?: string | null }

function engineOf(p: Profile): 'flm' | 'xdna' | null {
  if (p.runner === 'xdna' || p.cloned_from === 'xdna') return 'xdna'
  if (p.name === 'flm' || p.runner === 'flm' || p.cloned_from === 'flm') return 'flm'
  return null
}

function bodyOf(p: Profile): string {
  const parts: string[] = []
  if (p.ctx_size != null) parts.push(`ctx ${p.ctx_size}`)
  if (p.vision) parts.push(`vision ${p.vision}`)
  parts.push(p.flags ? `flags ${p.flags}` : 'no flags')
  return parts.join(' · ')
}

/** NPU profiles: the `xdna` runner's, plus `flm` and clones of `flm`/`xdna`. Seeds first. */
export function npuProfiles(profiles: Profile[] | null | undefined, slots: SlotLike[] | null | undefined): NpuProfileRow[] {
  const out: NpuProfileRow[] = []
  for (const p of profiles ?? []) {
    if (p.quarantined) continue
    const engine = engineOf(p)
    if (!engine) continue
    const usedBy = [...new Set([...(p.used_by ?? []), ...(slots ?? []).filter((s) => s.profile === p.name).map((s) => s.name)])]
    out.push({ name: p.name, engine, seed: !!p.seed, body: bodyOf(p), usedBy })
  }
  return out.sort((a, b) => Number(b.seed) - Number(a.seed) || a.name.localeCompare(b.name))
}
