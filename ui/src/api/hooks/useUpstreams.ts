// hal0 v3 dashboard — upstream-provider hooks (upstream controls).
//
// Backs Connections → Upstream providers (and the Slots → Endpoints tab).
// List + create + patch + delete + test, plus the static provider catalog
// for the "Add upstream" form and the credentials write. Secrets never
// round-trip: the API returns env-var NAMES and boolean presence only.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPatch, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface UpstreamModelFilters {
  models: string[]
  include: string[]
  exclude: string[]
}

export interface UpstreamEntry {
  name: string
  kind: 'slot' | 'remote'
  url: string
  auth_style: string
  auth_header: string
  /** Env-var NAME the key lives under — never the value. */
  auth_value_env: string
  auth_configured: boolean
  /** True when the env-var actually holds a value (drives the auth badge). */
  auth_key_present: boolean
  timeout_seconds: number
  slot_name: string | null
  warmup_strategy: string
  advertise_models: boolean
  enabled: boolean
  model_filters: UpstreamModelFilters | null
  models: string[]
  hint?: string
}

export interface CatalogEntry {
  id: string
  name: string
  base_url: string
  auth: string
  auth_header_name: string
  models_path: string
  default_models: string[]
  default_model: string
  capabilities: string[]
  docs_url: string
  category: 'cloud' | 'local' | 'custom'
  notes: string
}

// Type aliases (not interfaces) so they satisfy the api client's
// Record<string, unknown> body parameter via implicit index signatures.
export type UpstreamCreateBody = {
  name: string
  catalog_id?: string
  url?: string
  auth_style?: string
  auth_header?: string
  auth_value_env?: string
  timeout_seconds?: number
  advertise_models?: boolean
  enabled?: boolean
  model_filters?: UpstreamModelFilters
}

export type UpstreamPatchBody = {
  advertise_models?: boolean
  enabled?: boolean
  /** All-empty object clears the filters; omit to leave unchanged. */
  model_filters?: UpstreamModelFilters
  url?: string
  auth_style?: string
  auth_header?: string
  auth_value_env?: string
  timeout_seconds?: number
  warmup_strategy?: string
}

export interface UpstreamTestResult {
  ok: boolean
  status?: number | string
  latency_ms?: number
  models_count?: number
  error?: string
}

/** A row the operator manages here — genuine remotes only. Slot-backed
 * upstreams (kind=slot, container remotes with slot_name, the composite
 * `hal0` aggregate) are owned by the slot lifecycle and live on Slots. */
export function isManagedRemote(u: UpstreamEntry): boolean {
  return u.kind === 'remote' && !u.slot_name
}

export function useUpstreams() {
  return useQuery({
    queryKey: ['upstreams'],
    queryFn: () => apiGet<UpstreamEntry[]>(ENDPOINTS.upstreams),
  })
}

export function useProvidersCatalog() {
  return useQuery({
    queryKey: ['providers', 'catalog'],
    queryFn: () => apiGet<Record<string, CatalogEntry>>(ENDPOINTS.providersCatalog),
    staleTime: Infinity, // static, ships with the build
  })
}

export function useUpstreamCreate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: UpstreamCreateBody) =>
      apiPost<UpstreamEntry>(ENDPOINTS.upstreams, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['upstreams'] }),
  })
}

export function useUpstreamUpdate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ name, patch }: { name: string; patch: UpstreamPatchBody }) =>
      apiPatch<UpstreamEntry>(ENDPOINTS.upstream(name), patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['upstreams'] }),
  })
}

export function useUpstreamDelete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => apiDelete(ENDPOINTS.upstream(name)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['upstreams'] }),
  })
}

export function useUpstreamTest() {
  // No invalidation — a probe doesn't change config; callers keep the
  // result in local state next to the Test button.
  return useMutation({
    mutationFn: (name: string) =>
      apiPost<UpstreamTestResult>(ENDPOINTS.upstreamTest(name)),
  })
}

export function useProviderCredentialSet() {
  const qc = useQueryClient()
  return useMutation({
    // Contract: {key: <ENV_VAR_NAME>, value: <secret>} — key must match
    // the upstream's declared auth_value_env (server-enforced binding).
    mutationFn: ({ name, key, value }: { name: string; key: string; value: string }) =>
      apiPost(ENDPOINTS.providerCredentials(name), { key, value }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['upstreams'] }),
  })
}
