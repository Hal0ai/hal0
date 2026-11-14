// Pure helpers for the NPU array band: who owns which of the 8 AIE columns.
import { isNpuShadowSlot } from '../npu-modality.js'
import { slotIndicatorFromPhase } from '../slot-status.js'

export const NPU_COLS = 8
export interface ArrayOwner {
  name: string
  engine: 'flm' | 'xdna'
  serving: boolean
}
type OccSlot = { name: string; cols?: number[]; engine?: 'flm' | 'xdna'; state?: string | null; served_by?: string | null }
type SlotLike = { name: string; type?: string; binary?: string; runner?: string | null; device_class?: string | null; state?: string | null; phase?: string | null }

export interface ArrayHolder extends ArrayOwner {
  cols: number[]
}

/** Every NPU holder with the columns it claims. Two holders may claim the same columns (FLM and an
 *  xdna slot time-slice them), so "who holds the NPU" and "is it shared" derive from this list, not
 *  from the per-column map. Shadows (stt/embed) ride the anchor's process and never hold. Engine
 *  comes from the occupancy row, else the slot's runner. */
export function arrayHolders(occSlots: ReadonlyArray<OccSlot> | undefined, slots: ReadonlyArray<SlotLike> | undefined): ArrayHolder[] {
  const holders: ArrayHolder[] = []
  const byName = new Map((slots ?? []).map((s) => [s.name, s]))
  for (const o of occSlots ?? []) {
    // Rows synthesized for the anchor's STT/embed ride its process and never own columns.
    if (o.served_by) continue
    const slot = byName.get(o.name)
    if (slot && isNpuShadowSlot(slot)) continue
    const cols = (o.cols ?? []).filter((c) => c >= 0 && c < NPU_COLS)
    if (!cols.length) continue
    const engine: 'flm' | 'xdna' = o.engine ?? (slot?.binary === 'xdna' || slot?.runner === 'xdna' ? 'xdna' : 'flm')
    const serving = slot ? slotIndicatorFromPhase(slot).cls === 'serving' : String(o.state ?? '').toLowerCase() === 'serving'
    holders.push({ name: o.name, engine, serving, cols })
  }
  return holders
}

/** One owner object per holder, shared by reference across its columns (AieGrid merges runs by
 *  identity). Where holders overlap, the last one paints the column; use arrayHolders/arrayShared
 *  for the full picture. */
export function arrayOwners(occSlots: ReadonlyArray<OccSlot> | undefined, slots: ReadonlyArray<SlotLike> | undefined): Array<ArrayOwner | null> {
  const owners: Array<ArrayOwner | null> = Array(NPU_COLS).fill(null)
  for (const h of arrayHolders(occSlots, slots)) {
    const owner: ArrayOwner = { name: h.name, engine: h.engine, serving: h.serving }
    for (const c of h.cols) owners[c] = owner
  }
  return owners
}

export function arrayShared(holders: ReadonlyArray<{ name: string }>): boolean {
  return new Set(holders.map((h) => h.name)).size > 1
}
