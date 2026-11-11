// Every request the Operations tab makes, in one place. The tab's components import their hooks
// from here and from nowhere else.
//
//   useQueueHealth        GET  /api/memory/health                 the figures, and the cause
//   useOperationLists     GET  /api/memory/banks/{b}/operations   the rows, one request per bank
//   useBankConsolidation  GET  /api/memory/banks/{b}/stats        one bank's consolidation figures
//   useRetryOne           POST .../operations/{id}/retry          one row
//   useConsolidateNow     POST .../consolidate
//   collectFailed, retryOperation, discardOperation               "retry all" and "discard"
//
// The list asks the engine to leave a split batch's parent row out (`exclude_parents`), as the
// health route does when it counts. So a row is one piece of work, and the list's totals are the
// figures the summary shows.
//
// The many-at-once actions call the routes directly and refresh once at the end: the per-row
// mutation refreshes the list on every success, which a run of a hundred must not do.

import { useCallback, useEffect, useRef } from 'react'
import { useQueries, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPost } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import {
  useBankStats,
  useConsolidate,
  useOperationRetry,
  type BankOperation,
  type BankOperations,
} from '@/api/hooks/useHindsight'
import { MEMORY_HEALTH_KEY, useMemoryHealth } from '@/api/hooks/useMemory'
import { BULK_CAP, type BankList } from './operations-pure'

/** The engine's largest page. */
const ENGINE_PAGE = 100
/** How long the server reuses one health answer. */
const HEALTH_CACHE_MS = 10_000

const IN_FLIGHT = new Set(['pending', 'processing'])

export function useQueueHealth() {
  return useMemoryHealth()
}

/** One bank's stats, for its consolidation figures. The same cache entry the page header reads. */
export function useBankConsolidation(bank: string | null) {
  return useBankStats(bank)
}

export function useRetryOne() {
  return useOperationRetry()
}

export function useConsolidateNow() {
  return useConsolidate()
}

function operationsUrl(bank: string, status: string | undefined, limit: number, offset: number): string {
  const query = new URLSearchParams()
  if (status) query.set('status', status)
  query.set('limit', String(limit))
  query.set('offset', String(offset))
  query.set('exclude_parents', 'true')
  return `${ENDPOINTS.memoryBankOperations(bank)}?${query.toString()}`
}

/** One bank's answer, as the list reads it. */
export interface OperationListEntry extends BankList {
  /** No answer yet. */
  isLoading: boolean
  isError: boolean
  error: unknown
}

export interface OperationListsOptions {
  /** The engine's status value; leave out for every status. */
  status?: string
  limit: number
  offset?: number
  enabled?: boolean
}

/**
 * One page of operations from each of `bankIds`, newest first.
 *
 * Every key sits under `['memory', 'banks', bank, 'operations']`, which the retry, discard and
 * consolidate mutations refresh. A single bank is re-read every few seconds while it has work in
 * flight; several banks at once are re-read slowly.
 */
export function useOperationLists(
  bankIds: string[],
  { status, limit, offset = 0, enabled = true }: OperationListsOptions,
): { entries: OperationListEntry[]; refetch: () => void } {
  const statusKey = status ?? 'all'
  const single = bankIds.length === 1
  const results = useQueries({
    queries: bankIds.map((bank) => ({
      queryKey: ['memory', 'banks', bank, 'operations', statusKey, { limit, offset, exclude_parents: true }] as const,
      queryFn: () => apiGet<BankOperations>(operationsUrl(bank, status, limit, offset)),
      enabled,
      staleTime: 3_000,
      // Keep the page on screen while the next one loads, but never show one bank's rows, or
      // one status's, under another's name.
      placeholderData: (
        previous: BankOperations | undefined,
        previousQuery: { queryKey: readonly unknown[] } | undefined,
      ) =>
        previousQuery && previousQuery.queryKey[2] === bank && previousQuery.queryKey[4] === statusKey
          ? previous
          : undefined,
      refetchInterval: (query: { state: { data?: BankOperations } }) => {
        if (!single) return 30_000
        const rows = query.state.data?.operations ?? []
        return rows.some((op) => IN_FLIGHT.has(op.status)) ? 5_000 : 20_000
      },
    })),
  })
  const entries = bankIds.map((bankId, at): OperationListEntry => {
    const result = results[at]
    const data = result?.data
    const operations = Array.isArray(data?.operations) ? data.operations : []
    return {
      bankId,
      operations,
      total: typeof data?.total === 'number' ? data.total : null,
      isLoading: data === undefined && !result?.isError,
      isError: !!result?.isError && data === undefined,
      error: result?.error ?? null,
    }
  })
  return {
    entries,
    refetch: () => {
      for (const result of results) void result.refetch()
    },
  }
}

export interface BulkTarget {
  bank: string
  id: string
}

/**
 * The failed operations of `bankIds`, read to the end or to `cap`, whichever comes first.
 * `more` says the cap was reached with failed operations still unread.
 */
export async function collectFailed(
  bankIds: string[],
  cap: number = BULK_CAP,
): Promise<{ targets: BulkTarget[]; more: boolean }> {
  const targets: BulkTarget[] = []
  for (const bank of bankIds) {
    let offset = 0
    for (;;) {
      const page = await apiGet<BankOperations>(operationsUrl(bank, 'failed', ENGINE_PAGE, offset))
      const rows: BankOperation[] = Array.isArray(page?.operations) ? page.operations : []
      for (const op of rows) {
        if (!op?.id || op.status !== 'failed') continue
        if (targets.length >= cap) return { targets, more: true }
        targets.push({ bank, id: op.id })
      }
      offset += rows.length
      const total = typeof page?.total === 'number' ? page.total : null
      if (rows.length < ENGINE_PAGE || (total !== null && offset >= total)) break
    }
  }
  return { targets, more: false }
}

export function retryOperation(bank: string, id: string): Promise<unknown> {
  return apiPost(ENDPOINTS.memoryBankOperationRetry(bank, id), {})
}

export function discardOperation(bank: string, id: string): Promise<unknown> {
  return apiDelete(`${ENDPOINTS.memoryBankOperations(bank)}/${encodeURIComponent(id)}`)
}

/**
 * Re-read what an action changed: the lists and stats of `bankIds`, and the health answer. The
 * server may hand back the health answer it already had for up to ten seconds, so that one is
 * asked for a second time once it has aged out.
 */
export function useQueueRefresh(): (bankIds: string[]) => void {
  const queryClient = useQueryClient()
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )
  return useCallback(
    (bankIds: string[]) => {
      for (const bank of bankIds) {
        void queryClient.invalidateQueries({ queryKey: ['memory', 'banks', bank] })
      }
      void queryClient.invalidateQueries({ queryKey: MEMORY_HEALTH_KEY })
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: MEMORY_HEALTH_KEY })
      }, HEALTH_CACHE_MS + 1_000)
    },
    [queryClient],
  )
}
