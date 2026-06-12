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

// Static mapping: wizard tier ids → curated model ids accepted by
// POST /api/install/pick-default.
//
// Source: installer/manifests/omni/*.json primary.model_name, cross-verified
// against GET /api/install/curated-models (which exposes the curated catalogue
// filtered to recommended_slot in ("chat","primary")).
//
// Max and LMX manifests reference Qwen3.6-35B-A3B-MTP-GGUF which is NOT
// currently in the curated catalogue — both degrade to the 27B model.
// Log a backend follow-up to add the 35B entry so Max/LMX can use their
// intended primary.
const BUNDLE_CHAT_MODELS: Record<string, string> = {
  lite:    'qwen3.5-0.8b',  // manifest: qwen3.5-0.8b ✓
  default: 'qwen3.5-9b',    // manifest: qwen3.5-9b ✓
  pro:     'qwen3.6-27b',   // manifest: Qwen3.6-27B-MTP-GGUF → curated id qwen3.6-27b ✓
  max:     'qwen3.6-27b',   // manifest 35B not curated yet — degrade to 27b
  lmx:     'qwen3.6-27b',   // LMX 35B not curated yet — degrade to 27b
}

export interface PickDefaultResponse {
  model_id: string
  slot: string
  pull_job_id: string
  next: string
}

export function useFirstRunInstall() {
  const qc = useQueryClient()
  return useMutation({
    // Call POST /api/install/pick-default with the REAL curated model id
    // for the bundle's primary chat slot — NOT the bundle string itself.
    // Bundle ids like "pro" are not valid curated model ids and 404 on the
    // backend (CuratedModelNotFound).
    //
    // The progress pane (FirstRunProgress) receives { model_ids: [id] } so
    // FrDownloadRow can reattach to the in-flight SSE pull stream immediately.
    mutationFn: async ({ bundle, withNpu: _withNpu }: { bundle: string; withNpu?: boolean }) => {
      const modelId = BUNDLE_CHAT_MODELS[bundle]
      if (!modelId) {
        // Surface unknown bundle clearly — don't silently 404 and show fake
        // "Install started" when nothing was actually triggered.
        throw new Error(`No curated model mapping for bundle "${bundle}" — check BUNDLE_CHAT_MODELS`)
      }
      const resp = await apiPost<PickDefaultResponse>(ENDPOINTS.installPickDefault, {
        model_id: modelId,
        slot: 'chat',
      })
      // Normalise to the model_ids array shape the confirm → progress handoff
      // reads (res?.model_ids) so FrDownloadRow mounts with the real id.
      return { model_ids: [resp.model_id] }
    },
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
