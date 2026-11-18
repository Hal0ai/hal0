// hal0 v3 dashboard — upstream-provider hooks (upstream controls).
//
// Backs Connections → Upstream providers (and the Slots → Endpoints tab).
// List + create + patch + delete + test, plus the static provider catalog
// for the "Add upstream" form and the credentials write. Secrets never
// round-trip: the API returns env-var NAMES and boolean presence only.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPatch, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'
import type { ModelType } from '@/dash/models/providers-pure'

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
  /** Owner of an agent-managed entry (e.g. `agent:hermes`); null when operator-authored. */
  managed_by?: string | null
  /** A pinned entry's ids never fall through to another lane. */
  pinned?: boolean
  /** Model ids a pinned entry owns. */
  owned_models?: string[]
  hint?: string
  /** Preset this provider was made from; null for a custom one. */
  catalog_id: string | null
  /** True once the provider has per-model rows; false while it routes everything. */
  curated: boolean
  budget_monthly_usd: number | null
  /** Models switched on / models the provider has rows for. */
  enabled_count: number
  models_total: number
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
  category: 'cloud' | 'local' | 'peer' | 'custom'
  notes: string
  tools: boolean
  quirks: string[]
  rate_limit_headers: Record<string, string>
  /** The preset's own model rows (what a fresh provider of this kind offers). */
  models: Array<{
    id: string
    type: ModelType
    context: number | null
    price_in: number | null
    price_out: number | null
    price_unit: string
    recommended: boolean
  }>
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
  /** Per-model rows; present (even empty) makes the provider per-model. */
  models?: Array<
    Pick<UpstreamModelRow, 'id' | 'enabled' | 'type' | 'context' | 'price_in' | 'price_out' | 'price_unit'>
  >
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

/** One row of `GET /api/upstreams/{name}/models` (routes/providers.py `_row`). */
export interface UpstreamModelRow {
  id: string
  /** `<provider>/<id>`; null on a probe row, since no provider exists yet. */
  canonical_id: string | null
  enabled: boolean
  type: ModelType
  context: number | null
  price_in: number | null
  price_out: number | null
  price_unit: string
  /** False when the provider no longer lists the model. */
  discovered: boolean
  recommended: boolean
  in_use_by: string[]
}

export interface UpstreamListing {
  name: string
  curated: boolean
  protected: boolean
  fetched_at: number | null
  stale: boolean
  models: UpstreamModelRow[]
  /** Shown verbatim when present. */
  warning?: string | null
}

/** Body of `POST /api/providers/probe`. `key` is sent once and never kept. */
export type ProbeBody = {
  catalog_id?: string
  url?: string
  auth_style?: string
  auth_header?: string
  key?: string
}

/** Answer of the probe. Its model rows carry the `_row` fields except `enabled`,
 * `discovered` and `in_use_by`, plus `type_source`; callers must not rely on those. */
export interface ProbeResult {
  ok: boolean
  status: number | null
  latency_ms: number
  models: UpstreamModelRow[]
  rate_limit: Record<string, number | null>
  error: string | null
}

export type ModelPatch = Partial<
  Pick<UpstreamModelRow, 'enabled' | 'type' | 'context' | 'price_in' | 'price_out' | 'price_unit'>
>

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

/** "managed by <owner>" for an agent-managed entry, else null. Such a row is
 * read-only here: the API refuses PATCH, DELETE and credential writes. */
export function managedByLabel(u: UpstreamEntry): string | null {
  return u.managed_by ? `managed by ${u.managed_by}` : null
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

export function useUpstreamModels(name: string | null, opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ['upstream-models', name],
    queryFn: () => apiGet<UpstreamListing>(ENDPOINTS.upstreamModels(name as string)),
    enabled: name !== null && (opts.enabled ?? true),
  })
}

/**
 * Switch a model on or off, or correct its type, context or price. The cached
 * listing is patched at once and put back if the request fails, so a switch
 * never shows a state the server did not take.
 */
export function useUpstreamModelPatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ name, modelId, patch }: { name: string; modelId: string; patch: ModelPatch }) =>
      apiPatch<UpstreamModelRow>(ENDPOINTS.upstreamModel(name, modelId), patch),
    onMutate: async ({ name, modelId, patch }) => {
      const key = ['upstream-models', name]
      await qc.cancelQueries({ queryKey: key })
      const previous = qc.getQueryData<UpstreamListing>(key)
      if (previous) {
        qc.setQueryData<UpstreamListing>(key, {
          ...previous,
          models: previous.models.map((m) => (m.id === modelId ? { ...m, ...patch } : m)),
        })
      }
      return { previous }
    },
    onError: (_err, { name }, ctx) => {
      if (ctx?.previous) qc.setQueryData(['upstream-models', name], ctx.previous)
    },
    onSettled: (_data, _err, { name }) => {
      qc.invalidateQueries({ queryKey: ['upstreams'] })
      qc.invalidateQueries({ queryKey: ['upstream-models', name] })
    },
  })
}

/** Refetch what the provider offers; new models arrive switched off. */
export function useUpstreamRefresh() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => apiPost<UpstreamListing>(ENDPOINTS.upstreamRefresh(name)),
    onSuccess: (listing, name) => {
      qc.setQueryData(['upstream-models', name], listing)
      qc.invalidateQueries({ queryKey: ['upstreams'] })
    },
  })
}

/** Check a url + key and list the models, saving nothing. Never cached: the key
 * is in the variables, so call `reset()` once the answer is read. */
export function useProviderProbe() {
  return useMutation({
    gcTime: 0,
    mutationFn: (body: ProbeBody) => apiPost<ProbeResult>(ENDPOINTS.providerProbe, body),
  })
}
