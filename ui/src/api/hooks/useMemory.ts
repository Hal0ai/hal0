// hal0 v3 dashboard — memory hooks (ADR-0023).
//
// Wraps /api/memory/graph/{status} + PUT /api/memory/graph so the
// Memory tab can render the current gate state and flip it without
// reaching for the raw fetch client.
//
// Also exposes:
//   useMemoryList      — GET /api/memory/list (paginated records)
//   useAgentMemoryStats — GET /api/agents/{id}/memory/stats (per-agent counts)
//   useMemoryAdd       — POST /api/memory/add (task C3 — Bank workspace Add-fact
//                        modal; no pre-existing add-memory hook was found when
//                        this was added, per the task brief's fallback)
//   useMemoryDelete    — POST /api/memory/delete (task C4 — Inspector's audited
//                        delete action; same situation as useMemoryAdd, no
//                        pre-existing per-fact delete hook was found — only a
//                        whole-bank useBankDelete existed)

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPost, apiPut } from '../client'
import { ENDPOINTS } from '../endpoints'

// ── Add a memory item ───────────────────────────────────────────────────────
//
// routes/memory.py's memory_add: body {text, dataset?, tags?, metadata?,
// document_id?}. `dataset` is the bank to write into. `source` is NEVER
// sent — the server rejects it outright (stamped server-side from the
// X-hal0-Agent identity header so callers can't impersonate another agent).
export interface MemoryAddBody {
  text: string
  dataset?: string
  tags?: string[]
  metadata?: Record<string, unknown>
  document_id?: string
}

export interface MemoryAddResponse {
  id: string
  timestamp: string
  operation_id?: string
}

export function useMemoryAdd() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: MemoryAddBody) =>
      apiPost<MemoryAddResponse>(ENDPOINTS.memoryAdd, body as unknown as Record<string, unknown>),
    onSuccess: (_d, vars) => {
      // Refresh the written bank's unit/stats/tag caches (falls back to a
      // broad memory invalidate if no dataset was given, matching the
      // server's own dataset-resolution fallback).
      void qc.invalidateQueries({
        queryKey: vars.dataset ? ['memory', 'banks', vars.dataset] : ['memory'],
      })
    },
  })
}

// ── Delete memory item(s) ───────────────────────────────────────────────────
//
// routes/memory.py's memory_delete: body {ids: [...], dataset?}. `ids` are
// document ids, but a per-fact id (metadata.fact_id — which is what a
// Bank workspace unit id already is) is accepted as an alias and resolved
// to its owning document (#1456). Audited — the backend wraps the call in
// `record_action`, so this is the "audited + gated" delete the curation
// constraints call for (as opposed to the reversible, no-gate invalidate,
// which goes through useUnitCurate's PATCH instead).
export interface MemoryDeleteBody {
  ids: string[]
  dataset?: string
}

export interface MemoryDeleteResponse {
  deleted: number
}

export function useMemoryDelete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: MemoryDeleteBody) =>
      apiPost<MemoryDeleteResponse>(ENDPOINTS.memoryDelete, body as unknown as Record<string, unknown>),
    onSuccess: (_d, vars) => {
      void qc.invalidateQueries({
        queryKey: vars.dataset ? ['memory', 'banks', vars.dataset] : ['memory'],
      })
    },
  })
}

// ── Memory list ────────────────────────────────────────────────────────────

export interface MemoryRecord {
  id: string
  text: string
  timestamp: string
  dataset: string
  tags: string[]
  source: string | null
  metadata: Record<string, unknown>
  score: number | null
}

export interface MemoryListResponse {
  items: MemoryRecord[]
  next_cursor: string | null
}

export function useMemoryList(options?: {
  dataset?: string
  limit?: number
  enabled?: boolean
}) {
  const dataset = options?.dataset ?? 'shared'
  const limit = options?.limit ?? 10
  return useQuery<MemoryListResponse>({
    queryKey: ['memory', 'list', dataset, limit],
    // TODO endpoints.ts (ui-sweep-b owns) — inline path
    queryFn: () =>
      apiGet<MemoryListResponse>(
        `/api/memory/list?dataset=${encodeURIComponent(dataset)}&limit=${limit}`,
      ),
    staleTime: 10_000,
    refetchInterval: 30_000,
    enabled: options?.enabled ?? true,
  })
}

// ── Agent memory stats ──────────────────────────────────────────────────────

export interface AgentMemoryStats {
  agent_id: string
  namespace: string
  writes: number
  reads: number
  last_write: string | null
  available: boolean
}

export function useAgentMemoryStats(agentId: string | null | undefined) {
  // TODO endpoints.ts (ui-sweep-b owns) — inline path
  return useQuery<AgentMemoryStats>({
    queryKey: ['agents', agentId, 'memory', 'stats'],
    queryFn: () =>
      apiGet<AgentMemoryStats>(
        `/api/agents/${encodeURIComponent(agentId as string)}/memory/stats`,
      ),
    enabled: !!agentId,
    staleTime: 10_000,
    refetchInterval: 30_000,
  })
}

// ADR-0023: graph extraction is routed to a single local enabled-llm slot
// (`extraction_slot`), replacing the old `route` enum + `upstream` block.
export interface MemoryGraphStatus {
  enabled: boolean
  // The local llm slot used for extraction (e.g. "utility").
  extraction_slot: string
  // DEPRECATED mirror of extraction_slot (will be removed). Prefer reading
  // extraction_slot.
  route?: string
  // Does extraction_slot match an enabled llm slot right now?
  slot_resolves: boolean
  // Enabled llm slot names the operator may pick.
  available_slots: string[]
  // Hindsight daemon LLM timeout ([memory.graph].llm_timeout_s) — echoed by
  // the status route so the settings panel can edit it in one round trip.
  llm_timeout_s?: number
  in_flight: number
  builds_ok: number
  errors: number
  last_built_at: string | null
  last_error: string | null
}

export interface MemoryGraphUpdate {
  enabled?: boolean
  extraction_slot?: string
  llm_timeout_s?: number
}

// ADR-0023 §3: when the extraction slot changes, the PUT response carries a
// propagation block describing the hindsight-api drop-in write + restart.
// `error` is null on success; a non-null value means the gate saved but the
// hindsight-api restart failed and should be surfaced to the operator.
export interface MemoryGraphPropagation {
  slot: string
  model: string | null
  drop_in: string
  written: boolean
  daemon_reloaded: boolean
  restarted: boolean
  error: string | null
}

export interface MemoryGraphUpdateResponse extends MemoryGraphStatus {
  status: MemoryGraphStatus
  propagation?: MemoryGraphPropagation
}

// Per-bank tally returned by POST /api/memory/graph/retry.
export interface MemoryGraphRetryResponse {
  queued: number
  skipped: number
  banks: Record<string, { queued: number; skipped: number; failed: number }>
}

const POLL_MS = 15_000

export function useMemoryGraphStatus() {
  return useQuery<MemoryGraphStatus>({
    queryKey: ['memory', 'graph', 'status'],
    queryFn: () => apiGet<MemoryGraphStatus>(ENDPOINTS.memoryGraphStatus),
    // Poll faster while extraction is in flight so the "extracting…" badge
    // tracks live progress; back off to the idle cadence otherwise.
    refetchInterval: (query) => ((query.state.data?.in_flight ?? 0) > 0 ? 3_000 : POLL_MS),
  })
}

export function useUpdateMemoryGraph() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: MemoryGraphUpdate) =>
      apiPut<MemoryGraphUpdateResponse>(
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

// Bulk "retry all failed extractions" — requeues every failed op across banks.
// Invalidates both the graph-status counters and the per-bank operation lists
// so the health panel + operations queue reflect the requeue immediately.
export function useRetryFailedExtractions() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost<MemoryGraphRetryResponse>(ENDPOINTS.memoryGraphRetry),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['memory', 'graph', 'status'] })
      // useHindsight keys its operation lists under ['memory', ...]; a broad
      // invalidate refreshes the bank stats + operations queue too.
      qc.invalidateQueries({ queryKey: ['memory'] })
    },
  })
}

// 0.4 release gate. /api/status carries `memory_enabled`, gated by
// [memory].enabled in hal0.toml at create_app. The dashboard reads it to
// show/hide the Agent → Memory nav so the UI and backend can never disagree.
//
// Treat the loading/unknown state as OFF (`=== true`): 0.4 ships memory
// disabled, so the common case stays hidden with no flicker; a dev build
// with memory on simply reveals the Agent item once status lands
// (sub-second). Distinct query key from useSlots' /api/status race so the
// two consumers don't fight over one cache entry.
export function useMemoryEnabled(): boolean {
  const q = useQuery<{ memory_enabled?: boolean }>({
    queryKey: ['status', 'memory_enabled'],
    queryFn: () => apiGet<{ memory_enabled?: boolean }>(ENDPOINTS.status),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })
  return q.data?.memory_enabled === true
}

// Companion to useMemoryEnabled() — returns true while the /api/status
// query is still in-flight. Same cache key → zero extra requests.
// main.jsx reads this to guard the #agent→#dashboard redirect so the
// redirect doesn't fire during the transient loading window.
export function useMemoryEnabledPending(): boolean {
  const q = useQuery<{ memory_enabled?: boolean }>({
    queryKey: ['status', 'memory_enabled'],
    queryFn: () => apiGet<{ memory_enabled?: boolean }>(ENDPOINTS.status),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })
  return q.isPending
}
