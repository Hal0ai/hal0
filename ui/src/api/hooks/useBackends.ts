// hal0 v3 dashboard — backends hooks (Phase B1).
//
// Ported from ui-vue.bak/src/stores/backends.js. `/api/backends`
// envelope is `{backends: Backend[]}`.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPost } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface Backend {
  id: string
  version: string
  state: 'installed' | 'unavailable' | 'updating' | string
  usedBy?: string[]
  recommended?: boolean
  note?: string
  kind?: string
  device?: string
}

const POLL_MS = 30_000
const SNAPSHOT_POLL_MS = 5_000

export function useBackends() {
  return useQuery({
    queryKey: ['backends'],
    queryFn: async () => {
      const body = await apiGet<any>(ENDPOINTS.backends)
      if (Array.isArray(body)) {
        return { backends: body as Backend[] }
      }
      return {
        backends: (Array.isArray(body?.backends) ? body.backends : []) as Backend[],
      }
    },
    refetchInterval: POLL_MS,
  })
}

/** Per-backend snapshot (loaded models + status). 5s poll. */
export function useBackendSnapshot(id: string | null | undefined) {
  return useQuery({
    queryKey: ['backends', id],
    queryFn: () => apiGet<Backend & { loaded?: Array<{ model_name: string; slot: string }> }>(
      ENDPOINTS.backend(id as string),
    ),
    enabled: !!id,
    refetchInterval: SNAPSHOT_POLL_MS,
  })
}

export function useBackendInstall() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiPost(ENDPOINTS.backendInstall(id)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backends'] }),
  })
}

export function useBackendUninstall() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => apiDelete(ENDPOINTS.backend(id)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backends'] }),
  })
}

// ── NPU load / unload (POST /api/backends/npu/{load,unload}) ──────────────
// The only install-adjacent backend operations the server exposes today.
// TODO endpoints.ts (ui-sweep-b owns) — inline paths for now.

export function useNpuLoad() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost<unknown>('/api/backends/npu/load'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backends'] }),
  })
}

export function useNpuUnload() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost<unknown>('/api/backends/npu/unload'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backends'] }),
  })
}
