// hal0 v3 dashboard — memory hooks (ADR-0014).
//
// Wraps /api/memory/graph/{status} + PUT /api/memory/graph so the
// Memory tab can render the current gate state and flip it without
// reaching for the raw fetch client.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPut } from '../client'
import { ENDPOINTS } from '../endpoints'

export type GraphRoute = 'upstream' | 'primary' | 'agent'

export interface GraphUpstream {
  provider: string
  model: string
}

export interface MemoryGraphStatus {
  enabled: boolean
  route: GraphRoute
  upstream: GraphUpstream | null
  in_flight: number
  builds_ok: number
  errors: number
  last_built_at: string | null
  last_error: string | null
}

export interface MemoryGraphUpdate {
  enabled?: boolean
  route?: GraphRoute
  upstream?: GraphUpstream
}

const POLL_MS = 15_000

export function useMemoryGraphStatus() {
  return useQuery<MemoryGraphStatus>({
    queryKey: ['memory', 'graph', 'status'],
    queryFn: () => apiGet<MemoryGraphStatus>(ENDPOINTS.memoryGraphStatus),
    refetchInterval: POLL_MS,
  })
}

export function useUpdateMemoryGraph() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: MemoryGraphUpdate) =>
      apiPut<MemoryGraphStatus & { status: MemoryGraphStatus }>(
        ENDPOINTS.memoryGraph,
        body as unknown as Record<string, unknown>,
      ),
    onSuccess: () => {
      // Optimistic-style refresh — the backend echoes the new status
      // in the PUT response so we COULD seed the cache, but a
      // re-fetch keeps the polling timestamp honest.
      qc.invalidateQueries({ queryKey: ['memory', 'graph', 'status'] })
    },
  })
}
