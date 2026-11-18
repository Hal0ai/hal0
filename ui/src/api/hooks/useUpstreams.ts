// hal0 v3 dashboard — upstream-provider hooks (upstream controls).
//
// Backs Models → Providers (the list, the detail panel and the Add provider modal).
// List + create + patch + delete + test, the per-model listing, switch, refresh and probe,
// plus the static provider catalog for the Add provider tiles and the credentials write.
// Secrets never round-trip: the API returns env-var NAMES and boolean presence only.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPatch, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'
import type { ModelType } from '@/lib/providerModel'

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

/** One model of a probe answer (routes/providers.py probe_provider): no `enabled`,
 * `discovered` or `in_use_by`, since nothing is stored yet. */
export type ProbeModelRow = Pick<
  UpstreamModelRow,
  'id' | 'canonical_id' | 'type' | 'context' | 'price_in' | 'price_out' | 'price_unit' | 'recommended'
> & { type_source: string }

/** Answer of the probe. */
export interface ProbeResult {
  ok: boolean
  status: number | null
  latency_ms: number
  models: ProbeModelRow[]
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
    // A listing cached under this name belongs to an earlier provider of the same name.
    onSuccess: (_made, body) => {
      qc.removeQueries({ queryKey: ['upstream-models', body.name] })
      return qc.invalidateQueries({ queryKey: ['upstreams'] })
    },
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
    // The list refetch comes first: it unmounts the removed provider's section and panel. Dropping the
    // listing while they are still mounted would make them fetch it again and get a 404.
    onSuccess: async (_done, name) => {
      await qc.invalidateQueries({ queryKey: ['upstreams'] })
      qc.removeQueries({ queryKey: ['upstream-models', name] })
    },
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

/**
 * Store a provider's key. Deliberately not a react-query mutation: a mutation keeps its
 * variables (the key) in the mutation cache for minutes and hands them to the global
 * reauth handler. Here the body lives only in the `save` call; the hook keeps nothing but
 * the pending flag and the last error. Contract: `key` is the upstream's `auth_value_env`
 * name (server-enforced), `value` the secret.
 */
export function useProviderCredentialSet() {
  const qc = useQueryClient()
  const [isPending, setPending] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const alive = useRef(true)
  const latest = useRef(0)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const save = useCallback(
    async ({ name, key, value }: { name: string; key: string; value: string }): Promise<void> => {
      const mine = ++latest.current
      const current = () => alive.current && latest.current === mine
      setPending(true)
      setError(null)
      try {
        await apiPost(ENDPOINTS.providerCredentials(name), { key, value })
        void qc.invalidateQueries({ queryKey: ['upstreams'] })
      } catch (e) {
        if (current()) setError(e instanceof Error ? e : new Error(String(e)))
        throw e
      } finally {
        if (current()) setPending(false)
      }
    },
    [qc],
  )

  const clear = useCallback(() => {
    latest.current += 1
    setPending(false)
    setError(null)
  }, [])

  return { save, isPending, error, clear }
}

export function useUpstreamModels(name: string | null, opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ['upstream-models', name],
    queryFn: () => apiGet<UpstreamListing>(ENDPOINTS.upstreamModels(name as string)),
    enabled: name !== null && (opts.enabled ?? true),
  })
}

const MODEL_PATCH_KEY = ['upstream-model-patch']

/**
 * Switch a model on or off, or correct its type, context or price. The cached row is
 * patched at once; if the request fails only that row's patched fields go back to what they
 * were, so overlapping toggles on other rows of the same provider are left alone. The
 * listing is refetched only when no other patch for the provider is still in flight, so a
 * refetch never overwrites a row that is still waiting for its answer.
 */
export function useUpstreamModelPatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationKey: MODEL_PATCH_KEY,
    mutationFn: ({ name, modelId, patch }: { name: string; modelId: string; patch: ModelPatch }) =>
      apiPatch<UpstreamModelRow>(ENDPOINTS.upstreamModel(name, modelId), patch),
    onMutate: async ({ name, modelId, patch }) => {
      const key = ['upstream-models', name]
      await qc.cancelQueries({ queryKey: key })
      const current = qc.getQueryData<UpstreamListing>(key)
      const row = current?.models.find((m) => m.id === modelId)
      const before: Partial<UpstreamModelRow> = {}
      if (row) {
        for (const k of Object.keys(patch) as Array<keyof ModelPatch>) {
          ;(before as Record<string, unknown>)[k] = row[k]
        }
        qc.setQueryData<UpstreamListing>(key, {
          ...current!,
          models: current!.models.map((m) => (m.id === modelId ? { ...m, ...patch } : m)),
        })
      }
      return { before }
    },
    onError: (_err, { name, modelId }, ctx) => {
      if (!ctx || Object.keys(ctx.before).length === 0) return
      qc.setQueryData<UpstreamListing>(['upstream-models', name], (cur) =>
        cur ? { ...cur, models: cur.models.map((m) => (m.id === modelId ? { ...m, ...ctx.before } : m)) } : cur,
      )
    },
    onSettled: (_data, _err, variables) => {
      qc.invalidateQueries({ queryKey: ['upstreams'] })
      const othersPending = qc.isMutating({
        mutationKey: MODEL_PATCH_KEY,
        predicate: (m) =>
          m.state.variables !== variables && (m.state.variables as { name?: string } | undefined)?.name === variables.name,
      })
      if (!othersPending) qc.invalidateQueries({ queryKey: ['upstream-models', variables.name] })
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

/**
 * Check a url + key and list the models, saving nothing. This is deliberately not a
 * react-query mutation: a mutation keeps its variables (the key) in the mutation cache.
 * Here the request body lives only in the `probe` call; the hook keeps the last answer
 * (never the body) or the last error in its own state.
 *
 * Returns `{ probe(body): Promise<ProbeResult>, isPending, result, error, clear() }`.
 * `probe` rejects on a transport or HTTP failure (also stored in `error`); a provider that
 * answered `ok: false` resolves normally with that answer.
 */
export function useProviderProbe() {
  const [isPending, setPending] = useState(false)
  const [result, setResult] = useState<ProbeResult | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const alive = useRef(true)
  const latest = useRef(0)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const probe = useCallback(async (body: ProbeBody): Promise<ProbeResult> => {
    const mine = ++latest.current
    const current = () => alive.current && latest.current === mine
    setPending(true)
    setResult(null)
    setError(null)
    try {
      const r = await apiPost<ProbeResult>(ENDPOINTS.providerProbe, body)
      if (current()) setResult(r)
      return r
    } catch (e) {
      if (current()) setError(e instanceof Error ? e : new Error(String(e)))
      throw e
    } finally {
      if (current()) setPending(false)
    }
  }, [])

  const clear = useCallback(() => {
    latest.current += 1
    setPending(false)
    setResult(null)
    setError(null)
  }, [])

  return { probe, isPending, result, error, clear }
}
