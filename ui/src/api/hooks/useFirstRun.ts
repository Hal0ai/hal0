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

// FirstRun v2 (design D3): one orchestrated install. The backend resolves
// the per-slot models from the bundle manifest and derives device+profile
// from the hardware probe, so the UI no longer maps tiers → chat models
// (the old BUNDLE_CHAT_MODELS table is gone — it only ever covered chat).

export interface InstallApplySlot {
  slot: string
  model_id: string
  created: boolean
  device?: string
  profile?: string
  pull_job_id?: string
  skipped?: string
  error?: string
}

export interface InstallApplyResult {
  tier: string
  model_ids: string[]
  slots: InstallApplySlot[]
  next: string
}

export interface InstallApplyArgs {
  tier: string
  storageDir: string
  npuOptIn?: boolean
  overrides?: Record<string, unknown>
}

export function useInstallApply() {
  const qc = useQueryClient()
  return useMutation({
    // POST /api/install/apply — pulls every bundle slot, creates the slots
    // OFFLINE, and returns model_ids[] so FirstRunProgress can reattach the
    // per-model SSE pull streams via usePullJob.reattach().
    mutationFn: ({ tier, storageDir, npuOptIn, overrides }: InstallApplyArgs) =>
      apiPost<InstallApplyResult>(ENDPOINTS.installApply, {
        tier,
        storage_dir: storageDir,
        npu_opt_in: !!npuOptIn,
        overrides: overrides ?? {},
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['firstrun'] })
      qc.invalidateQueries({ queryKey: ['models'] })
      qc.invalidateQueries({ queryKey: ['slots'] })
    },
  })
}

export interface InstallService {
  unit: string
  label: string
  active: boolean
  repairable: boolean
}

export function useInstallServices() {
  return useQuery({
    queryKey: ['firstrun', 'services'],
    queryFn: () => apiGet<{ services: InstallService[] }>(ENDPOINTS.installServices),
    refetchInterval: 5000,
  })
}

export function useServiceRepair() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (unit: string) => apiPost(ENDPOINTS.installServiceRepair(unit), {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['firstrun', 'services'] }),
  })
}

export function useFirstRunComplete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost(ENDPOINTS.installComplete),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['firstrun'] }),
  })
}
