// hal0 v3 dashboard — Hindsight engine hooks (Memory view).
//
// Wraps the /api/memory/engine aggregator and the bank-scoped admin
// passthrough (/api/memory/banks/*) added by the memory_admin routes.
// One hook per resource; mutations invalidate the bank-scoped keys so
// cards/panels refresh without manual plumbing.

import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from '../client'
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

// GET /api/memory/banks is a verbatim Hindsight passthrough — it does NOT
// reliably carry a per-bank fact count, so there is no cheap client-side sum
// for "total facts across all banks". The real counts live on each bank's
// /stats response (`total_nodes`). This aggregates that across every given
// bank id via useQueries (the sanctioned way to fire a dynamic list of
// queries without violating rules-of-hooks) using the SAME query key +
// queryFn as useBankStats, so react-query shares/dedupes the cache with the
// per-bank bank-grid cards instead of doubling the request count.
export function useAggregateBankStats(bankIds: string[]) {
  const queries = useQueries({
    queries: bankIds.map((bank) => ({
      queryKey: ['memory', 'banks', bank, 'stats'] as const,
      queryFn: () => apiGet<BankStats>(ENDPOINTS.memoryBankStats(bank)),
      staleTime: 10_000,
      refetchInterval: 30_000,
    })),
  })
  const isLoading = bankIds.length > 0 && queries.some((q) => q.isLoading)
  const totalFacts = queries.reduce((sum, q) => sum + (q.data?.total_nodes ?? 0), 0)
  return { isLoading, totalFacts }
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
    // The backend guards bank deletion behind ?confirm=<bank_id> (the delete
    // is irreversible). The UI already gates this behind a two-step confirm
    // dialog, so echo the bank id to satisfy the guard.
    mutationFn: (bank: string) =>
      apiDelete(`${ENDPOINTS.memoryBank(bank)}?confirm=${encodeURIComponent(bank)}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['memory'] })
    },
  })
}

// ── operations ───────────────────────────────────────────────────────────────

const IN_FLIGHT_STATUSES = new Set(['pending', 'processing'])

/** Rolled-up per-bank operation counts for the live activity indicators. */
export interface BankActivity {
  pending: number
  processing: number
  completed: number
  failed: number
  cancelled: number
  /** pending + processing — "work in flight" drives the spinner/pulse. */
  inFlight: number
  /** operation_type of each failed op, for the failed-ops affordance. */
  failedTypes: string[]
  total: number
}

/** Fold a bank's operations list into the counts the activity badges render. */
export function summarizeBankOperations(ops?: BankOperations | null): BankActivity {
  const counts = { pending: 0, processing: 0, completed: 0, failed: 0, cancelled: 0 }
  const failedTypes: string[] = []
  const items = ops?.items ?? []
  for (const op of items) {
    if (op.status in counts) counts[op.status as keyof typeof counts] += 1
    if (op.status === 'failed') failedTypes.push(op.operation_type)
  }
  return {
    ...counts,
    inFlight: counts.pending + counts.processing,
    failedTypes,
    total: items.length,
  }
}

// Shared query — the bank list card AND the bank detail panel read the same
// key so there is one poll per bank, not one per consumer (no stampede).
// Polling is adaptive: fast (3s) while work is in flight so ingest/extraction
// visibly progresses, then it backs off (20s) once the bank is quiescent.
export function useBankOperations(
  bank: string | null,
  opts?: { status?: string; enabled?: boolean },
) {
  const qs = opts?.status ? `?status=${encodeURIComponent(opts.status)}` : ''
  return useQuery<BankOperations>({
    queryKey: ['memory', 'banks', bank, 'operations', opts?.status ?? 'all'],
    queryFn: () =>
      apiGet<BankOperations>(`${ENDPOINTS.memoryBankOperations(bank as string)}${qs}`),
    enabled: !!bank && opts?.enabled !== false,
    staleTime: 3_000,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? []
      const inFlight = items.some((o) => IN_FLIGHT_STATUSES.has(o.status))
      return inFlight ? 3_000 : 20_000
    },
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
      // The queued consolidation shows up in the Operations list — refetch it
      // explicitly so the new op appears without waiting for the poll tick.
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', bank, 'operations'] })
    },
  })
}

// ── graph explorer ───────────────────────────────────────────────────────────

/** Cytoscape-style payload from Hindsight graph endpoints (0.7.x). */
export interface GraphPayload {
  nodes: { data: Record<string, unknown> }[]
  edges: { data: Record<string, unknown> }[]
  total_units?: number
  total_entities?: number
  total_edges?: number
  returned_nodes?: number
  returned_edges?: number
  truncated?: boolean
  mode?: 'ego' | 'top'
  center?: string | null
  limit?: number
}

function qs(params: Record<string, string | number | undefined>): string {
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined && v !== '')
  if (!pairs.length) return ''
  return (
    '?' +
    pairs.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join('&')
  )
}

export function useBankGraph(
  bank: string | null,
  opts?: { type?: string; q?: string; limit?: number },
) {
  const query = qs({ type: opts?.type, q: opts?.q, limit: opts?.limit })
  return useQuery<GraphPayload>({
    queryKey: ['memory', 'banks', bank, 'graph', query],
    queryFn: () => apiGet<GraphPayload>(`${ENDPOINTS.memoryBankGraph(bank as string)}${query}`),
    enabled: !!bank,
    staleTime: 15_000,
  })
}

export function useBankSubgraph(
  bank: string | null,
  opts?: {
    kind?: 'memories' | 'entities'
    mode?: 'ego' | 'top'
    node?: string
    depth?: 1 | 2
    top_k?: number
    by?: 'degree' | 'recency'
    limit?: number
    type?: string
    q?: string
    enabled?: boolean
  },
) {
  const query = qs({
    kind: opts?.kind,
    mode: opts?.mode,
    node: opts?.node,
    depth: opts?.depth,
    top_k: opts?.top_k,
    by: opts?.by,
    limit: opts?.limit,
    type: opts?.type,
    q: opts?.q,
  })
  return useQuery<GraphPayload>({
    queryKey: ['memory', 'banks', bank, 'subgraph', query],
    queryFn: () =>
      apiGet<GraphPayload>(`${ENDPOINTS.memoryBankSubgraph(bank as string)}${query}`),
    enabled: !!bank && opts?.enabled !== false && (opts?.mode !== 'ego' || !!opts?.node),
    staleTime: 15_000,
  })
}

export function useEntityGraph(
  bank: string | null,
  opts?: { min_count?: number; limit?: number },
) {
  const query = qs({ min_count: opts?.min_count, limit: opts?.limit })
  return useQuery<GraphPayload>({
    queryKey: ['memory', 'banks', bank, 'entities-graph', query],
    queryFn: () =>
      apiGet<GraphPayload>(`${ENDPOINTS.memoryBankEntityGraph(bank as string)}${query}`),
    enabled: !!bank,
    staleTime: 15_000,
  })
}

// ── tools: recall / reflect consoles ─────────────────────────────────────────

export interface RecallResult {
  id: string
  text: string
  type: string
  entities?: unknown[]
  occurred_start?: string | null
  tags?: string[]
}

export function useRecall() {
  return useMutation({
    mutationFn: ({ bank, body }: { bank: string; body: Record<string, unknown> }) =>
      apiPost<{ results: RecallResult[] }>(ENDPOINTS.memoryBankRecall(bank), body),
  })
}

export function useReflect() {
  return useMutation({
    mutationFn: ({ bank, body }: { bank: string; body: Record<string, unknown> }) =>
      apiPost<{ text: string; based_on?: Record<string, number> }>(
        ENDPOINTS.memoryBankReflect(bank),
        body,
      ),
  })
}

// ── tools: documents ─────────────────────────────────────────────────────────

export interface BankDocument {
  id: string
  created_at?: string | null
  memory_unit_count?: number
  tags?: string[]
  original_text?: string
}

export function useBankDocuments(
  bank: string | null,
  opts?: { q?: string; limit?: number; offset?: number },
) {
  const query = qs({ q: opts?.q, limit: opts?.limit, offset: opts?.offset })
  return useQuery<{ items: BankDocument[]; total: number }>({
    queryKey: ['memory', 'banks', bank, 'documents', query],
    queryFn: () =>
      apiGet<{ items: BankDocument[]; total: number }>(
        `${ENDPOINTS.memoryBankDocuments(bank as string)}${query}`,
      ),
    enabled: !!bank,
    staleTime: 10_000,
  })
}

export function useDocumentDelete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ bank, id }: { bank: string; id: string }) =>
      apiDelete(ENDPOINTS.memoryBankDocument(bank, id)),
    onSuccess: (_d, vars) => {
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', vars.bank] })
    },
  })
}

export function useDocumentReprocess() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ bank, id }: { bank: string; id: string }) =>
      apiPost(`${ENDPOINTS.memoryBankDocument(bank, id)}/reprocess`, {}),
    onSuccess: (_d, vars) => {
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', vars.bank, 'operations'] })
    },
  })
}

// ── tools: mental models ─────────────────────────────────────────────────────

export interface MentalModel {
  id: string
  name: string
  source_query: string
  content?: string | null
  tags?: string[]
  is_stale?: boolean
  last_refreshed_at?: string | null
}

export function useMentalModels(bank: string | null) {
  return useQuery<{ items: MentalModel[]; total: number }>({
    queryKey: ['memory', 'banks', bank, 'mental-models'],
    queryFn: () =>
      apiGet<{ items: MentalModel[]; total: number }>(
        ENDPOINTS.memoryBankMentalModels(bank as string),
      ),
    enabled: !!bank,
    staleTime: 10_000,
  })
}

export function useMentalModelRefresh() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ bank, id }: { bank: string; id: string }) =>
      apiPost(
        `${ENDPOINTS.memoryBankMentalModels(bank)}/${encodeURIComponent(id)}/refresh`,
        {},
      ),
    onSuccess: (_d, vars) => {
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', vars.bank, 'mental-models'] })
    },
  })
}

export function useMentalModelCreate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ bank, body }: { bank: string; body: { name: string; source_query: string } }) =>
      apiPost(ENDPOINTS.memoryBankMentalModels(bank), body),
    onSuccess: (_d, vars) => {
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', vars.bank, 'mental-models'] })
    },
  })
}

export function useMentalModelDelete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ bank, id }: { bank: string; id: string }) =>
      apiDelete(`${ENDPOINTS.memoryBankMentalModels(bank)}/${encodeURIComponent(id)}`),
    onSuccess: (_d, vars) => {
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', vars.bank, 'mental-models'] })
    },
  })
}

// ── tools: directives ────────────────────────────────────────────────────────

export interface Directive {
  id: string
  name: string
  content: string
  priority?: number
  is_active?: boolean
  tags?: string[]
}

export function useDirectives(bank: string | null) {
  return useQuery<{ items: Directive[]; total: number }>({
    queryKey: ['memory', 'banks', bank, 'directives'],
    queryFn: () =>
      apiGet<{ items: Directive[]; total: number }>(
        ENDPOINTS.memoryBankDirectives(bank as string),
      ),
    enabled: !!bank,
    staleTime: 10_000,
  })
}

export function useDirectiveCreate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ bank, body }: { bank: string; body: Record<string, unknown> }) =>
      apiPost(ENDPOINTS.memoryBankDirectives(bank), body),
    onSuccess: (_d, vars) => {
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', vars.bank, 'directives'] })
    },
  })
}

export function useDirectiveUpdate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ bank, id, body }: { bank: string; id: string; body: Record<string, unknown> }) =>
      apiPatch(`${ENDPOINTS.memoryBankDirectives(bank)}/${encodeURIComponent(id)}`, body),
    onSuccess: (_d, vars) => {
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', vars.bank, 'directives'] })
    },
  })
}

export function useDirectiveDelete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ bank, id }: { bank: string; id: string }) =>
      apiDelete(`${ENDPOINTS.memoryBankDirectives(bank)}/${encodeURIComponent(id)}`),
    onSuccess: (_d, vars) => {
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', vars.bank, 'directives'] })
    },
  })
}
