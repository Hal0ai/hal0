// hal0 v3 dashboard — Connections hooks (issue #549).
//
// Thin react-query wrappers over the providers.py routes that back the
// dashboard's "Connections" surface:
//
//   GET  /api/providers            — remote (kind != "slot") upstreams
//   GET  /api/providers/catalog    — static integration catalog (templates)
//   GET  /api/upstreams            — all routing targets (slot + remote)
//   POST /api/upstreams/{name}/test — probe reachability + auth
//
// The shape is documented in src/hal0/api/routes/providers.py
// (_serialize_upstream). Every field is a string|bool|number|array —
// no nested envelopes, so the hook can pass the payload through after
// a light validation pass.

import { useMutation, useQuery, type UseQueryResult } from '@tanstack/react-query'
import { api, apiGet, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'

export type UpstreamKind = 'slot' | 'remote' | string

export interface Upstream {
  /** Registry key — the name callers use to address this upstream. */
  name: string
  /** 'slot' for lifecycle-managed slot fronts, 'remote' for third-party APIs. */
  kind: UpstreamKind
  /** Base URL of the upstream (or composite endpoint for slot kind). */
  url: string
  /** Auth scheme declared by the upstream ('bearer', 'x-api-key', …). */
  auth_style: string
  /** Env-var name the upstream reads its credential from. Never the value. */
  auth_value_env: string
  /** Convenience flag — true when auth_value_env is set (and presumably exported). */
  auth_configured: boolean
  /** Probe/connect timeout in seconds. */
  timeout_seconds: number
  /** For kind=='slot' upstreams: the slot this upstream fronts. */
  slot_name?: string | null
  /** Warmup hint — 'eager' | 'lazy' | …; opaque to the UI today. */
  warmup_strategy?: string
  /** Models the upstream has declared (post-resolve). */
  advertise_models?: string[]
  /** Cached model list, populated when the upstream has been probed. */
  models?: string[]
}

export interface UpstreamTestResult {
  ok: boolean
  /** HTTP status returned by the probe, when one was reached. */
  status?: number
  /** Round-trip latency of the probe in milliseconds. */
  latency_ms?: number
  /** Number of models the probe saw on /v1/models. */
  models_count?: number
  /** Human-readable error string on failure. */
  error?: string
}

const POLL_UPSTREAMS_MS = 15_000
// /api/providers is just an alias of /api/upstreams filtered server-side,
// so a single 15s poll backs both views. Sharing the cache keeps the
// sidebar/runtime widgets from issuing a second request per tick.

export function useProviders(): UseQueryResult<Upstream[]> {
  return useQuery({
    queryKey: ['providers'],
    queryFn: async () => {
      const body = await apiGet<unknown>(ENDPOINTS.providers)
      return normalizeUpstreamList(body)
    },
    refetchInterval: POLL_UPSTREAMS_MS,
  })
}

export function useUpstreams(): UseQueryResult<Upstream[]> {
  return useQuery({
    queryKey: ['upstreams'],
    queryFn: async () => {
      const body = await apiGet<unknown>(ENDPOINTS.upstreams)
      return normalizeUpstreamList(body)
    },
    refetchInterval: POLL_UPSTREAMS_MS,
  })
}

export function useUpstream(name: string | null | undefined): UseQueryResult<Upstream> {
  return useQuery({
    queryKey: ['upstreams', name],
    queryFn: () => apiGet<Upstream>(ENDPOINTS.upstream(name as string)),
    enabled: !!name,
    refetchInterval: POLL_UPSTREAMS_MS,
  })
}

/**
 * POST /api/upstreams/{name}/test — probe reachability + auth.
 * Returns the structured result envelope (`{ok, status?, latency_ms,
 * models_count?, error?}`) on 2xx; throws `Hal0Error` on 404 / 5xx.
 *
 * The dashboard keeps per-row test state in local state (so previous
 * results stay visible while a new probe is in flight), so this hook
 * intentionally does NOT invalidate the upstreams query on success —
 * the upstreams list shape is unchanged by a probe.
 */
export function useTestUpstream() {
  return useMutation<UpstreamTestResult, Hal0Error, string>({
    mutationFn: (name: string) =>
      api<UpstreamTestResult>(ENDPOINTS.upstreamTest(name), {
        method: 'POST',
        raw: true,
      }),
  })
}

/**
 * Coerce the upstream list payload into a typed array. The backend
 * always returns a top-level JSON array, but tolerate a `{upstreams: [...]}`
 * envelope so older / forked backends don't break the dashboard.
 */
function normalizeUpstreamList(body: unknown): Upstream[] {
  if (Array.isArray(body)) return body as Upstream[]
  if (body && typeof body === 'object') {
    const b = body as { upstreams?: unknown; providers?: unknown }
    if (Array.isArray(b.upstreams)) return b.upstreams as Upstream[]
    if (Array.isArray(b.providers)) return b.providers as Upstream[]
  }
  return []
}
