// hal0 v3 dashboard — Hindsight engine hooks (Memory view).
//
// Wraps the /api/memory/engine aggregator and the bank-scoped admin
// passthrough (/api/memory/banks/*) added by the memory_admin routes.
// One hook per resource; mutations invalidate the bank-scoped keys so
// cards/panels refresh without manual plumbing.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPost, apiPut } from '../client'
import { ENDPOINTS } from '../endpoints'

// ── types (mirror Hindsight 0.7.x response shapes we consume) ───────────────

export interface MemoryEngine {
  enabled: boolean
  engine: 'hindsight' | null
  reachable: boolean
  version: string | null
  features: Record<string, boolean> | null
  banks_total: number | null
}

export interface MemoryBank {
  bank_id: string
  name?: string | null
  mission?: string | null
  created_at?: string | null
  updated_at?: string | null
  fact_count?: number | null
  last_document_at?: string | null
}

export interface BankStats {
  bank_id: string
  total_nodes: number
  total_links: number
  total_documents: number
  nodes_by_fact_type: Record<string, number>
  links_by_link_type: Record<string, number>
  pending_operations: number
  failed_operations: number
  operations_by_status: Record<string, number>
  last_consolidated_at: string | null
  pending_consolidation: number
  failed_consolidation: number
  total_observations: number
}

export interface TimeseriesBucket {
  time: string
  world: number
  experience: number
  observation: number
}

export interface BankTimeseries {
  bucket_size?: string
  buckets: TimeseriesBucket[]
}

export interface BankOperation {
  operation_id: string
  operation_type: string
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled' | string
  created_at: string
  error_message: string | null
  retry_count: number
}

export interface BankOperations {
  items: BankOperation[]
  total: number
}

// ── engine card ──────────────────────────────────────────────────────────────

export function useMemoryEngine() {
  return useQuery<MemoryEngine>({
    queryKey: ['memory', 'engine'],
    queryFn: () => apiGet<MemoryEngine>(ENDPOINTS.memoryEngine),
    staleTime: 10_000,
    refetchInterval: 15_000,
  })
}

// ── banks ────────────────────────────────────────────────────────────────────

export function useMemoryBanks() {
  return useQuery<{ banks: MemoryBank[] }>({
    queryKey: ['memory', 'banks'],
    queryFn: () => apiGet<{ banks: MemoryBank[] }>(ENDPOINTS.memoryBanks),
    staleTime: 10_000,
    refetchInterval: 30_000,
  })
}

export function useBankStats(bank: string | null) {
  return useQuery<BankStats>({
    queryKey: ['memory', 'banks', bank, 'stats'],
    queryFn: () => apiGet<BankStats>(ENDPOINTS.memoryBankStats(bank as string)),
    enabled: !!bank,
    staleTime: 10_000,
    refetchInterval: 30_000,
  })
}

export function useBankTimeseries(bank: string | null, period: string) {
  return useQuery<BankTimeseries>({
    queryKey: ['memory', 'banks', bank, 'timeseries', period],
    queryFn: () =>
      apiGet<BankTimeseries>(
        `${ENDPOINTS.memoryBankTimeseries(bank as string)}?period=${encodeURIComponent(period)}`,
      ),
    enabled: !!bank,
    staleTime: 30_000,
  })
}

export function useBankUpsert() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ bank, body }: { bank: string; body: Record<string, unknown> }) =>
      apiPut(ENDPOINTS.memoryBank(bank), body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['memory'] })
    },
  })
}

export function useBankDelete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (bank: string) => apiDelete(ENDPOINTS.memoryBank(bank)),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['memory'] })
    },
  })
}

// ── operations ───────────────────────────────────────────────────────────────

export function useBankOperations(bank: string | null, opts?: { status?: string }) {
  const qs = opts?.status ? `?status=${encodeURIComponent(opts.status)}` : ''
  return useQuery<BankOperations>({
    queryKey: ['memory', 'banks', bank, 'operations', opts?.status ?? 'all'],
    queryFn: () =>
      apiGet<BankOperations>(`${ENDPOINTS.memoryBankOperations(bank as string)}${qs}`),
    enabled: !!bank,
    staleTime: 5_000,
    refetchInterval: 15_000,
  })
}

export function useOperationRetry() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ bank, id }: { bank: string; id: string }) =>
      apiPost(ENDPOINTS.memoryBankOperationRetry(bank, id), {}),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', vars.bank, 'operations'] })
    },
  })
}

export function useOperationCancel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ bank, id }: { bank: string; id: string }) =>
      apiDelete(`${ENDPOINTS.memoryBankOperations(bank)}/${encodeURIComponent(id)}`),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', vars.bank, 'operations'] })
    },
  })
}

export function useConsolidate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (bank: string) => apiPost(ENDPOINTS.memoryBankConsolidate(bank), {}),
    onSuccess: (_data, bank) => {
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', bank] })
    },
  })
}
