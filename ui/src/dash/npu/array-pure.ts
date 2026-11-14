// Pure helpers for the NPU array band: who owns which of the 8 AIE columns.
import { isNpuShadowSlot } from '../npu-modality.js'
import { slotIndicatorFromPhase } from '../slot-status.js'

export const NPU_COLS = 8
export interface ArrayOwner {
  name: string
  engine: 'flm' | 'xdna'
  serving: boolean
}
type OccSlot = { name: string; cols?: number[]; engine?: 'flm' | 'xdna'; state?: string | null }
type SlotLike = { name: string; type?: string; binary?: string; runner?: string | null; device_class?: string | null; state?: string | null; phase?: string | null }

/** One owner object per holder, shared by reference across its columns (AieGrid merges runs by
 *  identity). Shadows (stt/embed) ride the anchor's process and never own columns. Engine comes
 *  from the occupancy row, else the slot's runner. */
export function arrayOwners(occSlots: ReadonlyArray<OccSlot> | undefined, slots: ReadonlyArray<SlotLike> | undefined): Array<ArrayOwner | null> {
  const owners: Array<ArrayOwner | null> = Array(NPU_COLS).fill(null)
  const byName = new Map((slots ?? []).map((s) => [s.name, s]))
  for (const o of occSlots ?? []) {
    const slot = byName.get(o.name)
    if (slot && isNpuShadowSlot(slot)) continue
    const cols = (o.cols ?? []).filter((c) => c >= 0 && c < NPU_COLS)
    if (!cols.length) continue
    const engine: 'flm' | 'xdna' = o.engine ?? (slot?.binary === 'xdna' || slot?.runner === 'xdna' ? 'xdna' : 'flm')
    const serving = slot ? slotIndicatorFromPhase(slot).cls === 'serving' : String(o.state ?? '').toLowerCase() === 'serving'
    const owner: ArrayOwner = { name: o.name, engine, serving }
    for (const c of cols) owners[c] = owner
  }
  return owners
}

export function arrayShared(owners: ReadonlyArray<ArrayOwner | null>): boolean {
  return new Set(owners.filter(Boolean).map((o) => (o as ArrayOwner).name)).size > 1
}
