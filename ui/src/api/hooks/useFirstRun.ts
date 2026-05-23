// hal0 v3 dashboard — FirstRun hooks (Phase B1).
//
// Bundle picker → confirm → install pipeline. The dash/firstrun.jsx
// surface has three stages; this hook bag covers all of them:
//   - useFirstRunState() — current stage + picked bundle
//   - useCuratedBundles() — bundles + per-bundle details + curated models
//   - useFirstRunPickDefault() — set the default per slot
//   - useFirstRunInstall() — kick off the install (downloads + slot
//                            seeding); returns a job id polled via
//                            usePullJob per model
//   - useFirstRunComplete() — flip the "completed" flag

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface FirstRunState {
  stage: 'pick' | 'confirm' | 'progress' | 'done' | string
  bundle: string | null
  completed?: boolean
}

export interface CuratedBundle {
  id: string
  name: string
  ram: number
  sizeGB: number
  desc: string
  recommended?: boolean
  includes: Array<{ label: string; active: boolean }>
}

export interface CuratedBundles {
  bundles: CuratedBundle[]
  details?: Record<string, unknown>
}

export function useFirstRunState() {
  return useQuery({
    queryKey: ['firstrun', 'state'],
    queryFn: () => apiGet<FirstRunState>(ENDPOINTS.firstrunState),
  })
}

export function useCuratedBundles() {
  return useQuery({
    queryKey: ['firstrun', 'curated'],
    queryFn: () => apiGet<CuratedBundles>(ENDPOINTS.firstrunCuratedModels),
  })
}

export function useFirstRunPickDefault() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ slot, model_id }: { slot: string; model_id: string }) =>
      apiPost(ENDPOINTS.firstrunPickDefault, { slot, model_id }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['firstrun'] }),
  })
}

export function useFirstRunInstall() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ bundle, withNpu }: { bundle: string; withNpu?: boolean }) =>
      apiPost(ENDPOINTS.firstrunInstall, { bundle, with_npu: !!withNpu }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['firstrun'] })
      qc.invalidateQueries({ queryKey: ['models'] })
    },
  })
}

export function useFirstRunComplete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost(ENDPOINTS.firstrunComplete),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['firstrun'] }),
  })
}
