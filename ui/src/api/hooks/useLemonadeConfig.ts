// hal0 v3 dashboard — Lemonade admin config read / write hooks.
//
// Backs Settings → Lemonade admin (issue #461 / parent #433). The
// backend in `hal0.api.routes.lemonade_admin` already serves:
//
//   GET  /api/lemonade/config  — the verbatim `lemond /internal/config`
//                                snapshot plus a `_hal0` envelope with
//                                the immediate-vs-deferred key partition
//                                and the locked `extra_models_dir`.
//   POST /api/lemonade/config  — body `{key: value, ...}` (only changed
//                                keys). Returns `{applied, effects}` where
//                                `effects` splits the touched keys into
//                                immediate vs deferred-until-next-load.
//
// Validation guardrails are enforced server-side (--threads >= 2,
// FLM trio, locked store path); the hook surfaces the typed
// `lemonade.config_invalid` envelope so the form can echo per-field
// reasons via `Hal0Error.details`.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

/** Immediate-vs-deferred key partition, mirrored from the backend's
 *  IMMEDIATE_KEYS / DEFERRED_KEYS taxonomy. */
export interface LemonadeEffects {
  immediate: string[]
  deferred: string[]
}

/** The `_hal0` envelope the backend appends to the lemond snapshot. */
export interface LemonadeConfigEnvelope {
  effects: LemonadeEffects
  locked: {
    extra_models_dir: string
  }
}

/** Full GET response — the lemond config snapshot (arbitrary keys) plus
 *  the hal0-added `_hal0` envelope. The snapshot keys are loosely typed
 *  because lemond owns that schema; the form reads them by name. */
export interface LemonadeConfig {
  _hal0: LemonadeConfigEnvelope
  [key: string]: unknown
}

/** POST response — what lemond echoed back + the effect split for the
 *  exact keys this request touched. */
export interface LemonadeConfigPatchResult {
  applied: Record<string, unknown>
  effects: LemonadeEffects
}

const LEMONADE_CONFIG_KEY = ['lemonade', 'config'] as const

/**
 * Reads the live Lemonade config via `GET /api/lemonade/config`.
 *
 * No background polling — the config changes only when the operator
 * saves it, and they're in the seat when they care. The success toast
 * after a POST invalidates this query so the form refills from the
 * authoritative snapshot.
 */
export function useLemonadeConfig() {
  return useQuery({
    queryKey: LEMONADE_CONFIG_KEY,
    queryFn: () => apiGet<LemonadeConfig>(ENDPOINTS.lemonadeConfig),
    staleTime: 30_000,
  })
}

/**
 * Writes a partial config patch via `POST /api/lemonade/config`.
 *
 * Body is `{key: value, ...}` for only the keys being changed — the
 * backend rejects unknown keys and the locked-invariant violations
 * (--threads, FLM trio, store path) with a `lemonade.config_invalid`
 * envelope whose `details` map the consuming form surfaces per-field.
 * On success we invalidate the config query so the readouts refill.
 */
export function useLemonadeConfigSet() {
  const qc = useQueryClient()
  return useMutation<
    LemonadeConfigPatchResult,
    Hal0Error,
    Record<string, unknown>
  >({
    mutationFn: (patch) =>
      apiPost<LemonadeConfigPatchResult>(ENDPOINTS.lemonadeConfig, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: LEMONADE_CONFIG_KEY })
    },
  })
}
