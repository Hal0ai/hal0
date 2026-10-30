// hal0 v3 dashboard — GTT feasibility probe hook (drawer overhaul PR-3,
// Task 7 / host-truth GTT feasibility signal, 993ea3b6).
//
// POST /api/models/feasibility body {models: [{model_id, ctx?, profile?, slot?}]} →
// {results: [...]}. Batched so the model drawer can probe every slot
// candidate in one round trip; a non-list `models` body 400s server-side.
// The window tested is `ctx` when given (it wins), else the window the named
// `profile` launches the CANDIDATE model with (the server's launch resolver:
// the profile's ctx_size, else the candidate's native window capped at 32768,
// else 8192). Each result echoes the `ctx` tested and its `ctx_source`.
// This is a warn-never-block advisory signal — it never gates a save, it
// only feeds `feasibilityHint()` (dash/feasibility-copy.ts) for the row's
// hint text. Pure probe, no cache to invalidate.

import { useMutation } from '@tanstack/react-query'
import { apiPost, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface FeasibilityQuery {
  model_id: string
  /** Explicit window; wins over `profile` when both are sent. */
  ctx?: number
  /** Profile name the server resolves through the launch resolver. */
  profile?: string
  /** Slot name whose profile plus sparse tune override the server resolves. */
  slot?: string
}

export interface FeasibilityRequest {
  models: FeasibilityQuery[]
}

export type FeasibilityVerdict = 'fits' | 'tight' | 'exceeds' | 'exceeds_total' | 'unknown'

export interface FeasibilityResult {
  model_id: string
  verdict: FeasibilityVerdict
  needed_mb: number
  gtt_free_mb: number | null
  gtt_total_mb: number | null
  /** The window the verdict was computed for; null when unknown. */
  ctx?: number | null
  /** Where `ctx` came from; null when unknown. */
  ctx_source?: 'request' | 'slot' | 'profile' | 'native' | 'fallback' | null
}

export interface FeasibilityResponse {
  results: FeasibilityResult[]
}

export function useModelsFeasibility() {
  return useMutation<FeasibilityResponse, Hal0Error, FeasibilityRequest>({
    mutationFn: (body) =>
      apiPost<FeasibilityResponse>(ENDPOINTS.modelsFeasibility, body as unknown as Record<string, unknown>),
  })
}
