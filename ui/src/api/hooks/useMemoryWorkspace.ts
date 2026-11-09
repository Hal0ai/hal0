// hal0 v3 dashboard — Memory workspace hooks (Hindsight 1.2 showcase).
//
// The workspace is a thin, typed client over the backend's allowlisted
// passthrough (`/api/memory/engine/<upstream path>`) plus three bespoke
// aggregate endpoints (`lanes`, `agents`, `models`). Response shapes follow
// `hindsight-1.2.0-openapi.json`; this file does not re-derive anything —
// it fetches and lightly types what the backend already computed.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPatch, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

// ── Lanes / health ───────────────────────────────────────────────────────

export interface MemoryLaneDecision {
  health: string
  device: string
  requests: number
  busy_s: number
}

/** One managed HINDSIGHT_API_* key whose unit value differs from what hal0 renders. */
export interface MemoryLaneEnvDrift {
  key: string
  expected: string
  effective: string | null
}

export interface MemoryLaneLlm {
  /** Configured values (hal0.toml). */
  model: string
  max_concurrent: number
  timeout?: number
  per_op: Record<string, unknown>
  /** What the hindsight-api unit actually carries; null when unreadable.
   * Absent on older backends. */
  effective?: {
    model: string | null
    max_concurrent: number | null
    timeout: number | null
    per_op: Record<string, unknown>
  } | null
  drift?: MemoryLaneEnvDrift[]
  legacy_drop_in?: string | null
  warning?: string | null
}

export interface MemoryLaneBank {
  bank_id: string
  pending?: number
  processing?: number
  oldest_pending_age_s?: number | null
  error?: string
}

export interface MemoryLanes {
  decision: MemoryLaneDecision
  llm: MemoryLaneLlm
  banks: MemoryLaneBank[]
  npu: Record<string, unknown> | null
}

export function useMemoryLanes() {
  return useQuery({
    queryKey: ['memory', 'lanes'],
    queryFn: () => apiGet<MemoryLanes>(ENDPOINTS.memoryLanes),
    refetchInterval: 10_000,
  })
}

// ── Agents ───────────────────────────────────────────────────────────────

export interface MemoryAgentsInstall {
  harnesses: string[]
  command: string
}

export interface MemoryAgents {
  banks: string[]
  install: MemoryAgentsInstall
  /** Hindsight Control Plane console URL from [memory.console]; null when
   * disabled. Absent entirely on older backends that predate this field —
   * callers should fall back to the fixed-port default in that case. */
  console_url?: string | null
}

export function useMemoryWorkspaceAgents() {
  return useQuery({
    queryKey: ['memory', 'agents'],
    queryFn: () => apiGet<MemoryAgents>(ENDPOINTS.memoryAgents),
  })
}

// ── Models ───────────────────────────────────────────────────────────────

export interface MemoryModelInfo {
  model: string
  enabled?: boolean
  [key: string]: unknown
}

export interface MemoryModels {
  embeddings: MemoryModelInfo
  reranker: MemoryModelInfo
  decision: MemoryModelInfo
}

export function useMemoryModels() {
  return useQuery({
    queryKey: ['memory', 'models'],
    queryFn: () => apiGet<MemoryModels>(ENDPOINTS.memoryModels),
  })
}

// ── Engine passthrough ───────────────────────────────────────────────────
//
// `sub` is the upstream path, without the leading slash, e.g.
// `v1/default/banks` or `v1/default/banks/shared/knowledge-base/tree`.

export function useMemoryEngineQuery<T = unknown>(
  sub: string | null,
  opts?: { enabled?: boolean; refetchInterval?: number },
) {
  return useQuery({
    queryKey: ['memory', 'engine', sub],
    queryFn: () => apiGet<T>(ENDPOINTS.memoryEnginePath(sub as string)),
    enabled: (opts?.enabled ?? true) && !!sub,
    refetchInterval: opts?.refetchInterval,
  })
}

/** Invalidate every cached engine query under a given sub-path prefix (e.g. after a mutation). */
export function useInvalidateMemoryEngine() {
  const qc = useQueryClient()
  return (prefix?: string) =>
    qc.invalidateQueries({
      queryKey: ['memory', 'engine'],
      predicate: (q) => {
        if (!prefix) return true
        const sub = q.queryKey[2]
        return typeof sub === 'string' && sub.startsWith(prefix)
      },
    })
}

export function useMemoryEngineMutation<TBody = unknown, TResp = unknown>(
  method: 'post' | 'patch' | 'delete',
  invalidatePrefix?: string,
) {
  const invalidate = useInvalidateMemoryEngine()
  return useMutation({
    mutationFn: ({ sub, body }: { sub: string; body?: TBody }) => {
      const url = ENDPOINTS.memoryEnginePath(sub)
      if (method === 'delete') return apiDelete<TResp>(url)
      if (method === 'patch') return apiPatch<TResp>(url, body as Record<string, unknown>)
      return apiPost<TResp>(url, body as Record<string, unknown>)
    },
    onSuccess: () => invalidate(invalidatePrefix),
  })
}
