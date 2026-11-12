// What the Agents tab reads, in one place, so the tab's tests replace one module.
//
//   useAgentsRoute       GET /api/memory/agents: the coding-agent banks and the install command.
//   useAllBankTags       GET /api/memory/banks/{bank}/tags for every bank: how many facts carry
//                        each tag. The same query key and request as `useBankTags`, so the Facts
//                        tab and this one share a cache entry per bank.
//   useInstalledAgents   GET /api/agents: the agents hal0 ships that are installed on this box.
//   useMemoryKeyClients  GET /api/bridge/clients: the names that hold a key.

import { useQueries, useQueryClient } from '@tanstack/react-query'
import { apiGet } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import { useAgents } from '@/api/hooks/useAgents'
import { useBridgeClients } from '@/api/hooks/useBridge'
import type { BankTag } from '@/api/hooks/useHindsight'
import { useMemoryWorkspaceAgents } from '@/api/hooks/useMemory'
import type { BankTagsEntry } from './agents-pure'

export const useAgentsRoute = useMemoryWorkspaceAgents
export const useInstalledAgents = useAgents
export const useMemoryKeyClients = useBridgeClients

export function useAllBankTags(bankIds: string[]): BankTagsEntry[] {
  const results = useQueries({
    queries: bankIds.map((bank) => ({
      queryKey: ['memory', 'banks', bank, 'tags'] as const,
      queryFn: () => apiGet<{ items: BankTag[] }>(ENDPOINTS.memoryBankTags(bank)),
      staleTime: 30_000,
    })),
  })
  return bankIds.map((bankId, at) => {
    const result = results[at]
    const items = result?.data?.items
    const answered = result?.data !== undefined
    return {
      bankId,
      // An answer of another shape is a failure, never "no tags".
      items: Array.isArray(items) ? items : null,
      isLoading: !answered && !result?.isError,
      isError: !!result?.isError || (answered && !Array.isArray(items)),
    }
  })
}

/** Ask again for every bank's tags (the "Check again" of the evidence that failed). */
export function useTagsRefresh(): () => void {
  const client = useQueryClient()
  return () => {
    client.invalidateQueries({
      predicate: (query) => {
        const key = query.queryKey
        return key[0] === 'memory' && key[1] === 'banks' && key[3] === 'tags'
      },
    })
  }
}
