// hal0 v3 dashboard — /api/features hook (PR #736).
//
// GET /api/features → { memory, memory_engine, comfyui_switchover, npu, mcp_supervisor }
// memory_engine is the live engine name ("Hindsight", etc.) — used for the
// engine-neutral label in the memory tab.
//
// TODO endpoints.ts (ui-sweep-b owns) — inline path for now.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'

export interface Features {
  /** Memory subsystem enabled on this install */
  memory?: boolean
  /** Live engine name, e.g. "Hindsight". Use for display; never hard-code "Cognee". */
  memory_engine?: string
  /** ComfyUI switchover available */
  comfyui_switchover?: boolean
  /** NPU available */
  npu?: boolean
  /** MCP supervisor available */
  mcp_supervisor?: boolean
}

export function useFeatures() {
  return useQuery<Features>({
    // TODO endpoints.ts (ui-sweep-b owns)
    queryKey: ['features'],
    queryFn: () => apiGet<Features>('/api/features'),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })
}
