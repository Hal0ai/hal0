// hal0 v3 dashboard — GTT feasibility probe hook (drawer overhaul PR-3,
// Task 7 / host-truth GTT feasibility signal, 993ea3b6).
//
// POST /api/models/feasibility body {models: [{model_id, ctx?}]} →
// {results: [...]}. Batched so the model drawer can probe every slot
// candidate in one round trip; a non-list `models` body 400s server-side.
// This is a warn-never-block advisory signal — it never gates a save, it
// only feeds `feasibilityHint()` (dash/feasibility-copy.ts) for the row's
// hint text. Pure probe, no cache to invalidate.

import { useMutation } from '@tanstack/react-query'
import { apiPost, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface FeasibilityQuery {
  model_id: string
  ctx?: number
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
