// What the Memory area's component tests feed the mocked hooks: one engine answer, a bank list,
// a health answer, and the answers of the graph-status and models routes. Not imported by the app.
import type { MemoryBank, MemoryEngine } from '@/api/hooks/useHindsight'
import type { MemoryHealth, MemoryHealthReport, MemoryHealthStep } from '@/api/hooks/useMemory'
import { bankTotals, engineState, healthBankRows, readHealth } from './area-pure'
import { parseMemoryRoute, type MemoryRoute } from './memory-route-pure'

/** A query result as the components read it. */
export interface FakeQuery<T> {
  data?: T
  isError?: boolean
  isFetching?: boolean
  error?: { message: string } | null
  refetch?: () => void
}

/**
 * The `area` object MemoryArea hands to a tab, built the way MemoryArea builds it, for a test
 * that mounts a tab on its own. `health` is the body of `GET /api/memory/health` (the all-clear
 * report unless one is given); `healthQuery` replaces the whole query (loading, failed).
 */
export function makeArea(
  over: {
    hash?: string
    engineQuery?: FakeQuery<MemoryEngine>
    banksQuery?: FakeQuery<{ banks: MemoryBank[]; total?: number }>
    health?: MemoryHealth
    healthQuery?: FakeQuery<MemoryHealth>
    replaceRoute?: (patch: Partial<MemoryRoute>) => void
  } = {},
) {
  const engineQuery = over.engineQuery ?? { data: ENGINE }
  const banksQuery = over.banksQuery ?? { data: { banks: BANKS } }
  const banks = banksQuery.data?.banks ?? []
  const healthQuery: FakeQuery<MemoryHealth> = over.healthQuery ?? { data: over.health ?? healthReport() }
  const health = readHealth(healthQuery)
  const report = health.kind === 'ready' ? health.report : null
  const rows = healthBankRows(report)
  return {
    route: parseMemoryRoute(over.hash ?? '#memory/health'),
    replaceRoute: over.replaceRoute ?? (() => {}),
    engine: engineState(engineQuery),
    engineQuery,
    banksQuery,
    banks,
    banksLoading: banksQuery.data === undefined && !banksQuery.isError,
    banksFailed: !!banksQuery.isError && banksQuery.data === undefined,
    health,
    healthQuery,
    statsLoading: health.kind === 'loading',
    rows,
    totals: bankTotals(rows, report?.banks?.total),
  }
}

export const ENGINE: MemoryEngine = {
  enabled: true,
  engine: 'hindsight',
  reachable: true,
  version: '1.2.0',
  features: null,
  banks_total: 3,
}

/** Real id shapes: a double colon, a dot, mixed case. */
export const BANKS: MemoryBank[] = [
  { bank_id: 'shared' },
  { bank_id: 'Hal0::Agents' },
  { bank_id: 'coding-agent::ultrareview-1.4' },
]

/** The same three banks as the health answer lists them. 27,542 facts together. */
export const HEALTH_BANKS: MemoryHealthReport['banks'] = {
  layout: 'per_bank',
  total: 3,
  facts: 27542,
  list: [
    { bank_id: 'shared', facts: 3000, documents: 410 },
    { bank_id: 'Hal0::Agents', facts: 24530, documents: 4100 },
    { bank_id: 'coding-agent::ultrareview-1.4', facts: 12, documents: 2 },
  ],
}

const STEP_ORDER = ['engine', 'extraction', 'queue', 'embedder', 'knowledge']

/**
 * The route's steps for a set of open ones, by its own rule (src/hal0/memory/health_path.py):
 * the first open step is `current`, a later open one is `waiting` and names the open step before
 * it, the rest are `clear`. `open` maps a step id to its reason code and figures.
 */
export function pathSteps(
  open: Record<string, { reason: string; figures?: Record<string, unknown> }> = {},
  order: string[] = STEP_ORDER,
): MemoryHealthStep[] {
  let blocker: string | null = null
  return order.map((id) => {
    const entry = open[id]
    if (!entry) return { id, state: 'clear', waits_on: null, reason_code: null, figures: {} }
    const step: MemoryHealthStep = {
      id,
      state: blocker === null ? 'current' : 'waiting',
      waits_on: blocker,
      reason_code: entry.reason,
      figures: entry.figures ?? {},
    }
    blocker = id
    return step
  })
}

export const EMBEDDER_ID = { provider: 'local', model: 'hs-embed-1' }
export const OLD_EMBEDDER_ID = { provider: 'local', model: 'BAAI/bge-small-en-v1.5' }

/**
 * The body of `GET /api/memory/health` for a box where every step is clear: three banks, nothing
 * waiting, nothing failed, 34 knowledge pages and mental models, all fresh. `over` replaces whole
 * top-level fields; the verdict follows the steps unless it is given.
 */
export function healthReport(over: Partial<MemoryHealthReport> = {}): MemoryHealthReport {
  const steps = over.steps ?? pathSteps()
  return {
    enabled: true,
    verdict: steps.some((step) => step.state !== 'clear') ? 'attention' : 'clear',
    checked_at: new Date().toISOString(),
    partial: [],
    engine: { reachable: true, version: '1.2.0' },
    extraction: {
      slot: 'memory',
      mode: 'dedicated',
      slot_resolves: true,
      llm_reachable: true,
      concurrency: 2,
      failures_6h: 0,
      top_errors: [],
    },
    embedder: {
      recorded: EMBEDDER_ID,
      configured: EMBEDDER_ID,
      mixed: false,
      needs_switch: false,
      conflict: null,
      informational: false,
    },
    queue: {
      waiting: 0,
      failed: 0,
      oldest_waiting_s: null,
      consolidation_pending: 0,
      consolidation_failed: 0,
      by_bank: HEALTH_BANKS!.list.map((bank) => ({
        bank_id: bank.bank_id,
        waiting: 0,
        failed: 0,
        oldest_waiting_s: null,
      })),
      truncated: false,
    },
    knowledge: {
      pages: 29,
      pages_stale: 0,
      models: 5,
      models_stale: 0,
      refresh_failed: 0,
      last_success_at: '2026-11-05T08:00:00+00:00',
    },
    banks: HEALTH_BANKS,
    writes: null,
    ...over,
    steps,
  }
}

/** The engine does not answer: every section but `engine` is null, and the path is one step. */
export function healthUnreachable(): MemoryHealthReport {
  return {
    enabled: true,
    verdict: 'unreachable',
    checked_at: new Date().toISOString(),
    partial: [],
    engine: { reachable: false, version: null },
    extraction: null,
    embedder: null,
    queue: null,
    knowledge: null,
    banks: null,
    writes: null,
    steps: [
      {
        id: 'engine',
        state: 'current',
        waits_on: null,
        reason_code: 'engine_unreachable',
        figures: { reachable: false },
      },
    ],
  }
}

export const GRAPH = {
  enabled: true,
  extraction_slot: 'memory',
  mode: 'dedicated',
  slot_resolves: true,
  available_slots: ['agent', 'memory'],
}

/** The embedder record as GET /api/memory/models carries it. */
export function embedderRecord(over: Record<string, unknown> = {}) {
  return {
    provider: 'local',
    model: 'hs-embed-1',
    recorded: 'hs-embed-1',
    recorded_provider: 'local',
    configured: 'hs-embed-1',
    configured_provider: 'local',
    mixed: false,
    needs_switch: false,
    conflict: null,
    ...over,
  }
}

export function modelsAnswer(embeddings: unknown = embedderRecord()) {
  return {
    embeddings,
    reranker: { provider: 'local', model: 'hs-rerank-1' },
    decision: { enabled: false, provider: 'local', model: 'decider', reachable: false },
  }
}
