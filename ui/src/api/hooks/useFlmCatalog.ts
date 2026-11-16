// hal0 v3 dashboard — GET /api/slots/flm/models (FastFlowLM catalogue).
//
// A degraded payload ({} from a proxy, a bare array) must never leave a non-array `models`: a
// `.filter` on an object once crashed the whole slots view (slot-modals.jsx does the same guard).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, type Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface FlmModel {
  model: string
  installed: boolean
  capabilities?: string[]
  family?: string | null
  /** FLM's reported download size; 0 or absent when unknown. */
  size_bytes?: number
  /** FLM roles configured or serving this tag; removing it is refused while non-empty. */
  in_use_by?: FlmModelUse[]
}
export interface FlmModelUse {
  role: string
  slot: string
}
export interface FlmCatalog {
  models: FlmModel[]
  probe_ok: boolean
}

export function normalizeFlmCatalog(d: unknown): FlmCatalog {
  const o = (d ?? {}) as { models?: unknown; probe_ok?: unknown }
  const models = Array.isArray(o.models) ? o.models : Array.isArray(d) ? (d as unknown[]) : []
  return { models: models as FlmModel[], probe_ok: o.probe_ok === true }
}

export const FLM_CATALOG_QUERY_KEY = ['slots', 'flm', 'models'] as const

export function useFlmCatalog() {
  return useQuery<FlmCatalog>({
    queryKey: FLM_CATALOG_QUERY_KEY,
    queryFn: async () => normalizeFlmCatalog(await apiGet<unknown>(ENDPOINTS.flmModels)),
    staleTime: 30_000,
  })
}

export interface FlmRemoveResult {
  tag: string
  removed: boolean
  path: string | null
  bytes_freed: number
  registry_removed: boolean
  /** flm's own warnings, e.g. installed tags built on the removed one. */
  warnings: string[]
}

/** DELETE /api/slots/flm/models/{tag}: delete a downloaded FLM model's files. Refreshes the
 *  catalogue (its `installed` flag) and the model list (the registry row goes too). */
export function useRemoveFlmModel() {
  const qc = useQueryClient()
  return useMutation<FlmRemoveResult, Hal0Error, string>({
    mutationFn: (tag: string) => apiDelete<FlmRemoveResult>(ENDPOINTS.flmModel(tag)),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: FLM_CATALOG_QUERY_KEY })
      qc.invalidateQueries({ queryKey: ['models'] })
    },
  })
}
