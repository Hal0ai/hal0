// hal0 v3 dashboard — container-runtime rollup hook.
//
// Derives a chrome/footer-friendly runtime summary from the existing
// `useSlots()` poll (no extra network traffic): every slot is a podman
// container, so "runtime up" simply means the slots query resolves and
// readiness counts come from per-slot container_status/state.

import { useSlots, type Slot } from './useSlots'

/** A slot counts as ready when its container is running or its state
 *  string says it holds a servable model. */
const READY_STATES = new Set(['ready', 'serving', 'idle'])

function isSlotReady(s: Slot): boolean {
  if (s.container_status === 'running') return true
  return READY_STATES.has(String(s.state ?? '').toLowerCase())
}

export interface RuntimeRollup {
  /** 'up' when the slots query resolves; 'down' on error; 'connecting'
   *  before the first response. */
  status: 'up' | 'down' | 'connecting'
  /** Slots with a running container (or ready/serving/idle state). */
  ready: number
  /** Enabled slots. */
  total: number
  /** Alias of `ready` — slots currently holding a servable model. */
  loaded: number
}

/**
 * Roll-up suitable for chrome / footer chips. Shares the `['slots']`
 * query cache with `useSlots()`, so consumers add no polling cost.
 */
export function useRuntimeRollup(): RuntimeRollup {
  const slots = useSlots()
  const list = slots.data ?? []
  const enabled = list.filter((s) => s.enabled !== false)
  const ready = enabled.filter(isSlotReady).length
  return {
    status: slots.isSuccess ? 'up' : slots.isError ? 'down' : 'connecting',
    ready,
    total: enabled.length,
    loaded: ready,
  }
}
