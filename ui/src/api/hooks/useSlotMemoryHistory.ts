// hal0 dashboard — per-slot memory history hook (#373).
//
// Client-side ring buffer: accumulates per_slot[slot].mem_mb snapshots
// from useStatsHardware at 2.5s cadence. ~21 samples = 52.5s history.
// Pure client-side — no backend time-series store needed.
//
// Returns: Record<slotName, SlotMemoryTick[]> keyed by slot name,
//   ordered oldest→newest. Consumed by MemoryMap's expanded variant
//   to render a sparkline next to each LegendRow.

import { useRef, useEffect } from 'react'
import { useStatsHardware, StatsHardware } from './useStatsHardware'

export interface SlotMemoryTick {
  ts: number        // epoch milliseconds
  bytesGb: number   // GB (converted from mem_mb)
}

const MAX_SAMPLES = 21       // ~52.5s at 2.5s cadence
const MB_PER_GB = 1024

function mbToGb(mb: number): number {
  if (mb == null || Number.isNaN(mb)) return 0
  return Math.round((mb / MB_PER_GB) * 10) / 10
}

/**
 * Accumulate per-slot memory history from the 2.5s stats/hardware poll.
 * Returns a ref-stable ring buffer keyed by slot name.
 *
 * Edge cases:
 * - Slot appears: history starts collecting from first sighting.
 * - Slot disappears (unloaded): history is cleared so the sparkline
 *   doesn't show stale data for a slot that's gone.
 * - No per_slot data in payload: ring buffers unchanged (no spike to 0).
 */
export function useSlotMemoryHistory(): Record<string, SlotMemoryTick[]> {
  const statsQ = useStatsHardware()
  const stats: StatsHardware | undefined = statsQ.data

  // Stable ref so we don't reset buffers on every render.
  const ringRef = useRef<Record<string, SlotMemoryTick[]>>({})

  useEffect(() => {
    const perSlot = (stats as any)?.per_slot
    if (!perSlot || typeof perSlot !== 'object') return

    const now = Date.now()
    const activeNames = new Set(Object.keys(perSlot))

    // For each slot in the current payload, push a tick.
    for (const [name, info] of Object.entries(perSlot)) {
      const mem = (info as any)?.mem_mb
      const gb = typeof mem === 'number' ? mbToGb(mem) : 0
      const tick: SlotMemoryTick = { ts: now, bytesGb: gb }

      const buf = ringRef.current[name]
      if (buf) {
        buf.push(tick)
        // Trim to MAX_SAMPLES from the front (oldest).
        while (buf.length > MAX_SAMPLES) buf.shift()
      } else {
        ringRef.current[name] = [tick]
      }
    }

    // Prune slots that disappeared from the payload.
    for (const name of Object.keys(ringRef.current)) {
      if (!activeNames.has(name)) {
        delete ringRef.current[name]
      }
    }
  }, [stats])

  return ringRef.current
}
