// The reads MemoryArea holds for its tabs.
//
//   useMemoryHealth   GET /api/memory/health   the summary line on every tab, and all of Health
//   useAreaEngine     GET /api/memory/engine   for the tabs that list banks
//   useAreaBanks      GET /api/memory/banks    for the tabs that list banks
//
// The health answer carries the engine's state, every bank and its figures, so the summary line
// and the Health tab ask for nothing else, and nothing is asked once per bank. The engine check
// and the bank list are for the tabs with a bank control (Facts, Operations, Knowledge, Agents);
// they are not asked for while Health is the open tab.
//
// The two gated reads use the SAME query key and request as `useMemoryEngine` and
// `useMemoryBanks` (src/api/hooks/useHindsight.ts), so every reader shares one cache entry.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import type { MemoryBank, MemoryEngine } from '@/api/hooks/useHindsight'

export { useMemoryHealth } from '@/api/hooks/useMemory'

export interface MemoryBankList {
  banks: MemoryBank[]
  total?: number
  limit?: number
  offset?: number
}

export function useAreaEngine(enabled: boolean) {
  return useQuery<MemoryEngine>({
    queryKey: ['memory', 'engine'],
    queryFn: () => apiGet<MemoryEngine>(ENDPOINTS.memoryEngine),
    enabled,
    staleTime: 10_000,
    refetchInterval: 15_000,
  })
}

export function useAreaBanks(enabled: boolean) {
  return useQuery<MemoryBankList>({
    queryKey: ['memory', 'banks'],
    queryFn: () => apiGet<MemoryBankList>(ENDPOINTS.memoryBanks),
    enabled,
    staleTime: 10_000,
    refetchInterval: 30_000,
  })
}
