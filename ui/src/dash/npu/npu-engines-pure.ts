// Pure builders for the NPU Engines & profiles sub-tab (Slots → NPU → Engines).
// Host readiness: every row is judged from a fact hal0 read (npu_host / hardware stats);
// a null fact is `unknown` (or `info`) with the honest reason, never `ok`.
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
export const XRT_SMI_INSTALL_CMD = 'sudo apt install libxrt-utils-npu'

type Hw = { npu_pmode?: string | null; npu_fclk?: number | null } | null | undefined

export function readinessRows(occ: Pick<NpuOccupancy, 'npu_host'> | null | undefined, hw: Hw): ReadinessRow[] {
  const host = occ?.npu_host
  const pmode = hw?.npu_pmode ?? null
  const fclk = hw?.npu_fclk ?? null
  const notReported = 'not reported'

  const fwMin = host?.firmware_min ?? '1.1.2.65'
  const firmware: ReadinessRow = !host
    ? { id: 'firmware', label: 'Firmware', state: 'unknown', detail: notReported }
    : host.firmware_version == null
      ? { id: 'firmware', label: 'Firmware', state: 'unknown', detail: `needs ≥ ${fwMin}; could not read /sys/class/accel/accel*/device/fw_version` }
      : host.firmware_ok === false
        ? { id: 'firmware', label: 'Firmware', state: 'warn', detail: `${host.firmware_version}; the ggml-xdna lane needs ≥ ${fwMin}` }
        : { id: 'firmware', label: 'Firmware', state: 'ok', detail: `${host.firmware_version} (needs ≥ ${fwMin})` }

  const xrtMin = host?.xrt_min ?? '2.25.0'
  const xrt: ReadinessRow = !host
    ? { id: 'xrt', label: 'XRT', state: 'unknown', detail: notReported }
    : host.xrt_version == null
      ? { id: 'xrt', label: 'XRT', state: 'info', detail: `no XRT library on the host; GGML slots use the userspace XRT bundled in the xdna runner image (a host copy, ${xrtMin}, only matters with [xdna].host_xrt)` }
      : host.xrt_ok === false
        ? { id: 'xrt', label: 'XRT', state: 'warn', detail: `host ${host.xrt_version}; the xdna runner image is built against ${xrtMin}` }
        : { id: 'xrt', label: 'XRT', state: 'ok', detail: `host ${host.xrt_version} (needs ${xrtMin})` }

  const iommu: ReadinessRow = !host
    ? { id: 'iommu', label: 'Kernel params', state: 'unknown', detail: notReported }
    : host.cmdline_missing == null
      ? { id: 'iommu', label: 'Kernel params', state: 'unknown', detail: 'needs `iommu=pt amd_iommu=on`; could not read /proc/cmdline' }
      : host.cmdline_missing.length > 0
        ? { id: 'iommu', label: 'Kernel params', state: 'warn', detail: `missing ${host.cmdline_missing.map((t) => `\`${t}\``).join(' ')} on the host kernel command line` }
        : { id: 'iommu', label: 'Kernel params', state: 'ok', detail: '`iommu=pt amd_iommu=on` set' }

  const pmodeRow: ReadinessRow =
    pmode == null
      ? { id: 'pmode', label: 'Power mode', state: 'unknown', detail: 'not reported: hal0 reads it with xrt-smi on the host; install it (package libxrt-utils-npu, lemonade-team PPA), then set turbo', command: XRT_SMI_INSTALL_CMD }
      : pmode === 'turbo'
        ? { id: 'pmode', label: 'Power mode', state: 'ok', detail: 'turbo' }
        : { id: 'pmode', label: 'Power mode', state: 'warn', detail: `${pmode}; turbo measured about +15% prefill and +20% decode over the default power mode`, command: PMODE_TURBO_CMD }

  const mhz = fclk == null ? null : `${fclk} MHz`
  const fclkRow: ReadinessRow =
    host?.fclk_held === true
      ? { id: 'fclk', label: 'GPU fabric clock', state: 'ok', detail: `${mhz ? `${mhz}, ` : ''}held at the top level (${host.fclk_perf_level ?? 'high'})` }
      : host?.fclk_held === false
        ? { id: 'fclk', label: 'GPU fabric clock', state: 'warn', detail: `${mhz ? `${mhz}, ` : ''}not held (${host.fclk_perf_level ?? 'unknown level'}); running GPU slots beside an NPU slot can hang the box (ADR-0054), hold it on the Proxmox host with \`hal0-fabric-clock hold\`` }
        : fclk == null
          ? { id: 'fclk', label: 'GPU fabric clock', state: 'unknown', detail: 'could not read the GPU fabric clock from sysfs' }
          : { id: 'fclk', label: 'GPU fabric clock', state: 'info', detail: `${mhz}; whether it is held could not be read from here (ADR-0054: only the Proxmox host can hold it)` }

  const memlock: ReadinessRow =
    host?.memlock === 'unlimited'
      ? { id: 'memlock', label: 'Memlock', state: 'ok', detail: 'hal0 starts GGML slot containers with `--ulimit memlock=-1` (unlimited)' }
      : { id: 'memlock', label: 'Memlock', state: 'unknown', detail: notReported }

  return [
    host?.device_node
      ? { id: 'device', label: 'Device node', state: 'ok', detail: host.device_node }
      : { id: 'device', label: 'Device node', state: 'unknown', detail: host ? 'no device node found' : notReported },
    host?.driver_loaded == null
      ? { id: 'driver', label: 'amdxdna driver', state: 'unknown', detail: notReported }
      : host.driver_loaded
        ? { id: 'driver', label: 'amdxdna driver', state: 'ok', detail: 'loaded' }
        : { id: 'driver', label: 'amdxdna driver', state: 'warn', detail: 'not detected (hal0 may be unable to read the module list from its container)' },
    firmware,
    xrt,
    iommu,
    pmodeRow,
    fclkRow,
    memlock,
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
