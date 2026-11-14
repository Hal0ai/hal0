// Pure helpers for the NPU tab (Slots → NPU): visibility, sub-tab ids, the status line.
import type { NpuOccupancy } from '@/api/hooks/useNpuOccupancy'
import type { StatsHardware } from '@/api/hooks/useStatsHardware'

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

export function npuStatusParts(
  occ: NpuOccupancy | undefined,
  hw: Pick<StatsHardware, 'npu_pmode' | 'npu_fclk'> | undefined,
  _slots: ReadonlyArray<unknown> = [],
): StatusPart[] {
  const rows = occ?.slots ?? []
  const host = occ?.npu_host
  const engines = occ?.npu_engines
  const holders = rows.filter((s) => !DOWN.has((s.state ?? '') as string))
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
