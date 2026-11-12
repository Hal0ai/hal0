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
//   useMemoryWorkspaceAgents — GET /api/memory/agents
//   useMemoryModels    — GET /api/memory/models

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

// ── #2030: zero-fact retain visibility ──────────────────────────────────────
//
// Retain is async: /api/memory/add answers before fact extraction runs, and a
// retain whose extraction yields ZERO facts still finishes
// `status=completed, error_message=null` — the raw document is stored but
// nothing from it is ever retrievable, and the only toast the operator saw
// was "Fact added". The server now annotates the terminal operation record
// (`facts_extracted` / `nothing_learned` / `notice` on
// GET /api/memory/banks/{bank}/operations/{id}); this bounded poll carries
// that verdict to the surface the operator actually reads. Fail-soft: any
// fetch error or timeout ends the watch silently — it must never turn an
// engine hiccup into a scary toast for a write that landed fine.

/** Single-op record slice the yield watch reads (server-annotated, #2030). */
export interface RetainYieldOperation {
  status?: string
  facts_extracted?: number
  nothing_learned?: boolean
  notice?: string | null
}

const RETAIN_YIELD_POLL_MS = 3_000
// Extraction is model-bound and can queue behind other ops; ~60s covers the
// fleet-observed completion times without polling forever.
const RETAIN_YIELD_MAX_POLLS = 20

export const NOTHING_LEARNED_FALLBACK =
  'Completed, but nothing was learned: extraction produced 0 facts from that text.'

/**
 * Poll one retain operation to its terminal state and surface a zero-fact
 * completion as a warn toast. Returns a verdict for tests; production
 * callers fire-and-forget. `deps` exist for injection in unit tests only.
 */
export async function watchRetainYield(
  bank: string,
  operationId: string,
  deps?: {
    fetchOp?: (bank: string, id: string) => Promise<RetainYieldOperation>
    notify?: (msg: string, kind: 'warn') => void
    sleep?: (ms: number) => Promise<void>
    maxPolls?: number
  },
): Promise<'nothing_learned' | 'ok' | 'gone' | 'timeout'> {
  const fetchOp =
    deps?.fetchOp ??
    ((b: string, id: string) =>
      apiGet<RetainYieldOperation>(
        `/api/memory/banks/${encodeURIComponent(b)}/operations/${encodeURIComponent(id)}`,
      ))
  const notify =
    deps?.notify ??
    ((msg: string, kind: 'warn') => {
      const toast = (window as unknown as { __hal0Toast?: (m: string, k: string) => void })
        .__hal0Toast
      if (toast) toast(msg, kind)
    })
  const sleep = deps?.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)))
  const maxPolls = deps?.maxPolls ?? RETAIN_YIELD_MAX_POLLS

  for (let i = 0; i < maxPolls; i++) {
    await sleep(RETAIN_YIELD_POLL_MS)
    let op: RetainYieldOperation
    try {
      op = await fetchOp(bank, operationId)
    } catch {
      return 'gone' // op deleted / bank mismatch / engine unreachable
    }
    const status = op?.status
    if (status === 'failed' || status === 'cancelled') return 'ok' // loud elsewhere
    if (status === 'completed') {
      if (op.nothing_learned) {
        notify(op.notice || NOTHING_LEARNED_FALLBACK, 'warn')
        return 'nothing_learned'
      }
      return 'ok'
    }
  }
  return 'timeout'
}

export function useMemoryAdd() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: MemoryAddBody) =>
      apiPost<MemoryAddResponse>(ENDPOINTS.memoryAdd, body as unknown as Record<string, unknown>),
    onSuccess: (data, vars) => {
      // Refresh the written bank's unit/stats/tag caches (falls back to a
      // broad memory invalidate if no dataset was given, matching the
      // server's own dataset-resolution fallback).
      void qc.invalidateQueries({
        queryKey: vars.dataset ? ['memory', 'banks', vars.dataset] : ['memory'],
      })
      // #2030: watch the async extraction to its terminal state so a
      // zero-fact completion is announced instead of leaving "Fact added"
      // as the last word. Needs the concrete bank id — the Add modal always
      // sends one; adds without a dataset skip the watch.
      if (data.operation_id && vars.dataset) {
        void watchRetainYield(vars.dataset, data.operation_id)
      }
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
  // Does extraction_slot match an enabled llm slot right now?
  slot_resolves: boolean
  // Enabled llm slot names the operator may pick.
  available_slots: string[]
  // #2919: the upstream model the engine runs on instead of the slot, or null.
  extraction_model?: string | null
  // #2919: what the engine's fixed id `hal0/memory` resolves to right now.
  target?: MemoryEngineTarget
  // #2919: upstream model ids the operator may pick instead of a slot
  // (remote, non-pinned upstreams, from the dispatcher's models cache).
  available_models?: MemoryEngineModel[]
  // #2918: the LLM hindsight-api really loads, and whether
  // /etc/hal0/hindsight-llm.env overrides hal0's target.
  engine_llm?: MemoryEngineLlm
  // #3388/#3389: whether the hindsight-api unit env carries what hal0 renders.
  // `warning` is one operator sentence, null when the env matches. Absent on
  // older backends.
  engine_env?: {
    checked: boolean
    drift: { key: string; expected: string; effective: string | null }[]
    legacy_drop_in: string | null
    warning: string | null
  }
  // Hindsight daemon LLM timeout ([memory.graph].llm_timeout_s) — echoed by
  // the status route so the settings panel can edit it in one round trip.
  llm_timeout_s?: number
  // #1834 extraction limits ([memory.graph].extraction_*), echoed by the
  // status route for the same reason. Keys are the ExtractionLimits names on
  // the server; the PUT takes them as the schema's extraction_* fields.
  extraction_limits?: MemoryExtractionLimits
  // #2353: the cached throughput estimate for the current extraction slot, or
  // null until something has measured it (PUT enabling the gate / changing the
  // slot, or POST /api/memory/graph/throughput). Absent on an API that
  // predates it.
  throughput?: MemoryExtractionThroughput | null
  in_flight: number
  builds_ok: number
  errors: number
  last_built_at: string | null
  last_error: string | null
}

export interface MemoryEngineTarget {
  kind: 'slot' | 'upstream'
  model: string
  alias: string
}

export interface MemoryEngineModel {
  id: string
  owned_by: string
}

export interface MemoryEngineLlm {
  provider: string | null
  base_url: string | null
  model: string | null
  // null = cannot verify (engine process and env file both unreadable)
  overridden: boolean | null
  override_file: string | null
  override_keys: string[]
  // where the values came from: the running engine, or the unit (+ file)
  source?: 'process' | 'unit+file' | 'unit'
}

export interface MemoryExtractionLimits {
  max_concurrent: number
  max_tokens: number
  llm_retries: number
  task_retries: number
  retry_backoff_s: number
}

// #2353: ExtractionThroughput.as_dict() (hal0/memory/extraction_env.py) — what
// the extraction slot measured, projected onto one extraction call against
// [memory.graph].llm_timeout_s. `message` is the operator-facing sentence.
export interface MemoryExtractionThroughput {
  slot: string
  timeout_s: number
  max_tokens: number
  prompt_tps: number | null
  decode_tps: number | null
  // where the rates came from: the slot's /metrics, one probe, or 'none'
  source: 'metrics' | 'probe' | 'none' | string
  probe_timed_out: boolean
  measured_at: number
  detail: string
  projected_s: number | null
  prompt_tokens: number
  verdict: 'ok' | 'too_slow' | 'unknown'
  message: string
}

export interface MemoryGraphUpdate {
  enabled?: boolean
  extraction_slot?: string
  // #2919: an upstream model id, or null to return to the slot.
  extraction_model?: string | null
  llm_timeout_s?: number
  extraction_max_concurrent?: number
  extraction_max_tokens?: number
  extraction_llm_retries?: number
  extraction_task_retries?: number
  extraction_retry_backoff_s?: number
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
  target?: MemoryEngineTarget
  // #2353: present when the save measured (or re-projected) the extraction
  // slot's throughput. `warnings` is [throughput.message] on a too_slow
  // verdict and absent otherwise — a warning, never a refusal.
  warnings?: string[]
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

// ── Agents (GET /api/memory/agents) ─────────────────────────────────────────
//
// Connected coding-agent banks and the harness install one-liner.
// Read by the Memory area's Agents tab.

export interface MemoryAgentsInstall {
  harnesses: string[]
  command: string
}

export interface MemoryAgents {
  banks: string[]
  install: MemoryAgentsInstall
  /** Hindsight Control Plane console URL from [memory.console]; null when
   * disabled. Absent entirely on older backends that predate this field —
   * callers should fall back to the fixed-port default in that case. */
  console_url?: string | null
}

export function useMemoryWorkspaceAgents() {
  return useQuery({
    queryKey: ['memory', 'agents'],
    queryFn: () => apiGet<MemoryAgents>(ENDPOINTS.memoryAgents),
  })
}

// ── Models (GET /api/memory/models) ─────────────────────────────────────────
//
// Embedding / reranker / decision model identities.
// Read by the Memory area's Health tab.

export interface MemoryModelInfo {
  model: string
  enabled?: boolean
  [key: string]: unknown
}

export interface MemoryModels {
  embeddings: MemoryModelInfo
  reranker: MemoryModelInfo
  decision: MemoryModelInfo
}

export function useMemoryModels() {
  return useQuery({
    queryKey: ['memory', 'models'],
    queryFn: () => apiGet<MemoryModels>(ENDPOINTS.memoryModels),
  })
}

// ════════════════════════════════════════════════════════════════════════════
// Health (GET /api/memory/health)
// ════════════════════════════════════════════════════════════════════════════
//
// Does memory need the operator, and what first: an ordered repair path with
// the figures behind each step. The types mirror the route's response
// (src/hal0/api/routes/memory_health.py and its collector) field for field.
//
// The route answers 200 whatever state the engine is in, so an engine that is
// down is `verdict: 'unreachable'`, not an error. A failed request means
// hal0's own API did not answer.

/** The sections a read can leave unfinished, as `partial` names them. */
export type MemoryHealthSection = 'extraction' | 'embedder' | 'queue' | 'knowledge' | 'banks'

/** One group of failure messages, already reduced to text that is safe to show. */
export interface MemoryHealthErrorGroup {
  message: string
  count: number
}

export interface MemoryHealthExtraction {
  slot: string | null
  /** `dedicated`, `shared` or `custom`. */
  mode: string | null
  /** Whether the slot has a model. `null` when no slot is in play. */
  slot_resolves: boolean | null
  /** Whether the engine's LLM endpoint accepts a connection. `null` when it names none. */
  llm_reachable: boolean | null
  concurrency: number | null
  /** Retain operations that failed in the last 6 hours. */
  failures_6h: number
  /** Up to 5 groups, most frequent first. */
  top_errors: MemoryHealthErrorGroup[]
}

export interface MemoryHealthEmbedderId {
  provider: string | null
  model: string | null
}

export interface MemoryHealthEmbedder {
  recorded: MemoryHealthEmbedderId
  configured: MemoryHealthEmbedderId
  mixed: boolean
  needs_switch: boolean
  conflict: unknown
  /** The two differ only because settings still hold the shipped default. */
  informational: boolean
}

/** One bank's share of the queue. A figure that could not be read is `null`. */
export interface MemoryHealthQueueBank {
  bank_id: string
  waiting: number | null
  failed: number | null
  oldest_waiting_s: number | null
}

export interface MemoryHealthQueue {
  /** Operations in the engine's `pending` state, across banks. Split parent batches are left out. */
  waiting: number
  failed: number
  /** Age of the oldest waiting operation that is due. `null` when none is. */
  oldest_waiting_s: number | null
  /** Source memories still to be consolidated, and those whose consolidation failed. */
  consolidation_pending: number
  consolidation_failed: number
  by_bank: MemoryHealthQueueBank[]
  /** A bank held more operations than were read: counts stay exact, ages and reasons are a floor. */
  truncated: boolean
}

export interface MemoryHealthKnowledge {
  pages: number
  pages_stale: number
  models: number
  models_stale: number
  refresh_failed: number
  last_success_at: string | null
}

export interface MemoryHealthBanks {
  layout: 'per_bank' | string
  total: number | null
  facts: number | null
  list: Array<{ bank_id: string; facts: number | null; documents: number | null }>
}

/**
 * One step of the repair path. The first open step is `current`; a later open
 * step is `waiting` and names the open step before it in `waits_on`.
 */
export interface MemoryHealthStep {
  id: 'engine' | 'extraction' | 'queue' | 'fold_in' | 'embedder' | 'knowledge' | string
  state: 'current' | 'waiting' | 'clear'
  waits_on: string | null
  reason_code: string | null
  figures: Record<string, unknown>
}

/** The answer when there is an engine to report on. */
export interface MemoryHealthReport {
  enabled: true
  verdict: 'clear' | 'attention' | 'unreachable'
  checked_at: string
  /** Sections whose reads did not all finish; their figures are what was read. */
  partial: MemoryHealthSection[]
  engine: { reachable: boolean; version: string | null }
  /** Every section below is `null` while the engine is unreachable. */
  extraction: MemoryHealthExtraction | null
  embedder: MemoryHealthEmbedder | null
  queue: MemoryHealthQueue | null
  knowledge: MemoryHealthKnowledge | null
  banks: MemoryHealthBanks | null
  writes: null
  steps: MemoryHealthStep[]
}

/** The answer with memory off, or on an engine that has no banks or operations. */
export interface MemoryHealthNoPath {
  enabled: boolean
  reason: 'memory_disabled' | 'engine_unsupported'
  checked_at: string
  verdict: null
  partial: []
  steps: []
}

export type MemoryHealth = MemoryHealthReport | MemoryHealthNoPath

export const MEMORY_HEALTH_KEY = ['memory', 'health'] as const

/**
 * The server caches its answer for 10 seconds and one read can cost several
 * dozen engine requests, so this asks every 30 seconds and tries a failed
 * request once more, not three times.
 */
export function useMemoryHealth(opts: { enabled?: boolean } = {}) {
  return useQuery<MemoryHealth>({
    queryKey: MEMORY_HEALTH_KEY,
    queryFn: () => apiGet<MemoryHealth>(ENDPOINTS.memoryHealth),
    enabled: opts.enabled !== false,
    staleTime: 10_000,
    refetchInterval: 30_000,
    retry: 1,
  })
}
