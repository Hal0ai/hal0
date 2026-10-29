// Overview v2 "Serving now": slots grouped into lanes (the slot rail's groups), loaded ones as tiles,
// the rest as stopped chips. Pure, so the grouping, the ctx fill and the state text are tested
// without rendering (__tests__/serving-pure.test.ts); serving-now.jsx only draws the result.

import type { Slot } from '@/api/hooks/useSlots'
import type { SystemStats } from '@/api/hooks/useSystemStats'
import { RAIL_GROUPS, railGroup, type RailCls, type RailGroup } from '../slot-rail-pure'
import { slotIndicatorFromPhase } from '../slot-status.js'
import { slotMemLabel } from '../slots/slot-shared.js'

export interface Tile {
  name: string
  model: string | null
  port: number | null
  isDefault: boolean
  cls: Exclude<RailCls, 'paused'>
  stateText: string
  memLabel: string
  ctx: { used: number; max: number; pct: number } | null
  caption: string | null
  spark: number[] | null
  isImageEngine: boolean
}

export interface Stopped {
  name: string
  model: string | null
}

export interface Lane {
  key: RailGroup
  label: string
  loaded: Tile[]
  stopped: Stopped[]
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

function toksText(toks: number): string {
  return `${toks < 10 ? toks.toFixed(1) : Math.round(toks)} tok/s`
}

function buildTile(
  slot: Slot,
  cls: Tile['cls'],
  label: string,
  sysSlot: SystemStats['slots'][number] | undefined,
  perSlot: Record<string, number[]> | undefined,
): Tile {
  const serving = cls === 'serving'
  const toks = isNum(slot.metrics?.toks) ? slot.metrics.toks : isNum(slot.metrics?.tokens_per_sec) ? slot.metrics.tokens_per_sec : null
  let stateText = label
  if (serving && toks != null) {
    stateText = toksText(toks)
    if (isNum(sysSlot?.inflight)) stateText += ` · ${sysSlot.inflight} in flight`
  }

  const max = slot.ctx_effective ?? (slot as { ctx_max?: number | null }).ctx_max ?? null
  // kv_used is parts-per-million of the slot's KV cache in use (sampler: kv_cache_usage * 1e6), not tokens.
  const kv = sysSlot?.kv_used ?? null
  let ctx: Tile['ctx'] = null
  if (isNum(kv) && isNum(max) && max > 0) {
    const pct = Math.min(100, Math.round(kv / 10000))
    ctx = { used: Math.round((pct / 100) * max), max, pct }
  }

  const isImageEngine = slot.type === 'image' || slot.name === 'img'
  let caption: string | null = null
  if (isImageEngine) {
    caption = isNum(slot.mem_mb) && slot.mem_mb > 0 ? `engine ${(slot.mem_mb / 1024).toFixed(1)} GB resident` : 'engine resident'
  } else if (!ctx && slot.type && slot.type !== 'llm') {
    caption = 'support slot'
  }

  const points = perSlot?.[slot.name]
  return {
    name: slot.name,
    model: slot.model || null,
    port: isNum(slot.port) ? slot.port : null,
    isDefault: !!slot.isDefault,
    cls,
    stateText,
    memLabel: slotMemLabel(slot) as string,
    ctx,
    caption,
    spark: serving && Array.isArray(points) && points.length >= 2 ? points : null,
    isImageEngine,
  }
}

/** The lanes of the Serving now card: one per rail group that holds a slot, in RAIL_GROUPS order, slots
 *  in input order. A slot the indicator calls `offline` is a stopped chip; every other state (serving,
 *  ready, starting, error) is a tile so a failing slot stays visible. */
export function laneRows(
  slots: readonly Slot[],
  sys: SystemStats | undefined,
  perSlot: Record<string, number[]> | undefined,
): Lane[] {
  const now = Date.now()
  const byName = new Map((sys?.slots ?? []).map((s) => [s.slot_id, s]))
  const lanes: Lane[] = RAIL_GROUPS.map((g) => ({ key: g.key, label: g.label, loaded: [], stopped: [] }))
  for (const slot of slots) {
    const ind = slotIndicatorFromPhase(slot, now) as { cls: Tile['cls']; label: string }
    const lane = lanes.find((l) => l.key === railGroup(slot.device))!
    if (ind.cls === 'offline') lane.stopped.push({ name: slot.name, model: slot.model || null })
    else lane.loaded.push(buildTile(slot, ind.cls, ind.label, byName.get(slot.name), perSlot))
  }
  return lanes.filter((l) => l.loaded.length + l.stopped.length > 0)
}
