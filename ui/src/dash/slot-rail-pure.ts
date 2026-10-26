// The slot rail's rules: which device group a slot sits in, how the rows are ordered and counted,
// what the state cell says and what the footer totals. Pure, so the ordering and the counts are
// tested without rendering (__tests__/slot-rail-pure.test.ts); slot-rail.jsx only draws the result.
//
// The indicator class and label come from slot-status.js (slotIndicatorFromPhase), the one
// classifier every slot surface shares. The rail adds a single state of its own, `paused`: a slot
// the GPU arbiter unloaded to make room for image mode.

import type { Slot } from '@/api/hooks/useSlots'
import { slotIndicatorFromPhase } from './slot-status.js'

export type RailGroup = 'vulkan' | 'rocm' | 'npu' | 'cpu'

export const RAIL_GROUPS: ReadonlyArray<{ key: RailGroup; label: string }> = [
  { key: 'vulkan', label: 'GPU · vulkan' },
  { key: 'rocm', label: 'GPU · rocm' },
  { key: 'npu', label: 'NPU' },
  { key: 'cpu', label: 'CPU' },
]

/** The rail group of a device string (`gpu-vulkan`, `gpu-rocm`, `npu`, `cpu`, …). Anything that is
 *  not a GPU or NPU device lands in `cpu`. */
export function railGroup(device: string | null | undefined): RailGroup {
  const d = (device || '').toLowerCase()
  if (d.includes('npu')) return 'npu'
  if (d.includes('rocm')) return 'rocm'
  if (d.includes('vulkan') || d.startsWith('gpu')) return 'vulkan'
  return 'cpu'
}

export type RailCls = 'serving' | 'warming' | 'paused' | 'stale' | 'error' | 'offline'

export interface RailRow {
  name: string
  model: string | null
  device: string
  group: RailGroup
  cls: RailCls
  label: string
  tooltip: string
  toks: number | null
  mem: string
  up: boolean
}

// Serving first, then what is on its way up, then what the arbiter parked, then idle-but-loaded,
// then the rows that need an eye, then the stopped ones.
const CLS_ORDER: readonly RailCls[] = ['serving', 'warming', 'paused', 'stale', 'error', 'offline']

const NO_MEM = '—'

/** One rail row per slot: grouped by device, ordered by state inside a group, input order breaking
 *  ties. `cls` is `paused` while image mode is on and the arbiter's unload set holds the slot.
 *  `memLabel` renders a slot's resident memory (slotMemLabel in the page); it is only asked for
 *  rows that are up. */
export function railRows(
  slots: readonly Slot[],
  unloadSet: readonly string[],
  imageMode: boolean,
  now: number = Date.now(),
  memLabel: (slot: Slot) => string = () => NO_MEM,
): RailRow[] {
  const rows = slots.map((slot, index) => {
    const ind = slotIndicatorFromPhase(slot, now) as { cls: Exclude<RailCls, 'paused'>; label: string; tooltip: string }
    const paused = imageMode && unloadSet.includes(slot.name)
    const cls: RailCls = paused ? 'paused' : ind.cls
    const up = cls === 'serving' || cls === 'stale'
    const toks = typeof slot.metrics?.toks === 'number' ? slot.metrics.toks : null
    const row: RailRow = {
      name: slot.name,
      model: slot.model || null,
      device: slot.device || 'cpu',
      group: railGroup(slot.device),
      cls,
      label: paused ? 'paused' : ind.label,
      tooltip: paused ? 'Paused — unloaded for image mode' : ind.tooltip,
      toks,
      mem: up ? memLabel(slot) : NO_MEM,
      up,
    }
    return { row, index }
  })
  const groupIdx = (g: RailGroup) => RAIL_GROUPS.findIndex((x) => x.key === g)
  rows.sort(
    (a, b) =>
      groupIdx(a.row.group) - groupIdx(b.row.group) ||
      CLS_ORDER.indexOf(a.row.cls) - CLS_ORDER.indexOf(b.row.cls) ||
      a.index - b.index,
  )
  return rows.map((r) => r.row)
}

/** `ready` is serving plus stale (loaded and healthy); `byGroup` counts the same rows as `up`. */
export function railCounts(rows: readonly RailRow[]): {
  ready: number
  total: number
  byGroup: Record<RailGroup, { up: number; total: number }>
} {
  const byGroup: Record<RailGroup, { up: number; total: number }> = {
    vulkan: { up: 0, total: 0 },
    rocm: { up: 0, total: 0 },
    npu: { up: 0, total: 0 },
    cpu: { up: 0, total: 0 },
  }
  let ready = 0
  for (const r of rows) {
    byGroup[r.group].total += 1
    if (r.up) {
      byGroup[r.group].up += 1
      ready += 1
    }
  }
  return { ready, total: rows.length, byGroup }
}

/** The text and tone of a row's state cell: `42 tok/s` while serving with a figure, the indicator's
 *  own label otherwise. */
export function stateCell(row: RailRow): { text: string; tone: 'live' | 'paused' | 'warn' | 'err' | '' } {
  switch (row.cls) {
    case 'serving':
      return row.toks != null && row.toks > 0
        ? { text: `${Math.round(row.toks)} tok/s`, tone: 'live' }
        : { text: 'serving', tone: 'live' }
    case 'paused':
      return { text: row.label, tone: 'paused' }
    case 'warming':
      return { text: row.label, tone: 'warn' }
    case 'error':
      return { text: row.label, tone: 'err' }
    default:
      return { text: row.label, tone: '' }
  }
}

/** The footer: GB resident on the GPU and NPU rows that are up, the pool's free GB (null when the
 *  box reports none) and how many rows the arbiter has paused. CPU rows hold system RAM, not the
 *  shared pool, so they are left out of `loadedGb`. */
export function railFooter(
  slots: readonly Slot[],
  rows: readonly RailRow[],
  poolFreeGb: number | null,
): { loadedGb: number; poolFreeGb: number | null; paused: number } {
  const memMb = new Map(slots.map((s) => [s.name, typeof s.mem_mb === 'number' ? s.mem_mb : 0]))
  let mb = 0
  let paused = 0
  for (const r of rows) {
    if (r.cls === 'paused') paused += 1
    else if (r.up && r.group !== 'cpu') mb += memMb.get(r.name) ?? 0
  }
  return { loadedGb: mb / 1024, poolFreeGb, paused }
}
