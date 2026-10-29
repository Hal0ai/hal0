// hal0 Overview v2 — Hardware card model: sample rings and the five rows.
// No React, no window globals: unit-testable in isolation.
import { GPU_PINNED_LONG, gpuUtilReading } from '@/lib/gpuUtil'
import { freeGb, MB_PER_GB, pctOf } from './overview-pure'

const DASH = '—'

/** Append `value` to `ring`, keeping the newest `cap` samples. Returns a new
 *  array; null and non-finite values are skipped (no fabricated trend). */
export function pushSample(ring: number[], value: number | null, cap = 30): number[] {
  if (typeof value !== 'number' || !Number.isFinite(value)) return ring.slice(-cap)
  return [...ring, value].slice(-cap)
}

export interface HardwareStats {
  gpu_util?: number | null
  util_is_forced_high?: boolean | null
  gpu_clock_mhz?: number | null
  gpu_temp_c?: number | null
  cpu_util?: number | null
  npu_util?: number | null
  npu_status?: { ok?: boolean | null; model_mb?: number | null } | null
}

export interface HardwarePower {
  gpu_power_w?: number | null
  cpu_temp_c?: number | null
}

export interface HardwareDisk {
  free_mb?: number | null
  floor_mb?: number | null
  total_mb?: number | null
  path?: string | null
}

export interface HardwareInput {
  hasGpu: boolean
  hasNpu: boolean
  gpuName?: string | null
  /** Core summary from the hardware probe, e.g. "16c · 32t". */
  cores?: string | null
  stats?: HardwareStats | null
  power?: HardwarePower | null
  ram?: { usedGb: number; limitGb: number; source: 'container' | 'system' } | null
  disk?: HardwareDisk | null
  /** Names of coresident NPU (FLM) slots. */
  npuSlots?: string[]
}

export interface Row {
  key: 'gpu' | 'cpu' | 'npu' | 'ram' | 'disk'
  label: string
  color: string
  caption: string
  /** 0-100, or null when the probe cannot source it. */
  pct: number | null
  value: string
  pill?: 'active' | 'idle'
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const pct100 = (v: number | null | undefined): number | null => (isNum(v) ? Math.round(v * 100) : null)
const pctText = (p: number | null): string => (p == null ? DASH : `${p}%`)
const joinParts = (parts: Array<string | null | undefined | false>): string =>
  parts.filter((p): p is string => !!p).join(' · ')

function gpuRow(i: HardwareInput): Row {
  const st = i.stats
  const base = { key: 'gpu' as const, label: 'gpu', color: 'var(--dev-vulkan)' }
  // The DRM node is visible from any LXC sharing the host kernel, so gpu_util
  // leaks the HOST's state on a box with no compute access (#1797).
  if (!i.hasGpu) return { ...base, caption: 'no GPU access', pct: null, value: DASH }
  const pct = pct100(st?.gpu_util)
  const measured = joinParts([
    isNum(st?.gpu_clock_mhz) ? `${(st.gpu_clock_mhz / 1000).toFixed(1)} GHz` : null,
    isNum(st?.gpu_temp_c) ? `${Math.round(st.gpu_temp_c)}°C` : null,
    isNum(i.power?.gpu_power_w) ? `${Math.round(i.power.gpu_power_w)} W` : null,
  ])
  const detail = joinParts([i.gpuName, measured]) || (pct == null ? 'pending driver' : '')
  const pinned = gpuUtilReading(st).pinned
  return { ...base, caption: pinned ? joinParts([GPU_PINNED_LONG, detail]) : detail, pct, value: pctText(pct) }
}

function cpuRow(i: HardwareInput): Row {
  const pct = pct100(i.stats?.cpu_util)
  const temp = isNum(i.power?.cpu_temp_c) ? `${Math.round(i.power.cpu_temp_c)}°C` : null
  const caption = joinParts([i.cores, temp]) || (pct == null ? 'pending driver' : 'host')
  return { key: 'cpu', label: 'cpu', color: 'var(--dev-cpu)', caption, pct, value: pctText(pct) }
}

function npuRow(i: HardwareInput): Row {
  const status = i.stats?.npu_status
  const slots = i.npuSlots ?? []
  const gb = isNum(status?.model_mb) && status.model_mb > 0 ? status.model_mb / MB_PER_GB : null
  const named = slots.length > 1 ? `${slots.join(' + ')} coresident` : slots[0]
  const caption = joinParts([named, gb != null ? `${gb.toFixed(1)} GB` : null]) || 'no FLM slot loaded'
  const pct = pct100(i.stats?.npu_util)
  return {
    key: 'npu',
    label: 'npu',
    color: 'var(--dev-npu)',
    caption,
    pct,
    value: pctText(pct),
    // No npu_status at all is an unread driver, not an idle device: no pill, the row shows a dash.
    pill: status ? (status.ok ? 'active' : 'idle') : undefined,
  }
}

function ramRow(i: HardwareInput): Row {
  const base = { key: 'ram' as const, label: 'ram', color: 'var(--fg-4)' }
  const r = i.ram
  if (!r || !isNum(r.usedGb) || !isNum(r.limitGb) || r.limitGb <= 0) {
    return { ...base, caption: 'this box', pct: null, value: DASH }
  }
  const pct = pctOf(r.usedGb, r.limitGb)
  return {
    ...base,
    caption: r.source === 'container' ? 'this box · container limit' : 'this box · system',
    pct: pct == null ? null : Math.round(pct),
    value: `${r.usedGb.toFixed(1)} / ${Math.round(r.limitGb)} GB`,
  }
}

function diskRow(i: HardwareInput): Row {
  const base = { key: 'disk' as const, label: 'disk', color: 'var(--fg-4)' }
  const d = i.disk
  if (!d || !isNum(d.free_mb)) return { ...base, caption: 'state root', pct: null, value: DASH }
  const floorGb = isNum(d.floor_mb) ? `floor ${+(d.floor_mb / MB_PER_GB).toFixed(1)} GB` : null
  // The check reports free space; use needs the total, which older payloads omit.
  const used = isNum(d.total_mb) && d.total_mb > 0 ? pctOf(d.total_mb - d.free_mb, d.total_mb) : null
  return {
    ...base,
    caption: joinParts([d.path, floorGb]) || 'state root',
    pct: used == null ? null : Math.round(used),
    value: `${freeGb(d.free_mb)} GB free`,
  }
}

/** The Hardware card rows. The NPU row is omitted on a box with no XDNA device. */
export function hardwareRows(input: HardwareInput): Row[] {
  return [gpuRow(input), cpuRow(input), ...(input.hasNpu ? [npuRow(input)] : []), ramRow(input), diskRow(input)]
}
