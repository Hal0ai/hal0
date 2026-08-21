// hal0 v3 dashboard — Hindsight engine hooks (Memory view).
//
// Wraps the /api/memory/engine aggregator and the bank-scoped admin
// passthrough (/api/memory/banks/*) added by the memory_admin routes.
// One hook per resource; mutations invalidate the bank-scoped keys so
// cards/panels refresh without manual plumbing.

import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { apiDelete, apiGet, apiPatch, apiPost, apiPut, Hal0Error } from '../client'
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
  id: string
  task_type: string
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled' | string
  created_at: string
  error_message: string | null
  retry_count: number
}

// Real hindsight-api 0.8.4 envelope — verified live against
// GET /v1/default/banks/{bank}/operations. NOT the `{items, total}` shape
// every other Hindsight list endpoint uses (#1645).
export interface BankOperations {
  bank_id?: string
  total: number
  limit?: number
  offset?: number
  operations: BankOperation[]
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
  /** task_type of each failed op, for the failed-ops affordance. */
  failedTypes: string[]
  total: number
}

/** Fold a bank's operations list into the counts the activity badges render. */
export function summarizeBankOperations(ops?: BankOperations | null): BankActivity {
  const counts = { pending: 0, processing: 0, completed: 0, failed: 0, cancelled: 0 }
  const failedTypes: string[] = []
  const items = ops?.operations ?? []
  for (const op of items) {
    if (op.status in counts) counts[op.status as keyof typeof counts] += 1
    if (op.status === 'failed') failedTypes.push(op.task_type)
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
      const items = query.state.data?.operations ?? []
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
    // Widened from the original FU2 `1 | 2` cap for task C4's ego focus
    // view, which needs the full 1–10 depth slider the Global Constraints
    // require. The server already honours depth <= 10 (task A2, merged);
    // the only existing caller (memory-graph.jsx's Direction-C ego slice)
    // passes a hardcoded `depth: 1`, so this widening is backward
    // compatible — nothing depended on the old 2-cap.
    depth?: number
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

// ── Memory v2 (Bank workspace UI) — tags, curatable unit list, curate,
// history ───────────────────────────────────────────────────────────────
// Data plumbing for the new Bank workspace: the tag-usage sidebar, the
// paged/filterable unit list, the curate mutation (invalidate = reversible,
// no approval gate — delete stays on the existing gated directive-style
// routes), and per-unit audit history. Response shapes per the backend
// team's Interfaces spec (task A1/A3).

export interface BankTag {
  tag: string
  count: number
}

export function useBankTags(bank: string | null) {
  return useQuery<{ items: BankTag[] }>({
    queryKey: ['memory', 'banks', bank, 'tags'],
    queryFn: () => apiGet<{ items: BankTag[] }>(ENDPOINTS.memoryBankTags(bank as string)),
    enabled: !!bank,
    staleTime: 30_000,
  })
}

export interface BankUnitsParams {
  q?: string
  tags?: string[]
  type?: string
  from?: string
  to?: string
  documentId?: string
  // Forwarded verbatim (no transform) — upstream (hindsight-api 0.8.4)
  // archives invalidated facts out of the default /units listing; the
  // curation inspector's revert flow passes `state=invalidated` to list
  // them back in.
  state?: string
  // Server (memory_admin.bank_units) only accepts these two — anything else
  // 422s with memory.invalid_query. See PR #1987 review M10.
  sort?: 'recency' | 'salience'
  limit?: number
  offset?: number
}

// Pure query-string serializer, exported so it's unit-testable without
// mounting the hook (useHindsight.ts has no existing hook-level tests).
// camelCase `documentId` -> snake_case `document_id` to match the backend
// query param; every other key round-trips as-is. Falsy-but-meaningful
// values (`offset: 0`, `limit: 0`) are kept — only `undefined`/empty
// string/empty array are omitted.
export function serializeBankUnitsParams(params: BankUnitsParams = {}): string {
  const sp = new URLSearchParams()
  if (params.q) sp.set('q', params.q)
  if (params.tags && params.tags.length) sp.set('tags', params.tags.join(','))
  if (params.type) sp.set('type', params.type)
  if (params.from) sp.set('from', params.from)
  if (params.to) sp.set('to', params.to)
  if (params.documentId) sp.set('document_id', params.documentId)
  if (params.state) sp.set('state', params.state)
  if (params.sort) sp.set('sort', params.sort)
  if (params.limit !== undefined) sp.set('limit', String(params.limit))
  if (params.offset !== undefined) sp.set('offset', String(params.offset))
  const qs = sp.toString()
  return qs ? `?${qs}` : ''
}

export interface BankUnitRow {
  id: string
  text: string
  context?: string | null
  occurred_start: string
  occurred_end?: string | null
  fact_type: 'world' | 'experience' | 'observation' | string
  entities: string[]
  tags?: string[]
  document_id?: string | null
  state: 'valid' | 'invalidated' | string
  // task C7 (backend A3b): null for a unit that fell outside the
  // salience-scored slab (e.g. a large bank's capped graph) — no ranking
  // to offer, not a zero score.
  salience: number | null
  link_counts_by_type: Record<string, number>
}

export interface BankUnitsPage {
  items: BankUnitRow[]
  total_matched: number
  next_offset: number | null
  // True when the server's 2000-row upstream slab clipped the match set —
  // total_matched/paging are only accurate within that slab, and under
  // sort=salience the ranking is against a partial graph too. See PR #1987
  // review B2.
  truncated: boolean
}

export function useBankUnits(bank: string | null, params: BankUnitsParams = {}) {
  return useQuery<BankUnitsPage>({
    queryKey: ['memory', 'banks', bank, 'units', params],
    queryFn: () =>
      apiGet<BankUnitsPage>(
        `${ENDPOINTS.memoryBankUnits(bank as string)}${serializeBankUnitsParams(params)}`,
      ),
    enabled: !!bank,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  })
}

export interface UnitCurateBody {
  text?: string
  context?: string
  occurred_start?: string
  occurred_end?: string
  fact_type?: string
  entities?: string[]
  state?: 'invalidated' | 'valid'
  reason?: string
}

// Curate is reversible (invalidate/edit) and carries no approval gate,
// unlike delete — see the Global Constraints in the task brief.
export function useUnitCurate(bank: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UnitCurateBody }) =>
      apiPatch<BankUnitRow>(ENDPOINTS.memoryUnit(bank, id), body as Record<string, unknown>),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['memory', 'banks', bank] })
    },
  })
}

export interface UnitHistoryEvent {
  state?: string
  at?: string | null
  reason?: string | null
  [key: string]: unknown
}

export interface UnitHistory {
  events: UnitHistoryEvent[]
}

// Upstream (hindsight-api 0.8.4) returns a bare JSON ARRAY of history
// events for /memories/:id/history, not a `{events:[...]}` dict — normalize
// both shapes (plus null/undefined/anything-else) to `{events: [...]}` so
// callers never have to branch on the wire shape. Exported so it's
// unit-testable without mounting the hook.
export function normalizeUnitHistory(raw: unknown): UnitHistory {
  if (Array.isArray(raw)) return { events: raw as UnitHistoryEvent[] }
  if (raw && typeof raw === 'object') {
    const events = (raw as { events?: unknown }).events
    return { events: Array.isArray(events) ? (events as UnitHistoryEvent[]) : [] }
  }
  return { events: [] }
}

// Upstream 404s /memories/:id/history for non-observation facts (it only
// tracks history for observation-type facts) — that is "no history yet",
// not a fetch failure, so the drawer shouldn't render an error state for
// it. Any other error rethrows so react-query's isError path still fires.
// Exported so the 404-tolerance is unit-testable without mounting the hook.
export function unitHistoryOrEmptyOn404(err: unknown): UnitHistory {
  if (err instanceof Hal0Error && err.status === 404) return { events: [] }
  throw err
}

export function useUnitHistory(
  bank: string | null,
  id: string | null,
  opts: { enabled?: boolean } = {},
) {
  const enabled = (opts.enabled ?? true) && !!bank && !!id
  return useQuery<UnitHistory>({
    queryKey: ['memory', 'banks', bank, 'unit', id, 'history'],
    queryFn: async () => {
      try {
        const raw = await apiGet<unknown>(ENDPOINTS.memoryUnitHistory(bank as string, id as string))
        return normalizeUnitHistory(raw)
      } catch (err) {
        return unitHistoryOrEmptyOn404(err)
      }
    },
    enabled,
    staleTime: 30_000,
  })
}
