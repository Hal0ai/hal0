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
  // #1369: the denominator is the CONFIGURED slots (a model bound), which is
  // what "runtime" means here — a model-less slot has nothing to be ready.
  // The typed field is `modelDefault` — `inferSlotShape` camelCases the raw
  // `model_default` — so the wire name here never type-checked.
  const configured = list.filter((s) => !!(s.model || s.modelDefault))
  const ready = configured.filter(isSlotReady).length
  return {
    status: slots.isSuccess ? 'up' : slots.isError ? 'down' : 'connecting',
    ready,
    total: configured.length,
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
  /** Per-check outcome. The backend (routes/health.py health_system) emits a
   *  BOOLEAN `ok` for every check — disk_state, disk_config, slot_manager,
   *  event_bus, mcp_mount — and never a per-check `status` string (#1461). */
  ok: boolean
  /** Human detail. Present on SOME failure paths only: mcp_mount always sets
   *  it, slot_manager sets it when `sm.list()` raised or was never wired. */
  detail?: string | null
  /** slot_manager's failing slot names — the live failure path carries its
   *  reason here rather than in `detail`, so the tooltip must read both. */
  errored?: string[]
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

/** Every reason a check reports for its own failure, in tooltip order. */
function checkReasons(c: HealthCheck): string[] {
  const reasons: string[] = []
  if (typeof c.detail === 'string' && c.detail.trim()) reasons.push(c.detail.trim())
  if (Array.isArray(c.errored) && c.errored.length > 0) {
    reasons.push(`errored: ${c.errored.join(', ')}`)
  }
  return reasons
}

/**
 * Names of the checks that are actually FAILING — drives the degraded tooltip.
 *
 * Reads the real payload shape: a check fails when its boolean `ok` is
 * explicitly false. The previous `c.status !== 'ok'` filter matched
 * `undefined !== 'ok'` on every check the backend emits, so a single broken
 * subsystem listed all five as failing (#1461). Each failing check carries its
 * own reason — `detail` where the backend sets one, plus slot_manager's
 * `errored` slot names, which is where the live failure actually reports.
 */
export function failingChecks(health: HealthSystem | undefined): string[] {
  if (!health) return []
  return Object.entries(health.checks)
    .filter(([, c]) => c && c.ok === false)
    .map(([name, c]) => {
      const reasons = checkReasons(c)
      return reasons.length > 0 ? `${name}: ${reasons.join(' · ')}` : name
    })
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
