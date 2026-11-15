// The NPU array band's lane table: one row per NPU lane that is up, the anchor's STT/embed rows
// included (they share its process and columns, so they read "shares <anchor>").
import type { NpuOccupancySlot } from '@/api/hooks/useNpuOccupancy'
import type { SlotActivityLane } from '@/api/hooks/useSlotActivity'

export interface NpuLaneRow {
  name: string
  engine: 'flm' | 'xdna'
  role: 'chat' | 'stt' | 'embed'
  model: string | null
  /** "8", "8 · shared", or "shares flm" for a row riding its anchor's process. */
  cols: string
  /** Live tok/s from the slot's own metrics; null when not generating or not reported. */
  nowToks: number | null
  lastUsedAge: number | null
  activity: SlotActivityLane | null
}

type SlotLike = { name: string; metrics?: { toks?: number | null } | null }

const DOWN = new Set(['offline', 'off', ''])

function roleOf(row: Pick<NpuOccupancySlot, 'name' | 'served_by'>): NpuLaneRow['role'] {
  if (!row.served_by) return 'chat'
  return row.name.endsWith('-embed') ? 'embed' : 'stt'
}

export function npuLaneRows(
  occSlots: ReadonlyArray<NpuOccupancySlot> | undefined,
  slots: ReadonlyArray<SlotLike> | undefined,
  activity: Record<string, SlotActivityLane> | undefined,
  shared: boolean,
): NpuLaneRow[] {
  const byName = new Map((slots ?? []).map((s) => [s.name, s]))
  return (occSlots ?? [])
    .filter((o) => !DOWN.has(String(o.state ?? '')))
    .map((o) => {
      const toks = byName.get(o.name)?.metrics?.toks
      const n = (o.cols ?? []).length
      return {
        name: o.name,
        engine: o.engine === 'xdna' ? 'xdna' : 'flm',
        role: roleOf(o),
        model: o.model ?? null,
        cols: o.served_by ? `shares ${o.served_by}` : n ? `${n}${shared ? ' · shared' : ''}` : '—',
        nowToks: typeof toks === 'number' && toks > 0 ? toks : null,
        lastUsedAge: o.last_used_age_s ?? null,
        activity: activity?.[o.name] ?? null,
      }
    })
}
