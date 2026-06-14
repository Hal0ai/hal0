// hal0 v3 dashboard — container-runtime rollup hook.
//
// Derives a chrome/footer-friendly runtime summary from the existing
// `useSlots()` poll (no extra network traffic): every slot is a podman
// container, so "runtime up" simply means the slots query resolves and
// readiness counts come from per-slot container_status/state.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'
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

// ─── B12: honest system-health probe ─────────────────────────────────
// The runtime rollup above only knows whether /api/slots resolved — it
// can't see a runtime that is up-but-degraded (e.g. a failed dependency
// check). /api/health/system is the honest signal: it returns an overall
// `status` plus a per-check map so the chip can colour amber on degraded
// and tooltip the failing checks.

export interface HealthCheck {
  /** Per-check status. Anything other than 'ok' is treated as failing. */
  status: 'ok' | 'degraded' | 'error' | string
  /** Optional human detail for the tooltip. */
  detail?: string | null
  [k: string]: unknown
}

export interface HealthSystem {
  status: 'ok' | 'degraded'
  checks: Record<string, HealthCheck>
}

function normalizeHealth(raw: any): HealthSystem {
  const checks: Record<string, HealthCheck> =
    raw && typeof raw.checks === 'object' && raw.checks ? raw.checks : {}
  // Default to 'ok' when the endpoint is missing/empty (older backend) so
  // the chip doesn't false-alarm degraded on a partial deploy.
  const status = raw?.status === 'degraded' ? 'degraded' : 'ok'
  return { status, checks }
}

/** Names of checks not reporting 'ok' — drives the degraded tooltip. */
export function failingChecks(health: HealthSystem | undefined): string[] {
  if (!health) return []
  return Object.entries(health.checks)
    .filter(([, c]) => c && c.status !== 'ok')
    .map(([name, c]) => (c?.detail ? `${name}: ${c.detail}` : name))
}

const HEALTH_POLL_MS = 10_000

/**
 * Polls /api/health/system. Fail-soft: a 404 / network error from an older
 * backend resolves to an 'ok' status with no checks (the query's error is
 * still surfaced via `isError` for callers that care), so the runtime chip
 * never flips to a false "degraded".
 */
export function useHealthSystem() {
  return useQuery({
    queryKey: ['health', 'system'],
    queryFn: async () => normalizeHealth(await apiGet<any>(ENDPOINTS.healthSystem)),
    refetchInterval: HEALTH_POLL_MS,
    // Treat the endpoint as best-effort — don't spam retries on a backend
    // that doesn't ship it yet.
    retry: false,
  })
}
