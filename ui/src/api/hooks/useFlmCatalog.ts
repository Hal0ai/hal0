// hal0 v3 dashboard — GET /api/slots/flm/models (FastFlowLM catalogue).
//
// A degraded payload ({} from a proxy, a bare array) must never leave a non-array `models`: a
// `.filter` on an object once crashed the whole slots view (slot-modals.jsx does the same guard).
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface FlmModel {
  model: string
  installed: boolean
  capabilities?: string[]
  family?: string | null
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

export function useFlmCatalog() {
  return useQuery<FlmCatalog>({
    queryKey: ['slots', 'flm', 'models'],
    queryFn: async () => normalizeFlmCatalog(await apiGet<unknown>(ENDPOINTS.flmModels)),
    staleTime: 30_000,
  })
}
