// Pure helpers for the NPU tab (Slots → NPU): visibility, sub-tab ids, the status line.
import type { NpuOccupancy } from '@/api/hooks/useNpuOccupancy'
import type { StatsHardware } from '@/api/hooks/useStatsHardware'
import { devKind } from '@/lib/deviceMeta'
import { isNpuShadowSlot } from '../npu-modality.js'

export const NPU_TABS = [
  { id: 'lanes', label: 'Lanes' },
  { id: 'models', label: 'Models' },
  { id: 'engines', label: 'Engines & profiles' },
] as const
export type NpuTabId = (typeof NPU_TABS)[number]['id']

export function validNpuTab(id: string | null | undefined): NpuTabId | null {
  const hit = NPU_TABS.find((t) => t.id === id)
  return hit ? hit.id : null
}

/** D10: the tab shows when the NPU is present or any slot lives on it (so a box whose probe
 *  failed but whose NPU slots exist keeps its tab, degraded). */
export function npuTabVisible(
  occ: Pick<NpuOccupancy, 'present'> | undefined,
  slots: ReadonlyArray<{ device_class?: string | null }>,
): boolean {
  return occ?.present === true || (slots ?? []).some((s) => s?.device_class === 'npu')
}

export interface StatusPart {
  k: string
  v: string
  tone?: 'ok' | 'warn'
}

const DASH = '—'
const DOWN = new Set(['offline', 'off', ''])

/** Occupancy rows that currently hold the NPU (anything not offline/off). Rows the backend
 *  synthesizes for the anchor's STT/embed carry `served_by`: they ride the anchor's one process
 *  and are not holders. */
export function npuHolders<T extends { state?: string | null; served_by?: string | null }>(
  rows: ReadonlyArray<T> | undefined,
): T[] {
  return (rows ?? []).filter((s) => !s.served_by && !DOWN.has((s.state ?? '') as string))
}

/** Summed tok/s over the NPU slots only (not GPU slots, not STT/embed shadows), rounded to one
 *  decimal; null when none is generating. One helper so the array band and the Inference-tab
 *  summary cannot disagree. */
export function npuToks(
  slots: ReadonlyArray<{ device?: unknown; device_class?: string | null; metrics?: { toks?: number | null } | null }> | undefined,
): number | null {
  const rates = (slots ?? [])
    .filter((s) => s && (s.device_class === 'npu' || devKind(s.device as never) === 'npu') && !isNpuShadowSlot(s))
    .map((s) => s.metrics?.toks)
    .filter((t): t is number => typeof t === 'number' && t > 0)
  return rates.length ? Math.round(rates.reduce((a, b) => a + b, 0) * 10) / 10 : null
}

export function npuStatusParts(
  occ: NpuOccupancy | undefined,
  hw: Pick<StatsHardware, 'npu_pmode' | 'npu_fclk'> | undefined,
  _slots: ReadonlyArray<unknown> = [],
): StatusPart[] {
  const rows = occ?.slots ?? []
  const host = occ?.npu_host
  const engines = occ?.npu_engines
  const holders = npuHolders(rows)
  const xdnaUp = holders.some((s) => s.engine === 'xdna')

  const node = host?.device_node ?? null
  const driver = host?.driver_loaded
  const device = node
    ? `${node}${driver === true ? ' · driver loaded' : driver === false ? ' · no driver' : ''}`
    : DASH

  const pmode = hw?.npu_pmode ?? null
  const fclk = typeof hw?.npu_fclk === 'number' ? hw.npu_fclk : null

  return [
    { k: 'device', v: device, ...(driver === true ? { tone: 'ok' as const } : {}) },
    { k: 'pmode', v: pmode ?? DASH, ...(pmode && pmode !== 'turbo' && xdnaUp ? { tone: 'warn' as const } : {}) },
    { k: 'fclk', v: fclk != null ? `${fclk} MHz` : DASH },
    {
      k: 'holders',
      v: occ ? `${holders.length}${holders.length > 1 ? ' · time-sliced' : ''}` : DASH,
    },
    { k: 'flm', v: engines?.flm?.version ?? DASH },
    { k: 'ggml-xdna', v: engines?.xdna?.tag ?? DASH },
  ]
}
