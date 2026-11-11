// Per-bank reads for the views that show every bank at once (the page's meta line, Health).
//
// Each fires one request per bank through `useQueries`, with the SAME query key and request as
// the single-bank hook it mirrors (`useBankStats`, `useMentalModels`), so the two share one
// cache entry and a bank is never fetched twice. A bank that fails stays out of the result as
// a failure: it is never counted as zero.
//
// These go when Health reads the composed `GET /api/memory/health`, which answers the same
// questions in one call.

import { useQueries } from '@tanstack/react-query'
import { apiGet } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import type { BankStats, MentalModel } from '@/api/hooks/useHindsight'
import type { BankStatsEntry } from './area-pure'
import type { MentalModelsEntry } from './health-pure'

export function useAllBankStats(bankIds: string[]): BankStatsEntry[] {
  const results = useQueries({
    queries: bankIds.map((bank) => ({
      queryKey: ['memory', 'banks', bank, 'stats'] as const,
      queryFn: () => apiGet<BankStats>(ENDPOINTS.memoryBankStats(bank)),
      staleTime: 10_000,
      refetchInterval: 30_000,
    })),
  })
  return bankIds.map((bankId, at) => ({
    bankId,
    stats: results[at]?.data ?? null,
    isLoading: results[at]?.data === undefined && !results[at]?.isError,
    isError: !!results[at]?.isError,
  }))
}

type MentalModelList = { items: MentalModel[]; total: number }

export function useAllMentalModels(bankIds: string[]): MentalModelsEntry[] {
  const results = useQueries({
    queries: bankIds.map((bank) => ({
      queryKey: ['memory', 'banks', bank, 'mental-models'] as const,
      queryFn: () =>
        apiGet<MentalModelList>(`${ENDPOINTS.memoryBankMentalModels(bank)}?detail=content`),
      staleTime: 10_000,
    })),
  })
  return bankIds.map((bankId, at) => {
    const items = results[at]?.data?.items
    return {
      bankId,
      models: Array.isArray(items) ? items : null,
      isLoading: results[at]?.data === undefined && !results[at]?.isError,
      isError: !!results[at]?.isError,
    }
  })
}
