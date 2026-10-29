// The Now band's rules: the facts line, the mode label and the contextual actions. Pure, so the
// counts and the honesty rules (a missing reading is "—", never 0) are tested without rendering.

import type { Slot } from '@/api/hooks/useSlots'
import type { SystemStats } from '@/api/hooks/useSystemStats'
import type { ArbiterState } from '@/api/hooks/useComfyui'
import { slotIndicatorFromPhase } from '../slot-status.js'
import { railGroup } from '../slot-rail-pure'

const DASH = '—'

export interface GpuReading {
  access: boolean
  pct: number | null
  tempC: number | null
  watts: number | null
}

export interface NowFacts {
  loaded: number
  total: number
  serving: number
  tps: number | null
  inflight: number | null
  freeLabel: string
  gpuLabel: string
}

const isNum = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n)
const indicatorCls = (slot: Slot): string => (slotIndicatorFromPhase(slot) as { cls: string }).cls

export function nowFacts(
  slots: readonly Slot[],
  sys: SystemStats | undefined,
  freeGb: number | null,
  poolGb: number | null,
  gpu: GpuReading,
): NowFacts {
  const serving = slots.filter((s) => indicatorCls(s) === 'serving')
  const readings = serving.map((s) => s.metrics?.toks).filter(isNum)
  const tps = readings.length > 0 ? readings.reduce((a, b) => a + b, 0) : null

  let inflight: number | null = null
  if (sys) {
    const byId = new Map(sys.slots.map((r) => [r.slot_id, r.inflight]))
    const counts = serving.map((s) => byId.get(s.name)).filter(isNum)
    // Serving slots with no inflight figure between them: nothing to sum, not zero.
    inflight = serving.length > 0 && counts.length === 0 ? null : counts.reduce((a, b) => a + b, 0)
  }

  let gpuLabel = 'no GPU access'
  if (gpu.access) {
    const parts = [`gpu ${isNum(gpu.pct) ? `${Math.round(gpu.pct)}%` : DASH}`]
    if (isNum(gpu.tempC)) parts.push(`${Math.round(gpu.tempC)}°C`)
    if (isNum(gpu.watts)) parts.push(`${Math.round(gpu.watts)} W`)
    gpuLabel = parts.join(' · ')
  }

  return {
    // One rule with Serving now: every slot the indicator does not call offline is loaded.
    loaded: slots.filter((s) => indicatorCls(s) !== 'offline').length,
    total: slots.length,
    serving: serving.length,
    tps,
    inflight,
    freeLabel: isNum(freeGb) && isNum(poolGb) ? `${freeGb.toFixed(1)} GB free of ${Math.round(poolGb)}` : DASH,
    gpuLabel,
  }
}

export type ModeKind = 'chat' | 'image' | 'switching' | 'unknown'

export function modeLabel(
  arbiter: Pick<ArbiterState, 'mode' | 'switching'> | null | undefined,
): { kind: ModeKind; text: string } {
  if (!arbiter) return { kind: 'unknown', text: DASH }
  if (arbiter.switching) return { kind: 'switching', text: 'switching' }
  if (arbiter.mode === 'llm') return { kind: 'chat', text: 'chat mode' }
  if (arbiter.mode === 'img') return { kind: 'image', text: 'image mode' }
  return { kind: 'unknown', text: DASH }
}

export type NowActionId = 'hold-image' | 'back-to-chat' | 'load-slot' | 'agent-chat'
export interface NowAction {
  id: NowActionId
  label: string
  slot?: string
}

export function contextualActions(mode: ModeKind, slots: readonly Slot[]): NowAction[] {
  const agent: NowAction = { id: 'agent-chat', label: 'Agent Chat' }
  if (mode === 'image') return [{ id: 'back-to-chat', label: 'Back to chat' }, agent]
  if (mode !== 'chat') return [agent]
  const offline = slots.filter((s) => indicatorCls(s) === 'offline')
  const pick = offline.find((s) => railGroup(s.device) === 'vulkan') ?? offline[0]
  const acts: NowAction[] = [{ id: 'hold-image', label: 'Hold image mode' }]
  if (pick) acts.push({ id: 'load-slot', label: `Load ${pick.name}`, slot: pick.name })
  acts.push(agent)
  return acts
}
