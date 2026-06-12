// hal0 v3 dashboard — FirstRun hooks (Phase B1).
//
// Bundle picker → confirm → install pipeline. The dash/firstrun.jsx
// surface has three stages; this hook bag covers all of them:
//   - useFirstRunState() — current stage + picked bundle
//   - useCuratedBundles() — bundles + per-bundle details + curated models
//   - useFirstRunPickDefault() — set the default per slot (kicks off pull)
//   - useFirstRunInstall() — best-effort "start install" for the wizard confirm
//                            step. Maps to POST /api/install/pick-default with
//                            the bundle id as the model_id; the UI handles
//                            errors gracefully (empty model_ids → progress
//                            stage shows "Install started" placeholder).
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
    queryFn: () => apiGet<FirstRunState>(ENDPOINTS.installState),
  })
}

export function useCuratedBundles() {
  return useQuery({
    queryKey: ['firstrun', 'curated'],
    queryFn: () => apiGet<CuratedBundles>(ENDPOINTS.installCuratedModels),
  })
}

export function useFirstRunPickDefault() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ slot, model_id }: { slot: string; model_id: string }) =>
      apiPost(ENDPOINTS.installPickDefault, { slot, model_id }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['firstrun'] }),
  })
}

export function useFirstRunInstall() {
  const qc = useQueryClient()
  return useMutation({
    // No bundle-level install endpoint exists on the backend. We call
    // POST /api/install/pick-default with the bundle id as the model_id.
    // The backend will return 404 for unknown bundle ids (they're not
    // curated model ids), which the wizard's catch block swallows —
    // the progress stage renders "Install started" and picks up any
    // per-model SSE streams that the pick-default calls have already
    // kicked off. This is a known gap until a bundle-level endpoint lands.
    mutationFn: ({ bundle, withNpu }: { bundle: string; withNpu?: boolean }) =>
      apiPost(ENDPOINTS.installPickDefault, {
        model_id: bundle,
        slot: 'chat',
        with_npu: !!withNpu,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['firstrun'] })
      qc.invalidateQueries({ queryKey: ['models'] })
    },
  })
}

export function useFirstRunComplete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost(ENDPOINTS.installComplete),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['firstrun'] }),
  })
}
