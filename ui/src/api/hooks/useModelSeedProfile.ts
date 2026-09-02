// hal0 v3 dashboard — apply-profile-seed hook (drawer overhaul PR-3, Task 7).
//
// POST /api/models/{id}/seed-profile body {profile: name} → the updated
// model dict. Distinct from the profile system's first-add seeding — this
// lets the model drawer re-apply a named profile's seed onto an
// already-registered model row. Idiom mirrors useModelUpdate
// (useModels.ts:188): PUT-shaped mutation, invalidate models + the
// slot-side caches a model-defaults change feeds.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiPost, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'
import type { Model } from './useModels'

export interface ModelSeedProfileRequest {
  id: string
  profile: string
}

export function useModelSeedProfile() {
  const qc = useQueryClient()
  return useMutation<Model, Hal0Error, ModelSeedProfileRequest>({
    mutationFn: ({ id, profile }) =>
      apiPost<Model>(ENDPOINTS.modelSeedProfile(id), { profile }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['models'] })
      // Seeding a profile rewrites the model's defaults, which feed the
      // slot argv assembly (merge_flags) — same downstream invalidation
      // as useModelUpdate.
      qc.invalidateQueries({ queryKey: ['slots'] })
      qc.invalidateQueries({ queryKey: ['slot-resolved'] })
    },
  })
}
