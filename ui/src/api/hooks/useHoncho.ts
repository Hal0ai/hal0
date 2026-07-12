// hal0 v3 dashboard — Honcho memory provider hooks (Memory view).
//
// Wraps the provider-routing surface (/api/memory/provider — which provider
// each agent uses) and the Honcho-specific engine stats + graph-sync timer
// (/api/memory/honcho/*). Mirrors the useHindsight.ts conventions: one hook
// per resource, mutations invalidate the queries that display their effect.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, apiPut } from '../client'
import { ENDPOINTS } from '../endpoints'

// ── provider routing ─────────────────────────────────────────────────────────

export interface MemoryProviderEngine {
  healthy: boolean
  url: string | null
}

export interface MemoryProviderEngines {
  hindsight: MemoryProviderEngine
  honcho: MemoryProviderEngine
}

export type MemoryProviderKind = 'hindsight' | 'honcho'

export interface MemoryProviderAgent {
  provider: MemoryProviderKind
  private: boolean
}

export interface MemoryProviderStatus {
  engines: MemoryProviderEngines
  agents: Record<string, MemoryProviderAgent>
}

export function useMemoryProvider() {
  return useQuery<MemoryProviderStatus>({
    queryKey: ['memory', 'provider'],
    queryFn: () => apiGet<MemoryProviderStatus>(ENDPOINTS.memoryProvider),
    staleTime: 10_000,
    refetchInterval: 10_000,
  })
}

export interface SetMemoryProviderBody {
  agent: string
  provider: MemoryProviderKind
  private?: boolean
  restart?: boolean
}

export function useSetMemoryProvider() {
  const qc = useQueryClient()
  return useMutation({
    // restart defaults true server-side, but pin it explicitly so callers
    // never silently skip the agent restart the provider switch needs.
    mutationFn: (body: SetMemoryProviderBody) =>
      apiPut(ENDPOINTS.memoryProvider, { restart: true, ...body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['memory', 'provider'] })
    },
  })
}

// ── Honcho engine stats ──────────────────────────────────────────────────────

export interface HonchoStats {
  enabled: boolean
  reachable: boolean
  version: string | null
  url: string | null
  workspace: string | null
  peers: number | null
  observations: number | null
  conclusions: number | null
  deriver_pending: number | null
  deriver_processing: number | null
}

export function useHonchoStats() {
  return useQuery<HonchoStats>({
    queryKey: ['memory', 'honcho', 'stats'],
    queryFn: () => apiGet<HonchoStats>(ENDPOINTS.memoryHonchoStats),
    staleTime: 10_000,
    refetchInterval: 15_000,
  })
}

// ── Honcho graph-sync timer ──────────────────────────────────────────────────

export interface HonchoSyncStatus {
  timer_enabled: boolean
  interval: string | null
  last_run_at: string | null
  last_run_ok: boolean | null
  last_run_error: string | null
  last_synced_count: number | null
  next_run_at: string | null
}

export function useHonchoSync() {
  return useQuery<HonchoSyncStatus>({
    queryKey: ['memory', 'honcho', 'sync'],
    queryFn: () => apiGet<HonchoSyncStatus>(ENDPOINTS.memoryHonchoSync),
    staleTime: 10_000,
    refetchInterval: 15_000,
  })
}

export function useSetHonchoSync() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { enabled: boolean }) =>
      apiPut<HonchoSyncStatus>(ENDPOINTS.memoryHonchoSync, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['memory', 'honcho', 'sync'] })
    },
  })
}

// The backend is fail-soft here: a systemctl failure to kick off the sync
// unit still returns HTTP 200, with `started: false` and a `note` explaining
// why (see routes/memory.py). Callers MUST check `.started` — a 2xx response
// alone does not mean the sync actually started.
export interface HonchoSyncRunResult {
  started: boolean
  note?: string | null
}

export function useHonchoSyncRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost<HonchoSyncRunResult>(ENDPOINTS.memoryHonchoSyncRun),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['memory', 'honcho', 'sync'] })
      void qc.invalidateQueries({ queryKey: ['memory', 'honcho', 'stats'] })
    },
  })
}
